from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from typing import Any, Dict, Optional

from ai_quant_scientist.models.critic import CriticDecision, CriticDecisionType, CriticInvocation
from ai_quant_scientist.models.research import new_id
from ai_quant_scientist.services.research_critic import ResearchCritic

try:
    # Modern OpenAI SDK: client = OpenAI()
    from openai import OpenAI
except Exception:  # pragma: no cover - tests will mock imports
    OpenAI = None  # type: ignore


DEFAULT_MODEL = os.getenv("AI_QUANT_CRITIC_MODEL", "gpt-5.6-luna")
DEFAULT_PROMPT_VERSION = "v1"
DEFAULT_REASONING = "medium"
DEFAULT_MAX_OUTPUT_TOKENS = 512


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

    def _build_messages(self, context: Any) -> Dict[str, Any]:
        # Convert CriticContext into compact structured input
        inp = {
            "hypothesis": getattr(context, "hypothesis", None),
            "current_spec": getattr(context, "current_spec", None),
            "current_result": getattr(context, "current_result", None),
            "evaluation": getattr(context, "evaluation", None),
            "reason_codes": getattr(context, "reason_codes", None) or [],
            "lineage": getattr(context, "bounded_lineage", None) or [],
            "revision_constraints": getattr(context, "revision_constraints", None),
            "prompt_version": self.prompt_version,
        }
        return inp

    def _build_instructions(self, context: Any) -> str:
        # Minimal prompt per V0.6 critic instructions (v1)
        return (
            "You are a bounded quantitative research critic.\n"
            "Given: hypothesis, the current frozen ResearchSpec, measured results, a deterministic evaluation and reason codes, bounded prior lineage, and revision constraints.\n"
            "Return exactly one of: PROPOSE_REVISION (single parameter change) or NO_USEFUL_REVISION (no bounded follow-up justified).\n"
            "If proposing, include parent_spec_id, parameter, from, to, a concise rationale, a falsifiable prediction, and a confidence level.\n"
            "Do not introduce new parameters, redesign the strategy, or execute tests. Keep answers concise."
        )

    def critique(self, context) -> CriticDecision:
        # We'll capture raw response and usage locally; persistence is handled by caller
        invoked_at = time.time()
        raw_response_str = None


        if self._client is None:
            raise RuntimeError("OpenAI client not configured")

        # Build structured output schema and input
        schema = self._build_structured_schema()
        payload = self._build_messages(context)

        # attach request payload for provenance
        request_json = json.dumps({"model": self.model, "input": payload})

        # Responses API parse call (SDK v3.3.0): use explicit `responses.parse` with provider-specific Pydantic model
        instructions = self._build_instructions(context)

        # Provider-specific Pydantic model (required)
        from pydantic import BaseModel, Field
        from pydantic import ConfigDict

        class CriticChangeSchema(BaseModel):
            parameter: str
            from_value: Any | None = Field(alias="from")
            to: Any | None

            model_config = ConfigDict(extra="forbid")

        class CriticDecisionSchema(BaseModel):
            decision: str
            parent_spec_id: str | None = None
            change: CriticChangeSchema | None = None
            rationale: str | None = None
            prediction: str | None = None
            confidence: str | None = None

            model_config = ConfigDict(extra="forbid", populate_by_name=True)

        text_format = CriticDecisionSchema

        try:
            # The Responses API expects `input` to be a string or an array of input items.
            # Serialize deterministically to a JSON string so SDK receives a stable input.
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

        # Save raw response
        try:
            raw_response_str = json.dumps(response.__dict__ if hasattr(response, "__dict__") else response)
        except Exception:
            raw_response_str = str(response)

        # Extract usage metadata if present
        usage = getattr(response, "usage", None) or getattr(response, "meta", {}).get("usage", None)
        if usage:
            # include usage in raw_response for later persistence
            try:
                usage_json = json.dumps(usage)
                raw_response_str = json.dumps({"response": response.__dict__ if hasattr(response, "__dict__") else str(response), "usage": usage})
            except Exception:
                pass

        # SDK v3.3.0: use the parsed result returned by responses.parse()
        parsed = getattr(response, "parsed", None)
        if not parsed:
            raise ValueError("Structured output missing or unparseable")

        # If parsed is a Pydantic model instance, convert to dict using by-alias
        try:
            if hasattr(parsed, "model_dump"):
                parsed = parsed.model_dump(by_alias=True)
            elif isinstance(parsed, dict):
                parsed = parsed
            elif hasattr(parsed, "dict"):
                parsed = parsed.dict()
        except Exception:
            pass

        # Map parsed dict to CriticDecision
        decision_raw = parsed
        try:
            dec_type = CriticDecisionType[decision_raw.get("decision")]
        except Exception:
            raise ValueError("Invalid decision type from provider")

        parent_spec_id = decision_raw.get("parent_spec_id")
        change = decision_raw.get("change")
        if change is None:
            changes = None
        else:
            # convert to application change dict {parameter: to}
            changes = {change["parameter"]: change.get("to")}

        decision = CriticDecision(
            id=new_id(),
            research_run_id=getattr(context, "research_run_id", None),
            decision_type=dec_type,
            parent_spec_id=parent_spec_id,
            changes=changes,
            rationale=decision_raw.get("rationale"),
            prediction=decision_raw.get("prediction"),
            confidence=decision_raw.get("confidence"),
            provider=self.provider,
            model=self.model,
            raw_response=raw_response_str,
        )

        return decision
