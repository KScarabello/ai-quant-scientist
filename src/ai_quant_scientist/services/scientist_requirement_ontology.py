"""Deterministic AI-safe requirement ontology projection for the Hypothesis Scientist.

This projection exposes legal requirement language only. It must not reveal
capability availability, capability IDs, enabled/disabled state, or registry
truth about what the system can currently test.

Historical ontology versions remain reproducible once they have live evidence.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..capabilities.models import (
    AssetClass,
    CANONICAL_FIELDS_BY_DATA_KIND,
    DataKind,
    Resolution,
    ToolKind,
)

REQUIREMENT_ONTOLOGY_V1 = "requirement_ontology_v1"
REQUIREMENT_ONTOLOGY_VERSION = "requirement_ontology_v2"

DATA_REQUIREMENT_SEMANTICS = (
    "DataRequirement describes prerequisite input data needed before deterministic "
    "tool execution. It must not be used for generated outputs or evidence such as "
    "execution_price, trade_count, net_pnl, or Sharpe."
)

REQUIRED_PARAMETERS_V1_SEMANTICS = (
    "required_parameters names explicit input parameters that the supplying data or "
    "simulation capability must support. It is not a placeholder for arbitrary future "
    "ResearchSpec design or generated experiment outputs."
)

TOOL_REQUIREMENT_SEMANTICS = (
    "tool_kind expresses a semantic tool class only. It does not reveal capability IDs, "
    "availability state, registry matches, or present feasibility."
)

CANDIDATE_FEASIBILITY_SEMANTICS = (
    "Scientist requirements operate at candidate-feasibility stage only. They should "
    "describe broad prerequisite data and broad deterministic tool classes needed to "
    "proceed to experiment design before any ResearchSpec exists."
)

FUTURE_SPEC_FEASIBILITY_SEMANTICS = (
    "Exact parameter grids, named strategy rules, execution settings, sample windows, "
    "transaction-cost assumptions, and implementation-specific compatibility are "
    "validated later against a frozen ResearchSpec after READY_FOR_SPEC."
)

GENERATED_OUTPUT_EXAMPLES = (
    "execution_price",
    "trade_count",
    "net_pnl",
    "out_of_sample_sharpe",
)


def _ontology_payload_v1_without_fingerprint() -> dict:
    return {
        "version": REQUIREMENT_ONTOLOGY_V1,
        "allowed_data_kinds": [kind.value for kind in DataKind],
        "allowed_asset_classes": [asset_class.value for asset_class in AssetClass],
        "allowed_resolutions": [resolution.value for resolution in Resolution],
        "canonical_fields_by_data_kind": {
            data_kind.value: list(CANONICAL_FIELDS_BY_DATA_KIND[data_kind])
            for data_kind in DataKind
        },
        "tool_kinds": [tool_kind.value for tool_kind in ToolKind],
        "data_requirement_semantics": DATA_REQUIREMENT_SEMANTICS,
        "required_parameters_semantics": REQUIRED_PARAMETERS_V1_SEMANTICS,
        "tool_requirement_semantics": TOOL_REQUIREMENT_SEMANTICS,
        "generated_output_examples_not_valid_as_input_fields": list(GENERATED_OUTPUT_EXAMPLES),
    }


def _ontology_payload_v2_without_fingerprint() -> dict:
    return {
        "version": REQUIREMENT_ONTOLOGY_VERSION,
        "allowed_data_kinds": [kind.value for kind in DataKind],
        "allowed_asset_classes": [asset_class.value for asset_class in AssetClass],
        "allowed_resolutions": [resolution.value for resolution in Resolution],
        "canonical_fields_by_data_kind": {
            data_kind.value: list(CANONICAL_FIELDS_BY_DATA_KIND[data_kind])
            for data_kind in DataKind
        },
        "tool_kinds": [tool_kind.value for tool_kind in ToolKind],
        "data_requirement_semantics": DATA_REQUIREMENT_SEMANTICS,
        "tool_requirement_semantics": TOOL_REQUIREMENT_SEMANTICS,
        "candidate_feasibility_semantics": CANDIDATE_FEASIBILITY_SEMANTICS,
        "future_spec_feasibility_semantics": FUTURE_SPEC_FEASIBILITY_SEMANTICS,
        "generated_output_examples_not_valid_as_input_fields": list(GENERATED_OUTPUT_EXAMPLES),
    }


def _compute_ontology_fingerprint(payload_without_fingerprint: dict) -> str:
    canon = json.dumps(
        payload_without_fingerprint,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RequirementOntologySnapshot:
    """Deterministic, AI-safe vocabulary snapshot for scientist invocations."""

    version: str
    fingerprint: str
    allowed_data_kinds: tuple[str, ...]
    allowed_asset_classes: tuple[str, ...]
    allowed_resolutions: tuple[str, ...]
    canonical_fields_by_data_kind: dict[str, tuple[str, ...]]
    tool_kinds: tuple[str, ...]
    data_requirement_semantics: str
    tool_requirement_semantics: str
    candidate_feasibility_semantics: str | None
    future_spec_feasibility_semantics: str | None
    generated_output_examples_not_valid_as_input_fields: tuple[str, ...]
    required_parameters_semantics: str | None = None

    def to_payload(self) -> dict:
        payload = {
            "version": self.version,
            "fingerprint": self.fingerprint,
            "allowed_data_kinds": list(self.allowed_data_kinds),
            "allowed_asset_classes": list(self.allowed_asset_classes),
            "allowed_resolutions": list(self.allowed_resolutions),
            "canonical_fields_by_data_kind": {
                data_kind: list(fields)
                for data_kind, fields in self.canonical_fields_by_data_kind.items()
            },
            "tool_kinds": list(self.tool_kinds),
            "data_requirement_semantics": self.data_requirement_semantics,
            "tool_requirement_semantics": self.tool_requirement_semantics,
            "generated_output_examples_not_valid_as_input_fields": list(
                self.generated_output_examples_not_valid_as_input_fields
            ),
        }
        if self.required_parameters_semantics is not None:
            payload["required_parameters_semantics"] = self.required_parameters_semantics
        if self.candidate_feasibility_semantics is not None:
            payload["candidate_feasibility_semantics"] = self.candidate_feasibility_semantics
        if self.future_spec_feasibility_semantics is not None:
            payload["future_spec_feasibility_semantics"] = self.future_spec_feasibility_semantics
        return payload


def build_requirement_ontology_snapshot(
    version: str = REQUIREMENT_ONTOLOGY_VERSION,
) -> RequirementOntologySnapshot:
    if version == REQUIREMENT_ONTOLOGY_V1:
        payload = _ontology_payload_v1_without_fingerprint()
    elif version == REQUIREMENT_ONTOLOGY_VERSION:
        payload = _ontology_payload_v2_without_fingerprint()
    else:
        raise KeyError(
            f"Unknown requirement ontology version {version!r}. "
            f"Known: {[REQUIREMENT_ONTOLOGY_V1, REQUIREMENT_ONTOLOGY_VERSION]}"
        )
    fingerprint = _compute_ontology_fingerprint(payload)
    return RequirementOntologySnapshot(
        version=payload["version"],
        fingerprint=fingerprint,
        allowed_data_kinds=tuple(payload["allowed_data_kinds"]),
        allowed_asset_classes=tuple(payload["allowed_asset_classes"]),
        allowed_resolutions=tuple(payload["allowed_resolutions"]),
        canonical_fields_by_data_kind={
            data_kind: tuple(fields)
            for data_kind, fields in payload["canonical_fields_by_data_kind"].items()
        },
        tool_kinds=tuple(payload["tool_kinds"]),
        data_requirement_semantics=payload["data_requirement_semantics"],
        tool_requirement_semantics=payload["tool_requirement_semantics"],
        candidate_feasibility_semantics=payload.get("candidate_feasibility_semantics"),
        future_spec_feasibility_semantics=payload.get("future_spec_feasibility_semantics"),
        generated_output_examples_not_valid_as_input_fields=tuple(
            payload["generated_output_examples_not_valid_as_input_fields"]
        ),
        required_parameters_semantics=payload.get("required_parameters_semantics"),
    )
