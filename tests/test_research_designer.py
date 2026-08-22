from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ai_quant_scientist.capabilities import (
    AssetClass,
    CapabilityRegistry,
    DataKind,
    DataRequirement,
    Resolution,
    ToolKind,
    ToolRequirement,
    build_v1_registry,
)
from ai_quant_scientist.capabilities.gate import GateDecision, ResearchCandidate
from ai_quant_scientist.capabilities.intake import GovernedResearchIntake
from ai_quant_scientist.evals.research_designer_eval import (
    ResearchDesignerEvalSuite,
    load_cases_from_file,
)
from ai_quant_scientist.evals.run_live_research_designer_eval import run_live_research_designer_eval
from ai_quant_scientist.models.design import (
    AnalysisIntent,
    ComparisonIntent,
    DesignOutcome,
    DesignVariable,
    ResearchDesignIntent,
    ResearchDesignKind,
)
from ai_quant_scientist.models.research_designer import (
    RESEARCH_DESIGN_INTENT_CONTRACT_VERSION,
    ResearchDesignerDecision,
    ResearchDesignerDecisionType,
)
from ai_quant_scientist.services.openai_research_designer import OpenAIResearchDesigner
from ai_quant_scientist.services.research_design_ontology import (
    RESEARCH_DESIGN_ONTOLOGY_VERSION,
    build_research_design_ontology_snapshot,
)
from ai_quant_scientist.services.research_designer import (
    FakeResearchDesigner,
    GovernedResearchDesigner,
    ResearchDesignProposalValidator,
    build_research_designer_context,
    context_to_payload,
)
from ai_quant_scientist.services.research_designer_prompts import (
    RESEARCH_DESIGNER_VERSION,
    available_versions,
    get_research_designer_instructions,
)
from ai_quant_scientist.services.spec_materialization import SpecMaterializer
from ai_quant_scientist.storage.sqlite_store import SQLiteStore


def _store(tmp_path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "test.db")


def _registry() -> CapabilityRegistry:
    return build_v1_registry()


def _ready_candidate(
    *,
    statement: str = "Signal-threshold strictness changes synthetic trade frequency and risk-adjusted performance.",
    rationale: str = "A stricter threshold should alter which opportunities fire while lookback stays fixed.",
) -> ResearchCandidate:
    return ResearchCandidate.create(
        hypothesis_statement=statement,
        hypothesis_rationale=rationale,
        requirements=[
            DataRequirement(
                requirement_id="data",
                data_kind=DataKind.SYNTHETIC_PARAMETRIC,
                asset_class=AssetClass.SYNTHETIC,
            ),
            ToolRequirement(
                requirement_id="tool",
                tool_kind=ToolKind.BACKTEST_EXECUTION,
            ),
        ],
    )


def _blocked_candidate() -> ResearchCandidate:
    return ResearchCandidate.create(
        hypothesis_statement="MES order-book imbalance predicts one-second futures returns.",
        hypothesis_rationale="Requires unavailable real market data and futures execution support.",
        requirements=[
            DataRequirement(
                requirement_id="book",
                data_kind=DataKind.ORDER_BOOK,
                asset_class=AssetClass.FUTURES,
                instruments=("MES",),
                resolution=Resolution.SECOND_1,
            ),
            ToolRequirement(
                requirement_id="tool",
                tool_kind=ToolKind.BACKTEST_EXECUTION,
            ),
        ],
    )


def _submit_candidate(store: SQLiteStore, registry: CapabilityRegistry, candidate: ResearchCandidate):
    intake = GovernedResearchIntake(store, registry)
    result = intake.submit(candidate)
    latest = store.get_latest_feasibility_decision(candidate.id)
    assert latest is not None
    return result, latest


def _make_openai_response(parsed: dict):
    text = json.dumps(parsed)
    item = SimpleNamespace(type="output_text", parsed=parsed, text=text)
    msg = SimpleNamespace(type="message", content=[item])
    return SimpleNamespace(
        output=[msg],
        usage={},
        id="r1",
        model="gpt-5.6-terra",
        status="completed",
        created_at=1.0,
        completed_at=2.0,
    )


def _design_decision(candidate_id: str, **overrides) -> ResearchDesignerDecision:
    payload = {
        "id": "decision-1",
        "candidate_id": candidate_id,
        "decision_type": ResearchDesignerDecisionType.DESIGN,
        "design_kind": ResearchDesignKind.PARAMETER_SENSITIVITY,
        "independent_variables": (DesignVariable.SIGNAL_THRESHOLD,),
        "dependent_outcomes": (
            DesignOutcome.TRADE_COUNT,
            DesignOutcome.NET_PNL,
            DesignOutcome.SHARPE,
        ),
        "controls": (DesignVariable.LOOKBACK,),
        "comparison_intent": ComparisonIntent.CONTRAST_PARAMETER_LEVELS,
        "analysis_intent": AnalysisIntent.SENSITIVITY_ANALYSIS,
        "falsification_condition": (
            "If changing signal threshold does not change trade_count or risk-adjusted performance "
            "while lookback remains fixed, the hypothesis is weakened."
        ),
        "rationale": (
            "Use a bounded parameter-sensitivity design that varies signal threshold while holding "
            "lookback fixed."
        ),
        "provider": "fake",
        "model": "fake-v1",
        "prompt_version": "v1",
        "ontology_version": RESEARCH_DESIGN_ONTOLOGY_VERSION,
        "ontology_fingerprint": build_research_design_ontology_snapshot().fingerprint,
    }
    payload.update(overrides)
    return ResearchDesignerDecision(**payload)


class _StaticDesigner:
    provider = "test"
    model = "test-model"
    prompt_version = "v1"

    def __init__(self, decision: ResearchDesignerDecision) -> None:
        self._decision = decision
        self.called = 0

    def design(self, context):
        self.called += 1
        return self._decision


class _RaisingDesigner:
    provider = "test"
    model = "test-model"
    prompt_version = "v1"

    def design(self, context):
        raise RuntimeError("boom")


def test_research_design_ontology_snapshot_is_deterministic():
    first = build_research_design_ontology_snapshot()
    second = build_research_design_ontology_snapshot()
    assert first.version == RESEARCH_DESIGN_ONTOLOGY_VERSION
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64


def test_research_design_ontology_v1_fingerprint_preserved():
    ontology = build_research_design_ontology_snapshot()
    assert ontology.version == RESEARCH_DESIGN_ONTOLOGY_VERSION
    assert ontology.fingerprint == "7fd37d3302833d582bde6ad8b17b6b7c1be2d52e8f345b5156037e2c3058002e"


def test_research_design_ontology_payload_omits_exact_materialization_values():
    payload_str = json.dumps(build_research_design_ontology_snapshot().to_payload(), sort_keys=True)
    assert "2.0" not in payload_str
    assert "2.5" not in payload_str
    assert "20" not in payload_str
    assert "stub_backtester_v1" not in payload_str
    assert "selected_capability_id" not in payload_str


def test_research_designer_prompt_v1_available_and_hash_locked():
    assert available_versions() == ("v1",)
    prompt = get_research_designer_instructions("v1")
    assert RESEARCH_DESIGNER_VERSION == "research_designer_v1"
    assert "NO_VALID_DESIGN" in prompt
    assert "signal_threshold is the only supported independent variable" in prompt
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == (
        "8744692f166fdb6058a4597abb6bcbad17489817efc1879c3506643e1d922fac"
    )


def test_context_contains_candidate_science_and_ontology_without_registry_leakage():
    candidate = _ready_candidate()
    ontology = build_research_design_ontology_snapshot()
    context = build_research_designer_context(
        candidate=candidate,
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    payload = context_to_payload(context, ontology=ontology)
    payload_str = json.dumps(payload, sort_keys=True)
    assert payload["candidate_id"] == candidate.id
    assert payload["hypothesis_statement"] == candidate.hypothesis_statement
    assert payload["research_design_ontology"]["version"] == ontology.version
    assert payload["intent_contract_version"] == RESEARCH_DESIGN_INTENT_CONTRACT_VERSION
    assert "stub_backtester_v1" not in payload_str
    assert "enabled" not in payload_str
    assert "registry_fingerprint" not in payload_str
    assert "selected_capability_id" not in payload_str
    assert "baseline_parameters" not in payload_str
    assert "2.0" not in payload_str
    assert "2.5" not in payload_str
    assert "20" not in payload_str


def test_validator_accepts_valid_design():
    candidate = _ready_candidate()
    ontology = build_research_design_ontology_snapshot()
    context = build_research_designer_context(
        candidate=candidate,
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    valid, errors = ResearchDesignProposalValidator(
        capability_id_tokens=("stub_backtester_v1",)
    ).validate(_design_decision(candidate.id), context, ontology)
    assert valid
    assert errors == {}


def test_validator_rejects_invalid_enum_like_values():
    candidate = _ready_candidate()
    ontology = build_research_design_ontology_snapshot()
    context = build_research_designer_context(
        candidate=candidate,
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    decision = _design_decision(
        candidate.id,
        design_kind="NOT_A_KIND",
        independent_variables=("not_a_variable",),
    )
    valid, errors = ResearchDesignProposalValidator().validate(decision, context, ontology)
    assert not valid
    assert "design_kind" in errors
    assert "independent_variables" in errors


def test_validator_rejects_exact_value_leakage():
    candidate = _ready_candidate()
    ontology = build_research_design_ontology_snapshot()
    context = build_research_designer_context(
        candidate=candidate,
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    decision = _design_decision(
        candidate.id,
        rationale="Compare signal_threshold = 2.0 against another level while lookback stays fixed.",
    )
    valid, errors = ResearchDesignProposalValidator().validate(decision, context, ontology)
    assert not valid
    assert errors["rationale"] == "rationale must not encode exact execution parameter values"


def test_validator_rejects_capability_id_leakage():
    candidate = _ready_candidate()
    ontology = build_research_design_ontology_snapshot()
    context = build_research_designer_context(
        candidate=candidate,
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    decision = _design_decision(
        candidate.id,
        rationale="Use stub_backtester_v1 for the design.",
    )
    valid, errors = ResearchDesignProposalValidator(
        capability_id_tokens=("stub_backtester_v1",)
    ).validate(decision, context, ontology)
    assert not valid
    assert errors["rationale"] == "rationale must not leak capability IDs"


def test_validator_rejects_condition_ordering_language():
    candidate = _ready_candidate()
    ontology = build_research_design_ontology_snapshot()
    context = build_research_designer_context(
        candidate=candidate,
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    decision = _design_decision(
        candidate.id,
        falsification_condition="If the baseline beats the comparator, keep the first condition.",
    )
    valid, errors = ResearchDesignProposalValidator().validate(decision, context, ontology)
    assert not valid
    assert errors["falsification_condition"] == (
        "falsification_condition must not choose condition ordering or roles"
    )


def test_ready_for_spec_authorization_required_before_provider_invocation(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _blocked_candidate()
    _, latest = _submit_candidate(store, registry, candidate)
    designer = _StaticDesigner(_design_decision(candidate.id))
    governed = GovernedResearchDesigner(store=store, registry=registry, designer=designer)
    with pytest.raises(RuntimeError, match="READY_FOR_SPEC"):
        governed.generate_design_intent(
            candidate_id=candidate.id,
            candidate_feasibility_decision_id=latest.id,
        )
    assert designer.called == 0
    assert store.get_research_designer_invocations(candidate.id) == []


def test_explicit_authorization_id_required(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _ready_candidate()
    _submit_candidate(store, registry, candidate)
    governed = GovernedResearchDesigner(store=store, registry=registry, designer=FakeResearchDesigner())
    with pytest.raises(KeyError, match="Feasibility decision not found"):
        governed.generate_design_intent(
            candidate_id=candidate.id,
            candidate_feasibility_decision_id="missing-auth",
        )


def test_wrong_candidate_authorization_rejected(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate_a = _ready_candidate()
    _, latest_a = _submit_candidate(store, registry, candidate_a)
    candidate_b = _ready_candidate(statement="Different candidate")
    _submit_candidate(store, registry, candidate_b)
    governed = GovernedResearchDesigner(store=store, registry=registry, designer=FakeResearchDesigner())
    with pytest.raises(RuntimeError, match="does not belong"):
        governed.generate_design_intent(
            candidate_id=candidate_b.id,
            candidate_feasibility_decision_id=latest_a.id,
        )


def test_valid_design_materializes_authoritative_intent_and_persists_invocation(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _ready_candidate()
    _, latest = _submit_candidate(store, registry, candidate)
    governed = GovernedResearchDesigner(store=store, registry=registry, designer=FakeResearchDesigner())

    result = governed.generate_design_intent(
        candidate_id=candidate.id,
        candidate_feasibility_decision_id=latest.id,
    )

    assert result.design_intent is not None
    assert result.design_intent.source.startswith("research_designer_v1:fake:fake-v1")
    assert result.design_intent.ontology_version == RESEARCH_DESIGN_ONTOLOGY_VERSION
    assert result.invocation.validation_status == "VALID"
    invocations = store.get_research_designer_invocations(candidate.id)
    assert len(invocations) == 1
    assert invocations[0].resulting_design_intent_id == result.design_intent.id


def test_no_valid_design_persists_invocation_without_creating_intent(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _ready_candidate(
        statement="Lookback length drives the stability of synthetic outcomes.",
        rationale="This candidate is about lookback sensitivity only.",
    )
    _, latest = _submit_candidate(store, registry, candidate)
    governed = GovernedResearchDesigner(store=store, registry=registry, designer=FakeResearchDesigner())

    result = governed.generate_design_intent(
        candidate_id=candidate.id,
        candidate_feasibility_decision_id=latest.id,
    )

    assert result.design_intent is None
    assert result.decision is not None
    assert result.decision.decision_type == ResearchDesignerDecisionType.NO_VALID_DESIGN
    invocations = store.get_research_designer_invocations(candidate.id)
    assert len(invocations) == 1
    assert invocations[0].validation_status == "VALID"
    assert invocations[0].resulting_design_intent_id is None
    assert store.list_research_design_intents(candidate.id) == []


def test_validation_failure_persists_invocation_and_no_intent(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _ready_candidate()
    _, latest = _submit_candidate(store, registry, candidate)
    invalid_designer = _StaticDesigner(
        _design_decision(candidate.id, rationale="Compare signal_threshold = 2.0 to another level.")
    )
    governed = GovernedResearchDesigner(store=store, registry=registry, designer=invalid_designer)

    result = governed.generate_design_intent(
        candidate_id=candidate.id,
        candidate_feasibility_decision_id=latest.id,
    )

    assert result.design_intent is None
    invocations = store.get_research_designer_invocations(candidate.id)
    assert len(invocations) == 1
    assert invocations[0].validation_status == "INVALID"
    errors = json.loads(invocations[0].validation_errors_json)
    assert "rationale" in errors


def test_invocation_history_is_append_only(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _ready_candidate()
    _, latest = _submit_candidate(store, registry, candidate)
    governed = GovernedResearchDesigner(store=store, registry=registry, designer=FakeResearchDesigner())

    governed.generate_design_intent(candidate_id=candidate.id, candidate_feasibility_decision_id=latest.id)
    governed.generate_design_intent(candidate_id=candidate.id, candidate_feasibility_decision_id=latest.id)

    invocations = store.get_research_designer_invocations(candidate.id)
    assert len(invocations) == 2
    assert invocations[0].id != invocations[1].id


def test_provider_error_is_persisted_then_re_raised(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _ready_candidate()
    _, latest = _submit_candidate(store, registry, candidate)
    governed = GovernedResearchDesigner(store=store, registry=registry, designer=_RaisingDesigner())

    with pytest.raises(RuntimeError, match="boom"):
        governed.generate_design_intent(
            candidate_id=candidate.id,
            candidate_feasibility_decision_id=latest.id,
        )

    invocations = store.get_research_designer_invocations(candidate.id)
    assert len(invocations) == 1
    assert invocations[0].validation_status == "ERROR"
    assert "infrastructure_error" in json.loads(invocations[0].validation_errors_json)


def test_ai_created_intent_is_downstream_compatible_with_existing_materializer(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _ready_candidate(
        rationale="Even if someone mentions 2.0, 2.5, or 20 in prose, the design intent should stay abstract."
    )
    _, latest = _submit_candidate(store, registry, candidate)
    governed = GovernedResearchDesigner(store=store, registry=registry, designer=FakeResearchDesigner())
    result = governed.generate_design_intent(
        candidate_id=candidate.id,
        candidate_feasibility_decision_id=latest.id,
    )
    assert result.design_intent is not None

    materialized = SpecMaterializer().materialize(
        candidate=store.get_research_candidate(candidate.id),
        design_intent=result.design_intent,
        candidate_feasibility_decision=latest,
        registry=registry,
    )

    baseline, comparator = materialized.plan.ordered_conditions
    assert baseline.exact_parameters["signal_threshold"] == 2.0
    assert comparator.exact_parameters["signal_threshold"] == 2.5
    assert baseline.exact_parameters["lookback"] == comparator.exact_parameters["lookback"] == 20


def test_openai_research_designer_adapter_parses_design():
    parsed = {
        "decision": "DESIGN",
        "design_kind": "PARAMETER_SENSITIVITY",
        "independent_variables": ["signal_threshold"],
        "dependent_outcomes": ["trade_count", "net_pnl", "sharpe"],
        "controls": ["lookback"],
        "comparison_intent": "CONTRAST_PARAMETER_LEVELS",
        "analysis_intent": "SENSITIVITY_ANALYSIS",
        "falsification_condition": "If changing signal threshold does not change trade_count or risk-adjusted performance, the hypothesis is weakened.",
        "rationale": "Use bounded parameter sensitivity while holding lookback fixed.",
        "no_valid_design_reason": None,
    }
    client = MagicMock()
    client.responses.parse.return_value = _make_openai_response(parsed)
    candidate = _ready_candidate()
    context = build_research_designer_context(
        candidate=candidate,
        candidate_feasibility_decision_id="auth-1",
        ontology=build_research_design_ontology_snapshot(),
    )
    decision = OpenAIResearchDesigner(client=client).design(context)
    assert decision.decision_type == ResearchDesignerDecisionType.DESIGN
    assert decision.independent_variables == (DesignVariable.SIGNAL_THRESHOLD,)
    assert decision.controls == (DesignVariable.LOOKBACK,)


def test_openai_research_designer_input_contains_ontology_without_capability_or_policy_leakage():
    captured: dict = {}
    parsed = {
        "decision": "NO_VALID_DESIGN",
        "design_kind": None,
        "independent_variables": None,
        "dependent_outcomes": None,
        "controls": None,
        "comparison_intent": None,
        "analysis_intent": None,
        "falsification_condition": None,
        "rationale": None,
        "no_valid_design_reason": "too vague",
    }
    resp = _make_openai_response(parsed)
    client = MagicMock()
    client.responses.parse.side_effect = lambda **kw: (captured.update(kw), resp)[1]
    candidate = _ready_candidate()
    context = build_research_designer_context(
        candidate=candidate,
        candidate_feasibility_decision_id="auth-1",
        ontology=build_research_design_ontology_snapshot(),
    )
    OpenAIResearchDesigner(client=client).design(context)
    payload = json.loads(captured["input"])
    payload_str = json.dumps(payload, sort_keys=True)
    assert payload["research_design_ontology"]["version"] == RESEARCH_DESIGN_ONTOLOGY_VERSION
    assert "stub_backtester_v1" not in payload_str
    assert "enabled" not in payload_str
    assert "selected_capability_id" not in payload_str
    assert "baseline_parameters" not in payload_str
    assert "2.0" not in payload_str


def test_openai_research_designer_schema_has_no_governance_or_exact_value_fields():
    captured: dict = {}
    parsed = {
        "decision": "NO_VALID_DESIGN",
        "design_kind": None,
        "independent_variables": None,
        "dependent_outcomes": None,
        "controls": None,
        "comparison_intent": None,
        "analysis_intent": None,
        "falsification_condition": None,
        "rationale": None,
        "no_valid_design_reason": "too vague",
    }
    resp = _make_openai_response(parsed)
    client = MagicMock()
    client.responses.parse.side_effect = lambda **kw: (captured.update(kw), resp)[1]
    candidate = _ready_candidate()
    context = build_research_designer_context(
        candidate=candidate,
        candidate_feasibility_decision_id="auth-1",
        ontology=build_research_design_ontology_snapshot(),
    )
    OpenAIResearchDesigner(client=client).design(context)
    tf = captured["text_format"]
    fields = list(tf.model_fields.keys())
    for forbidden in (
        "id",
        "candidate_id",
        "source",
        "created_at",
        "plan_id",
        "condition_id",
        "selected_capability_id",
        "baseline_parameters",
        "comparator_parameters",
    ):
        assert forbidden not in fields


def test_fixture_loads_8_cases():
    cases = load_cases_from_file("evals/research_designer_v1.json")
    assert len(cases) == 8
    assert len({case.id for case in cases}) == 8


def test_eval_harness_runs_fake_designer_without_api_calls(monkeypatch):
    import urllib.request

    called = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: called.append(True))
    cases = load_cases_from_file("evals/research_designer_v1.json")
    results = ResearchDesignerEvalSuite(cases).run(FakeResearchDesigner())
    assert len(results) == 8
    assert not called


def test_eval_harness_blocked_case_stays_pre_call():
    cases = {case.id: case for case in load_cases_from_file("evals/research_designer_v1.json")}
    result = ResearchDesignerEvalSuite([cases["case-07"]]).run(FakeResearchDesigner())[0]
    assert result.runner_outcome == "BLOCKED_PRE_CALL"
    assert result.resulting_design_intent_id is None


def test_live_runner_requires_allow_live_api():
    with pytest.raises(RuntimeError, match="--allow-live-api"):
        run_live_research_designer_eval(
            model="test",
            eval_path="evals/research_designer_v1.json",
            allow_live_api=False,
        )


def test_v8_to_v9_migration_adds_research_designer_invocations_and_intent_provenance(tmp_path):
    db = Path(tmp_path) / "v8.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL);
        INSERT INTO schema_version (id, version) VALUES (1, 8);
        CREATE TABLE research_candidates (
            id TEXT PRIMARY KEY,
            hypothesis_statement TEXT NOT NULL,
            hypothesis_rationale TEXT NOT NULL,
            source TEXT NOT NULL,
            requirements_json TEXT NOT NULL,
            candidate_fingerprint TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE research_design_intents (
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            design_kind TEXT NOT NULL,
            independent_variables_json TEXT NOT NULL,
            dependent_outcomes_json TEXT NOT NULL,
            controls_json TEXT NOT NULL,
            comparison_intent TEXT NOT NULL,
            analysis_intent TEXT NOT NULL,
            falsification_condition TEXT NOT NULL,
            rationale TEXT NOT NULL,
            source TEXT NOT NULL,
            provider TEXT,
            model TEXT,
            prompt_version TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()

    store = SQLiteStore(db)
    with store.connect() as connection:
        version = connection.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
        assert version == 9
        intent_columns = [
            row[1] for row in connection.execute("PRAGMA table_info(research_design_intents)").fetchall()
        ]
        assert "ontology_version" in intent_columns
        assert "ontology_fingerprint" in intent_columns
        tables = [
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        assert "research_designer_invocations" in tables


def test_fresh_v9_db_has_research_designer_tables(tmp_path):
    store = SQLiteStore(tmp_path / "fresh.db")
    with store.connect() as connection:
        version = connection.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
        tables = [
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        intent_columns = [
            row[1] for row in connection.execute("PRAGMA table_info(research_design_intents)").fetchall()
        ]
    assert version == 9
    assert "research_designer_invocations" in tables
    assert "ontology_version" in intent_columns
    assert "ontology_fingerprint" in intent_columns
