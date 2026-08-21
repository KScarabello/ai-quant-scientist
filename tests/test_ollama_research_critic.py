"""Mocked tests for OllamaResearchCritic and run_ollama_critic_eval. Zero real HTTP calls."""
from __future__ import annotations

import json
import unittest.mock as mock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ai_quant_scientist.evals.critic_eval import load_cases_from_file
from ai_quant_scientist.evals.run_ollama_critic_eval import run_ollama_eval
from ai_quant_scientist.services.ollama_research_critic import (
    OllamaResearchCritic,
    _DECISION_SCHEMA,
)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _ollama_response(content: dict, extra: dict | None = None) -> str:
    body = {
        "model": "llama3.1:8b",
        "message": {"role": "assistant", "content": json.dumps(content)},
        "done": True,
        "total_duration": 1_000_000_000,
        "load_duration": 50_000_000,
        "prompt_eval_count": 100,
        "eval_count": 40,
        **(extra or {}),
    }
    return json.dumps(body).encode()


def _mock_urlopen(response_bytes: bytes):
    """Return a context-manager mock that yields a readable response."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=SimpleNamespace(read=lambda: response_bytes))
    cm.__exit__ = MagicMock(return_value=False)
    return cm


# ─── adapter tests ────────────────────────────────────────────────────────────

def test_endpoint_is_localhost_11434():
    critic = OllamaResearchCritic()
    assert critic.base_url == "http://localhost:11434"


def test_model_name_passed():
    critic = OllamaResearchCritic(model="llama3.1:8b")
    assert critic.model == "llama3.1:8b"


def test_context_fields_populated():
    cases = load_cases_from_file("evals/critic_v1.json")
    critic = OllamaResearchCritic()
    payload = critic._build_payload(cases[0].context)
    assert payload["hypothesis"] is not None
    assert payload["current_spec"] is not None
    assert payload["current_result"] is not None
    assert payload["evaluation"] is not None


def test_stream_is_false_and_format_schema_sent():
    cases = load_cases_from_file("evals/critic_v1.json")
    case = cases[0]
    response_content = {
        "decision": "NO_USEFUL_REVISION",
        "parent_spec_id": None,
        "intent": None,
        "rationale": "no useful",
        "prediction": None,
        "confidence": None,
    }
    resp_bytes = _ollama_response(response_content)

    captured_body: dict = {}

    def fake_urlopen(req, timeout=None):
        captured_body.update(json.loads(req.data.decode()))
        return _mock_urlopen(resp_bytes)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        critic = OllamaResearchCritic()
        critic.critique(case.context)

    assert captured_body.get("stream") is False
    assert "format" in captured_body
    assert captured_body["format"] == _DECISION_SCHEMA


def test_valid_propose_revision_maps_to_critic_decision():
    from ai_quant_scientist.evals.critic_eval import build_critic_context
    cases = load_cases_from_file("evals/critic_v1.json")
    case = cases[0]  # spec-01: signal_threshold=2.0; planner selects 1.5
    ctx = build_critic_context(case)
    content = {
        "decision": "PROPOSE_REVISION",
        "parent_spec_id": case.context.get("current_spec", {}).get("id"),
        "intent": {"parameter": "signal_threshold", "direction": "DECREASE", "experiment_type": "MECHANISTIC_DIAGNOSTIC"},
        "rationale": "TOO_FEW_TRADES: lower threshold increases trade frequency",
        "prediction": "trade count will increase",
        "confidence": "medium",
    }
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(_ollama_response(content))):
        decision = OllamaResearchCritic().critique(ctx)
    assert decision.decision_type.name == "PROPOSE_REVISION"
    assert decision.changes == {"signal_threshold": 1.5}
    assert decision.provider == "ollama"


def test_valid_no_useful_revision_maps_to_critic_decision():
    cases = load_cases_from_file("evals/critic_v1.json")
    case = cases[1]
    content = {
        "decision": "NO_USEFUL_REVISION",
        "parent_spec_id": None,
        "intent": None,
        "rationale": "nothing useful",
        "prediction": None,
        "confidence": None,
    }
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(_ollama_response(content))):
        decision = OllamaResearchCritic().critique(case.context)

    assert decision.decision_type.name == "NO_USEFUL_REVISION"
    assert decision.changes is None


def test_usage_metadata_captured_in_raw_response():
    cases = load_cases_from_file("evals/critic_v1.json")
    case = cases[0]
    content = {
        "decision": "NO_USEFUL_REVISION",
        "parent_spec_id": None,
        "intent": None,
        "rationale": "ok",
        "prediction": None,
        "confidence": None,
    }
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(_ollama_response(content))):
        decision = OllamaResearchCritic().critique(case.context)

    raw = json.loads(decision.raw_response)
    assert raw["provenance"]["total_duration"] == 1_000_000_000
    assert raw["provenance"]["prompt_eval_count"] == 100
    assert raw["provenance"]["eval_count"] == 40


def test_http_error_raises_runtime_error():
    import urllib.error
    cases = load_cases_from_file("evals/critic_v1.json")
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        with pytest.raises(RuntimeError, match="Ollama HTTP error"):
            OllamaResearchCritic().critique(cases[0].context)


def test_malformed_json_raises_value_error():
    cases = load_cases_from_file("evals/critic_v1.json")
    bad_resp = json.dumps({"message": {"role": "assistant", "content": "not json at all"}}).encode()
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(bad_resp)):
        with pytest.raises(ValueError, match="not valid JSON"):
            OllamaResearchCritic().critique(cases[0].context)


def test_invalid_decision_value_raises_value_error():
    cases = load_cases_from_file("evals/critic_v1.json")
    content = {
        "decision": "DO_SOMETHING_ELSE",
        "parent_spec_id": None,
        "intent": None,
        "rationale": "bad",
        "prediction": None,
        "confidence": None,
    }
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(_ollama_response(content))):
        with pytest.raises(ValueError, match="Invalid decision"):
            OllamaResearchCritic().critique(cases[0].context)


def test_no_openai_fallback():
    """Adapter must not import the openai package."""
    import ai_quant_scientist.services.ollama_research_critic as mod
    src = open(mod.__file__).read()
    # check that no 'import openai' or 'from openai' line exists
    import re
    assert not re.search(r"^\s*(import openai|from openai)", src, re.MULTILINE)


# ─── benchmark runner tests ───────────────────────────────────────────────────

def test_runner_serialises_output_to_json(tmp_path):
    cases = load_cases_from_file("evals/critic_v1.json")
    case = cases[0]
    content = {
        "decision": "NO_USEFUL_REVISION",
        "parent_spec_id": None,
        "intent": None,
        "rationale": "ok",
        "prediction": None,
        "confidence": None,
    }
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(_ollama_response(content))):
        out = run_ollama_eval(
            model="llama3.1:8b",
            eval_path="evals/critic_v1.json",
            max_cases=1,
            output_dir=str(tmp_path),
        )

    data = json.loads(open(out).read())
    assert data["provider"] == "ollama"
    assert data["model"] == "llama3.1:8b"
    assert len(data["results"]) == 1
    r = data["results"][0]
    assert r["decision"] == "NO_USEFUL_REVISION"
    assert isinstance(r["parsed"], dict)


def test_runner_records_error_on_bad_response(tmp_path):
    import urllib.error
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
        out = run_ollama_eval(
            model="llama3.1:8b",
            eval_path="evals/critic_v1.json",
            max_cases=1,
            output_dir=str(tmp_path),
        )

    data = json.loads(open(out).read())
    assert "error" in data["results"][0]


def test_runner_continues_after_one_case_failure(tmp_path):
    """A failure on case N must not prevent case N+1 from running."""
    import urllib.error
    cases = load_cases_from_file("evals/critic_v1.json")

    content = {
        "decision": "NO_USEFUL_REVISION",
        "parent_spec_id": None,
        "intent": None,
        "rationale": "ok",
        "prediction": None,
        "confidence": None,
    }
    call_count = 0

    def side_effect(req, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise urllib.error.URLError("transient")
        return _mock_urlopen(_ollama_response(content))

    with patch("urllib.request.urlopen", side_effect=side_effect):
        out = run_ollama_eval(
            model="llama3.1:8b",
            eval_path="evals/critic_v1.json",
            max_cases=2,
            output_dir=str(tmp_path),
        )

    data = json.loads(open(out).read())
    assert len(data["results"]) == 2
    assert "error" in data["results"][0]
    assert data["results"][1]["decision"] == "NO_USEFUL_REVISION"


def test_runner_no_authoritative_db_writes(tmp_path):
    """Runner must not call any SQLiteStore methods."""
    content = {
        "decision": "NO_USEFUL_REVISION",
        "parent_spec_id": None,
        "intent": None,
        "rationale": "ok",
        "prediction": None,
        "confidence": None,
    }
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(_ollama_response(content))):
        with patch("ai_quant_scientist.storage.sqlite_store.SQLiteStore") as mock_store:
            run_ollama_eval(
                model="llama3.1:8b",
                eval_path="evals/critic_v1.json",
                max_cases=1,
                output_dir=str(tmp_path),
            )
    mock_store.assert_not_called()
