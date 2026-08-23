"""Deterministic AI-safe scientific-claim ontology for the Hypothesis Scientist."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..models.design import DesignOutcome, DesignVariable, ExpectedDirection
from ..models.hypothesis_scientist import HYPOTHESIS_CLAIM_SET_CONTRACT_VERSION, HypothesisClaimAggregation

HYPOTHESIS_CLAIM_ONTOLOGY_VERSION = "hypothesis_claim_ontology_v1"

CLAIM_AUTHORITY_BOUNDARY = (
    "Structured scientific claims are authoritative downstream semantics. Free-form hypothesis prose remains "
    "human-readable narrative and must not add, remove, or override authoritative claim meaning."
)

EXACT_VALUE_BOUNDARY = (
    "Scientific claims must not encode exact execution parameter values, baseline/comparator settings, condition "
    "order, numeric targets, tolerances, significance thresholds, or verdicts."
)

AMBIGUITY_BOUNDARY = (
    "If the scientist cannot responsibly state a directional claim for every material supported outcome, it must "
    "return NO_HYPOTHESIS rather than inventing directions or silently omitting claims."
)


def _payload_without_fingerprint() -> dict[str, object]:
    return {
        "version": HYPOTHESIS_CLAIM_ONTOLOGY_VERSION,
        "claim_contract_version": HYPOTHESIS_CLAIM_SET_CONTRACT_VERSION,
        "supported_independent_variables": [DesignVariable.SIGNAL_THRESHOLD.value],
        "supported_independent_variable_directions": [
            ExpectedDirection.INCREASE.value,
            ExpectedDirection.DECREASE.value,
        ],
        "supported_outcomes": [
            DesignOutcome.TRADE_COUNT.value,
            DesignOutcome.NET_PNL.value,
            DesignOutcome.SHARPE.value,
        ],
        "supported_expected_directions": [
            ExpectedDirection.INCREASE.value,
            ExpectedDirection.DECREASE.value,
        ],
        "supported_aggregation_semantics": [HypothesisClaimAggregation.ALL_CLAIMS_REQUIRED.value],
        "authority_boundary": CLAIM_AUTHORITY_BOUNDARY,
        "exact_value_boundary": EXACT_VALUE_BOUNDARY,
        "ambiguity_boundary": AMBIGUITY_BOUNDARY,
    }


def _compute_fingerprint(payload_without_fingerprint: dict[str, object]) -> str:
    canon = json.dumps(
        payload_without_fingerprint,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class HypothesisClaimOntologySnapshot:
    version: str
    fingerprint: str
    claim_contract_version: str
    supported_independent_variables: tuple[str, ...]
    supported_independent_variable_directions: tuple[str, ...]
    supported_outcomes: tuple[str, ...]
    supported_expected_directions: tuple[str, ...]
    supported_aggregation_semantics: tuple[str, ...]
    authority_boundary: str
    exact_value_boundary: str
    ambiguity_boundary: str

    def to_payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "fingerprint": self.fingerprint,
            "claim_contract_version": self.claim_contract_version,
            "supported_independent_variables": list(self.supported_independent_variables),
            "supported_independent_variable_directions": list(self.supported_independent_variable_directions),
            "supported_outcomes": list(self.supported_outcomes),
            "supported_expected_directions": list(self.supported_expected_directions),
            "supported_aggregation_semantics": list(self.supported_aggregation_semantics),
            "authority_boundary": self.authority_boundary,
            "exact_value_boundary": self.exact_value_boundary,
            "ambiguity_boundary": self.ambiguity_boundary,
        }


def compute_hypothesis_claim_ontology_fingerprint(payload: dict[str, object]) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Hypothesis claim ontology payload must be an object")
    semantic_payload = {key: value for key, value in payload.items() if key != "fingerprint"}
    return _compute_fingerprint(semantic_payload)


def build_hypothesis_claim_ontology_snapshot() -> HypothesisClaimOntologySnapshot:
    payload = _payload_without_fingerprint()
    fingerprint = _compute_fingerprint(payload)
    return HypothesisClaimOntologySnapshot(
        version=payload["version"],
        fingerprint=fingerprint,
        claim_contract_version=payload["claim_contract_version"],
        supported_independent_variables=tuple(payload["supported_independent_variables"]),
        supported_independent_variable_directions=tuple(payload["supported_independent_variable_directions"]),
        supported_outcomes=tuple(payload["supported_outcomes"]),
        supported_expected_directions=tuple(payload["supported_expected_directions"]),
        supported_aggregation_semantics=tuple(payload["supported_aggregation_semantics"]),
        authority_boundary=payload["authority_boundary"],
        exact_value_boundary=payload["exact_value_boundary"],
        ambiguity_boundary=payload["ambiguity_boundary"],
    )
