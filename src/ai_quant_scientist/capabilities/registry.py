"""Deterministic capability registry and feasibility evaluator.

Policy version: capability_registry_v1

Same registry + same requirements → same FeasibilityResult (aside from evaluated_at).
Makes no network calls. Fails closed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from .models import (
    AssetClass,
    AnyRequirement,
    Capability,
    DataKind,
    DataRequirement,
    FeasibilityReasonCode,
    FeasibilityResult,
    FeasibilityStatus,
    RequirementResult,
    Resolution,
    ToolRequirement,
    validate_required_field_names,
    validate_required_parameter_names,
    compute_registry_fingerprint,
)

REGISTRY_VERSION = "capability_registry_v1"


class CapabilityRegistry:
    """Deterministic, fail-closed capability registry.

    All matching is explicit: if a capability is not registered and enabled,
    it does NOT satisfy any requirement.  Never infer "we could probably fetch that."
    """

    def __init__(self, capabilities: Sequence[Capability], version: str = REGISTRY_VERSION) -> None:
        self._caps = list(capabilities)
        self._version = version
        self._fingerprint = compute_registry_fingerprint(self._caps)

    # ─── inspection ──────────────────────────────────────────────────────────

    @property
    def version(self) -> str:
        return self._version

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def list_capabilities(self) -> list[Capability]:
        return list(self._caps)

    # ─── evaluation ──────────────────────────────────────────────────────────

    def evaluate_requirement(self, req: DataRequirement) -> RequirementResult:
        """Evaluate a single DataRequirement against all enabled capabilities.

        A requirement is satisfied only when an ENABLED capability EXPLICITLY
        covers every constrained dimension.  Fail-closed on every dimension.
        This is candidate-feasibility matching, not exact frozen-spec execution validation.
        """
        enabled = [c for c in self._caps if c.enabled]

        try:
            validate_required_field_names(req.data_kind, req.required_fields)
        except ValueError as exc:
            return RequirementResult(
                requirement=req,
                satisfied=False,
                matched_capability=None,
                reason_codes=(FeasibilityReasonCode.REQUIRED_FIELD_MISSING,),
                notes=str(exc),
            )

        try:
            validate_required_parameter_names(req.required_parameters)
        except ValueError as exc:
            return RequirementResult(
                requirement=req,
                satisfied=False,
                matched_capability=None,
                reason_codes=(FeasibilityReasonCode.REQUIRED_PARAMETER_MISSING,),
                notes=str(exc),
            )

        # Filter: data_kind must match exactly
        kind_match = [c for c in enabled if c.data_kind == req.data_kind]
        if not kind_match:
            return RequirementResult(
                requirement=req,
                satisfied=False,
                matched_capability=None,
                reason_codes=(FeasibilityReasonCode.NO_MATCHING_DATA_KIND,),
                notes=f"No enabled capability provides data_kind={req.data_kind.value}",
            )

        # Filter: asset class
        if req.asset_class is not None:
            kind_match = [c for c in kind_match if req.asset_class in c.asset_classes]
            if not kind_match:
                return RequirementResult(
                    requirement=req, satisfied=False, matched_capability=None,
                    reason_codes=(FeasibilityReasonCode.ASSET_CLASS_UNAVAILABLE,),
                    notes=f"asset_class={req.asset_class.value} not in any capability",
                )

        # Filter: resolution
        if req.resolution is not None:
            kind_match = [c for c in kind_match if req.resolution in c.resolutions]
            if not kind_match:
                return RequirementResult(
                    requirement=req, satisfied=False, matched_capability=None,
                    reason_codes=(FeasibilityReasonCode.RESOLUTION_UNAVAILABLE,),
                    notes=f"resolution={req.resolution.value} not in any capability",
                )

        # Filter: instruments
        # Capability.instruments=None means NOT DECLARED — fails a constrained requirement.
        if req.instruments is not None:
            req_set = set(req.instruments)
            kind_match = [
                c for c in kind_match
                if c.instruments is not None and req_set.issubset(set(c.instruments))
            ]
            if not kind_match:
                return RequirementResult(
                    requirement=req, satisfied=False, matched_capability=None,
                    reason_codes=(FeasibilityReasonCode.INSTRUMENT_UNAVAILABLE,),
                    notes=f"Required instruments not available: {sorted(req.instruments)}",
                )

        # Filter: required fields
        # Capability.available_fields=None means NOT DECLARED — fails a constrained requirement.
        if req.required_fields is not None:
            req_fields = set(req.required_fields)
            kind_match = [
                c for c in kind_match
                if c.available_fields is not None and req_fields.issubset(set(c.available_fields))
            ]
            if not kind_match:
                return RequirementResult(
                    requirement=req, satisfied=False, matched_capability=None,
                    reason_codes=(FeasibilityReasonCode.REQUIRED_FIELD_MISSING,),
                    notes=f"Required fields not available: {sorted(req.required_fields)}",
                )

        # Filter: date coverage (capability coverage None = unknown, fails closed for bounded req)
        if req.start_date is not None or req.end_date is not None:
            def covers(c: Capability) -> bool:
                if c.coverage_start is None or c.coverage_end is None:
                    # Unknown coverage → cannot satisfy bounded date requirement
                    return False
                if req.start_date is not None and c.coverage_start > req.start_date:
                    return False
                if req.end_date is not None and c.coverage_end < req.end_date:
                    return False
                return True
            kind_match = [c for c in kind_match if covers(c)]
            if not kind_match:
                return RequirementResult(
                    requirement=req, satisfied=False, matched_capability=None,
                    reason_codes=(FeasibilityReasonCode.DATE_COVERAGE_INSUFFICIENT,),
                    notes=(
                        f"No capability covers [{req.start_date}, {req.end_date}]; "
                        "capabilities with unknown coverage do not satisfy bounded date requirements"
                    ),
                )

        # Filter: point-in-time
        if req.point_in_time_required:
            kind_match = [c for c in kind_match if c.point_in_time]
            if not kind_match:
                return RequirementResult(
                    requirement=req, satisfied=False, matched_capability=None,
                    reason_codes=(FeasibilityReasonCode.POINT_IN_TIME_UNAVAILABLE,),
                    notes="Point-in-time data not available",
                )

        # Filter: required parameters
        # Historical/manual capability-detail check only.
        # New AI-authored candidates should normally not rely on this field; exact
        # parameter compatibility belongs to later ResearchSpec validation.
        # Capability.supported_parameters=None means NOT DECLARED — fails a constrained requirement.
        if req.required_parameters is not None:
            req_params = set(req.required_parameters)
            kind_match = [
                c for c in kind_match
                if c.supported_parameters is not None and req_params.issubset(set(c.supported_parameters))
            ]
            if not kind_match:
                return RequirementResult(
                    requirement=req, satisfied=False, matched_capability=None,
                    reason_codes=(FeasibilityReasonCode.REQUIRED_PARAMETER_MISSING,),
                    notes=f"Required parameters not supported: {sorted(req.required_parameters)}",
                )

        # First matching capability satisfies the requirement
        return RequirementResult(
            requirement=req,
            satisfied=True,
            matched_capability=kind_match[0],
            reason_codes=(),
        )

    def evaluate_tool_requirement(self, req: ToolRequirement) -> RequirementResult:
        """Evaluate semantic tool requirements exactly, with legacy read-compatibility."""
        enabled = [c for c in self._caps if c.enabled]

        if req.tool_kind is not None:
            matches = [
                c for c in enabled
                if c.supported_tool_kinds is not None and req.tool_kind in c.supported_tool_kinds
            ]
            if not matches:
                return RequirementResult(
                    requirement=req,
                    satisfied=False,
                    matched_capability=None,
                    reason_codes=(FeasibilityReasonCode.TOOL_UNAVAILABLE,),
                    notes=(
                        f"Canonical tool kind '{req.tool_kind.value}' is not registered or enabled"
                    ),
                )
            return RequirementResult(
                requirement=req,
                satisfied=True,
                matched_capability=matches[0],
                reason_codes=(),
            )

        matches = [c for c in enabled if c.capability_id == req.tool_name]
        if not matches:
            return RequirementResult(
                requirement=req,
                satisfied=False,
                matched_capability=None,
                reason_codes=(FeasibilityReasonCode.TOOL_UNAVAILABLE,),
                notes=(
                    f"Legacy tool name '{req.tool_name}' is not registered or enabled "
                    "(historical exact capability_id lookup only)"
                ),
            )
        return RequirementResult(
            requirement=req,
            satisfied=True,
            matched_capability=matches[0],
            reason_codes=(),
        )

    def evaluate(self, requirements: Sequence[DataRequirement | ToolRequirement]) -> FeasibilityResult:
        """Evaluate candidate-stage broad requirements.

        TESTABLE only if every requirement is individually satisfied.
        Designed for future Hypothesis Scientist integration:
            result = registry.evaluate(requirements)
            if result.status == FeasibilityStatus.TESTABLE:
                # proceed to Spec construction
        """
        if not requirements:
            return FeasibilityResult(
                status=FeasibilityStatus.TESTABLE,
                requirement_results=(),
                satisfied_ids=(),
                unsatisfied_ids=(),
                reason_codes=(),
                registry_version=self._version,
                registry_fingerprint=self._fingerprint,
            )

        results = tuple(
            self.evaluate_tool_requirement(r) if isinstance(r, ToolRequirement)
            else self.evaluate_requirement(r)
            for r in requirements
        )
        satisfied = tuple(r.requirement.requirement_id for r in results if r.satisfied)
        unsatisfied = tuple(r.requirement.requirement_id for r in results if not r.satisfied)

        all_reason_codes: list[FeasibilityReasonCode] = []
        for r in results:
            all_reason_codes.extend(r.reason_codes)
        # deduplicate while preserving first-seen order
        seen: set[FeasibilityReasonCode] = set()
        deduped: list[FeasibilityReasonCode] = []
        for code in all_reason_codes:
            if code not in seen:
                seen.add(code)
                deduped.append(code)

        status = FeasibilityStatus.TESTABLE if not unsatisfied else FeasibilityStatus.NOT_TESTABLE

        return FeasibilityResult(
            status=status,
            requirement_results=results,
            satisfied_ids=satisfied,
            unsatisfied_ids=unsatisfied,
            reason_codes=tuple(deduped),
            registry_version=self._version,
            registry_fingerprint=self._fingerprint,
        )
