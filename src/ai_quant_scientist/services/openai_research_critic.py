from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from typing import Any, Dict, Optional

from ai_quant_scientist.models.critic import CriticDecision, CriticDecisionType, CriticInvocation, validate_critic_decision
from ai_quant_scientist.services.critic_prompts import get_instructions
from ai_quant_scientist.models.research import new_id
from ai_quant_scientist.models.revision import (
    ExperimentType,
    RevisionDirection,
    RevisionIntent,
    validate_revision_intent,
)
from ai_quant_scientist.services.research_critic import ResearchCritic, build_default_constraints
from ai_quant_scientist.services.revision_planner import PlannerRejectionError, RevisionPlanner

try:
    # Modern OpenAI SDK: client = OpenAI()
    from openai import OpenAI
except Exception:  # pragma: no cover - tests will mock imports
    OpenAI = None  # type: ignore


DEFAULT_MODEL = os.getenv("AI_QUANT_CRITIC_MODEL", "gpt-5.6-luna")
DEFAULT_PROMPT_VERSION = "v1"
DEFAULT_REASONING = "medium"
DEFAULT_MAX_OUTPUT_TOKENS = 512


def _get(obj: Any, key: str) -> Any:
    """Read a field from either a dict or an SDK object."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _extract_compact_provenance(response: Any) -> str:
    """Return a compact JSON provenance string — no encrypted reasoning or schema dumps."""
    usage = getattr(response, "usage", None)
    usage_dict: Dict[str, Any] = {}
    if usage is not None:
        usage_dict = {
            "input_tokens": _get(usage, "input_tokens"),
            "output_tokens": _get(usage, "output_tokens"),
        }
        out_det = _get(usage, "output_tokens_details")
        if out_det is not None:
            usage_dict["reasoning_tokens"] = _get(out_det, "reasoning_tokens")
        in_det = _get(usage, "input_tokens_details")
        if in_det is not None:
            usage_dict["cached_tokens"] = _get(in_det, "cached_tokens")

    output_text: Optional[str] = None
    for out in getattr(response, "output", None) or []:
        if getattr(out, "type", None) == "message":
            for item in getattr(out, "content", []) or []:
                if getattr(item, "type", None) == "output_text":
                    output_text = getattr(item, "text", None)
                    break

    prov = {
        "response_id": getattr(response, "id", None),
        "model": getattr(response, "model", None),
        "status": getattr(response, "status", None),
        "created_at": getattr(response, "created_at", None),
        "completed_at": getattr(response, "completed_at", None),
        "store": False,
        "usage": usage_dict or None,
        "output_text": output_text,
    }
    try:
        return json.dumps(prov)
    except Exception:
        return json.dumps({"error": "provenance_serialisation_failed"})


class OpenAIResearchCritic(ResearchCritic):
    """Adapter that calls OpenAI Responses API with Structured Output schema.

    This adapter only proposes revisions (or NO_USEFUL_REVISION). It does not
    accept or apply proposals. All provider responses are recorded in
    `CriticInvocation` payloads and parsed into `CriticDecision` objects.
    """

    provider = "openai"

    def __init__(self, model: str = DEFAULT_MODEL, prompt_version: str = DEFAULT_PROMPT_VERSION, reasoning: str = DEFAULT_REASONING, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS, client: Optional[Any] = None):
        self.model = model
        self.prompt_version = prompt_version
        self.reasoning = reasoning
        self.max_output_tokens = max_output_tokens
        self._client = client or (OpenAI() if OpenAI is not None else None)

    def _build_structured_schema(self) -> Dict[str, Any]:
        # Schema defines decision, parent_spec_id, change (parameter/from/to), rationale, prediction, confidence
        return {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["PROPOSE_REVISION", "NO_USEFUL_REVISION"]},
                "parent_spec_id": {"type": ["string", "null"]},
                "change": {
                    "type": ["object", "null"],
                    "properties": {
                        "parameter": {"type": "string"},
                        "from": {"type": ["number", "string", "null"]},
                        "to": {"type": ["number", "string", "null"]},
                    },
                    "required": ["parameter", "from", "to"],
                },
                "rationale": {"type": ["string", "null"]},
                "prediction": {"type": ["string", "null"]},
                "confidence": {"type": ["string", "null"]},
            },
            "required": ["decision"],
        }

    @staticmethod
    def _ctx(context: Any, key: str, default: Any = None) -> Any:
        """Read a field from either a dict or a dataclass/object context."""
        if isinstance(context, dict):
            return context.get(key, default)
        return getattr(context, key, default)

    def _build_messages(self, context: Any) -> Dict[str, Any]:
        evaluation = self._ctx(context, "evaluation") or {}
        # reason_codes may be explicit or nested inside evaluation
        reason_codes = (
            self._ctx(context, "reason_codes")
            or (evaluation.get("reason_codes") if isinstance(evaluation, dict) else None)
            or []
        )
        inp = {
            "hypothesis": self._ctx(context, "hypothesis"),
            "current_spec": self._ctx(context, "current_spec"),
            "current_result": self._ctx(context, "result"),
            "evaluation": evaluation,
            "reason_codes": reason_codes,
            "lineage": self._ctx(context, "prior_lineage") or [],
            "revision_constraints": self._ctx(context, "allowed_revision_constraints"),
            "prompt_version": self.prompt_version,
        }
        return inp

    def _build_instructions(self, context: Any) -> str:
        return get_instructions(self.prompt_version)

    def critique(self, context) -> CriticDecision:
        import dataclasses as _dc
        raw_response_str = None

        if self._client is None:
            raise RuntimeError("OpenAI client not configured")

        payload = self._build_messages(context)
        instructions = self._build_instructions(context)

        from pydantic import BaseModel, Field
        from pydantic import ConfigDict
        from typing import Literal

        class CriticIntentSchema(BaseModel):
            parameter: str
            direction: Literal["INCREASE", "DECREASE", "PERTURB"]
            experiment_type: Literal["MECHANISTIC_DIAGNOSTIC", "PARAMETER_SENSITIVITY"]
            model_config = ConfigDict(extra="forbid")

        class CriticDecisionSchema(BaseModel):
            decision: Literal["PROPOSE_REVISION", "NO_USEFUL_REVISION"]
            intent: CriticIntentSchema | None = None
            rationale: str | None = None
            prediction: str | None = None
            confidence: Literal["low", "medium", "high"] | None = None
            model_config = ConfigDict(extra="forbid", populate_by_name=True)

        text_format = CriticDecisionSchema

        try:
            input_str = json.dumps(payload, sort_keys=True)
            response = self._client.responses.parse(
                model=self.model,
                instructions=instructions,
                input=input_str,
                text_format=text_format,
                reasoning={"effort": self.reasoning},
                max_output_tokens=self.max_output_tokens,
                store=False,
            )
        except Exception:
            raise

        raw_response_str = _extract_compact_provenance(response)

        parsed = None
        for out in getattr(response, "output", None) or []:
            if getattr(out, "type", None) != "message":
                continue
            for item in getattr(out, "content", []) or []:
                if getattr(item, "type", None) == "output_text":
                    parsed = getattr(item, "parsed", None)
                    if parsed is not None:
                        break
            if parsed is not None:
                break
        if not parsed:
            raise ValueError("Structured output missing or unparseable")

        try:
            if hasattr(parsed, "model_dump"):
                parsed = parsed.model_dump(by_alias=True)
            elif not isinstance(parsed, dict) and hasattr(parsed, "dict"):
                parsed = parsed.dict()
        except Exception:
            pass

        try:
            dec_type = CriticDecisionType[parsed.get("decision")]
        except Exception:
            raise ValueError("Invalid decision type from provider")

        # parent_spec_id is authoritative from context, never from AI output
        authoritative_spec_id = (self._ctx(context, "current_spec") or {}).get("id")

        if dec_type == CriticDecisionType.NO_USEFUL_REVISION:
            decision = CriticDecision(
                id=new_id(),
                research_run_id=self._ctx(context, "research_run_id"),
                decision_type=dec_type,
                parent_spec_id=authoritative_spec_id,
                changes=None,
                rationale=parsed.get("rationale"),
                prediction=parsed.get("prediction"),
                confidence=parsed.get("confidence"),
                provider=self.provider,
                model=self.model,
                raw_response=raw_response_str,
            )
            validate_critic_decision(decision)
            return decision

        # PROPOSE_REVISION — fail closed if authoritative spec ID is unavailable
        if not authoritative_spec_id:
            raise ValueError("Cannot propose revision: authoritative current_spec has no id")

        # PROPOSE_REVISION — parse intent and run deterministic planner
        intent_raw = parsed.get("intent")
        if not intent_raw:
            raise ValueError("PROPOSE_REVISION requires intent field")

        if isinstance(intent_raw, dict):
            intent_param = intent_raw.get("parameter")
            direction_raw = intent_raw.get("direction")
            experiment_raw = intent_raw.get("experiment_type")
        else:
            intent_param = getattr(intent_raw, "parameter", None)
            direction_raw = getattr(intent_raw, "direction", None)
            experiment_raw = getattr(intent_raw, "experiment_type", None)

        try:
            direction = RevisionDirection[direction_raw]
            experiment_type = ExperimentType[experiment_raw]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Invalid intent direction or experiment_type: {exc}") from exc

        intent = RevisionIntent(
            id=new_id(),
            research_run_id=self._ctx(context, "research_run_id"),
            parent_spec_id=authoritative_spec_id,
            parameter=intent_param,
            direction=direction,
            experiment_type=experiment_type,
            rationale=parsed.get("rationale"),
            prediction=parsed.get("prediction"),
            confidence=parsed.get("confidence"),
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
        )
        validate_revision_intent(intent)

        current_spec = self._ctx(context, "current_spec") or {}
        constraints = self._ctx(context, "allowed_revision_constraints") or build_default_constraints()
        lineage = self._ctx(context, "prior_lineage") or []

        plan_result = RevisionPlanner().plan(intent, current_spec, constraints, lineage)
        if plan_result.rejection_reason is not None:
            raise PlannerRejectionError(plan_result.rejection_reason, plan_result)

        decision = CriticDecision(
            id=new_id(),
            research_run_id=self._ctx(context, "research_run_id"),
            decision_type=dec_type,
            parent_spec_id=authoritative_spec_id,
            changes=plan_result.planned_change,
            rationale=intent.rationale,
            prediction=intent.prediction,
            confidence=intent.confidence,
            provider=self.provider,
            model=self.model,
            raw_response=raw_response_str,
            revision_intent=_dc.asdict(intent),
            planner_version=plan_result.planner_version,
        )
        validate_critic_decision(decision)
        return decision
