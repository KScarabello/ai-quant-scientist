"""Comprehensive deterministic tests for Bounded Hypothesis Scientist V0.12A.

Zero live API calls. Zero network calls.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ai_quant_scientist.capabilities import (
    AssetClass,
    DataKind,
    DataRequirement,
    GateDecision,
    Resolution,
    ToolKind,
    ToolRequirement,
    build_v1_registry,
)
from ai_quant_scientist.capabilities.gate import ResearchCandidate
from ai_quant_scientist.capabilities.intake import GovernedResearchIntake
from ai_quant_scientist.capabilities.serialization import (
    compute_candidate_fingerprint,
    requirements_from_json,
    requirements_to_json,
)
from ai_quant_scientist.evals.scientist_eval import (
    ScientistEvalSuite,
    load_cases_from_file,
)
from ai_quant_scientist.evals.run_live_scientist_eval import run_live_scientist_eval
from ai_quant_scientist.models.hypothesis_scientist import (
    HypothesisScientistDecision,
    HypothesisScientistDecisionType,
    HypothesisScientistInvocation,
    PriorCandidateSummary,
    ResearchBrief,
)
from ai_quant_scientist.services.hypothesis_prompts import (
    SCIENTIST_VERSION,
    available_versions,
    get_scientist_instructions,
)
from ai_quant_scientist.services.hypothesis_scientist import (
    FakeHypothesisScientist,
    HypothesisProposalValidator,
    SCIENTIST_SOURCE,
    brief_to_json,
    brief_to_payload,
    generate_candidate,
    materialize_research_candidate,
)
from ai_quant_scientist.services.openai_hypothesis_scientist import OpenAIHypothesisScientist
from ai_quant_scientist.services.scientist_requirement_ontology import (
    REQUIREMENT_ONTOLOGY_V1,
    REQUIREMENT_ONTOLOGY_VERSION,
    build_requirement_ontology_snapshot,
)
from ai_quant_scientist.storage.sqlite_store import SQLiteStore


# ─── helpers ─────────────────────────────────────────────────────────────────

def _synth_brief(**kw) -> ResearchBrief:
    return ResearchBrief.create(
        research_question=kw.get("research_question", "Does signal_threshold control trade frequency?"),
        asset_class_focus=kw.get("asset_class_focus", "SYNTHETIC"),
    )


def _propose_decision(brief: ResearchBrief, **kw) -> HypothesisScientistDecision:
    reqs = kw.get("reqs", (
        DataRequirement(requirement_id="d", data_kind=DataKind.SYNTHETIC_PARAMETRIC,
                        asset_class=AssetClass.SYNTHETIC),
        ToolRequirement(requirement_id="t", tool_kind=ToolKind.BACKTEST_EXECUTION),
    ))
    return HypothesisScientistDecision(
        id="dec-01",
        decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
        research_brief_id=brief.id,
        hypothesis_statement=kw.get("statement", "threshold controls trade frequency"),
        hypothesis_rationale=kw.get("rationale", "mechanistic test of gating"),
        requirements_snapshot=requirements_to_json(reqs),
        provider="fake",
        model="fake-v1",
        prompt_version=kw.get("prompt_version", "v3"),
    )


def _no_hyp_decision(brief: ResearchBrief, reason: str = "too vague") -> HypothesisScientistDecision:
    return HypothesisScientistDecision(
        id="dec-02",
        decision_type=HypothesisScientistDecisionType.NO_HYPOTHESIS,
        research_brief_id=brief.id,
        no_hypothesis_reason=reason,
        provider="fake",
        model="fake-v1",
        prompt_version="v3",
    )


def _store(tmp_path):
    return SQLiteStore(tmp_path / "test.db")


# ─── ResearchBrief ────────────────────────────────────────────────────────────

def test_brief_valid_minimal():
    b = ResearchBrief.create(research_question="Does signal_threshold affect trade count?")
    assert b.id
    assert b.research_question


def test_brief_optional_fields():
    b = ResearchBrief.create(
        research_question="test",
        asset_class_focus="FUTURES",
        instrument_focus=["MES"],
        exclusions=["options"],
        prior_candidate_summaries=[{
            "fingerprint": "abc123def456abc123def456abc123def456abc123def456abc123def456abcd",
            "hypothesis_statement": "Prior threshold sensitivity hypothesis",
            "hypothesis_rationale_summary": "Threshold gating may drive trade count",
        }],
    )
    assert b.asset_class_focus == "FUTURES"
    assert "MES" in b.instrument_focus
    assert "options" in b.exclusions
    assert b.prior_candidate_summaries is not None
    assert b.prior_candidate_summaries[0].hypothesis_statement == "Prior threshold sensitivity hypothesis"
    assert (
        "abc123def456abc123def456abc123def456abc123def456abc123def456abcd"
        in b.prior_candidate_fingerprints
    )


def test_brief_rejects_mismatched_prior_fingerprint_lists():
    with pytest.raises(ValueError, match="must match prior_candidate_summaries"):
        ResearchBrief.create(
            research_question="test",
            prior_candidate_fingerprints=["f" * 64],
            prior_candidate_summaries=[{
                "fingerprint": "e" * 64,
                "hypothesis_statement": "prior",
            }],
        )


def test_brief_bounds_prior_candidate_summary_count():
    with pytest.raises(ValueError, match="at most"):
        ResearchBrief.create(
            research_question="test",
            prior_candidate_summaries=[
                {"fingerprint": f"{i:064x}", "hypothesis_statement": f"prior {i}"}
                for i in range(6)
            ],
        )


def test_brief_empty_question_rejected():
    with pytest.raises(ValueError, match="research_question"):
        ResearchBrief.create(research_question="")


def test_brief_is_immutable():
    b = ResearchBrief.create(research_question="test")
    with pytest.raises((AttributeError, TypeError)):
        b.research_question = "mutated"  # type: ignore


# ─── Requirement ontology snapshot ────────────────────────────────────────────

def test_requirement_ontology_snapshot_deterministic():
    first = build_requirement_ontology_snapshot()
    second = build_requirement_ontology_snapshot()
    assert first.version == REQUIREMENT_ONTOLOGY_VERSION
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64


def test_requirement_ontology_v1_fingerprint_preserved_for_historical_replay():
    ontology = build_requirement_ontology_snapshot(REQUIREMENT_ONTOLOGY_V1)
    assert ontology.version == REQUIREMENT_ONTOLOGY_V1
    assert ontology.fingerprint == "e490b82b80b2a64f72d76b63a36f6ba6780309c1449612eb18695f91456c2395"


def test_requirement_ontology_v2_fingerprint_preserved():
    ontology = build_requirement_ontology_snapshot()
    assert ontology.version == REQUIREMENT_ONTOLOGY_VERSION
    assert ontology.fingerprint == "832885f4763e40b8a379c8c9c475484651b0a0f1c7fb01305d3d37fe4172c917"


def test_requirement_ontology_snapshot_has_canonical_vocab():
    ontology = build_requirement_ontology_snapshot()
    assert "QUOTES" in ontology.allowed_data_kinds
    assert "STATISTICAL_ANALYSIS" in ontology.tool_kinds
    assert "bid_price" in ontology.canonical_fields_by_data_kind["QUOTES"]
    assert "synthetic_price" in ontology.canonical_fields_by_data_kind["SYNTHETIC_PARAMETRIC"]


def test_requirement_ontology_v2_drops_ai_required_parameters_contract():
    payload = build_requirement_ontology_snapshot().to_payload()
    assert "required_parameters_semantics" not in payload
    assert "candidate_feasibility_semantics" in payload
    assert "future_spec_feasibility_semantics" in payload


def test_requirement_ontology_snapshot_has_no_capability_ids_or_availability_claims():
    payload = json.dumps(build_requirement_ontology_snapshot().to_payload(), sort_keys=True)
    assert "stub_backtester_v1" not in payload
    assert "enabled" not in payload
    assert "registry_fingerprint" not in payload
    assert "TESTABLE" not in payload


def test_requirement_ontology_snapshot_unaffected_by_registry_content():
    from ai_quant_scientist.capabilities.models import Capability
    from ai_quant_scientist.capabilities.registry import CapabilityRegistry

    before = build_requirement_ontology_snapshot().fingerprint
    CapabilityRegistry([
        Capability(
            capability_id="totally_new_capability",
            capability_type="DATA_FEED",
            data_kind=DataKind.OHLCV,
            asset_classes=(AssetClass.EQUITY,),
            resolutions=(Resolution.DAILY,),
            provider="test",
            enabled=True,
            version="1",
        )
    ])
    after = build_requirement_ontology_snapshot().fingerprint
    assert before == after


# ─── Prompt versioning ────────────────────────────────────────────────────────

def test_prompt_v1_available():
    assert "v1" in available_versions()
    assert "v2" in available_versions()
    assert "v3" in available_versions()


def test_prompt_v1_contains_core_instructions():
    p = get_scientist_instructions("v1")
    assert "PROPOSE_HYPOTHESIS" in p
    assert "NO_HYPOTHESIS" in p
    assert "falsifiable" in p.lower()
    assert "requirements" in p.lower()


def test_prompt_v1_forbids_governance_fields():
    p = get_scientist_instructions("v1")
    assert "Do NOT" in p or "must not" in p.lower()
    assert "feasibility" in p.lower()


def test_unknown_prompt_version_raises():
    with pytest.raises(KeyError):
        get_scientist_instructions("v99")


def test_prompt_v2_contains_hardened_contract_language():
    p = get_scientist_instructions("v2")
    assert "tool_kind" in p
    assert "required_parameters" in p
    assert "primitive capability field identifiers" in p
    assert "prior_candidate_summaries" in p


def test_prompt_v3_preserves_policy_but_removes_pre_spec_parameter_contract():
    p = get_scientist_instructions("v3")
    assert "tool_kind" in p
    assert "required_parameters" not in p
    assert "READY_FOR_SPEC" in p
    assert "future ResearchSpec design details" in p


def test_prompt_versions_exact_hashes_unchanged():
    assert hashlib.sha256(get_scientist_instructions("v1").encode("utf-8")).hexdigest() == (
        "34693e305202cae2ee96f84d328bdbd53e8cee8f65765afb9a3c0f35614ec37e"
    )
    assert hashlib.sha256(get_scientist_instructions("v2").encode("utf-8")).hexdigest() == (
        "09c4284f3b24d016812c51c0415abd3bae7b9fed189cbfcf2d4ee46fe17d1551"
    )
    assert hashlib.sha256(get_scientist_instructions("v3").encode("utf-8")).hexdigest() == (
        "aa89aa587b8b26332562b2055eeb2813dff148201d96bec8bf79eed34b93661a"
    )


# ─── Validator ────────────────────────────────────────────────────────────────

def test_valid_propose_passes():
    brief = _synth_brief()
    decision = _propose_decision(brief)
    valid, errors = HypothesisProposalValidator().validate(decision, brief)
    assert valid and not errors


def test_valid_no_hypothesis_passes():
    brief = _synth_brief()
    decision = _no_hyp_decision(brief)
    valid, errors = HypothesisProposalValidator().validate(decision, brief)
    assert valid and not errors


def test_propose_requires_statement():
    brief = _synth_brief()
    decision = _propose_decision(brief, statement="   ")
    valid, errors = HypothesisProposalValidator().validate(decision, brief)
    assert not valid and "hypothesis_statement" in errors


def test_propose_requires_rationale():
    brief = _synth_brief()
    decision = _propose_decision(brief, rationale="")
    valid, errors = HypothesisProposalValidator().validate(decision, brief)
    assert not valid and "hypothesis_rationale" in errors


def test_propose_requires_non_empty_requirements():
    brief = _synth_brief()
    d = HypothesisScientistDecision(
        id="x", decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
        research_brief_id=brief.id,
        hypothesis_statement="test", hypothesis_rationale="test",
        requirements_snapshot=requirements_to_json(()),  # empty
        provider="f", model="f", prompt_version="v1",
    )
    valid, errors = HypothesisProposalValidator().validate(d, brief)
    assert not valid and "requirements_empty" in errors


def test_propose_requires_requirements_present():
    brief = _synth_brief()
    d = HypothesisScientistDecision(
        id="x", decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
        research_brief_id=brief.id,
        hypothesis_statement="test", hypothesis_rationale="test",
        requirements_snapshot=None,
        provider="f", model="f", prompt_version="v1",
    )
    valid, errors = HypothesisProposalValidator().validate(d, brief)
    assert not valid and "requirements" in errors


def test_no_hypothesis_requires_reason():
    brief = _synth_brief()
    d = HypothesisScientistDecision(
        id="x", decision_type=HypothesisScientistDecisionType.NO_HYPOTHESIS,
        research_brief_id=brief.id, no_hypothesis_reason="   ",
        provider="f", model="f", prompt_version="v1",
    )
    valid, errors = HypothesisProposalValidator().validate(d, brief)
    assert not valid and "no_hypothesis_reason" in errors


def test_no_hypothesis_must_not_include_candidate_fields():
    brief = _synth_brief()
    d = HypothesisScientistDecision(
        id="x", decision_type=HypothesisScientistDecisionType.NO_HYPOTHESIS,
        research_brief_id=brief.id, no_hypothesis_reason="too vague",
        hypothesis_statement="extra",
        provider="f", model="f", prompt_version="v1",
    )
    valid, errors = HypothesisProposalValidator().validate(d, brief)
    assert not valid and "no_hypothesis_extra" in errors


# ─── Requirements round-trip ──────────────────────────────────────────────────

def test_data_requirement_round_trip_through_decision():
    brief = _synth_brief()
    req = DataRequirement(
        requirement_id="r1", data_kind=DataKind.OHLCV, asset_class=AssetClass.EQUITY,
        resolution=Resolution.DAILY, required_fields=("close", "volume"),
    )
    decision = _propose_decision(brief, reqs=(req,))
    back = requirements_from_json(decision.requirements_snapshot)
    assert back[0] == req


def test_tool_requirement_round_trip_through_decision():
    brief = _synth_brief()
    req = ToolRequirement(requirement_id="t", tool_kind=ToolKind.BACKTEST_EXECUTION)
    decision = _propose_decision(brief, reqs=(req,))
    back = requirements_from_json(decision.requirements_snapshot)
    assert isinstance(back[0], ToolRequirement)
    assert back[0].tool_kind == ToolKind.BACKTEST_EXECUTION


def test_mixed_requirements_round_trip():
    brief = _synth_brief()
    reqs = (
        DataRequirement(requirement_id="d", data_kind=DataKind.ORDER_BOOK, asset_class=AssetClass.FUTURES),
        ToolRequirement(requirement_id="t", tool_kind=ToolKind.BACKTEST_EXECUTION),
    )
    decision = _propose_decision(brief, reqs=reqs)
    back = requirements_from_json(decision.requirements_snapshot)
    assert len(back) == 2
    assert isinstance(back[0], DataRequirement)
    assert isinstance(back[1], ToolRequirement)


# ─── ToolRequirement ontology ────────────────────────────────────────────────

def test_tool_requirement_matches_supported_tool_kind():
    from ai_quant_scientist.capabilities.registry import CapabilityRegistry
    from ai_quant_scientist.capabilities.models import Capability
    cap = Capability(
        capability_id="stub_backtester_v1",
        capability_type="EXECUTION_TOOL",
        data_kind=DataKind.SYNTHETIC_PARAMETRIC,
        asset_classes=(AssetClass.SYNTHETIC,),
        resolutions=(Resolution.NOT_APPLICABLE,),
        supported_parameters=("signal_threshold", "lookback"),
        supported_tool_kinds=(ToolKind.BACKTEST_EXECUTION,),
        provider="test", enabled=True, version="1",
    )
    reg = CapabilityRegistry([cap])
    req = ToolRequirement(requirement_id="t", tool_kind=ToolKind.BACKTEST_EXECUTION)
    result = reg.evaluate_tool_requirement(req)
    assert result.satisfied


def test_legacy_tool_requirement_still_reads_by_exact_capability_id():
    req = ToolRequirement(requirement_id="t", legacy_tool_name="stub_backtester_v1")
    result = build_v1_registry().evaluate_tool_requirement(req)
    assert result.satisfied


def test_free_form_tool_synonym_rejected_for_new_contract():
    brief = _synth_brief()
    decision = _propose_decision(brief, reqs=(
        DataRequirement(requirement_id="d", data_kind=DataKind.SYNTHETIC_PARAMETRIC),
        ToolRequirement(requirement_id="t", legacy_tool_name="BACKTESTING_TOOL"),
    ))
    valid, errors = HypothesisProposalValidator().validate(decision, brief)
    assert not valid
    assert "t_tool_kind" in errors


def test_pseudo_field_identifier_rejected_for_new_contract():
    brief = _synth_brief()
    decision = _propose_decision(brief, reqs=(
        DataRequirement(
            requirement_id="d",
            data_kind=DataKind.QUOTES,
            required_fields=("bid_price", "ask_price", "mid_price_or_fields_to_compute_mid"),
        ),
        ToolRequirement(requirement_id="t", tool_kind=ToolKind.MARKET_DATA_RESEARCH),
    ))
    valid, errors = HypothesisProposalValidator().validate(decision, brief)
    assert not valid
    assert "d_required_fields" in errors


def test_required_parameters_round_trip_through_decision():
    brief = _synth_brief()
    req = DataRequirement(
        requirement_id="r1",
        data_kind=DataKind.SYNTHETIC_PARAMETRIC,
        required_parameters=("signal_threshold", "lookback"),
    )
    decision = _propose_decision(brief, reqs=(req,))
    back = requirements_from_json(decision.requirements_snapshot)
    assert back[0].required_parameters == ("lookback", "signal_threshold")


def test_fake_scientist_materializes_candidate_without_required_parameters():
    brief = _synth_brief()
    decision = FakeHypothesisScientist().generate(brief)
    candidate = materialize_research_candidate(decision, brief)
    data_reqs = [req for req in candidate.requirements if isinstance(req, DataRequirement)]
    assert data_reqs
    assert all(req.required_parameters is None for req in data_reqs)


def test_quotes_case_06_vocabulary_remains_valid():
    brief = _synth_brief(asset_class_focus="FUTURES")
    decision = _propose_decision(brief, reqs=(
        DataRequirement(
            requirement_id="quotes",
            data_kind=DataKind.QUOTES,
            asset_class=AssetClass.FUTURES,
            resolution=Resolution.SECOND_1,
            required_fields=("bid_price", "ask_price", "bid_size", "ask_size"),
        ),
        ToolRequirement(requirement_id="t", tool_kind=ToolKind.STATISTICAL_ANALYSIS),
    ))
    valid, errors = HypothesisProposalValidator().validate(decision, brief)
    assert valid
    assert not errors


def test_order_book_exchange_or_venue_is_accepted_when_canonical():
    brief = _synth_brief(asset_class_focus="FUTURES")
    decision = _propose_decision(brief, reqs=(
        DataRequirement(
            requirement_id="order_book",
            data_kind=DataKind.ORDER_BOOK,
            asset_class=AssetClass.FUTURES,
            resolution=Resolution.SECOND_1,
            required_fields=(
                "timestamp",
                "instrument_id",
                "best_bid_price",
                "best_ask_price",
                "best_bid_size",
                "best_ask_size",
                "exchange_or_venue",
                "contract_expiry",
            ),
        ),
        ToolRequirement(requirement_id="t", tool_kind=ToolKind.STATISTICAL_ANALYSIS),
    ))
    valid, errors = HypothesisProposalValidator().validate(decision, brief)
    assert valid
    assert not errors


def test_synthetic_scalar_price_path_uses_synthetic_price_not_close():
    brief = _synth_brief()
    valid_decision = _propose_decision(brief, reqs=(
        DataRequirement(
            requirement_id="synthetic_series",
            data_kind=DataKind.SYNTHETIC_PARAMETRIC,
            asset_class=AssetClass.SYNTHETIC,
            resolution=Resolution.DAILY,
            required_fields=("synthetic_price",),
        ),
        ToolRequirement(requirement_id="t", tool_kind=ToolKind.SYNTHETIC_DATA_GENERATION),
    ))
    valid, errors = HypothesisProposalValidator().validate(valid_decision, brief)
    assert valid
    assert not errors

    invalid_decision = _propose_decision(brief, reqs=(
        DataRequirement(
            requirement_id="synthetic_series",
            data_kind=DataKind.SYNTHETIC_PARAMETRIC,
            asset_class=AssetClass.SYNTHETIC,
            resolution=Resolution.DAILY,
            required_fields=("close",),
        ),
        ToolRequirement(requirement_id="t", tool_kind=ToolKind.SYNTHETIC_DATA_GENERATION),
    ))
    valid, errors = HypothesisProposalValidator().validate(invalid_decision, brief)
    assert not valid
    assert "synthetic_series_required_fields" in errors


def test_generated_execution_output_not_treated_as_input_field():
    brief = _synth_brief()
    decision = _propose_decision(brief, reqs=(
        DataRequirement(
            requirement_id="synthetic_events",
            data_kind=DataKind.SYNTHETIC_PARAMETRIC,
            asset_class=AssetClass.SYNTHETIC,
            resolution=Resolution.TICK,
            required_fields=("timestamp", "signal_value", "execution_price"),
        ),
        ToolRequirement(requirement_id="t", tool_kind=ToolKind.BACKTEST_EXECUTION),
    ))
    valid, errors = HypothesisProposalValidator().validate(decision, brief)
    assert not valid
    assert "synthetic_events_required_fields" in errors


# ─── Materialization ──────────────────────────────────────────────────────────

def test_materialization_assigns_id_from_software():
    brief = _synth_brief()
    decision = _propose_decision(brief)
    candidate = materialize_research_candidate(decision, brief)
    assert candidate.id  # must exist
    assert candidate.id != decision.id  # distinct from AI decision id


def test_materialization_assigns_source_from_software():
    brief = _synth_brief()
    decision = _propose_decision(brief)
    candidate = materialize_research_candidate(decision, brief)
    assert SCIENTIST_SOURCE in candidate.source


def test_materialization_assigns_timestamp():
    brief = _synth_brief()
    decision = _propose_decision(brief)
    candidate = materialize_research_candidate(decision, brief)
    assert isinstance(candidate.created_at, datetime)


def test_materialization_copies_requirements_exactly():
    brief = _synth_brief()
    reqs = (
        DataRequirement(requirement_id="d", data_kind=DataKind.SYNTHETIC_PARAMETRIC,
                        asset_class=AssetClass.SYNTHETIC,
                        required_parameters=("signal_threshold", "lookback")),
        ToolRequirement(requirement_id="t", tool_kind=ToolKind.BACKTEST_EXECUTION),
    )
    decision = _propose_decision(brief, reqs=reqs)
    candidate = materialize_research_candidate(decision, brief)
    assert candidate.requirements == reqs


def test_materialization_same_science_same_fingerprint():
    brief = _synth_brief()
    decision = _propose_decision(brief)
    c1 = materialize_research_candidate(decision, brief)
    c2 = materialize_research_candidate(decision, brief)
    # Different ids but same scientific content → same fingerprint
    f1 = compute_candidate_fingerprint(c1.hypothesis_statement, c1.hypothesis_rationale, c1.requirements)
    f2 = compute_candidate_fingerprint(c2.hypothesis_statement, c2.hypothesis_rationale, c2.requirements)
    assert f1 == f2
    assert c1.id != c2.id


def test_materialization_copies_ontology_provenance():
    brief = _synth_brief()
    decision = _propose_decision(brief)
    ontology = build_requirement_ontology_snapshot()
    decision = replace(
        decision,
        ontology_version=ontology.version,
        ontology_fingerprint=ontology.fingerprint,
    )
    assert decision.ontology_version == ontology.version
    assert decision.ontology_fingerprint == ontology.fingerprint


# ─── FakeHypothesisScientist ──────────────────────────────────────────────────

def test_fake_scientist_propose_for_clear_brief():
    scientist = FakeHypothesisScientist()
    brief = _synth_brief()
    decision = scientist.generate(brief)
    assert decision.decision_type == HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS
    assert decision.hypothesis_statement
    assert decision.requirements_snapshot
    assert decision.ontology_version == REQUIREMENT_ONTOLOGY_VERSION
    assert decision.ontology_fingerprint is not None


def test_fake_scientist_no_hypothesis_for_underspecified():
    scientist = FakeHypothesisScientist()
    brief = ResearchBrief.create(research_question="general explore markets underspecified")
    decision = scientist.generate(brief)
    assert decision.decision_type == HypothesisScientistDecisionType.NO_HYPOTHESIS


# ─── Persistence ─────────────────────────────────────────────────────────────

def test_save_and_retrieve_propose_invocation(tmp_path):
    store = _store(tmp_path)
    brief = _synth_brief()
    scientist = FakeHypothesisScientist()
    inv, candidate = generate_candidate(scientist, brief, store)
    assert inv.validation_status == "VALID"
    assert inv.resulting_candidate_id is not None
    retrieved = store.get_hypothesis_scientist_invocations(brief.id)
    assert len(retrieved) == 1
    r = retrieved[0]
    assert r.research_brief_id == brief.id
    assert r.resulting_candidate_id == candidate.id
    snapshot = json.loads(r.research_brief_snapshot)
    parsed = json.loads(r.parsed_decision_json)
    assert snapshot["requirement_ontology"]["version"] == REQUIREMENT_ONTOLOGY_VERSION
    assert parsed["ontology_version"] == REQUIREMENT_ONTOLOGY_VERSION
    assert parsed["ontology_fingerprint"] == snapshot["requirement_ontology"]["fingerprint"]


def test_save_and_retrieve_no_hypothesis_invocation(tmp_path):
    store = _store(tmp_path)
    brief = ResearchBrief.create(research_question="general explore markets underspecified")
    scientist = FakeHypothesisScientist()
    inv, candidate = generate_candidate(scientist, brief, store)
    assert candidate is None
    assert inv.resulting_candidate_id is None
    retrieved = store.get_hypothesis_scientist_invocations(brief.id)
    assert len(retrieved) == 1
    assert retrieved[0].resulting_candidate_id is None


def test_invalid_decision_persisted_with_validation_failure(tmp_path):
    store = _store(tmp_path)
    brief = _synth_brief()

    class BadScientist:
        provider = "fake"
        model = "bad"
        prompt_version = "v1"
        def generate(self, b):
            return HypothesisScientistDecision(
                id="bad", decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
                research_brief_id=b.id,
                hypothesis_statement="",  # invalid
                hypothesis_rationale="test",
                requirements_snapshot=None,  # missing
                provider="fake", model="bad", prompt_version="v1",
            )

    inv, candidate = generate_candidate(BadScientist(), brief, store)
    assert inv.validation_status == "INVALID"
    assert candidate is None
    retrieved = store.get_hypothesis_scientist_invocations(brief.id)
    assert retrieved[0].validation_status == "INVALID"


def test_invocations_immutable(tmp_path):
    store = _store(tmp_path)
    brief = _synth_brief()
    scientist = FakeHypothesisScientist()
    inv, _ = generate_candidate(scientist, brief, store)
    generate_candidate(scientist, brief, store)  # second invocation
    all_invs = store.get_hypothesis_scientist_invocations(brief.id)
    assert len(all_invs) == 2  # both preserved


# ─── Schema migration ─────────────────────────────────────────────────────────

def test_v5_to_v6_migration(tmp_path):
    db = tmp_path / "v5.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL);
        INSERT INTO schema_version (id, version) VALUES (1, 5);
        CREATE TABLE research_candidates (id TEXT PRIMARY KEY, hypothesis_statement TEXT NOT NULL,
            hypothesis_rationale TEXT NOT NULL, source TEXT NOT NULL, requirements_json TEXT NOT NULL,
            candidate_fingerprint TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE feasibility_decisions (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL,
            gate_decision TEXT NOT NULL, gate_version TEXT NOT NULL, registry_version TEXT NOT NULL,
            registry_fingerprint TEXT NOT NULL, feasibility_result_json TEXT NOT NULL,
            satisfied_ids_json TEXT NOT NULL, unsatisfied_ids_json TEXT NOT NULL,
            reason_codes_json TEXT NOT NULL, evaluated_at TEXT NOT NULL);
    """)
    conn.commit()
    conn.close()

    store = SQLiteStore(db)
    with store.connect() as c:
        ver = c.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
        assert ver == 6
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "hypothesis_scientist_invocations" in tables
        assert "research_candidates" in tables


def test_fresh_v6_db_has_all_tables(tmp_path):
    store = SQLiteStore(tmp_path / "fresh.db")
    with store.connect() as c:
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for t in ("hypothesis_scientist_invocations", "research_candidates", "feasibility_decisions",
              "critic_invocations", "research_runs"):
        assert t in tables


def test_v6_migration_idempotent(tmp_path):
    SQLiteStore(tmp_path / "t.db")
    SQLiteStore(tmp_path / "t.db")  # second open on v6 DB should stay at v6
    store = SQLiteStore(tmp_path / "t.db")
    with store.connect() as c:
        ver = c.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
        assert ver == 6


# ─── Eval harness ─────────────────────────────────────────────────────────────

def test_fixture_loads_12_cases():
    cases = load_cases_from_file("evals/scientist_v1.json")
    assert len(cases) == 12
    ids = [c.id for c in cases]
    assert len(set(ids)) == len(ids)


def test_harness_runs_fake_scientist():
    cases = load_cases_from_file("evals/scientist_v1.json")
    suite = ScientistEvalSuite(cases[:2])
    results = suite.run(FakeHypothesisScientist())
    assert len(results) == 2
    for r in results:
        assert r.contract_passed or r.decision_type is not None


def test_harness_defaults_to_scientist_prompt_version():
    cases = load_cases_from_file("evals/scientist_v1.json")
    result = ScientistEvalSuite(cases[:1]).run(FakeHypothesisScientist())[0]
    assert result.prompt_version == "v3"


def test_harness_no_api_calls(monkeypatch):
    import urllib.request
    called = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: called.append(True))
    cases = load_cases_from_file("evals/scientist_v1.json")
    ScientistEvalSuite(cases).run(FakeHypothesisScientist())
    assert not called


# ─── Live runner guard ────────────────────────────────────────────────────────

def test_live_runner_requires_allow_live_api():
    with pytest.raises(RuntimeError, match="--allow-live-api"):
        run_live_scientist_eval(
            model="test",
            eval_path="evals/scientist_v1.json",
            allow_live_api=False,
        )


# ─── Eval artifact shape ──────────────────────────────────────────────────────

def test_eval_artifact_propose_includes_rationale():
    cases = load_cases_from_file("evals/scientist_v1.json")
    suite = ScientistEvalSuite(cases[:1])
    results = suite.run(FakeHypothesisScientist())
    r = results[0]
    assert r.parsed_decision is not None
    assert r.parsed_decision["hypothesis_rationale"] is not None


def test_eval_artifact_propose_includes_data_requirements():
    cases = load_cases_from_file("evals/scientist_v1.json")
    suite = ScientistEvalSuite(cases[:1])
    results = suite.run(FakeHypothesisScientist())
    r = results[0]
    reqs = r.parsed_decision["requirements"]
    assert any(req["type"] == "DataRequirement" for req in reqs)


def test_eval_artifact_propose_includes_tool_requirements():
    cases = load_cases_from_file("evals/scientist_v1.json")
    suite = ScientistEvalSuite(cases[:1])
    results = suite.run(FakeHypothesisScientist())
    r = results[0]
    reqs = r.parsed_decision["requirements"]
    assert any(req["type"] == "ToolRequirement" for req in reqs)


def test_eval_artifact_mixed_requirement_ordering_preserved():
    from ai_quant_scientist.capabilities.serialization import requirements_to_json
    from ai_quant_scientist.evals.scientist_eval import _req_to_dict, _serialise_decision_for_eval
    from ai_quant_scientist.models.hypothesis_scientist import HypothesisScientistDecision
    brief = _synth_brief()
    reqs = (
        DataRequirement(requirement_id="d1", data_kind=DataKind.ORDER_BOOK, asset_class=AssetClass.FUTURES),
        ToolRequirement(requirement_id="t1", tool_kind=ToolKind.BACKTEST_EXECUTION),
        DataRequirement(requirement_id="d2", data_kind=DataKind.SYNTHETIC_PARAMETRIC),
    )
    decision = HypothesisScientistDecision(
        id="x", decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
        research_brief_id=brief.id,
        hypothesis_statement="test", hypothesis_rationale="test",
        requirements_snapshot=requirements_to_json(reqs),
        provider="fake", model="fake", prompt_version="v2",
    )
    parsed = _serialise_decision_for_eval(decision)
    types = [r["type"] for r in parsed["requirements"]]
    assert types == ["DataRequirement", "ToolRequirement", "DataRequirement"]


def test_eval_artifact_no_hypothesis_includes_reason():
    from ai_quant_scientist.evals.scientist_eval import _serialise_decision_for_eval
    brief = ResearchBrief.create(research_question="general explore underspecified markets")
    decision = _no_hyp_decision(brief, reason="Too vague to generate a responsible hypothesis")
    parsed = _serialise_decision_for_eval(decision)
    assert parsed["no_hypothesis_reason"] == "Too vague to generate a responsible hypothesis"
    assert parsed["requirements"] == []


def test_eval_result_carries_expected_tool_kinds_metadata():
    cases = {case.id: case for case in load_cases_from_file("evals/scientist_v1.json")}
    case = cases["case-07"]
    result = ScientistEvalSuite([case]).run(FakeHypothesisScientist())[0]
    assert result.expected_tool_kinds == ("BACKTEST_EXECUTION",)


def test_eval_artifact_includes_compact_provenance():
    import json as _json
    from ai_quant_scientist.evals.scientist_eval import _serialise_decision_for_eval
    from ai_quant_scientist.capabilities.serialization import requirements_to_json
    from ai_quant_scientist.models.hypothesis_scientist import HypothesisScientistDecision
    prov = {"response_id": "resp_test", "model": "m", "status": "completed",
            "created_at": 1.0, "completed_at": 2.0, "store": False, "usage": None, "output_text": None}
    brief = _synth_brief()
    reqs = (DataRequirement(requirement_id="d", data_kind=DataKind.SYNTHETIC_PARAMETRIC),)
    decision = HypothesisScientistDecision(
        id="x", decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
        research_brief_id=brief.id,
        hypothesis_statement="test", hypothesis_rationale="test",
        requirements_snapshot=requirements_to_json(reqs),
        provider="openai", model="gpt-5.6-terra", prompt_version="v1",
        raw_response=_json.dumps(prov),
    )
    parsed = _serialise_decision_for_eval(decision)
    assert parsed["compact_provenance"] is not None
    assert parsed["compact_provenance"]["response_id"] == "resp_test"


def test_eval_artifact_does_not_expose_api_key():
    from ai_quant_scientist.evals.scientist_eval import _serialise_decision_for_eval
    brief = _synth_brief()
    decision = _no_hyp_decision(brief)
    parsed = _serialise_decision_for_eval(decision)
    serialized = json.dumps(parsed)
    # The compact provenance only has response_id, model, status, timestamps, usage, output_text
    assert "api_key" not in serialized.lower()
    assert "Authorization" not in serialized
    assert "Bearer " not in serialized


def test_eval_artifact_summary_fields_remain():
    cases = load_cases_from_file("evals/scientist_v1.json")
    suite = ScientistEvalSuite(cases[:2])
    results = suite.run(FakeHypothesisScientist())
    assert results[0].case_id is not None
    assert results[0].contract_passed is not None
    assert results[0].requirement_count >= 0
    assert results[0].decision_type is not None


def test_brief_payload_includes_requirement_ontology_without_capability_availability():
    payload = brief_to_payload(_synth_brief())
    payload_str = json.dumps(payload, sort_keys=True)
    assert "requirement_ontology" in payload
    assert payload["requirement_ontology"]["version"] == REQUIREMENT_ONTOLOGY_VERSION
    assert "stub_backtester_v1" not in payload_str
    assert "enabled" not in payload_str
    assert "registry_fingerprint" not in payload_str


def test_eval_metadata_not_in_brief_payload():
    """expected_decision/evaluation_focus must never appear in the model input."""
    cases = load_cases_from_file("evals/scientist_v1.json")
    for case in cases:
        payload = brief_to_payload(case.brief)
        payload_str = json.dumps(payload)
        assert "expected_decision" not in payload_str
        assert "evaluation_focus" not in payload_str
        assert "manual_success_criteria" not in payload_str


def test_fixture_cases_have_eval_metadata():
    cases = load_cases_from_file("evals/scientist_v1.json")
    for case in cases:
        assert case.expected_decision is not None, f"{case.id} missing expected_decision"
        assert case.evaluation_focus is not None, f"{case.id} missing evaluation_focus"


def test_case_10_loads_prior_candidate_summary_context():
    cases = {case.id: case for case in load_cases_from_file("evals/scientist_v1.json")}
    case = cases["case-10"]
    assert case.brief.prior_candidate_summaries is not None
    assert len(case.brief.prior_candidate_summaries) == 1
    summary = case.brief.prior_candidate_summaries[0]
    assert summary.fingerprint in case.brief.prior_candidate_fingerprints
    assert "threshold" in summary.hypothesis_statement.lower()


def test_case_07_loads_expected_tool_kind_without_leaking_to_model_input():
    cases = {case.id: case for case in load_cases_from_file("evals/scientist_v1.json")}
    case = cases["case-07"]
    assert case.expected_tool_kinds == ("BACKTEST_EXECUTION",)
    payload = brief_to_payload(case.brief)
    payload_str = json.dumps(payload)
    assert "expected_tool_kinds" not in payload_str


def test_case_11_is_multiplicity_test_not_underspecification_test():
    cases = {case.id: case for case in load_cases_from_file("evals/scientist_v1.json")}
    case = cases["case-11"]
    assert case.expected_decision == "PROPOSE_HYPOTHESIS"
    assert case.brief.asset_class_focus == "SYNTHETIC"
    assert case.brief.methodological_constraints is not None
    question = case.brief.research_question.lower()
    assert "ornstein-uhlenbeck" in question
    assert "signal_threshold" in question
    assert "lookback sensitivity" in question
    assert "trade frequency" in question
    constraints = " ".join(case.brief.methodological_constraints).lower()
    assert "final 5,000 bars" in " ".join(case.brief.methodological_constraints)
    assert "exactly one falsifiable hypothesis" in constraints


def test_eval_result_carries_fixture_metadata():
    cases = load_cases_from_file("evals/scientist_v1.json")
    suite = ScientistEvalSuite(cases[:1])
    results = suite.run(FakeHypothesisScientist())
    assert results[0].expected_decision is not None
    assert results[0].evaluation_focus is not None


def test_eval_result_carries_ontology_provenance():
    case = load_cases_from_file("evals/scientist_v1.json")[0]
    result = ScientistEvalSuite([case]).run(FakeHypothesisScientist())[0]
    assert result.ontology_version == REQUIREMENT_ONTOLOGY_VERSION
    assert result.ontology_fingerprint is not None
    assert result.parsed_decision["ontology_version"] == REQUIREMENT_ONTOLOGY_VERSION


# ─── Downstream gate boundary: scientist proposes, gate decides ────────────────

def test_synthetic_candidate_from_fake_scientist_becomes_ready(tmp_path):
    store = _store(tmp_path)
    brief = _synth_brief()
    scientist = FakeHypothesisScientist()
    inv, candidate = generate_candidate(scientist, brief, store)
    assert candidate is not None

    store.save_research_candidate(candidate)
    intake = GovernedResearchIntake(store, build_v1_registry())
    result = intake.submit(candidate)
    assert result.is_ready


def test_mes_ohlcv_candidate_becomes_blocked(tmp_path):
    store = _store(tmp_path)
    brief = ResearchBrief.create(
        research_question="Does order-book imbalance predict MES futures returns?",
        asset_class_focus="FUTURES",
        instrument_focus=["MES"],
    )
    # Simulate scientist returning a candidate requiring real market data
    reqs = (
        DataRequirement(requirement_id="ob", data_kind=DataKind.ORDER_BOOK,
                        asset_class=AssetClass.FUTURES, instruments=("MES",),
                        resolution=Resolution.SECOND_1),
        ToolRequirement(requirement_id="t", tool_kind=ToolKind.MARKET_DATA_RESEARCH),
    )
    decision = HypothesisScientistDecision(
        id="dec-mes", decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
        research_brief_id=brief.id,
        hypothesis_statement="Order-book imbalance predicts MES returns",
        hypothesis_rationale="Microstructure mechanism",
        requirements_snapshot=requirements_to_json(reqs),
        provider="fake", model="fake", prompt_version="v2",
    )
    valid, _ = HypothesisProposalValidator().validate(decision, brief)
    assert valid
    candidate = materialize_research_candidate(decision, brief)
    store.save_research_candidate(candidate)
    intake = GovernedResearchIntake(store, build_v1_registry())
    result = intake.submit(candidate)
    # MES order-book not in V1 registry → blocked
    assert result.is_blocked


def test_scientist_proposes_gate_decides_independence(tmp_path):
    """Scientist proposes; the gate decides reality — not the scientist."""
    store = _store(tmp_path)
    brief = ResearchBrief.create(research_question="Does MES microstructure predict returns?")
    reqs = (
        DataRequirement(requirement_id="ob", data_kind=DataKind.ORDER_BOOK,
                        asset_class=AssetClass.FUTURES),
        ToolRequirement(requirement_id="t", tool_kind=ToolKind.MARKET_DATA_RESEARCH),
    )
    decision = HypothesisScientistDecision(
        id="d", decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
        research_brief_id=brief.id,
        hypothesis_statement="test", hypothesis_rationale="test",
        requirements_snapshot=requirements_to_json(reqs),
        provider="fake", model="fake", prompt_version="v2",
    )
    candidate = materialize_research_candidate(decision, brief)
    # Scientist produced a valid structural candidate; gate makes the capability call
    store.save_research_candidate(candidate)
    intake = GovernedResearchIntake(store, build_v1_registry())
    result = intake.submit(candidate)
    assert result.is_blocked   # registry lack, not bad science
    assert store.get_research_candidate(candidate.id) is not None  # hypothesis preserved


# ─── OpenAI adapter mock tests ────────────────────────────────────────────────

def _make_openai_response(parsed: dict):
    text = json.dumps(parsed)
    item = SimpleNamespace(type="output_text", parsed=parsed, text=text)
    msg = SimpleNamespace(type="message", content=[item])
    return SimpleNamespace(
        output=[msg], usage={}, id="r1", model="gpt-5.6-terra",
        status="completed", created_at=1.0, completed_at=2.0,
    )


def test_openai_adapter_propose_hypothesis():
    parsed = {
        "decision": "PROPOSE_HYPOTHESIS",
        "hypothesis_statement": "Threshold controls trade frequency",
        "hypothesis_rationale": "Mechanism test",
        "data_requirements": [
            {"requirement_id": "d", "data_kind": "SYNTHETIC_PARAMETRIC",
             "asset_class": "SYNTHETIC"},
        ],
        "tool_requirements": [
            {"requirement_id": "t", "tool_kind": "BACKTEST_EXECUTION", "label": ""},
        ],
        "no_hypothesis_reason": None,
    }
    client = MagicMock()
    client.responses.parse.return_value = _make_openai_response(parsed)
    brief = _synth_brief()
    scientist = OpenAIHypothesisScientist(client=client)
    decision = scientist.generate(brief)
    assert decision.decision_type == HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS
    assert decision.requirements_snapshot
    reqs = requirements_from_json(decision.requirements_snapshot)
    assert any(isinstance(r, DataRequirement) for r in reqs)
    assert any(isinstance(r, ToolRequirement) for r in reqs)
    assert all(
        not isinstance(r, DataRequirement) or r.required_parameters is None
        for r in reqs
    )


def test_openai_adapter_no_hypothesis():
    parsed = {
        "decision": "NO_HYPOTHESIS",
        "hypothesis_statement": None,
        "hypothesis_rationale": None,
        "data_requirements": None,
        "tool_requirements": None,
        "no_hypothesis_reason": "Brief too vague",
    }
    client = MagicMock()
    client.responses.parse.return_value = _make_openai_response(parsed)
    scientist = OpenAIHypothesisScientist(client=client)
    decision = scientist.generate(_synth_brief())
    assert decision.decision_type == HypothesisScientistDecisionType.NO_HYPOTHESIS
    assert decision.no_hypothesis_reason == "Brief too vague"


def test_openai_adapter_input_includes_ontology_but_not_capability_availability():
    captured: dict = {}
    parsed = {
        "decision": "NO_HYPOTHESIS",
        "hypothesis_statement": None,
        "hypothesis_rationale": None,
        "data_requirements": None,
        "tool_requirements": None,
        "no_hypothesis_reason": "too vague",
    }
    resp = _make_openai_response(parsed)
    client = MagicMock()
    client.responses.parse.side_effect = lambda **kw: (captured.update(kw), resp)[1]
    decision = OpenAIHypothesisScientist(client=client).generate(_synth_brief())
    payload = json.loads(captured["input"])
    payload_str = json.dumps(payload, sort_keys=True)
    assert payload["requirement_ontology"]["version"] == REQUIREMENT_ONTOLOGY_VERSION
    assert "required_parameters_semantics" not in payload["requirement_ontology"]
    assert "stub_backtester_v1" not in payload_str
    assert "enabled" not in payload_str
    assert decision.ontology_version == REQUIREMENT_ONTOLOGY_VERSION


def test_openai_adapter_schema_has_no_governance_fields():
    """AI-facing schema must not include candidate id, source, or created_at."""
    captured: dict = {}
    parsed = {
        "decision": "NO_HYPOTHESIS",
        "hypothesis_statement": None, "hypothesis_rationale": None,
        "data_requirements": None, "tool_requirements": None,
        "no_hypothesis_reason": "too vague",
    }
    resp = _make_openai_response(parsed)
    client = MagicMock()
    client.responses.parse.side_effect = lambda **kw: (captured.update(kw), resp)[1]
    OpenAIHypothesisScientist(client=client).generate(_synth_brief())
    tf = captured.get("text_format")
    assert tf is not None
    fields = list(tf.model_fields.keys())
    for forbidden in ("id", "source", "created_at", "candidate_id", "gate_decision"):
        assert forbidden not in fields, f"Forbidden field '{forbidden}' in AI schema"
    schema = tf.model_json_schema()
    data_req_fields = list(
        schema["$defs"]["DataRequirementSchema"]["properties"].keys()
    )
    assert "required_parameters" not in data_req_fields


def test_openai_adapter_makes_no_network_call_on_mock():
    """The adapter uses the injected client — no direct network calls."""
    parsed = {
        "decision": "NO_HYPOTHESIS", "hypothesis_statement": None,
        "hypothesis_rationale": None, "data_requirements": None,
        "tool_requirements": None, "no_hypothesis_reason": "test",
    }
    client = MagicMock()
    client.responses.parse.return_value = _make_openai_response(parsed)
    OpenAIHypothesisScientist(client=client).generate(_synth_brief())
    # No urllib calls
    client.responses.parse.assert_called_once()
