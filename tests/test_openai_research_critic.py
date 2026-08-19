from __future__ import annotations

import json
import types
from unittest.mock import MagicMock

import pytest

from ai_quant_scientist.services.openai_research_critic import OpenAIResearchCritic
from ai_quant_scientist.evals.critic_eval import load_cases_from_file
from ai_quant_scientist.evals.run_live_critic_eval import run_live_eval


class DummyResponse:
    def __init__(self, parsed=None, usage=None):
        self.parsed = parsed
        self.usage = usage or {}


def make_client_that_returns(parsed: dict, usage: dict | None = None):
    client = MagicMock()
    # build response.output structure: list of message objects with content items
    from types import SimpleNamespace

    output_item = SimpleNamespace(type="output_text", parsed=parsed, text=None)
    message = SimpleNamespace(type="message", content=[output_item])
    resp = SimpleNamespace(output=[message], usage=usage or {})
    client.responses.parse.return_value = resp
    return client


def make_client_that_raises_then_returns(parsed: dict):
    client = MagicMock()
    from types import SimpleNamespace
    output_item = SimpleNamespace(type="output_text", parsed=parsed, text=None)
    message = SimpleNamespace(type="message", content=[output_item])
    resp = SimpleNamespace(output=[message], usage={"input_tokens": 10, "output_tokens": 20})
    client.responses.parse.side_effect = [RuntimeError("429"), resp]
    return client


def test_structured_parsing_propose_revision(monkeypatch):
    cases = load_cases_from_file("evals/critic_v1.json")
    case = cases[0]
    parsed = {
        "decision": "PROPOSE_REVISION",
        "parent_spec_id": case.context.get("current_spec", {}).get("id"),
        "change": {"parameter": "signal_threshold", "from": 2.0, "to": 1.5},
        "rationale": "short",
        "prediction": "trade_count up",
        "confidence": "MEDIUM",
    }
    client = make_client_that_returns(parsed, usage={"input_tokens": 5, "output_tokens": 10})
    # inject client
    crit = OpenAIResearchCritic(client=client)
    decision = crit.critique(case.context)
    assert decision.decision_type.name == "PROPOSE_REVISION"
    assert decision.changes == {"signal_threshold": 1.5}


def test_structured_parsing_no_useful(monkeypatch):
    cases = load_cases_from_file("evals/critic_v1.json")
    case = cases[1]
    parsed = {"decision": "NO_USEFUL_REVISION", "parent_spec_id": case.context.get("current_spec", {}).get("id"), "change": None, "rationale": "no useful"}
    client = make_client_that_returns(parsed)
    crit = OpenAIResearchCritic(client=client)
    decision = crit.critique(case.context)
    assert decision.decision_type.name == "NO_USEFUL_REVISION"


def test_usage_extraction_in_invocation(monkeypatch):
    cases = load_cases_from_file("evals/critic_v1.json")
    case = cases[0]
    parsed = {"decision": "NO_USEFUL_REVISION", "parent_spec_id": case.context.get("current_spec", {}).get("id"), "change": None}
    client = make_client_that_returns(parsed, usage={"input_tokens": 7, "output_tokens": 13})
    crit = OpenAIResearchCritic(client=client)
    decision = crit.critique(case.context)
    # invocation metadata is embedded in decision.raw_response via adapter
    assert decision.raw_response is not None


def test_provider_failure_propagates(monkeypatch):
    cases = load_cases_from_file("evals/critic_v1.json")
    case = cases[0]
    client = MagicMock()
    client.responses.parse.side_effect = RuntimeError("auth failure")
    crit = OpenAIResearchCritic(client=client)
    with pytest.raises(RuntimeError):
        crit.critique(case.context)


def test_live_runner_retries_on_transient(monkeypatch, tmp_path):
    cases = load_cases_from_file("evals/critic_v1.json")
    # client will raise then succeed
    parsed = {"decision": "NO_USEFUL_REVISION", "parent_spec_id": cases[0].context.get("current_spec", {}).get("id"), "change": None}
    client = make_client_that_raises_then_returns(parsed)

    # monkeypatch the OpenAIResearchCritic to use our client instance
    orig_init = OpenAIResearchCritic.__init__

    def fake_init(self, model: str = None, prompt_version: str = None, reasoning: str = None, max_output_tokens: int = None, client_arg: None = None):
        self.model = model or "gpt-5.6-luna"
        self.prompt_version = prompt_version or "v1"
        self.reasoning = reasoning or "medium"
        self.max_output_tokens = max_output_tokens or 512
        self._client = client

    monkeypatch.setattr(OpenAIResearchCritic, "__init__", fake_init)

    out = tmp_path / "out"
    out.mkdir()
    path = run_live_eval(model="gpt-5.6-luna", eval_path="evals/critic_v1.json", allow_live_api=True, max_cases=1, output_dir=str(out))
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert len(data["results"]) == 1


def test_parse_called_with_expected_kwargs(monkeypatch):
    cases = load_cases_from_file("evals/critic_v1.json")
    parsed = {"decision": "NO_USEFUL_REVISION", "parent_spec_id": cases[0].context.get("current_spec", {}).get("id"), "change": None}
    client = make_client_that_returns(parsed)
    crit = OpenAIResearchCritic(client=client)
    # capture expected structured payload before invocation
    expected_payload = crit._build_messages(cases[0].context)
    decision = crit.critique(cases[0].context)
    # assert parse was called
    client.responses.parse.assert_called()
    # inspect last call args
    kwargs = client.responses.parse.call_args.kwargs
    assert kwargs["model"] == crit.model
    assert "instructions" in kwargs
    assert "input" in kwargs
    # input must be a JSON string that decodes to the expected payload
    assert isinstance(kwargs["input"], str)
    assert json.loads(kwargs["input"]) == expected_payload
    assert "text_format" in kwargs
    # text_format should be a provider-specific class named CriticDecisionSchema
    tf = kwargs["text_format"]
    assert isinstance(tf, type)
    assert getattr(tf, "__name__", "") == "CriticDecisionSchema"
    assert kwargs["reasoning"] == {"effort": crit.reasoning}
    assert kwargs["max_output_tokens"] == crit.max_output_tokens
    assert kwargs["store"] is False


def test_live_runner_guard_no_allow(monkeypatch):
    with pytest.raises(RuntimeError):
        run_live_eval(model="gpt-5.6-luna", eval_path="evals/critic_v1.json", allow_live_api=False, max_cases=1)


def test_payload_keys_populated_from_dict_context():
    # Ensure _build_messages correctly reads dict context (as used by live runner with eval fixtures)
    cases = load_cases_from_file("evals/critic_v1.json")
    crit = OpenAIResearchCritic(client=MagicMock())
    payload = crit._build_messages(cases[0].context)
    assert payload["hypothesis"] is not None, "hypothesis must be populated from dict context"
    assert payload["current_spec"] is not None, "current_spec must be populated from dict context"
    assert payload["current_result"] is not None, "current_result must be populated from dict context"
    assert payload["evaluation"] is not None, "evaluation must be populated from dict context"


def test_live_runner_serialises_decision(tmp_path):
    # Prove runner writes valid JSON without hitting __dict__ on a slotted dataclass
    cases = load_cases_from_file("evals/critic_v1.json")
    parsed = {"decision": "NO_USEFUL_REVISION", "parent_spec_id": cases[0].context.get("current_spec", {}).get("id"), "change": None}
    client = make_client_that_returns(parsed)

    def fake_init(self, model=None, **kw):
        self.model = model or "gpt-5.6-luna"
        self.prompt_version = "v1"
        self.reasoning = "medium"
        self.max_output_tokens = 512
        self._client = client

    from unittest.mock import patch
    with patch.object(OpenAIResearchCritic, "__init__", fake_init):
        out = run_live_eval(
            model="gpt-5.6-luna",
            eval_path="evals/critic_v1.json",
            allow_live_api=True,
            max_cases=1,
            output_dir=str(tmp_path),
        )
    data = json.loads(open(out).read())
    result = data["results"][0]
    assert result["decision"] == "NO_USEFUL_REVISION"
    assert "parsed" in result
    # parsed must be a plain dict, not raise AttributeError
    assert isinstance(result["parsed"], dict)
    assert "decision_type" in result["parsed"] or "decision" in result["parsed"]
