"""Regression tests for the canonical CriticContext plumbing fix.

All tests are deterministic (zero API calls).

These tests document and enforce the plumbing contract that was broken before the
canonical build_critic_context() builder was introduced:
  - live runners were passing raw case.context dicts (no revision constraints)
  - reason_codes at top-level was always empty (only present inside evaluation)
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from ai_quant_scientist.evals.critic_eval import (
    CriticEvalSuite,
    build_critic_context,
    load_cases_from_file,
)
from ai_quant_scientist.evals.run_live_critic_eval import run_live_eval
from ai_quant_scientist.evals.run_ollama_critic_eval import run_ollama_eval
from ai_quant_scientist.models.critic import CriticContext
from ai_quant_scientist.services.ollama_research_critic import OllamaResearchCritic
from ai_quant_scientist.services.openai_research_critic import OpenAIResearchCritic
from ai_quant_scientist.services.research_critic import build_default_constraints


# ─── fixture helpers ─────────────────────────────────────────────────────────

def _load(case_id: str):
    return next(c for c in load_cases_from_file("evals/critic_v1.json") if c.id == case_id)


def _make_response(parsed: dict):
    """Minimal fake OpenAI SDK response."""
    text = json.dumps(parsed)
    item = SimpleNamespace(type="output_text", parsed=parsed, text=text)
    msg = SimpleNamespace(type="message", content=[item])
    return SimpleNamespace(
        output=[msg], usage={}, id="r1", model="m",
        status="completed", created_at=1.0, completed_at=2.0,
    )


def _make_ollama_resp(parsed: dict) -> bytes:
    return json.dumps({
        "message": {"role": "assistant", "content": json.dumps(parsed)},
        "done": True, "total_duration": 1, "load_duration": 1,
        "prompt_eval_count": 1, "eval_count": 1,
    }).encode()


_NO_USEFUL = {
    "decision": "NO_USEFUL_REVISION", "parent_spec_id": None,
    "change": None, "rationale": "none", "prediction": None, "confidence": None,
}


# ─── 1 & 2: canonical context has non-null constraints ───────────────────────

def test_case_02_canonical_context_has_revision_constraints():
    ctx = build_critic_context(_load("case-02"))
    assert ctx.allowed_revision_constraints is not None


def test_case_06_canonical_context_has_revision_constraints():
    ctx = build_critic_context(_load("case-06"))
    assert ctx.allowed_revision_constraints is not None


# ─── 3 & 4: expected parameters are present in constraints ───────────────────

def test_case_02_signal_threshold_constraint_present():
    ctx = build_critic_context(_load("case-02"))
    assert "signal_threshold" in ctx.allowed_revision_constraints


def test_case_06_lookback_constraint_present():
    ctx = build_critic_context(_load("case-06"))
    assert "lookback" in ctx.allowed_revision_constraints


# ─── regression: raw case.context would have null constraints ────────────────

def test_raw_context_dict_lacks_constraints_old_bug():
    """This test documents the bug: the raw fixture dict has no allowed_revision_constraints."""
    case = _load("case-02")
    assert case.context.get("allowed_revision_constraints") is None  # old bug
    ctx = build_critic_context(case)
    assert ctx.allowed_revision_constraints is not None  # fix


def test_canonical_context_is_critiquecontext_instance():
    ctx = build_critic_context(_load("case-06"))
    assert isinstance(ctx, CriticContext)


# ─── 5: OpenAI live runner uses canonical context ────────────────────────────

def test_openai_live_runner_passes_critiquecontext_to_critique(tmp_path):
    client = MagicMock()
    client.responses.parse.return_value = _make_response(_NO_USEFUL)
    contexts_seen: list = []

    def fake_init(self, model=None, prompt_version="v1", **kw):
        self.model = model or "m"
        self.prompt_version = prompt_version
        self.reasoning = "medium"
        self.max_output_tokens = 512
        self._client = client

    original_critique = OpenAIResearchCritic.critique

    def capturing_critique(self, context):
        contexts_seen.append(context)
        return original_critique(self, context)

    with patch.object(OpenAIResearchCritic, "__init__", fake_init), \
         patch.object(OpenAIResearchCritic, "critique", capturing_critique):
        run_live_eval(
            model="m", eval_path="evals/critic_v1.json",
            allow_live_api=True, max_cases=1, output_dir=str(tmp_path),
        )

    assert len(contexts_seen) == 1
    assert isinstance(contexts_seen[0], CriticContext)
    assert contexts_seen[0].allowed_revision_constraints is not None


# ─── 6: Ollama live runner uses canonical context ────────────────────────────

def test_ollama_live_runner_passes_critiquecontext_to_critique(tmp_path):
    contexts_seen: list = []
    resp_bytes = _make_ollama_resp(_NO_USEFUL)

    def fake_urlopen(req, timeout=None):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=SimpleNamespace(read=lambda: resp_bytes))
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    original_critique = OllamaResearchCritic.critique

    def capturing_critique(self, context):
        contexts_seen.append(context)
        return original_critique(self, context)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
         patch.object(OllamaResearchCritic, "critique", capturing_critique):
        run_ollama_eval(
            model="llama3.1:8b", eval_path="evals/critic_v1.json",
            max_cases=1, output_dir=str(tmp_path),
        )

    assert len(contexts_seen) == 1
    assert isinstance(contexts_seen[0], CriticContext)
    assert contexts_seen[0].allowed_revision_constraints is not None


# ─── 7: CriticEvalSuite uses the same builder ────────────────────────────────

def test_critica_eval_suite_uses_same_constraints():
    contexts_seen: list = []

    class CapturingCritic:
        provider = "fake"
        model = "test"

        def critique(self, ctx):
            contexts_seen.append(ctx)
            from ai_quant_scientist.models.critic import CriticDecision, CriticDecisionType
            from ai_quant_scientist.models.research import new_id
            return CriticDecision(
                id=new_id(), research_run_id=ctx.research_run_id,
                decision_type=CriticDecisionType.NO_USEFUL_REVISION,
                parent_spec_id=None, changes=None, rationale="none",
                prediction=None, confidence=None,
            )

    cases = load_cases_from_file("evals/critic_v1.json")
    suite = CriticEvalSuite([c for c in cases if c.id in ("case-02", "case-06")])
    suite.run(CapturingCritic())

    assert len(contexts_seen) == 2
    for ctx in contexts_seen:
        assert isinstance(ctx, CriticContext)
        assert ctx.allowed_revision_constraints is not None


# ─── 8: OpenAI payload has non-null revision_constraints ─────────────────────

def test_openai_payload_has_non_null_revision_constraints():
    case = _load("case-02")
    ctx = build_critic_context(case)
    crit = OpenAIResearchCritic.__new__(OpenAIResearchCritic)
    crit.model = "m"
    crit.prompt_version = "v3"
    crit.reasoning = "medium"
    crit.max_output_tokens = 512
    crit._client = None
    payload = crit._build_messages(ctx)
    assert payload["revision_constraints"] is not None
    assert "signal_threshold" in payload["revision_constraints"]


# ─── 9: Ollama payload has non-null revision_constraints ─────────────────────

def test_ollama_payload_has_non_null_revision_constraints():
    case = _load("case-06")
    ctx = build_critic_context(case)
    crit = OllamaResearchCritic(model="llama3.1:8b")
    payload = crit._build_payload(ctx)
    assert payload["revision_constraints"] is not None
    assert "lookback" in payload["revision_constraints"]


# ─── 10: top-level reason_codes matches evaluation.reason_codes ──────────────

def test_openai_reason_codes_match_evaluation():
    case = _load("case-02")
    ctx = build_critic_context(case)
    crit = OpenAIResearchCritic.__new__(OpenAIResearchCritic)
    crit.model = "m"
    crit.prompt_version = "v3"
    crit.reasoning = "medium"
    crit.max_output_tokens = 512
    crit._client = None
    payload = crit._build_messages(ctx)
    assert payload["reason_codes"] == ctx.evaluation.get("reason_codes")


# ─── 11: case-02 and case-06 reason codes correct ────────────────────────────

def test_case_02_reason_codes_correct_in_openai_payload():
    case = _load("case-02")
    ctx = build_critic_context(case)
    crit = OpenAIResearchCritic.__new__(OpenAIResearchCritic)
    crit.model = "m"
    crit.prompt_version = "v3"
    crit.reasoning = "medium"
    crit.max_output_tokens = 512
    crit._client = None
    payload = crit._build_messages(ctx)
    assert payload["reason_codes"] == ["TOO_FEW_TRADES"]


def test_case_06_reason_codes_correct_in_openai_payload():
    case = _load("case-06")
    ctx = build_critic_context(case)
    crit = OpenAIResearchCritic.__new__(OpenAIResearchCritic)
    crit.model = "m"
    crit.prompt_version = "v3"
    crit.reasoning = "medium"
    crit.max_output_tokens = 512
    crit._client = None
    payload = crit._build_messages(ctx)
    assert payload["reason_codes"] == ["LOOKBACK_SENSITIVITY"]


def test_case_02_reason_codes_correct_in_ollama_payload():
    case = _load("case-02")
    ctx = build_critic_context(case)
    payload = OllamaResearchCritic(model="llama3.1:8b")._build_payload(ctx)
    assert payload["reason_codes"] == ["TOO_FEW_TRADES"]


def test_case_06_reason_codes_correct_in_ollama_payload():
    case = _load("case-06")
    ctx = build_critic_context(case)
    payload = OllamaResearchCritic(model="llama3.1:8b")._build_payload(ctx)
    assert payload["reason_codes"] == ["LOOKBACK_SENSITIVITY"]


# ─── 12: repeats mode uses canonical context for every repetition ─────────────

def test_repeats_mode_uses_canonical_context_for_every_rep(tmp_path):
    client = MagicMock()
    client.responses.parse.return_value = _make_response(_NO_USEFUL)
    contexts_seen: list = []

    def fake_init(self, model=None, prompt_version="v1", **kw):
        self.model = model or "m"
        self.prompt_version = prompt_version
        self.reasoning = "medium"
        self.max_output_tokens = 512
        self._client = client

    original_critique = OpenAIResearchCritic.critique

    def capturing_critique(self, context):
        contexts_seen.append(context)
        return original_critique(self, context)

    with patch.object(OpenAIResearchCritic, "__init__", fake_init), \
         patch.object(OpenAIResearchCritic, "critique", capturing_critique):
        run_live_eval(
            model="m", eval_path="evals/critic_v1.json",
            allow_live_api=True, max_cases=1, output_dir=str(tmp_path),
            repeats=3,
        )

    # 3 repetitions, each must receive a proper CriticContext with constraints
    assert len(contexts_seen) == 3
    for ctx in contexts_seen:
        assert isinstance(ctx, CriticContext)
        assert ctx.allowed_revision_constraints is not None
    # all repetitions share the same canonical context object (built once per case)
    assert all(c is contexts_seen[0] for c in contexts_seen)
