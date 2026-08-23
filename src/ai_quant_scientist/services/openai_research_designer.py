"""OpenAI Responses API adapter for the bounded Research Designer V1."""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from ..models.design import (
    AnalysisIntent,
    ComparisonIntent,
    DesignOutcome,
    DesignVariable,
    ExpectedDirection,
    OutcomePrediction,
    ResearchDesignKind,
)
from ..models.research import new_id
from ..models.research_designer import (
    ResearchDesignerContext,
    ResearchDesignerDecision,
    ResearchDesignerDecisionType,
)
from .research_designer import context_to_payload
from .research_designer_prompts import get_research_designer_instructions

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore


DEFAULT_MODEL = os.getenv("AI_QUANT_SCIENTIST_MODEL", "gpt-5.6-terra")
DEFAULT_PROMPT_VERSION = "v3"
DEFAULT_REASONING = "medium"
DEFAULT_MAX_OUTPUT_TOKENS = 1024


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
    return json.dumps(
        {
            "response_id": getattr(response, "id", None),
            "model": getattr(response, "model", None),
            "status": getattr(response, "status", None),
            "created_at": getattr(response, "created_at", None),
            "completed_at": getattr(response, "completed_at", None),
            "store": False,
            "usage": usage_dict or None,
            "output_text": output_text,
        }
    )


class OpenAIResearchDesigner:
    """Research Designer backed by the OpenAI Responses API with Structured Outputs."""

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

    def design(self, context: ResearchDesignerContext) -> ResearchDesignerDecision:
        if self._client is None:
            raise RuntimeError("OpenAI client not configured")

        from pydantic import BaseModel, ConfigDict
        from typing import Literal

        if self.prompt_version == "v2":
            class PredictionSchema(BaseModel):
                outcome: Literal["trade_count", "net_pnl", "sharpe"]
                expected_direction: Literal["INCREASE", "DECREASE", "NO_CHANGE"]
                model_config = ConfigDict(extra="forbid")

            class ResearchDesignerOutputSchema(BaseModel):
                decision: Literal["DESIGN", "NO_VALID_DESIGN"]
                design_kind: Literal["PARAMETER_SENSITIVITY"] | None = None
                independent_variables: list[Literal["signal_threshold", "lookback"]] | None = None
                dependent_outcomes: list[Literal["trade_count", "net_pnl", "sharpe"]] | None = None
                controls: list[Literal["signal_threshold", "lookback"]] | None = None
                comparison_intent: Literal["CONTRAST_PARAMETER_LEVELS"] | None = None
                analysis_intent: Literal["SENSITIVITY_ANALYSIS"] | None = None
                predictions: list[PredictionSchema] | None = None
                falsification_condition: str | None = None
                rationale: str | None = None
                no_valid_design_reason: str | None = None
                model_config = ConfigDict(extra="forbid")
        else:
            class ResearchDesignerOutputSchema(BaseModel):
                decision: Literal["DESIGN", "NO_VALID_DESIGN"]
                design_kind: Literal["PARAMETER_SENSITIVITY"] | None = None
                independent_variables: list[Literal["signal_threshold", "lookback"]] | None = None
                dependent_outcomes: list[Literal["trade_count", "net_pnl", "sharpe"]] | None = None
                controls: list[Literal["signal_threshold", "lookback"]] | None = None
                comparison_intent: Literal["CONTRAST_PARAMETER_LEVELS"] | None = None
                analysis_intent: Literal["SENSITIVITY_ANALYSIS"] | None = None
                falsification_condition: str | None = None
                rationale: str | None = None
                no_valid_design_reason: str | None = None
                model_config = ConfigDict(extra="forbid")

        instructions = get_research_designer_instructions(self.prompt_version)
        input_str = json.dumps(context_to_payload(context), sort_keys=True)

        response = self._client.responses.parse(
            model=self.model,
            instructions=instructions,
            input=input_str,
            text_format=ResearchDesignerOutputSchema,
            reasoning={"effort": self.reasoning},
            max_output_tokens=self.max_output_tokens,
            store=False,
        )

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
            raise ValueError("Research designer: structured output missing or unparseable")

        try:
            if hasattr(parsed, "model_dump"):
                parsed = parsed.model_dump(by_alias=True)
            elif not isinstance(parsed, dict) and hasattr(parsed, "dict"):
                parsed = parsed.dict()
        except Exception:
            pass

        try:
            decision_type = ResearchDesignerDecisionType[parsed.get("decision")]
        except (KeyError, TypeError):
            raise ValueError(f"Invalid decision type from provider: {parsed.get('decision')!r}")

        return ResearchDesignerDecision(
            id=new_id(),
            candidate_id=context.candidate_id,
            decision_type=decision_type,
            design_kind=(
                ResearchDesignKind(parsed["design_kind"])
                if parsed.get("design_kind")
                else None
            ),
            independent_variables=(
                tuple(DesignVariable(item) for item in parsed.get("independent_variables", []))
                if parsed.get("independent_variables") is not None
                else None
            ),
            dependent_outcomes=(
                tuple(DesignOutcome(item) for item in parsed.get("dependent_outcomes", []))
                if parsed.get("dependent_outcomes") is not None
                else None
            ),
            controls=(
                tuple(DesignVariable(item) for item in parsed.get("controls", []))
                if parsed.get("controls") is not None
                else None
            ),
            comparison_intent=(
                ComparisonIntent(parsed["comparison_intent"])
                if parsed.get("comparison_intent")
                else None
            ),
            analysis_intent=(
                AnalysisIntent(parsed["analysis_intent"])
                if parsed.get("analysis_intent")
                else None
            ),
            predictions=(
                tuple(
                    OutcomePrediction(
                        outcome=DesignOutcome(item["outcome"]),
                        expected_direction=ExpectedDirection(item["expected_direction"]),
                    )
                    for item in parsed.get("predictions", [])
                )
                if parsed.get("predictions") is not None
                else None
            ),
            falsification_condition=parsed.get("falsification_condition"),
            rationale=parsed.get("rationale"),
            no_valid_design_reason=parsed.get("no_valid_design_reason"),
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
            ontology_version=context.design_ontology_version,
            ontology_fingerprint=context.design_ontology_fingerprint,
            raw_response=raw_response_str,
        )
