"""Tests for critic prompt versioning (zero API calls)."""
from __future__ import annotations

import json
import re
import unittest.mock as mock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ai_quant_scientist.evals.critic_eval import load_cases_from_file
from ai_quant_scientist.evals.run_live_critic_eval import run_live_eval
from ai_quant_scientist.evals.run_ollama_critic_eval import run_ollama_eval
from ai_quant_scientist.services.critic_prompts import (
    _V1,
    _V2,
    _V3,
    available_versions,
    get_instructions,
)
from ai_quant_scientist.services.ollama_research_critic import OllamaResearchCritic
from ai_quant_scientist.services.openai_research_critic import OpenAIResearchCritic


# ─── basic registry ───────────────────────────────────────────────────────────

def test_v1_available():
    assert "v1" in available_versions()


def test_v2_available():
    assert "v2" in available_versions()


def test_unknown_version_raises():
    with pytest.raises(KeyError, match="v99"):
        get_instructions("v99")


def test_v1_text_unchanged():
    """V1 must exactly match the text that was used during Benchmark V1."""
    txt = get_instructions("v1")
    assert "You are a bounded quantitative research critic." in txt
    assert "PROPOSE_REVISION" in txt
    assert "NO_USEFUL_REVISION" in txt


def test_v1_and_v2_are_different():
    assert get_instructions("v1") != get_instructions("v2")


# ─── V2 scientific principles ─────────────────────────────────────────────────

def test_v2_contains_evidence_burden_principle():
    txt = _V2
    assert "justified only when the supplied evidence" in txt or "grounded in the supplied" in txt


def test_v2_states_iterate_does_not_require_revision():
    txt = _V2.lower()
    assert "iterate" in txt
    assert "does not" in txt or "not mean" in txt


def test_v2_states_negative_performance_insufficient():
    txt = _V2.lower()
    assert "negative pnl" in txt or "negative performance" in txt or "poor aggregate performance" in txt


def test_v2_requires_lineage_awareness():
    txt = _V2.lower()
    assert "lineage" in txt
    assert "repeat" in txt or "already-tested" in txt


def test_v2_prohibits_repeating_tested_specifications():
    txt = _V2
    assert "already-tested" in txt or "do not repeat" in txt.lower()


def test_v2_discourages_unsupported_numerical_predictions():
    txt = _V2.lower()
    assert "arbitrary numerical" in txt or "arbitrary improvement" in txt or "unsupported" in txt


def test_v2_contains_confidence_vocabulary():
    txt = _V2
    for level in ("low", "medium", "high"):
        assert level in txt


# ─── anti-overfit: no benchmark IDs in V2 ────────────────────────────────────

def test_v2_contains_no_benchmark_case_ids():
    """V2 must not mention any eval-case IDs."""
    for i in range(1, 16):
        assert f"case-{i:02d}" not in _V2
        assert f"case-{i}" not in _V2


def test_v2_contains_no_known_spec_ids():
    """V2 must not contain spec IDs copied from the eval fixture."""
    for spec_id in ("spec-01", "spec-02", "spec-05-v3", "spec-13-v2"):
        assert spec_id not in _V2


def test_v2_contains_no_expected_decisions():
    """V2 must not hard-code expected benchmark answers."""
    # These would be signs of benchmark overfitting
    assert "PROPOSE_REVISION for case" not in _V2
    assert "NO_USEFUL_REVISION for case" not in _V2


# ─── adapter version selection ───────────────────────────────────────────────

def test_openai_adapter_defaults_to_v1():
    crit = OpenAIResearchCritic(client=MagicMock())
    assert crit.prompt_version == "v1"


def test_openai_adapter_selects_v2():
    crit = OpenAIResearchCritic(client=MagicMock(), prompt_version="v2")
    assert crit.prompt_version == "v2"


def test_openai_adapter_sends_v2_instructions(monkeypatch):
    """The instructions kwarg sent to the SDK must contain V2 text when v2 is selected."""
    cases = load_cases_from_file("evals/critic_v1.json")
    parsed = {
        "decision": "NO_USEFUL_REVISION",
        "parent_spec_id": None,
        "change": None,
        "rationale": "no useful revision justified",
        "prediction": None,
        "confidence": None,
    }
    captured: dict = {}

    output_item = SimpleNamespace(type="output_text", parsed=parsed, text=json.dumps(parsed))
    message = SimpleNamespace(type="message", content=[output_item])
    resp = SimpleNamespace(
        output=[message], usage={}, id="r1", model="m", status="completed",
        created_at=1.0, completed_at=2.0,
    )

    client = MagicMock()
    client.responses.parse.side_effect = lambda **kw: (captured.update(kw), resp)[1]

    crit = OpenAIResearchCritic(client=client, prompt_version="v2")
    crit.critique(cases[0].context)

    instructions = captured.get("instructions", "")
    assert "ITERATE" in instructions
    assert "does not" in instructions.lower() or "not mean" in instructions.lower()
    assert "You are a bounded quantitative research critic." in instructions


def test_openai_adapter_v1_instructions_match_v1_text(monkeypatch):
    cases = load_cases_from_file("evals/critic_v1.json")
    parsed = {
        "decision": "NO_USEFUL_REVISION",
        "parent_spec_id": None,
        "change": None,
        "rationale": "none",
        "prediction": None,
        "confidence": None,
    }
    captured: dict = {}

    output_item = SimpleNamespace(type="output_text", parsed=parsed, text=json.dumps(parsed))
    message = SimpleNamespace(type="message", content=[output_item])
    resp = SimpleNamespace(
        output=[message], usage={}, id="r1", model="m", status="completed",
        created_at=1.0, completed_at=2.0,
    )
    client = MagicMock()
    client.responses.parse.side_effect = lambda **kw: (captured.update(kw), resp)[1]

    OpenAIResearchCritic(client=client, prompt_version="v1").critique(cases[0].context)
    assert captured["instructions"] == get_instructions("v1")


def test_ollama_adapter_defaults_to_v1():
    assert OllamaResearchCritic().prompt_version == "v1"


def test_ollama_adapter_selects_v2():
    assert OllamaResearchCritic(prompt_version="v2").prompt_version == "v2"


def test_ollama_adapter_sends_v2_instructions():
    cases = load_cases_from_file("evals/critic_v1.json")
    content = {
        "decision": "NO_USEFUL_REVISION",
        "parent_spec_id": None,
        "change": None,
        "rationale": "no useful",
        "prediction": None,
        "confidence": None,
    }
    resp_bytes = json.dumps({
        "message": {"role": "assistant", "content": json.dumps(content)},
        "done": True, "total_duration": 1, "load_duration": 1,
        "prompt_eval_count": 10, "eval_count": 5,
    }).encode()

    captured_body: dict = {}

    def fake_urlopen(req, timeout=None):
        captured_body.update(json.loads(req.data.decode()))
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=SimpleNamespace(read=lambda: resp_bytes))
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        OllamaResearchCritic(prompt_version="v2").critique(cases[0].context)

    system_msg = next(m for m in captured_body["messages"] if m["role"] == "system")
    assert "ITERATE" in system_msg["content"]
    assert "does not" in system_msg["content"].lower() or "not mean" in system_msg["content"].lower()


# ─── provenance: prompt_version persisted in artifacts ───────────────────────

def _make_ollama_resp_bytes(content: dict) -> bytes:
    return json.dumps({
        "message": {"role": "assistant", "content": json.dumps(content)},
        "done": True, "total_duration": 1, "load_duration": 1,
        "prompt_eval_count": 1, "eval_count": 1,
    }).encode()


def test_ollama_runner_records_prompt_version_in_artifact(tmp_path):
    content = {
        "decision": "NO_USEFUL_REVISION", "parent_spec_id": None,
        "change": None, "rationale": "none", "prediction": None, "confidence": None,
    }
    resp_bytes = _make_ollama_resp_bytes(content)
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=SimpleNamespace(read=lambda: resp_bytes))
    cm.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=cm):
        path = run_ollama_eval(
            model="llama3.1:8b",
            eval_path="evals/critic_v1.json",
            max_cases=1,
            output_dir=str(tmp_path),
            prompt_version="v2",
        )

    data = json.loads(open(path).read())
    assert data["prompt_version"] == "v2"


def test_openai_runner_records_prompt_version_in_artifact(tmp_path):
    cases = load_cases_from_file("evals/critic_v1.json")
    parsed = {
        "decision": "NO_USEFUL_REVISION", "parent_spec_id": None,
        "change": None, "rationale": "none", "prediction": None, "confidence": None,
    }
    output_item = SimpleNamespace(type="output_text", parsed=parsed, text=json.dumps(parsed))
    message = SimpleNamespace(type="message", content=[output_item])
    resp = SimpleNamespace(
        output=[message], usage={}, id="r1", model="m", status="completed",
        created_at=1.0, completed_at=2.0,
    )
    client = MagicMock()
    client.responses.parse.return_value = resp

    def fake_init(self, model=None, prompt_version="v1", **kw):
        self.model = model or "gpt-5.6-terra"
        self.prompt_version = prompt_version
        self.reasoning = "medium"
        self.max_output_tokens = 512
        self._client = client

    with patch.object(OpenAIResearchCritic, "__init__", fake_init):
        path = run_live_eval(
            model="gpt-5.6-terra",
            eval_path="evals/critic_v1.json",
            allow_live_api=True,
            max_cases=1,
            output_dir=str(tmp_path),
            prompt_version="v2",
        )

    data = json.loads(open(path).read())
    assert data["prompt_version"] == "v2"


# ─── V3 registry and immutability ────────────────────────────────────────────

def test_v3_available():
    assert "v3" in available_versions()


def test_v1_unchanged_by_v3_addition():
    assert get_instructions("v1") == _V1


def test_v2_unchanged_by_v3_addition():
    assert get_instructions("v2") == _V2


def test_v3_distinct_from_v1_and_v2():
    assert _V3 != _V1
    assert _V3 != _V2


# ─── V3 scientific principles ─────────────────────────────────────────────────

def test_v3_says_performance_improvement_not_required():
    assert "does NOT require" in _V3 or "does not require" in _V3.lower()


def test_v3_describes_mechanistic_diagnostic_experiments():
    txt = _V3.lower()
    assert "mechanistic" in txt or "mechanism" in txt
    assert "diagnostic" in txt


def test_v3_describes_parameter_sensitivity_experiments():
    txt = _V3.lower()
    assert "sensitivity" in txt
    assert "sensitive" in txt or "uncertainty" in txt


def test_v3_experiment_teaches_not_proves():
    assert "teach us" in _V3 or "informative" in _V3


def test_v3_prohibits_optimization_stories():
    txt = _V3.lower()
    assert "pnl is negative" in txt or "negative" in txt
    assert "sharpe" in txt
    assert "another legal" in txt


def test_v3_preserves_lineage_rules():
    txt = _V3.lower()
    assert "repeat" in txt
    assert "previously tested" in txt
    assert "lineage" in txt


def test_v3_preserves_degrading_direction_rule():
    txt = _V3.lower()
    assert "degrading" in txt or "degraded" in txt


def test_v3_allows_no_useful_revision():
    txt = _V3
    assert "NO_USEFUL_REVISION" in txt
    assert "valid" in txt.lower() or "often correct" in txt.lower()


def test_v3_iterate_semantics():
    txt = _V3.lower()
    assert "iterate" in txt
    assert "does not mean" in txt or "not mean" in txt


def test_v3_predictions_are_mechanistic():
    txt = _V3.lower()
    assert "trade count" in txt or "signal frequency" in txt or "frequency" in txt


def test_v3_discourages_unsupported_numerics():
    txt = _V3.lower()
    assert "do not invent" in txt


def test_v3_confidence_is_epistemic():
    txt = _V3.lower()
    assert "justified and informative" in txt or "scientifically justified" in txt


def test_v3_no_benchmark_case_ids():
    for i in range(1, 16):
        assert f"case-{i:02d}" not in _V3


def test_v3_no_benchmark_spec_ids():
    for spec_id in ("spec-01", "spec-02", "spec-05-v3", "spec-13-v2"):
        assert spec_id not in _V3


def test_v3_no_expected_decisions():
    assert "PROPOSE_REVISION for case" not in _V3
    assert "NO_USEFUL_REVISION for case" not in _V3


# ─── V3 adapter integration ───────────────────────────────────────────────────

def test_openai_adapter_selects_v3():
    assert OpenAIResearchCritic(client=MagicMock(), prompt_version="v3").prompt_version == "v3"


def test_openai_adapter_sends_v3_instructions():
    cases = load_cases_from_file("evals/critic_v1.json")
    parsed = {
        "decision": "NO_USEFUL_REVISION", "parent_spec_id": None,
        "change": None, "rationale": "none", "prediction": None, "confidence": None,
    }
    captured: dict = {}
    output_item = SimpleNamespace(type="output_text", parsed=parsed, text=json.dumps(parsed))
    message = SimpleNamespace(type="message", content=[output_item])
    resp = SimpleNamespace(
        output=[message], usage={}, id="r1", model="m", status="completed",
        created_at=1.0, completed_at=2.0,
    )
    client = MagicMock()
    client.responses.parse.side_effect = lambda **kw: (captured.update(kw), resp)[1]
    OpenAIResearchCritic(client=client, prompt_version="v3").critique(cases[0].context)
    instructions = captured.get("instructions", "")
    assert "mechanism" in instructions.lower() or "mechanistic" in instructions.lower()
    assert "sensitivity" in instructions.lower()
    assert "does NOT require" in instructions or "does not require" in instructions.lower()


def test_ollama_adapter_selects_v3():
    assert OllamaResearchCritic(prompt_version="v3").prompt_version == "v3"


def test_ollama_adapter_sends_v3_instructions():
    cases = load_cases_from_file("evals/critic_v1.json")
    content = {
        "decision": "NO_USEFUL_REVISION", "parent_spec_id": None,
        "change": None, "rationale": "none", "prediction": None, "confidence": None,
    }
    captured_body: dict = {}

    def fake_urlopen(req, timeout=None):
        captured_body.update(json.loads(req.data.decode()))
        cm2 = MagicMock()
        cm2.__enter__ = MagicMock(return_value=SimpleNamespace(read=lambda: _make_ollama_resp_bytes(content)))
        cm2.__exit__ = MagicMock(return_value=False)
        return cm2

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        OllamaResearchCritic(prompt_version="v3").critique(cases[0].context)

    sys_msg = next(m for m in captured_body["messages"] if m["role"] == "system")
    assert "mechanism" in sys_msg["content"].lower() or "mechanistic" in sys_msg["content"].lower()
    assert "sensitivity" in sys_msg["content"].lower()


# ─── V3 runner provenance ─────────────────────────────────────────────────────

def test_openai_runner_records_v3_in_artifact(tmp_path):
    parsed = {
        "decision": "NO_USEFUL_REVISION", "parent_spec_id": None,
        "change": None, "rationale": "none", "prediction": None, "confidence": None,
    }
    output_item = SimpleNamespace(type="output_text", parsed=parsed, text=json.dumps(parsed))
    message = SimpleNamespace(type="message", content=[output_item])
    resp = SimpleNamespace(
        output=[message], usage={}, id="r1", model="m", status="completed",
        created_at=1.0, completed_at=2.0,
    )
    client = MagicMock()
    client.responses.parse.return_value = resp

    def fake_init(self, model=None, prompt_version="v1", **kw):
        self.model = model or "gpt-5.6-terra"
        self.prompt_version = prompt_version
        self.reasoning = "medium"
        self.max_output_tokens = 512
        self._client = client

    with patch.object(OpenAIResearchCritic, "__init__", fake_init):
        path = run_live_eval(
            model="gpt-5.6-terra",
            eval_path="evals/critic_v1.json",
            allow_live_api=True,
            max_cases=1,
            output_dir=str(tmp_path),
            prompt_version="v3",
        )

    assert json.loads(open(path).read())["prompt_version"] == "v3"


def test_ollama_runner_records_v3_in_artifact(tmp_path):
    content = {
        "decision": "NO_USEFUL_REVISION", "parent_spec_id": None,
        "change": None, "rationale": "none", "prediction": None, "confidence": None,
    }
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=SimpleNamespace(read=lambda: _make_ollama_resp_bytes(content)))
    cm.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=cm):
        path = run_ollama_eval(
            model="llama3.1:8b",
            eval_path="evals/critic_v1.json",
            max_cases=1,
            output_dir=str(tmp_path),
            prompt_version="v3",
        )

    assert json.loads(open(path).read())["prompt_version"] == "v3"
