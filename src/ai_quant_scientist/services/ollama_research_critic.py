"""Ollama-backed ResearchCritic using the local Ollama HTTP API (no API key)."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from ai_quant_scientist.models.critic import CriticDecision, CriticDecisionType, validate_critic_decision
from ai_quant_scientist.services.critic_prompts import get_instructions
from ai_quant_scientist.models.research import new_id
from ai_quant_scientist.services.research_critic import ResearchCritic

DEFAULT_MODEL = os.getenv("OLLAMA_CRITIC_MODEL", "llama3.1:8b")
DEFAULT_PROMPT_VERSION = "v1"
DEFAULT_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))

# Strict JSON schema matching CriticDecisionSchema from the OpenAI adapter
_DECISION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["PROPOSE_REVISION", "NO_USEFUL_REVISION"]},
        "parent_spec_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "change": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "parameter": {"type": "string"},
                        "from": {
                            "anyOf": [
                                {"type": "string"},
                                {"type": "integer"},
                                {"type": "number"},
                                {"type": "boolean"},
                                {"type": "null"},
                            ]
                        },
                        "to": {
                            "anyOf": [
                                {"type": "string"},
                                {"type": "integer"},
                                {"type": "number"},
                                {"type": "boolean"},
                                {"type": "null"},
                            ]
                        },
                    },
                    "required": ["parameter", "from", "to"],
                    "additionalProperties": False,
                },
                {"type": "null"},
            ]
        },
        "rationale": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "prediction": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "confidence": {"anyOf": [{"type": "string", "enum": ["low", "medium", "high"]}, {"type": "null"}]},
    },
    "required": ["decision", "parent_spec_id", "change", "rationale", "prediction", "confidence"],
    "additionalProperties": False,
}


def _build_instructions(prompt_version: str = "v1") -> str:
    return get_instructions(prompt_version)


def _get_context_field(context: Any, key: str, default: Any = None) -> Any:
    if isinstance(context, dict):
        return context.get(key, default)
    return getattr(context, key, default)


class OllamaResearchCritic(ResearchCritic):
    """Research critic backed by a local Ollama model via the /api/chat endpoint."""

    provider = "ollama"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        prompt_version: str = DEFAULT_PROMPT_VERSION,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.model = model
        self.prompt_version = prompt_version
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _build_payload(self, context: Any) -> Dict[str, Any]:
        evaluation = _get_context_field(context, "evaluation") or {}
        # reason_codes may be explicit or nested inside evaluation
        reason_codes = (
            _get_context_field(context, "reason_codes")
            or (evaluation.get("reason_codes") if isinstance(evaluation, dict) else None)
            or []
        )
        ctx: Dict[str, Any] = {
            "hypothesis": _get_context_field(context, "hypothesis"),
            "current_spec": _get_context_field(context, "current_spec"),
            "current_result": _get_context_field(context, "result"),
            "evaluation": evaluation,
            "reason_codes": reason_codes,
            "lineage": _get_context_field(context, "prior_lineage") or [],
            "revision_constraints": _get_context_field(context, "allowed_revision_constraints"),
            "prompt_version": self.prompt_version,
        }
        return ctx

    def critique(self, context: Any) -> CriticDecision:
        instructions = _build_instructions(self.prompt_version)
        ctx_payload = self._build_payload(context)
        user_content = json.dumps(ctx_payload, sort_keys=True)

        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            "format": _DECISION_SCHEMA,
        }

        url = f"{self.base_url}/api/chat"
        body_bytes = json.dumps(request_body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body_bytes,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama HTTP error: {exc}") from exc

        try:
            response_obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Ollama returned non-JSON: {raw[:200]}") from exc

        # Ollama /api/chat response: {"message": {"role": "assistant", "content": "..."}, ...}
        message = response_obj.get("message") or {}
        content_str = message.get("content", "")
        if not content_str:
            raise ValueError("Ollama response missing message.content")

        try:
            parsed = json.loads(content_str)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Ollama message.content is not valid JSON: {content_str[:200]}") from exc

        if not isinstance(parsed, dict):
            raise ValueError("Ollama structured output is not a JSON object")

        raw_decision = parsed.get("decision")
        try:
            dec_type = CriticDecisionType[raw_decision]
        except (KeyError, TypeError):
            raise ValueError(f"Invalid decision value from Ollama: {raw_decision!r}")

        change = parsed.get("change")
        changes: Optional[Dict[str, Any]]
        if change is None:
            changes = None
        elif isinstance(change, dict):
            changes = {change["parameter"]: change.get("to")}
        else:
            raise ValueError(f"Unexpected change shape from Ollama: {change!r}")

        # Capture Ollama provenance fields (durations/token counts)
        provenance = {
            "total_duration": response_obj.get("total_duration"),
            "load_duration": response_obj.get("load_duration"),
            "prompt_eval_count": response_obj.get("prompt_eval_count"),
            "eval_count": response_obj.get("eval_count"),
        }

        decision = CriticDecision(
            id=new_id(),
            research_run_id=_get_context_field(context, "research_run_id"),
            decision_type=dec_type,
            parent_spec_id=parsed.get("parent_spec_id"),
            changes=changes,
            rationale=parsed.get("rationale"),
            prediction=parsed.get("prediction"),
            confidence=parsed.get("confidence"),
            provider=self.provider,
            model=self.model,
            raw_response=json.dumps({"content": parsed, "provenance": provenance}),
        )

        validate_critic_decision(decision)
        return decision
