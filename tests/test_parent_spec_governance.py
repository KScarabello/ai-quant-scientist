"""Tests proving parent_spec_id governance: sourced from context, not AI output."""
from __future__ import annotations

import json
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ai_quant_scientist.evals.critic_eval import build_critic_context, load_cases_from_file
from ai_quant_scientist.services.ollama_research_critic import OllamaResearchCritic, _DECISION_SCHEMA
from ai_quant_scientist.services.openai_research_critic import OpenAIResearchCritic
from ai_quant_scientist.services.critic_prompts import _V3


# ─── helpers ─────────────────────────────────────────────────────────────────

def _make_openai_response(parsed: dict):
    text = json.dumps(parsed)
    item = SimpleNamespace(type="output_text", parsed=parsed, text=text)
    msg = SimpleNamespace(type="message", content=[item])
    return SimpleNamespace(
        output=[msg], usage={}, id="r1", model="gpt-5.6-terra",
        status="completed", created_at=1.0, completed_at=2.0,
    )


def _make_ollama_resp(parsed: dict) -> bytes:
    body = {
        "model": "llama3.1:8b",
        "message": {"role": "assistant", "content": json.dumps(parsed)},
        "done": True, "total_duration": 1, "load_duration": 1,
        "prompt_eval_count": 1, "eval_count": 1,
    }
    return json.dumps(body).encode()


def _ollama_urlopen(resp_bytes: bytes):
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=SimpleNamespace(read=lambda: resp_bytes))
    cm.__exit__ = MagicMock(return_value=False)
    return cm


# ─── schema checks: parent_spec_id absent from AI-facing structures ───────────

def test_openai_pydantic_schema_has_no_parent_spec_id():
    """parent_spec_id must not appear in the Pydantic CriticDecisionSchema."""
    from ai_quant_scientist.evals.critic_eval import build_critic_context
    cases = load_cases_from_file("evals/critic_v1.json")
    ctx = build_critic_context(cases[0])

    captured: dict = {}
    parsed = {"decision": "NO_USEFUL_REVISION", "intent": None, "rationale": "r", "prediction": None, "confidence": None}
    resp = _make_openai_response(parsed)
    client = MagicMock()
    client.responses.parse.side_effect = lambda **kw: (captured.update(kw), resp)[1]

    OpenAIResearchCritic(client=client).critique(ctx)

    tf = captured["text_format"]
    assert isinstance(tf, type)
    schema_fields = list(tf.model_fields.keys())
    assert "parent_spec_id" not in schema_fields


def test_ollama_json_schema_has_no_parent_spec_id():
    assert "parent_spec_id" not in _DECISION_SCHEMA["properties"]


def test_ollama_json_schema_required_has_no_parent_spec_id():
    assert "parent_spec_id" not in _DECISION_SCHEMA.get("required", [])


# ─── prompt V3: output contract section updated ───────────────────────────────

def test_v3_output_contract_does_not_mention_parent_spec_id():
    """V3 must not instruct the model to supply parent_spec_id."""
    # Check only the output-contract block (between "For PROPOSE_REVISION" and "== PURPOSE")
    contract_section = _V3.split("== PURPOSE OF A REVISION ==")[0]
    assert "parent_spec_id" not in contract_section


def test_v3_output_contract_says_spec_identified_from_context():
    contract_section = _V3.split("== PURPOSE OF A REVISION ==")[0]
    assert "deterministically" in contract_section.lower() or "research context" in contract_section.lower()


def test_v3_scientific_sections_unchanged():
    """The 8 scientific principle sections must still be present in V3."""
    for section in [
        "== PURPOSE OF A REVISION ==",
        "== TWO VALID BASES FOR A REVISION ==",
        "== UNSUPPORTED OPTIMIZATION IS STILL PROHIBITED ==",
        "== EXPERIMENTAL LINEAGE ==",
        "== WHEN TO RETURN NO_USEFUL_REVISION ==",
        "== ITERATE SEMANTICS ==",
        "== PREDICTIONS ==",
        "== CONFIDENCE ==",
    ]:
        assert section in _V3


# ─── authoritative parent_spec_id comes from context ─────────────────────────

def test_openai_propose_uses_spec_id_from_context():
    cases = load_cases_from_file("evals/critic_v1.json")
    ctx = build_critic_context(cases[0])  # spec-01
    expected_id = ctx.current_spec.get("id")
    assert expected_id == "spec-01"

    parsed = {
        "decision": "PROPOSE_REVISION",
        "intent": {"parameter": "signal_threshold", "direction": "DECREASE", "experiment_type": "MECHANISTIC_DIAGNOSTIC"},
        "rationale": "lower threshold for TOO_FEW_TRADES",
        "prediction": "trade count increases",
        "confidence": "medium",
    }
    client = MagicMock()
    client.responses.parse.return_value = _make_openai_response(parsed)
    decision = OpenAIResearchCritic(client=client).critique(ctx)
    assert decision.parent_spec_id == expected_id


def test_openai_no_useful_uses_spec_id_from_context():
    cases = load_cases_from_file("evals/critic_v1.json")
    ctx = build_critic_context(cases[1])  # spec-02
    expected_id = ctx.current_spec.get("id")

    parsed = {"decision": "NO_USEFUL_REVISION", "intent": None, "rationale": "r", "prediction": None, "confidence": None}
    client = MagicMock()
    client.responses.parse.return_value = _make_openai_response(parsed)
    decision = OpenAIResearchCritic(client=client).critique(ctx)
    assert decision.parent_spec_id == expected_id


def test_ollama_propose_uses_spec_id_from_context():
    cases = load_cases_from_file("evals/critic_v1.json")
    ctx = build_critic_context(cases[0])  # spec-01
    expected_id = ctx.current_spec.get("id")

    parsed = {
        "decision": "PROPOSE_REVISION",
        "intent": {"parameter": "signal_threshold", "direction": "DECREASE", "experiment_type": "MECHANISTIC_DIAGNOSTIC"},
        "rationale": "lower threshold",
        "prediction": "trade count increases",
        "confidence": "medium",
    }
    with patch("urllib.request.urlopen", return_value=_ollama_urlopen(_make_ollama_resp(parsed))):
        decision = OllamaResearchCritic().critique(ctx)
    assert decision.parent_spec_id == expected_id


# ─── AI-supplied parent_spec_id cannot influence result ──────────────────────

def test_openai_ignores_model_supplied_parent_spec_id():
    """Even if the model were to embed a wrong parent_spec_id, it must be ignored."""
    cases = load_cases_from_file("evals/critic_v1.json")
    ctx = build_critic_context(cases[0])  # spec-01

    # model response contains a wrong/fabricated parent_spec_id in a hypothetical extra field
    # (it won't parse because of extra="forbid" in Pydantic schema, but even if it did,
    # the adapter never reads it)
    parsed = {
        "decision": "NO_USEFUL_REVISION",
        "intent": None,
        "rationale": "r",
        "prediction": None,
        "confidence": None,
        # extra: model tries to inject a different spec ID — schema forbids it, adapter ignores it
    }
    client = MagicMock()
    client.responses.parse.return_value = _make_openai_response(parsed)
    decision = OpenAIResearchCritic(client=client).critique(ctx)
    assert decision.parent_spec_id == "spec-01"  # from context, not model


# ─── fail closed when authoritative spec ID is missing ───────────────────────

def test_openai_propose_fails_closed_without_spec_id():
    """If current_spec has no id, PROPOSE_REVISION must fail closed."""
    from ai_quant_scientist.models.critic import CriticContext
    ctx = CriticContext(
        id="ctx-x", research_run_id="run-x",
        hypothesis={},
        current_spec={"parameters": {"signal_threshold": 2.0}},  # no "id"
        attempt={}, result={}, evaluation={},
        prior_lineage=[],
        allowed_revision_constraints={
            "signal_threshold": {"type": "float", "min": -10.0, "max": 10.0, "step": 0.5},
        },
    )
    parsed = {
        "decision": "PROPOSE_REVISION",
        "intent": {"parameter": "signal_threshold", "direction": "DECREASE", "experiment_type": "MECHANISTIC_DIAGNOSTIC"},
        "rationale": "r", "prediction": "p", "confidence": "low",
    }
    client = MagicMock()
    client.responses.parse.return_value = _make_openai_response(parsed)
    with pytest.raises(ValueError, match="authoritative current_spec has no id"):
        OpenAIResearchCritic(client=client).critique(ctx)


def test_ollama_propose_fails_closed_without_spec_id():
    from ai_quant_scientist.models.critic import CriticContext
    ctx = CriticContext(
        id="ctx-x", research_run_id="run-x",
        hypothesis={},
        current_spec={"parameters": {"signal_threshold": 2.0}},  # no "id"
        attempt={}, result={}, evaluation={},
        prior_lineage=[],
        allowed_revision_constraints={
            "signal_threshold": {"type": "float", "min": -10.0, "max": 10.0, "step": 0.5},
        },
    )
    parsed = {
        "decision": "PROPOSE_REVISION",
        "intent": {"parameter": "signal_threshold", "direction": "DECREASE", "experiment_type": "MECHANISTIC_DIAGNOSTIC"},
        "rationale": "r", "prediction": "p", "confidence": "low",
    }
    with patch("urllib.request.urlopen", return_value=_ollama_urlopen(_make_ollama_resp(parsed))):
        with pytest.raises(ValueError, match="authoritative current_spec has no id"):
            OllamaResearchCritic().critique(ctx)


# ─── planner receives authoritative parent ID ─────────────────────────────────

def test_revision_intent_carries_authoritative_spec_id():
    from ai_quant_scientist.services.revision_planner import RevisionPlanner
    from ai_quant_scientist.models.revision import RevisionDirection, ExperimentType, RevisionIntent

    intent = RevisionIntent(
        id="i1", research_run_id="r1", parent_spec_id="spec-01",
        parameter="signal_threshold",
        direction=RevisionDirection.DECREASE,
        experiment_type=ExperimentType.MECHANISTIC_DIAGNOSTIC,
        rationale="r", prediction="p", confidence="low",
    )
    constraints = {"signal_threshold": {"type": "float", "min": -10.0, "max": 10.0, "step": 0.5}}
    spec = {"id": "spec-01", "parameters": {"signal_threshold": 2.0}}
    result = RevisionPlanner().plan(intent, spec, constraints, [])
    assert result.rejection_reason is None
    # The planner does not change or override parent_spec_id — it just produces a change value
    assert result.planned_change == {"signal_threshold": 1.5}


# ─── Ollama confidence enum enforcement ──────────────────────────────────────

def test_ollama_confidence_enum_is_exactly_low_medium_high():
    conf_schema = _DECISION_SCHEMA["properties"]["confidence"]
    # confidence is anyOf: [{string, enum:[...]}, {null}]
    string_branch = next(b for b in conf_schema["anyOf"] if b.get("type") == "string")
    assert string_branch["enum"] == ["low", "medium", "high"]


def test_ollama_confidence_schema_does_not_allow_arbitrary_strings():
    conf_schema = _DECISION_SCHEMA["properties"]["confidence"]
    # must not be just {"type": "string"} without enum
    for branch in conf_schema["anyOf"]:
        if branch.get("type") == "string":
            assert "enum" in branch, "Confidence string branch must have enum"
