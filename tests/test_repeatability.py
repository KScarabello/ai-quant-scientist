"""Tests for --repeats evaluation mode. Zero live API calls."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from ai_quant_scientist.evals.run_live_critic_eval import run_live_eval
from ai_quant_scientist.services.openai_research_critic import OpenAIResearchCritic


# ─── helpers ─────────────────────────────────────────────────────────────────

def _make_prov(input_tokens: int, output_tokens: int, reasoning_tokens: int = 0) -> str:
    return json.dumps({
        "response_id": "r1", "model": "m", "status": "completed",
        "created_at": 1.0, "completed_at": 2.0, "store": False,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens, "reasoning_tokens": reasoning_tokens},
        "output_text": "{}",
    })


def _make_parsed(decision: str, confidence: str | None = "high", input_tok: int = 10, output_tok: int = 5, reasoning_tok: int = 0) -> dict:
    if decision == "PROPOSE_REVISION":
        return {
            "decision": decision,
            "parent_spec_id": "spec-01",
            "intent": {"parameter": "signal_threshold", "direction": "DECREASE", "experiment_type": "MECHANISTIC_DIAGNOSTIC"},
            "rationale": "test rationale",
            "prediction": "trade count will change",
            "confidence": confidence,
        }
    return {
        "decision": decision,
        "parent_spec_id": None,
        "intent": None,
        "rationale": "none",
        "prediction": None,
        "confidence": confidence,
    }


def _make_response(parsed: dict, input_tok: int = 10, output_tok: int = 5, reasoning_tok: int = 0):
    """Build a fake OpenAI SDK response with the given structured parsed output."""
    raw_resp = _make_prov(input_tok, output_tok, reasoning_tok)
    # The serialised decision returned by _serialise_decision includes raw_response
    # so we embed provenance JSON into the parsed text field
    text = json.dumps({
        "decision": parsed["decision"],
        "parent_spec_id": parsed.get("parent_spec_id"),
        "change": parsed.get("change"),
        "rationale": parsed.get("rationale"),
        "prediction": parsed.get("prediction"),
        "confidence": parsed.get("confidence"),
    })
    output_item = SimpleNamespace(type="output_text", parsed=parsed, text=text)
    message = SimpleNamespace(type="message", content=[output_item])
    usage_obj = SimpleNamespace(
        input_tokens=input_tok,
        output_tokens=output_tok,
        output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tok),
        input_tokens_details=SimpleNamespace(cached_tokens=0),
    )
    return SimpleNamespace(
        output=[message],
        usage=usage_obj,
        id="r1", model="gpt-5.6-terra", status="completed",
        created_at=1.0, completed_at=2.0,
    )


def _make_fake_init(client):
    def fake_init(self, model=None, prompt_version="v1", **kw):
        self.model = model or "gpt-5.6-terra"
        self.prompt_version = prompt_version
        self.reasoning = "medium"
        self.max_output_tokens = 512
        self._client = client
    return fake_init


# ─── default repeats=1 preserves existing behavior ───────────────────────────

def test_default_repeats_1_produces_results_key(tmp_path):
    parsed = _make_parsed("NO_USEFUL_REVISION")
    client = MagicMock()
    client.responses.parse.return_value = _make_response(parsed)

    with patch.object(OpenAIResearchCritic, "__init__", _make_fake_init(client)):
        path = run_live_eval(
            model="gpt-5.6-terra", eval_path="evals/critic_v1.json",
            allow_live_api=True, max_cases=1, output_dir=str(tmp_path),
        )

    data = json.loads(open(path).read())
    assert "results" in data
    assert "cases" not in data


def test_default_repeats_1_invokes_critic_once(tmp_path):
    parsed = _make_parsed("NO_USEFUL_REVISION")
    client = MagicMock()
    client.responses.parse.return_value = _make_response(parsed)

    with patch.object(OpenAIResearchCritic, "__init__", _make_fake_init(client)):
        run_live_eval(
            model="gpt-5.6-terra", eval_path="evals/critic_v1.json",
            allow_live_api=True, max_cases=1, output_dir=str(tmp_path),
        )

    assert client.responses.parse.call_count == 1


# ─── repeats > 1 core behavior ───────────────────────────────────────────────

def test_repeats_5_invokes_critic_five_times(tmp_path):
    parsed = _make_parsed("NO_USEFUL_REVISION")
    client = MagicMock()
    client.responses.parse.return_value = _make_response(parsed)

    with patch.object(OpenAIResearchCritic, "__init__", _make_fake_init(client)):
        run_live_eval(
            model="gpt-5.6-terra", eval_path="evals/critic_v1.json",
            allow_live_api=True, max_cases=1, output_dir=str(tmp_path),
            repeats=5,
        )

    assert client.responses.parse.call_count == 5


def test_repeat_indexes_are_consecutive(tmp_path):
    parsed = _make_parsed("NO_USEFUL_REVISION")
    client = MagicMock()
    client.responses.parse.return_value = _make_response(parsed)

    with patch.object(OpenAIResearchCritic, "__init__", _make_fake_init(client)):
        path = run_live_eval(
            model="gpt-5.6-terra", eval_path="evals/critic_v1.json",
            allow_live_api=True, max_cases=1, output_dir=str(tmp_path),
            repeats=3,
        )

    data = json.loads(open(path).read())
    indexes = [r["repeat_index"] for r in data["cases"][0]["repetitions"]]
    assert indexes == [0, 1, 2]


def test_artifact_uses_cases_key_for_repeats(tmp_path):
    parsed = _make_parsed("NO_USEFUL_REVISION")
    client = MagicMock()
    client.responses.parse.return_value = _make_response(parsed)

    with patch.object(OpenAIResearchCritic, "__init__", _make_fake_init(client)):
        path = run_live_eval(
            model="gpt-5.6-terra", eval_path="evals/critic_v1.json",
            allow_live_api=True, max_cases=1, output_dir=str(tmp_path),
            repeats=2,
        )

    data = json.loads(open(path).read())
    assert "cases" in data
    assert "results" not in data


# ─── aggregate statistics ─────────────────────────────────────────────────────

def test_unanimous_decisions_produce_agreement_rate_1(tmp_path):
    parsed = _make_parsed("PROPOSE_REVISION", confidence="low")
    client = MagicMock()
    client.responses.parse.return_value = _make_response(parsed)

    with patch.object(OpenAIResearchCritic, "__init__", _make_fake_init(client)):
        path = run_live_eval(
            model="gpt-5.6-terra", eval_path="evals/critic_v1.json",
            allow_live_api=True, max_cases=1, output_dir=str(tmp_path),
            repeats=4,
        )

    agg = json.loads(open(path).read())["cases"][0]["aggregate"]
    assert agg["PROPOSE_REVISION"] == 4
    assert agg["NO_USEFUL_REVISION"] == 0
    assert agg["decision_agreement_rate"] == 1.0
    assert agg["majority_decision"] == "PROPOSE_REVISION"
    assert agg["successful_runs"] == 4
    assert agg["failed_runs"] == 0


def test_mixed_decisions_aggregate_correctly(tmp_path):
    # 3 PROPOSE, 2 NO_USEFUL in 5 calls
    responses = (
        [_make_response(_make_parsed("PROPOSE_REVISION", confidence="low"))] * 3 +
        [_make_response(_make_parsed("NO_USEFUL_REVISION", confidence="high"))] * 2
    )
    client = MagicMock()
    client.responses.parse.side_effect = responses

    with patch.object(OpenAIResearchCritic, "__init__", _make_fake_init(client)):
        path = run_live_eval(
            model="gpt-5.6-terra", eval_path="evals/critic_v1.json",
            allow_live_api=True, max_cases=1, output_dir=str(tmp_path),
            repeats=5,
        )

    agg = json.loads(open(path).read())["cases"][0]["aggregate"]
    assert agg["PROPOSE_REVISION"] == 3
    assert agg["NO_USEFUL_REVISION"] == 2
    assert agg["decision_agreement_rate"] == pytest.approx(3 / 5)
    assert agg["majority_decision"] == "PROPOSE_REVISION"
    assert agg["successful_runs"] == 5


def test_failed_invocation_does_not_erase_successful_repetitions(tmp_path):
    import urllib.error
    parsed = _make_parsed("NO_USEFUL_REVISION")
    responses = [
        _make_response(parsed),
        RuntimeError("api failure"),
        _make_response(parsed),
    ]
    client = MagicMock()
    client.responses.parse.side_effect = responses

    with patch.object(OpenAIResearchCritic, "__init__", _make_fake_init(client)):
        path = run_live_eval(
            model="gpt-5.6-terra", eval_path="evals/critic_v1.json",
            allow_live_api=True, max_cases=1, output_dir=str(tmp_path),
            repeats=3,
        )

    data = json.loads(open(path).read())
    case = data["cases"][0]
    assert len(case["repetitions"]) == 3
    errors = [r["error"] for r in case["repetitions"]]
    assert errors.count(None) == 2   # two successes
    assert sum(1 for e in errors if e is not None) == 1  # one failure
    assert case["aggregate"]["successful_runs"] == 2
    assert case["aggregate"]["failed_runs"] == 1


def test_token_totals_aggregate_correctly(tmp_path):
    client = MagicMock()
    client.responses.parse.side_effect = [
        _make_response(_make_parsed("NO_USEFUL_REVISION"), input_tok=100, output_tok=20, reasoning_tok=5),
        _make_response(_make_parsed("NO_USEFUL_REVISION"), input_tok=110, output_tok=25, reasoning_tok=10),
    ]

    with patch.object(OpenAIResearchCritic, "__init__", _make_fake_init(client)):
        path = run_live_eval(
            model="gpt-5.6-terra", eval_path="evals/critic_v1.json",
            allow_live_api=True, max_cases=1, output_dir=str(tmp_path),
            repeats=2,
        )

    agg = json.loads(open(path).read())["cases"][0]["aggregate"]
    assert agg["input_tokens_total"] == 210
    assert agg["output_tokens_total"] == 45
    assert agg["reasoning_tokens_total"] == 15


# ─── artifact integrity ───────────────────────────────────────────────────────

def test_artifact_preserves_individual_parsed_responses(tmp_path):
    parsed = _make_parsed("PROPOSE_REVISION", confidence="medium")
    client = MagicMock()
    client.responses.parse.return_value = _make_response(parsed)

    with patch.object(OpenAIResearchCritic, "__init__", _make_fake_init(client)):
        path = run_live_eval(
            model="gpt-5.6-terra", eval_path="evals/critic_v1.json",
            allow_live_api=True, max_cases=1, output_dir=str(tmp_path),
            repeats=2,
        )

    data = json.loads(open(path).read())
    for rep in data["cases"][0]["repetitions"]:
        assert rep["parsed"] is not None
        assert isinstance(rep["parsed"], dict)


def test_prompt_version_recorded_consistently(tmp_path):
    parsed = _make_parsed("NO_USEFUL_REVISION")
    client = MagicMock()
    client.responses.parse.return_value = _make_response(parsed)

    with patch.object(OpenAIResearchCritic, "__init__", _make_fake_init(client)):
        path = run_live_eval(
            model="gpt-5.6-terra", eval_path="evals/critic_v1.json",
            allow_live_api=True, max_cases=1, output_dir=str(tmp_path),
            repeats=3, prompt_version="v3",
        )

    data = json.loads(open(path).read())
    assert data["prompt_version"] == "v3"
    assert data["repeats"] == 3


def test_overall_summary_token_totals_across_multiple_cases(tmp_path):
    parsed = _make_parsed("NO_USEFUL_REVISION")
    client = MagicMock()
    # 2 cases × 2 repeats = 4 calls, each with 10 input tokens
    client.responses.parse.return_value = _make_response(parsed, input_tok=10, output_tok=5)

    with patch.object(OpenAIResearchCritic, "__init__", _make_fake_init(client)):
        path = run_live_eval(
            model="gpt-5.6-terra", eval_path="evals/critic_v1.json",
            allow_live_api=True, max_cases=2, output_dir=str(tmp_path),
            repeats=2,
        )

    summary = json.loads(open(path).read())["summary"]
    assert summary["total_invocations"] == 4
    assert summary["total_successful"] == 4
    assert summary["total_input_tokens"] == 40
    assert summary["total_output_tokens"] == 20


def test_unanimous_cases_counted_in_summary(tmp_path):
    parsed = _make_parsed("NO_USEFUL_REVISION")
    client = MagicMock()
    client.responses.parse.return_value = _make_response(parsed)

    with patch.object(OpenAIResearchCritic, "__init__", _make_fake_init(client)):
        path = run_live_eval(
            model="gpt-5.6-terra", eval_path="evals/critic_v1.json",
            allow_live_api=True, max_cases=2, output_dir=str(tmp_path),
            repeats=3,
        )

    summary = json.loads(open(path).read())["summary"]
    assert summary["unanimous_cases"] == 2
    assert summary["mixed_cases"] == 0


def test_mixed_cases_counted_in_summary(tmp_path):
    # alternate decisions across 4 repeats so both cases are mixed
    decisions = ["PROPOSE_REVISION", "NO_USEFUL_REVISION"] * 10
    idx = 0

    def side_effect(**kw):
        nonlocal idx
        parsed = _make_parsed(decisions[idx % len(decisions)])
        idx += 1
        return _make_response(parsed)

    client = MagicMock()
    client.responses.parse.side_effect = lambda **kw: side_effect()

    with patch.object(OpenAIResearchCritic, "__init__", _make_fake_init(client)):
        path = run_live_eval(
            model="gpt-5.6-terra", eval_path="evals/critic_v1.json",
            allow_live_api=True, max_cases=2, output_dir=str(tmp_path),
            repeats=2,
        )

    summary = json.loads(open(path).read())["summary"]
    assert summary["mixed_cases"] >= 1


# ─── guard and safety ─────────────────────────────────────────────────────────

def test_live_guard_also_blocks_repeats():
    with pytest.raises(RuntimeError):
        run_live_eval(
            model="gpt-5.6-terra", eval_path="evals/critic_v1.json",
            allow_live_api=False, max_cases=1, repeats=5,
        )
