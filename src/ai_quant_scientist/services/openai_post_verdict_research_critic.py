"""OpenAI Responses adapter for the bounded V0.16 post-verdict Critic."""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from ..models.post_verdict_critic import (
    PostVerdictCriticContext,
    PostVerdictCriticDecision,
    PostVerdictCriticDecisionType,
    PostVerdictRevisionKind,
)
from ..models.research import new_id
from .post_verdict_research_critic import context_to_payload
from .post_verdict_research_critic_prompts import (
    CURRENT_POST_VERDICT_RESEARCH_CRITIC_PROMPT_VERSION,
    get_post_verdict_research_critic_instructions,
)

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore


DEFAULT_MODEL = os.getenv("AI_QUANT_SCIENTIST_MODEL", "gpt-5.6-terra")
DEFAULT_PROMPT_VERSION = CURRENT_POST_VERDICT_RESEARCH_CRITIC_PROMPT_VERSION
DEFAULT_REASONING = "medium"
DEFAULT_MAX_OUTPUT_TOKENS = 768


def _extract_compact_provenance(response: Any) -> str:
    usage = getattr(response, "usage", None)
    usage_dict: dict[str, Any] = {}
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


class OpenAIPostVerdictResearchCritic:
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

    def critique(self, context: PostVerdictCriticContext) -> PostVerdictCriticDecision:
        if self._client is None:
            raise RuntimeError("OpenAI client not configured")

        from pydantic import BaseModel, ConfigDict
        from typing import Literal

        class PostVerdictResearchCriticOutputSchema(BaseModel):
            decision: Literal["CONTINUE", "STOP"]
            revision_kind: Literal[
                "SCOPE_PRESERVING_HYPOTHESIS_REVISION",
                "MECHANISM_REVISION",
                "REPLICATION",
                "NONE",
            ]
            diagnosis: str
            next_step_rationale: str
            model_config = ConfigDict(extra="forbid")

        instructions = get_post_verdict_research_critic_instructions(self.prompt_version)
        input_str = json.dumps(context_to_payload(context), sort_keys=True)

        response = self._client.responses.parse(
            model=self.model,
            instructions=instructions,
            input=input_str,
            text_format=PostVerdictResearchCriticOutputSchema,
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
            raise ValueError("Post-verdict Critic: structured output missing or unparseable")

        try:
            if hasattr(parsed, "model_dump"):
                parsed = parsed.model_dump(by_alias=True)
            elif not isinstance(parsed, dict) and hasattr(parsed, "dict"):
                parsed = parsed.dict()
        except Exception:
            pass

        return PostVerdictCriticDecision(
            id=new_id(),
            scientific_verdict_id=context.scientific_verdict_id,
            decision=PostVerdictCriticDecisionType(parsed["decision"]),
            revision_kind=PostVerdictRevisionKind(parsed["revision_kind"]),
            diagnosis=parsed["diagnosis"],
            next_step_rationale=parsed["next_step_rationale"],
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
            raw_response=raw_response_str,
        )
