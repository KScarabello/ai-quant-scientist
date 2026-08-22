"""OpenAI Responses API adapter for the Bounded Hypothesis Scientist."""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

from ..capabilities.models import AssetClass, CANONICAL_FIELDS_BY_DATA_KIND, DataKind, Resolution, ToolKind
from ..capabilities.serialization import requirements_to_json
from ..models.hypothesis_scientist import (
    HypothesisScientistDecision,
    HypothesisScientistDecisionType,
    ResearchBrief,
)
from ..models.research import new_id
from ..services.hypothesis_prompts import get_scientist_instructions
from ..services.hypothesis_scientist import brief_to_payload
from ..services.scientist_requirement_ontology import build_requirement_ontology_snapshot

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore

DEFAULT_MODEL = os.getenv("AI_QUANT_SCIENTIST_MODEL", "gpt-5.6-terra")
DEFAULT_PROMPT_VERSION = "v2"
DEFAULT_REASONING = "medium"
DEFAULT_MAX_OUTPUT_TOKENS = 1024
ALL_CANONICAL_FIELD_NAMES = tuple(sorted({
    field_name
    for field_names in CANONICAL_FIELDS_BY_DATA_KIND.values()
    for field_name in field_names
}))


def _extract_compact_provenance(response: Any) -> str:
    usage = getattr(response, "usage", None)
    usage_dict: dict = {}
    if usage is not None:
        usage_dict = {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }
        out_det = getattr(usage, "output_tokens_details", None)
        if out_det is not None:
            usage_dict["reasoning_tokens"] = getattr(out_det, "reasoning_tokens", None)
    output_text = None
    for out in getattr(response, "output", None) or []:
        if getattr(out, "type", None) == "message":
            for item in getattr(out, "content", []) or []:
                if getattr(item, "type", None) == "output_text":
                    output_text = getattr(item, "text", None)
                    break
    return json.dumps({
        "response_id": getattr(response, "id", None),
        "model": getattr(response, "model", None),
        "status": getattr(response, "status", None),
        "created_at": getattr(response, "created_at", None),
        "completed_at": getattr(response, "completed_at", None),
        "store": False,
        "usage": usage_dict or None,
        "output_text": output_text,
    })


class OpenAIHypothesisScientist:
    """Hypothesis Scientist backed by the OpenAI Responses API with Structured Outputs.

    Authority: propose exactly one ResearchCandidate proposal.
    Cannot declare feasibility, assign governance fields, or run research.
    """

    provider = "openai"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        prompt_version: str = DEFAULT_PROMPT_VERSION,
        reasoning: str = DEFAULT_REASONING,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        client: Optional[Any] = None,
    ) -> None:
        self.model = model
        self.prompt_version = prompt_version
        self.reasoning = reasoning
        self.max_output_tokens = max_output_tokens
        self._client = client or (OpenAI() if OpenAI is not None else None)

    def generate(self, brief: ResearchBrief) -> HypothesisScientistDecision:
        if self._client is None:
            raise RuntimeError("OpenAI client not configured")

        from pydantic import BaseModel, ConfigDict
        from typing import Literal

        CanonicalFieldName = Literal.__getitem__(ALL_CANONICAL_FIELD_NAMES)

        class DataRequirementSchema(BaseModel):
            requirement_id: str
            data_kind: Literal[
                "OHLCV", "TRADES", "QUOTES", "ORDER_BOOK", "FUNDAMENTALS",
                "CORPORATE_ACTIONS", "EARNINGS", "BORROW", "ALTERNATIVE", "SYNTHETIC_PARAMETRIC"
            ]
            asset_class: Literal[
                "EQUITY", "FUTURES", "OPTIONS", "CRYPTO", "FX", "FIXED_INCOME", "SYNTHETIC"
            ] | None = None
            resolution: Literal[
                "TICK", "1s", "1m", "5m", "15m", "30m", "1h", "1d", "1w", "1M", "N/A"
            ] | None = None
            required_fields: list[CanonicalFieldName] | None = None
            instruments: list[str] | None = None
            point_in_time_required: bool = False
            required_parameters: list[str] | None = None
            model_config = ConfigDict(extra="forbid")

        class ToolRequirementSchema(BaseModel):
            requirement_id: str
            tool_kind: Literal[
                "BACKTEST_EXECUTION",
                "SYNTHETIC_DATA_GENERATION",
                "STATISTICAL_ANALYSIS",
                "MARKET_DATA_RESEARCH",
            ]
            label: str = ""
            model_config = ConfigDict(extra="forbid")

        class HypothesisOutputSchema(BaseModel):
            decision: Literal["PROPOSE_HYPOTHESIS", "NO_HYPOTHESIS"]
            hypothesis_statement: str | None = None
            hypothesis_rationale: str | None = None
            data_requirements: list[DataRequirementSchema] | None = None
            tool_requirements: list[ToolRequirementSchema] | None = None
            no_hypothesis_reason: str | None = None
            model_config = ConfigDict(extra="forbid")

        instructions = get_scientist_instructions(self.prompt_version)
        input_str = json.dumps(brief_to_payload(brief), sort_keys=True)
        ontology = build_requirement_ontology_snapshot()

        try:
            response = self._client.responses.parse(
                model=self.model,
                instructions=instructions,
                input=input_str,
                text_format=HypothesisOutputSchema,
                reasoning={"effort": self.reasoning},
                max_output_tokens=self.max_output_tokens,
                store=False,
            )
        except Exception:
            raise

        raw_response_str = _extract_compact_provenance(response)

        # Extract parsed from response.output
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
            raise ValueError("Hypothesis scientist: structured output missing or unparseable")

        try:
            if hasattr(parsed, "model_dump"):
                parsed = parsed.model_dump(by_alias=True)
            elif not isinstance(parsed, dict) and hasattr(parsed, "dict"):
                parsed = parsed.dict()
        except Exception:
            pass

        try:
            dec_type = HypothesisScientistDecisionType[parsed.get("decision")]
        except (KeyError, TypeError):
            raise ValueError(f"Invalid decision type from provider: {parsed.get('decision')!r}")

        requirements_snapshot = None
        if dec_type == HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS:
            from ..capabilities.models import DataKind as DK, AssetClass as AC, Resolution as R, DataRequirement, ToolRequirement
            reqs = []
            for dr in (parsed.get("data_requirements") or []):
                if isinstance(dr, dict):
                    d = dr
                else:
                    d = dr
                reqs.append(DataRequirement(
                    requirement_id=d.get("requirement_id", new_id()),
                    data_kind=DK[d["data_kind"]],
                    asset_class=AC[d["asset_class"]] if d.get("asset_class") else None,
                    resolution=next((rv for rv in R if rv.value == d["resolution"]), None) if d.get("resolution") else None,
                    required_fields=tuple(d["required_fields"]) if d.get("required_fields") else None,
                    instruments=tuple(d["instruments"]) if d.get("instruments") else None,
                    point_in_time_required=d.get("point_in_time_required", False),
                    required_parameters=tuple(d["required_parameters"]) if d.get("required_parameters") else None,
                ))
            for tr in (parsed.get("tool_requirements") or []):
                if isinstance(tr, dict):
                    t = tr
                else:
                    t = tr
                reqs.append(ToolRequirement(
                    requirement_id=t.get("requirement_id", new_id()),
                    tool_kind=ToolKind(t["tool_kind"]),
                    label=t.get("label", ""),
                ))
            requirements_snapshot = requirements_to_json(tuple(reqs))

        return HypothesisScientistDecision(
            id=new_id(),
            decision_type=dec_type,
            research_brief_id=brief.id,
            hypothesis_statement=parsed.get("hypothesis_statement"),
            hypothesis_rationale=parsed.get("hypothesis_rationale"),
            requirements_snapshot=requirements_snapshot,
            no_hypothesis_reason=parsed.get("no_hypothesis_reason"),
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
            ontology_version=ontology.version,
            ontology_fingerprint=ontology.fingerprint,
            raw_response=raw_response_str,
        )
