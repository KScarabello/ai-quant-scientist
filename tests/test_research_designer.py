from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType
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
    ExpectedDirection,
    OutcomePrediction,
    ResearchPredictionPlan,
    ResearchDesignIntent,
    ResearchDesignKind,
)
from ai_quant_scientist.models.research_designer import (
    RESEARCH_DESIGN_INTENT_CONTRACT_VERSION,
    ResearchDesignerContext,
    ResearchDesignerDecision,
    ResearchDesignerDecisionType,
)
from ai_quant_scientist.models.hypothesis_scientist import (
    ResearchBrief,
    ResearchScope,
    ResearchScopeOutcomeAggregation,
)
from ai_quant_scientist.services.openai_research_designer import OpenAIResearchDesigner
from ai_quant_scientist.services.research_design_ontology import (
    RESEARCH_DESIGN_ONTOLOGY_VERSION,
    RESEARCH_DESIGN_ONTOLOGY_V2_VERSION,
    RESEARCH_DESIGN_ONTOLOGY_V3_VERSION,
    RESEARCH_PREDICTION_PLAN_CONTRACT_VERSION,
    build_current_research_design_ontology_snapshot,
    build_research_design_ontology_snapshot,
    compute_research_design_ontology_fingerprint,
)
from ai_quant_scientist.services.research_designer import (
    FakeResearchDesigner,
    GovernedResearchDesigner,
    ResearchDesignProposalValidator,
    build_research_designer_context,
    context_to_payload,
)
from ai_quant_scientist.services.hypothesis_scientist import FakeHypothesisScientist, generate_candidate
from ai_quant_scientist.services.research_designer_prompts import (
    CURRENT_RESEARCH_DESIGNER_VERSION,
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


def _ready_directional_candidate() -> ResearchCandidate:
    return _ready_candidate(
        statement=(
            "A stricter signal threshold should lower trade frequency and higher "
            "risk-adjusted performance."
        ),
        rationale=(
            "Filtering weaker signal realizations should reduce trade frequency while improving "
            "risk-adjusted performance under fixed lookback."
        ),
    )


def _ontology_v1():
    return build_research_design_ontology_snapshot(version="v1")


def _ontology_v2():
    return build_research_design_ontology_snapshot(version="v2")


def _ontology_v3():
    return build_current_research_design_ontology_snapshot()


def _governed_v1(store: SQLiteStore, registry: CapabilityRegistry, designer) -> GovernedResearchDesigner:
    return GovernedResearchDesigner(
        store=store,
        registry=registry,
        designer=designer,
        ontology=_ontology_v1(),
    )


def _governed_v2(store: SQLiteStore, registry: CapabilityRegistry, designer) -> GovernedResearchDesigner:
    return GovernedResearchDesigner(
        store=store,
        registry=registry,
        designer=designer,
        ontology=_ontology_v2(),
    )


def _governed_v3(store: SQLiteStore, registry: CapabilityRegistry, designer) -> GovernedResearchDesigner:
    return GovernedResearchDesigner(
        store=store,
        registry=registry,
        designer=designer,
        ontology=_ontology_v3(),
    )


def _persist_v4_candidate_and_claim_set(store: SQLiteStore):
    brief = ResearchBrief.create(
        research_question=(
            "For identical synthetic strategy logic, does a stricter signal threshold reduce trade frequency "
            "and improve risk-adjusted performance?"
        ),
        asset_class_focus="SYNTHETIC",
        research_scope=ResearchScope.create(
            independent_variable=DesignVariable.SIGNAL_THRESHOLD,
            requested_outcomes=(DesignOutcome.TRADE_COUNT, DesignOutcome.SHARPE),
            outcome_aggregation=ResearchScopeOutcomeAggregation.ALL_OUTCOMES_REQUIRED,
        ),
    )
    invocation, candidate = generate_candidate(FakeHypothesisScientist(), brief, store)
    assert candidate is not None
    claim_set = store.get_hypothesis_claim_set(invocation.resulting_claim_set_id)
    assert claim_set is not None
    return candidate, claim_set


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
        "ontology_fingerprint": _ontology_v1().fingerprint,
    }
    payload.update(overrides)
    return ResearchDesignerDecision(**payload)


def _design_decision_v2(candidate_id: str, **overrides) -> ResearchDesignerDecision:
    payload = {
        "id": "decision-v2",
        "candidate_id": candidate_id,
        "decision_type": ResearchDesignerDecisionType.DESIGN,
        "design_kind": ResearchDesignKind.PARAMETER_SENSITIVITY,
        "independent_variables": (DesignVariable.SIGNAL_THRESHOLD,),
        "dependent_outcomes": (
            DesignOutcome.SHARPE,
            DesignOutcome.TRADE_COUNT,
        ),
        "controls": (DesignVariable.LOOKBACK,),
        "comparison_intent": ComparisonIntent.CONTRAST_PARAMETER_LEVELS,
        "analysis_intent": AnalysisIntent.SENSITIVITY_ANALYSIS,
        "predictions": (
            OutcomePrediction(
                outcome=DesignOutcome.TRADE_COUNT,
                expected_direction=ExpectedDirection.DECREASE,
            ),
            OutcomePrediction(
                outcome=DesignOutcome.SHARPE,
                expected_direction=ExpectedDirection.INCREASE,
            ),
        ),
        "falsification_condition": (
            "The hypothesis is falsified if a stricter signal threshold does not yield lower trade "
            "frequency and higher risk-adjusted performance under fixed controls."
        ),
        "rationale": (
            "Use a bounded parameter-sensitivity design and precommit directional predictions for "
            "trade_count and sharpe."
        ),
        "provider": "fake",
        "model": "fake-v2",
        "prompt_version": "v2",
        "ontology_version": RESEARCH_DESIGN_ONTOLOGY_V2_VERSION,
        "ontology_fingerprint": _ontology_v2().fingerprint,
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


class _CapturingDesigner:
    provider = "capture"
    model = "capture-model"
    prompt_version = "v1"

    def __init__(self) -> None:
        self.called = 0
        self.context = None

    def design(self, context):
        self.called += 1
        self.context = context
        return _design_decision(
            context.candidate_id,
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
            ontology_version=context.design_ontology_version,
            ontology_fingerprint=context.design_ontology_fingerprint,
        )


def _canonical_payload_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _payload_with_fingerprint(payload: dict) -> dict:
    payload_with_fingerprint = dict(payload)
    payload_with_fingerprint["fingerprint"] = compute_research_design_ontology_fingerprint(payload_with_fingerprint)
    return payload_with_fingerprint


def _context_with_payload(candidate: ResearchCandidate, payload: dict) -> ResearchDesignerContext:
    return ResearchDesignerContext(
        candidate=candidate,
        candidate_feasibility_decision_id="auth-1",
        design_ontology_version=payload["version"],
        design_ontology_fingerprint=payload["fingerprint"],
        design_ontology_payload_json=_canonical_payload_json(payload),
        intent_contract_version=payload["intent_contract_version"],
    )


def test_research_design_ontology_snapshot_is_deterministic():
    first = _ontology_v1()
    second = _ontology_v1()
    assert first.version == RESEARCH_DESIGN_ONTOLOGY_VERSION
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64


def test_research_design_ontology_v1_fingerprint_preserved():
    ontology = _ontology_v1()
    assert ontology.version == RESEARCH_DESIGN_ONTOLOGY_VERSION
    assert ontology.fingerprint == "7fd37d3302833d582bde6ad8b17b6b7c1be2d52e8f345b5156037e2c3058002e"
    assert compute_research_design_ontology_fingerprint(ontology.to_payload()) == ontology.fingerprint


def test_research_design_ontology_payload_omits_exact_materialization_values():
    payload_str = json.dumps(_ontology_v1().to_payload(), sort_keys=True)
    assert "2.0" not in payload_str
    assert "2.5" not in payload_str
    assert "20" not in payload_str
    assert "stub_backtester_v1" not in payload_str
    assert "selected_capability_id" not in payload_str


def test_research_designer_prompt_v1_available_and_hash_locked():
    assert available_versions() == ("v1", "v2", "v3")
    prompt = get_research_designer_instructions("v1")
    assert RESEARCH_DESIGNER_VERSION == "research_designer_v1"
    assert CURRENT_RESEARCH_DESIGNER_VERSION == "research_designer_v3"
    assert "NO_VALID_DESIGN" in prompt
    assert "signal_threshold is the only supported independent variable" in prompt
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == (
        "8744692f166fdb6058a4597abb6bcbad17489817efc1879c3506643e1d922fac"
    )


def test_research_designer_prompt_v2_available_and_hash_locked():
    prompt = get_research_designer_instructions("v2")
    assert "exactly one directional prediction" in prompt
    assert "must not contain exact numeric targets" in prompt
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == (
        "721392d5160f82c8de83eaef67f4c3fc96fc13872bd1823f43b7c681737187cb"
    )


def test_research_designer_prompt_v3_available():
    prompt = get_research_designer_instructions("v3")
    assert "HypothesisClaimSet" in prompt
    assert "must cover every authoritative material outcome claim exactly" in prompt
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == (
        "2f94172fb0219955bced7deab320d778ab4e83fe8c8e57466aeeed707955df36"
    )


def test_context_contains_candidate_science_and_ontology_without_registry_leakage():
    candidate = _ready_candidate()
    ontology = build_research_design_ontology_snapshot()
    context = build_research_designer_context(
        candidate=candidate,
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    payload = context_to_payload(context)
    payload_str = json.dumps(payload, sort_keys=True)
    ontology_payload_str = json.dumps(payload["research_design_ontology"], sort_keys=True)
    assert payload["candidate_id"] == candidate.id
    assert payload["hypothesis_statement"] == candidate.hypothesis_statement
    assert payload["research_design_ontology"]["version"] == ontology.version
    assert payload["intent_contract_version"] == RESEARCH_DESIGN_INTENT_CONTRACT_VERSION
    assert "stub_backtester_v1" not in payload_str
    assert "enabled" not in payload_str
    assert "registry_fingerprint" not in payload_str
    assert "selected_capability_id" not in payload_str
    assert "baseline_parameters" not in ontology_payload_str
    assert "2.0" not in ontology_payload_str
    assert "2.5" not in ontology_payload_str
    assert "20" not in ontology_payload_str


def test_context_carries_exact_canonical_ontology_payload_json():
    ontology = build_research_design_ontology_snapshot()
    context = build_research_designer_context(
        candidate=_ready_candidate(),
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    assert context.design_ontology_payload_json == _canonical_payload_json(ontology.to_payload())


def test_context_built_from_ontology_v1_passes_semantic_fingerprint_validation():
    ontology = build_research_design_ontology_snapshot()
    context = build_research_designer_context(
        candidate=_ready_candidate(),
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    assert context.design_ontology_payload["fingerprint"] == ontology.fingerprint


def test_context_mismatch_between_version_and_payload_fails_closed():
    candidate = _ready_candidate()
    ontology = build_research_design_ontology_snapshot()
    payload = ontology.to_payload()
    with pytest.raises(ValueError, match="payload version must match"):
        ResearchDesignerContext(
            candidate=candidate,
            candidate_feasibility_decision_id="auth-1",
            design_ontology_version="wrong_version",
            design_ontology_fingerprint=payload["fingerprint"],
            design_ontology_payload_json=_canonical_payload_json(payload),
            intent_contract_version=payload["intent_contract_version"],
        )


def test_context_mismatch_between_fingerprint_and_payload_fails_closed():
    candidate = _ready_candidate()
    ontology = build_research_design_ontology_snapshot()
    payload = ontology.to_payload()
    with pytest.raises(ValueError, match="design_ontology_fingerprint must match the semantic ontology payload"):
        ResearchDesignerContext(
            candidate=candidate,
            candidate_feasibility_decision_id="auth-1",
            design_ontology_version=payload["version"],
            design_ontology_fingerprint="0" * 64,
            design_ontology_payload_json=_canonical_payload_json(payload),
            intent_contract_version=payload["intent_contract_version"],
        )


def test_context_semantic_payload_change_with_stale_embedded_fingerprint_fails_closed():
    candidate = _ready_candidate()
    payload = build_research_design_ontology_snapshot().to_payload()
    payload["parameter_sensitivity_semantics"] = "Tampered semantic boundary."
    with pytest.raises(ValueError, match="payload fingerprint must match the semantic ontology payload"):
        _context_with_payload(candidate, payload)


def test_context_nested_semantic_payload_change_with_stale_embedded_fingerprint_fails_closed():
    candidate = _ready_candidate()
    payload = build_research_design_ontology_snapshot().to_payload()
    payload["variable_semantics"]["signal_threshold"] = "Tampered nested variable semantics."
    with pytest.raises(ValueError, match="payload fingerprint must match the semantic ontology payload"):
        _context_with_payload(candidate, payload)


def test_context_embedded_payload_fingerprint_change_alone_fails_closed():
    candidate = _ready_candidate()
    payload = build_research_design_ontology_snapshot().to_payload()
    payload["fingerprint"] = "1" * 64
    with pytest.raises(ValueError, match="payload fingerprint must match the semantic ontology payload"):
        _context_with_payload(candidate, payload)


def test_context_mismatch_between_contract_version_and_payload_fails_closed():
    candidate = _ready_candidate()
    ontology = build_research_design_ontology_snapshot()
    payload = ontology.to_payload()
    with pytest.raises(ValueError, match="payload intent_contract_version must match"):
        ResearchDesignerContext(
            candidate=candidate,
            candidate_feasibility_decision_id="auth-1",
            design_ontology_version=payload["version"],
            design_ontology_fingerprint=payload["fingerprint"],
            design_ontology_payload_json=_canonical_payload_json(payload),
            intent_contract_version="wrong_contract_version",
        )


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


def test_research_design_ontology_v2_exposes_prediction_contract():
    ontology = _ontology_v2()
    assert ontology.version == RESEARCH_DESIGN_ONTOLOGY_V2_VERSION
    assert ontology.fingerprint == "73364d9d50de6bd0585fe74dd1061f9002515d972d746d45bcb06883bd1d608d"
    assert ontology.prediction_contract_version == RESEARCH_PREDICTION_PLAN_CONTRACT_VERSION
    assert ontology.supported_expected_directions == ("DECREASE", "INCREASE", "NO_CHANGE")
    assert "directional prediction" in ontology.prediction_semantics.lower()
    assert compute_research_design_ontology_fingerprint(ontology.to_payload()) == ontology.fingerprint


def test_research_design_ontology_v3_exposes_claim_coverage_contract():
    ontology = _ontology_v3()
    assert ontology.version == RESEARCH_DESIGN_ONTOLOGY_V3_VERSION
    assert ontology.fingerprint == "792528b090a549609e03484afdee4ea661ae247e9affe9416e62aae1f7b99183"
    assert ontology.prediction_contract_version == RESEARCH_PREDICTION_PLAN_CONTRACT_VERSION
    assert ontology.prediction_authority == "DETERMINISTIC_FROM_CLAIM_SET"
    assert ontology.claim_coverage_semantics is not None


def test_context_built_from_ontology_v2_passes_semantic_fingerprint_validation():
    ontology = _ontology_v2()
    context = build_research_designer_context(
        candidate=_ready_candidate(),
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    assert context.design_ontology_payload["fingerprint"] == ontology.fingerprint
    assert context.design_ontology_payload["prediction_contract_version"] == RESEARCH_PREDICTION_PLAN_CONTRACT_VERSION


def test_validator_accepts_valid_v2_design_with_predictions():
    candidate = _ready_candidate()
    ontology = _ontology_v2()
    context = build_research_designer_context(
        candidate=candidate,
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    valid, errors = ResearchDesignProposalValidator(
        capability_id_tokens=("stub_backtester_v1",)
    ).validate(_design_decision_v2(candidate.id), context, ontology)
    assert valid
    assert errors == {}


def test_validator_rejects_missing_prediction_for_selected_outcome():
    candidate = _ready_candidate()
    ontology = _ontology_v2()
    context = build_research_designer_context(
        candidate=candidate,
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    decision = _design_decision_v2(
        candidate.id,
        predictions=(
            OutcomePrediction(
                outcome=DesignOutcome.TRADE_COUNT,
                expected_direction=ExpectedDirection.DECREASE,
            ),
        ),
    )
    valid, errors = ResearchDesignProposalValidator().validate(decision, context, ontology)
    assert not valid
    assert "predictions_missing" in errors


def test_validator_rejects_duplicate_prediction_for_selected_outcome():
    candidate = _ready_candidate()
    ontology = _ontology_v2()
    context = build_research_designer_context(
        candidate=candidate,
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    decision = _design_decision_v2(
        candidate.id,
        predictions=(
            OutcomePrediction(
                outcome=DesignOutcome.TRADE_COUNT,
                expected_direction=ExpectedDirection.DECREASE,
            ),
            OutcomePrediction(
                outcome=DesignOutcome.TRADE_COUNT,
                expected_direction=ExpectedDirection.NO_CHANGE,
            ),
        ),
    )
    valid, errors = ResearchDesignProposalValidator().validate(decision, context, ontology)
    assert not valid
    assert "predictions_duplicate" in errors


def test_validator_rejects_prediction_for_unselected_outcome():
    candidate = _ready_candidate()
    ontology = _ontology_v2()
    context = build_research_designer_context(
        candidate=candidate,
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    decision = _design_decision_v2(
        candidate.id,
        dependent_outcomes=(DesignOutcome.TRADE_COUNT,),
        predictions=(
            OutcomePrediction(
                outcome=DesignOutcome.TRADE_COUNT,
                expected_direction=ExpectedDirection.DECREASE,
            ),
            OutcomePrediction(
                outcome=DesignOutcome.SHARPE,
                expected_direction=ExpectedDirection.INCREASE,
            ),
        ),
    )
    valid, errors = ResearchDesignProposalValidator().validate(decision, context, ontology)
    assert not valid
    assert "predictions_extra" in errors


def test_validator_accepts_complete_v3_claim_coverage(tmp_path):
    store = _store(tmp_path)
    candidate, claim_set = _persist_v4_candidate_and_claim_set(store)
    ontology = _ontology_v3()
    context = build_research_designer_context(
        candidate=candidate,
        hypothesis_claim_set=claim_set,
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    decision = _design_decision(
        candidate.id,
        dependent_outcomes=tuple(item.outcome for item in claim_set.claims),
        prompt_version="v3",
        ontology_version=ontology.version,
        ontology_fingerprint=ontology.fingerprint,
    )
    valid, errors = ResearchDesignProposalValidator().validate(decision, context, ontology)
    assert valid
    assert errors == {}


def test_validator_rejects_v3_design_that_omits_authoritative_claim_outcome(tmp_path):
    store = _store(tmp_path)
    candidate, claim_set = _persist_v4_candidate_and_claim_set(store)
    ontology = _ontology_v3()
    context = build_research_designer_context(
        candidate=candidate,
        hypothesis_claim_set=claim_set,
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    decision = _design_decision(
        candidate.id,
        dependent_outcomes=(DesignOutcome.TRADE_COUNT,),
        prompt_version="v3",
        ontology_version=ontology.version,
        ontology_fingerprint=ontology.fingerprint,
    )
    valid, errors = ResearchDesignProposalValidator().validate(decision, context, ontology)
    assert not valid
    assert errors["claim_set_outcomes_missing"] == (
        "DESIGN must cover every authoritative claim outcome: missing ['sharpe']"
    )


def test_validator_rejects_v3_design_that_adds_extra_scientific_outcome(tmp_path):
    store = _store(tmp_path)
    candidate, claim_set = _persist_v4_candidate_and_claim_set(store)
    ontology = _ontology_v3()
    context = build_research_designer_context(
        candidate=candidate,
        hypothesis_claim_set=claim_set,
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    decision = _design_decision(
        candidate.id,
        dependent_outcomes=(
            DesignOutcome.TRADE_COUNT,
            DesignOutcome.SHARPE,
            DesignOutcome.NET_PNL,
        ),
        prompt_version="v3",
        ontology_version=ontology.version,
        ontology_fingerprint=ontology.fingerprint,
    )
    valid, errors = ResearchDesignProposalValidator().validate(decision, context, ontology)
    assert not valid
    assert errors["claim_set_outcomes_extra"] == (
        "DESIGN must not add scientific outcomes outside the authoritative claim set: ['net_pnl']"
    )


def test_validator_rejects_unsupported_direction_like_value():
    from types import SimpleNamespace

    candidate = _ready_candidate()
    ontology = _ontology_v2()
    context = build_research_designer_context(
        candidate=candidate,
        candidate_feasibility_decision_id="auth-1",
        ontology=ontology,
    )
    decision = _design_decision_v2(
        candidate.id,
        predictions=(
            SimpleNamespace(
                outcome=DesignOutcome.TRADE_COUNT,
                expected_direction=SimpleNamespace(value="SIDEWAYS"),
            ),
            OutcomePrediction(
                outcome=DesignOutcome.SHARPE,
                expected_direction=ExpectedDirection.INCREASE,
            ),
        ),
    )
    valid, errors = ResearchDesignProposalValidator().validate(decision, context, ontology)
    assert not valid
    assert "predictions_directions" in errors or "predictions" in errors


def test_research_design_ontology_snapshot_is_deeply_immutable():
    ontology = build_research_design_ontology_snapshot()
    assert isinstance(ontology.eligible_independent_variables_by_design_kind, MappingProxyType)
    assert isinstance(ontology.required_controls_by_design_kind, MappingProxyType)
    assert isinstance(ontology.variable_semantics, MappingProxyType)
    assert isinstance(ontology.outcome_semantics, MappingProxyType)
    with pytest.raises(TypeError):
        ontology.variable_semantics["signal_threshold"] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        ontology.eligible_independent_variables_by_design_kind["PARAMETER_SENSITIVITY"] = ("lookback",)  # type: ignore[index]
    with pytest.raises(TypeError):
        ontology.required_controls_by_design_kind["PARAMETER_SENSITIVITY"] = ("signal_threshold",)  # type: ignore[index]


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
    governed = _governed_v1(store, registry, designer)
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
    governed = _governed_v1(store, registry, FakeResearchDesigner(prompt_version="v1"))
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
    governed = _governed_v1(store, registry, FakeResearchDesigner(prompt_version="v1"))
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
    governed = _governed_v1(store, registry, FakeResearchDesigner(prompt_version="v1"))

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


def test_valid_v2_design_materializes_prediction_plan_before_downstream_execution(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _ready_directional_candidate()
    _, latest = _submit_candidate(store, registry, candidate)
    governed = _governed_v2(store, registry, FakeResearchDesigner(prompt_version="v2"))

    result = governed.generate_design_intent(
        candidate_id=candidate.id,
        candidate_feasibility_decision_id=latest.id,
    )

    assert result.design_intent is not None
    assert result.prediction_plan is not None
    assert result.design_intent.source.startswith("research_designer_v2:fake:fake-v1")
    assert result.design_intent.ontology_version == RESEARCH_DESIGN_ONTOLOGY_V2_VERSION
    assert result.prediction_plan.design_intent_id == result.design_intent.id
    assert result.prediction_plan.candidate_id == candidate.id
    assert result.prediction_plan.research_designer_invocation_id == result.invocation.id
    assert result.prediction_plan.prediction_contract_version == RESEARCH_PREDICTION_PLAN_CONTRACT_VERSION
    assert [
        (item.outcome.value, item.expected_direction.value)
        for item in result.prediction_plan.predictions
    ] == [
        ("sharpe", "INCREASE"),
        ("trade_count", "DECREASE"),
    ]
    persisted = store.get_research_prediction_plan(result.prediction_plan.id)
    assert persisted is not None
    assert persisted.research_designer_invocation_id == result.invocation.id


def test_v2_design_and_prediction_persist_atomically(tmp_path, monkeypatch):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _ready_directional_candidate()
    _, latest = _submit_candidate(store, registry, candidate)
    governed = _governed_v2(store, registry, FakeResearchDesigner(prompt_version="v2"))

    original_connect = store.connect

    @contextmanager
    def failing_connect():
        with original_connect() as conn:
            class _Proxy:
                def __init__(self, inner):
                    self._inner = inner

                def execute(self, sql, params=()):
                    if "INSERT INTO research_prediction_plans" in sql:
                        raise RuntimeError("simulated_prediction_persistence_failure")
                    return self._inner.execute(sql, params)

                def __getattr__(self, name):
                    return getattr(self._inner, name)

            yield _Proxy(conn)

    monkeypatch.setattr(store, "connect", failing_connect)

    with pytest.raises(RuntimeError, match="simulated_prediction_persistence_failure"):
        governed.generate_design_intent(
            candidate_id=candidate.id,
            candidate_feasibility_decision_id=latest.id,
        )

    assert store.list_research_design_intents(candidate.id) == []
    assert store.get_research_designer_invocations(candidate.id) == []
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_prediction_plans").fetchone()[0] == 0


def test_ambiguous_v2_candidate_returns_no_valid_design_instead_of_guessing_direction(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _ready_candidate(
        statement="Signal threshold changes synthetic performance.",
        rationale="Changing threshold may alter outcomes, but no defensible direction is specified.",
    )
    _, latest = _submit_candidate(store, registry, candidate)
    governed = _governed_v2(store, registry, FakeResearchDesigner(prompt_version="v2"))

    result = governed.generate_design_intent(
        candidate_id=candidate.id,
        candidate_feasibility_decision_id=latest.id,
    )

    assert result.design_intent is None
    assert result.prediction_plan is None
    assert result.decision is not None
    assert result.decision.decision_type == ResearchDesignerDecisionType.NO_VALID_DESIGN
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_prediction_plans").fetchone()[0] == 0


def test_governed_service_uses_exact_context_owned_ontology_snapshot_for_provider_and_invocation(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _ready_candidate()
    _, latest = _submit_candidate(store, registry, candidate)
    capturing = _CapturingDesigner()
    governed = _governed_v1(store, registry, capturing)

    result = governed.generate_design_intent(
        candidate_id=candidate.id,
        candidate_feasibility_decision_id=latest.id,
    )

    assert capturing.called == 1
    assert capturing.context is not None
    provider_payload = context_to_payload(capturing.context)
    invocation = store.get_research_designer_invocations(candidate.id)[0]
    assert provider_payload["research_design_ontology"]["version"] == invocation.ontology_version
    assert provider_payload["research_design_ontology"]["fingerprint"] == invocation.ontology_fingerprint
    assert provider_payload["research_design_ontology"] == capturing.context.design_ontology_payload
    assert result.invocation.ontology_version == capturing.context.design_ontology_version
    assert result.invocation.ontology_fingerprint == capturing.context.design_ontology_fingerprint


def test_no_valid_design_persists_invocation_without_creating_intent(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _ready_candidate(
        statement="Lookback length drives the stability of synthetic outcomes.",
        rationale="This candidate is about lookback sensitivity only.",
    )
    _, latest = _submit_candidate(store, registry, candidate)
    governed = _governed_v1(store, registry, FakeResearchDesigner(prompt_version="v1"))

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
    governed = _governed_v1(store, registry, invalid_designer)

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
    governed = _governed_v1(store, registry, FakeResearchDesigner(prompt_version="v1"))

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
    governed = _governed_v1(store, registry, _RaisingDesigner())

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
    governed = _governed_v1(store, registry, FakeResearchDesigner(prompt_version="v1"))
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
    decision = OpenAIResearchDesigner(client=client, prompt_version="v1").design(context)
    assert decision.decision_type == ResearchDesignerDecisionType.DESIGN
    assert decision.independent_variables == (DesignVariable.SIGNAL_THRESHOLD,)
    assert decision.controls == (DesignVariable.LOOKBACK,)


def test_openai_research_designer_sends_exact_supplied_context_ontology_snapshot():
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
    decision = OpenAIResearchDesigner(client=client, prompt_version="v1").design(context)
    provider_payload = json.loads(captured["input"])
    assert provider_payload["research_design_ontology"] == context.design_ontology_payload
    assert decision.ontology_version == context.design_ontology_version
    assert decision.ontology_fingerprint == context.design_ontology_fingerprint


def test_openai_research_designer_does_not_substitute_global_current_ontology():
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
        "no_valid_design_reason": "custom snapshot cannot express this candidate",
    }
    resp = _make_openai_response(parsed)
    client = MagicMock()
    client.responses.parse.side_effect = lambda **kw: (captured.update(kw), resp)[1]
    candidate = _ready_candidate()
    custom_payload = _payload_with_fingerprint({
        "version": "research_design_ontology_test_injected",
        "intent_contract_version": RESEARCH_DESIGN_INTENT_CONTRACT_VERSION,
        "supported_design_kinds": ["PARAMETER_SENSITIVITY"],
        "design_variables": ["signal_threshold", "lookback"],
        "eligible_independent_variables_by_design_kind": {
            "PARAMETER_SENSITIVITY": ["signal_threshold"]
        },
        "required_controls_by_design_kind": {
            "PARAMETER_SENSITIVITY": ["lookback"]
        },
        "supported_dependent_outcomes": ["trade_count", "net_pnl", "sharpe"],
        "comparison_intents": ["CONTRAST_PARAMETER_LEVELS"],
        "analysis_intents": ["SENSITIVITY_ANALYSIS"],
        "variable_semantics": {
            "signal_threshold": "Injected test snapshot variable semantics.",
            "lookback": "Injected lookback control semantics."
        },
        "outcome_semantics": {
            "trade_count": "Injected trade count semantics.",
            "net_pnl": "Injected net pnl semantics.",
            "sharpe": "Injected sharpe semantics."
        },
        "parameter_sensitivity_semantics": "Injected parameter sensitivity semantics.",
        "exact_value_boundary": "Injected exact-value boundary.",
        "falsification_boundary": "Injected falsification boundary.",
        "control_boundary": "Injected control boundary.",
        "constraints": [
            "Injected constraint one.",
            "Injected constraint two.",
        ],
    })
    context = _context_with_payload(candidate, custom_payload)
    decision = OpenAIResearchDesigner(client=client, prompt_version="v1").design(context)
    provider_payload = json.loads(captured["input"])
    assert provider_payload["research_design_ontology"] == custom_payload
    assert provider_payload["research_design_ontology"]["version"] != RESEARCH_DESIGN_ONTOLOGY_VERSION
    assert provider_payload["research_design_ontology"]["fingerprint"] == custom_payload["fingerprint"]
    assert decision.ontology_version == custom_payload["version"]
    assert decision.ontology_fingerprint == custom_payload["fingerprint"]


def test_openai_research_designer_fails_closed_before_provider_invocation_on_payload_tampering():
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
    client = MagicMock()
    client.responses.parse.return_value = _make_openai_response(parsed)
    context = build_research_designer_context(
        candidate=_ready_candidate(),
        candidate_feasibility_decision_id="auth-1",
        ontology=build_research_design_ontology_snapshot(),
    )
    tampered_payload = context.design_ontology_payload
    tampered_payload["constraints"] = [*tampered_payload["constraints"], "Tampered extra constraint."]
    object.__setattr__(context, "design_ontology_payload_json", _canonical_payload_json(tampered_payload))

    with pytest.raises(ValueError, match="payload fingerprint must match the semantic ontology payload"):
        OpenAIResearchDesigner(client=client, prompt_version="v1").design(context)

    client.responses.parse.assert_not_called()


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
    OpenAIResearchDesigner(client=client, prompt_version="v1").design(context)
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
    OpenAIResearchDesigner(client=client, prompt_version="v1").design(context)
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
    results = ResearchDesignerEvalSuite(cases).run(FakeResearchDesigner(prompt_version="v1"))
    assert len(results) == 8
    assert not called


def test_eval_harness_blocked_case_stays_pre_call():
    cases = {case.id: case for case in load_cases_from_file("evals/research_designer_v1.json")}
    result = ResearchDesignerEvalSuite([cases["case-07"]]).run(FakeResearchDesigner(prompt_version="v1"))[0]
    assert result.runner_outcome == "BLOCKED_PRE_CALL"
    assert result.resulting_design_intent_id is None


def test_blocked_pre_call_representation_remains_separate_from_contract_passed():
    cases = {case.id: case for case in load_cases_from_file("evals/research_designer_v1.json")}
    result = ResearchDesignerEvalSuite([cases["case-07"]]).run(FakeResearchDesigner(prompt_version="v1"))[0]
    assert result.runner_outcome == "BLOCKED_PRE_CALL"
    assert result.contract_passed is False


def test_live_runner_requires_allow_live_api():
    with pytest.raises(RuntimeError, match="--allow-live-api"):
        run_live_research_designer_eval(
            model="test",
            eval_path="evals/research_designer_v1.json",
            allow_live_api=False,
        )


def test_v8_to_v12_migration_adds_research_designer_invocations_and_preserves_v15_1_tables(tmp_path):
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
        assert version == 12
        intent_columns = [
            row[1] for row in connection.execute("PRAGMA table_info(research_design_intents)").fetchall()
        ]
        assert "ontology_version" in intent_columns
        assert "ontology_fingerprint" in intent_columns
        tables = [
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        assert "research_designer_invocations" in tables
        assert "hypothesis_claim_sets" in tables
        assert "research_prediction_plans" in tables
        assert "scientific_verdicts" in tables
        assert "post_verdict_critic_invocations" in tables
        assert "post_verdict_research_intents" in tables


def test_fresh_v12_db_has_research_designer_and_prediction_tables(tmp_path):
    store = SQLiteStore(tmp_path / "fresh.db")
    with store.connect() as connection:
        version = connection.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
        tables = [
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        intent_columns = [
            row[1] for row in connection.execute("PRAGMA table_info(research_design_intents)").fetchall()
        ]
    assert version == 12
    assert "research_designer_invocations" in tables
    assert "hypothesis_claim_sets" in tables
    assert "research_prediction_plans" in tables
    assert "scientific_verdicts" in tables
    assert "post_verdict_critic_invocations" in tables
    assert "post_verdict_research_intents" in tables
    assert "ontology_version" in intent_columns
    assert "ontology_fingerprint" in intent_columns
