"""Domain models for Capabilities & Data Requirements (V0.9)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any


# ─── vocabularies ─────────────────────────────────────────────────────────────

class DataKind(str, Enum):
    """What kind of data an experiment needs or a capability provides."""
    OHLCV = "OHLCV"
    TRADES = "TRADES"
    QUOTES = "QUOTES"
    ORDER_BOOK = "ORDER_BOOK"
    FUNDAMENTALS = "FUNDAMENTALS"
    CORPORATE_ACTIONS = "CORPORATE_ACTIONS"
    EARNINGS = "EARNINGS"
    BORROW = "BORROW"
    ALTERNATIVE = "ALTERNATIVE"
    # Deterministic parametric simulation — no real market data
    SYNTHETIC_PARAMETRIC = "SYNTHETIC_PARAMETRIC"


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    FUTURES = "FUTURES"
    OPTIONS = "OPTIONS"
    CRYPTO = "CRYPTO"
    FX = "FX"
    FIXED_INCOME = "FIXED_INCOME"
    # Used for synthetic/parametric research with no real underlying asset
    SYNTHETIC = "SYNTHETIC"


class Resolution(str, Enum):
    TICK = "TICK"
    SECOND_1 = "1s"
    MINUTE_1 = "1m"
    MINUTE_5 = "5m"
    MINUTE_15 = "15m"
    MINUTE_30 = "30m"
    HOUR_1 = "1h"
    DAILY = "1d"
    WEEKLY = "1w"
    MONTHLY = "1M"
    # For synthetic/parametric research that has no time-resolution concept
    NOT_APPLICABLE = "N/A"


class FeasibilityStatus(str, Enum):
    TESTABLE = "TESTABLE"
    NOT_TESTABLE = "NOT_TESTABLE"


class FeasibilityReasonCode(str, Enum):
    NO_MATCHING_DATA_KIND = "NO_MATCHING_DATA_KIND"
    ASSET_CLASS_UNAVAILABLE = "ASSET_CLASS_UNAVAILABLE"
    INSTRUMENT_UNAVAILABLE = "INSTRUMENT_UNAVAILABLE"
    RESOLUTION_UNAVAILABLE = "RESOLUTION_UNAVAILABLE"
    REQUIRED_FIELD_MISSING = "REQUIRED_FIELD_MISSING"
    DATE_COVERAGE_INSUFFICIENT = "DATE_COVERAGE_INSUFFICIENT"
    POINT_IN_TIME_UNAVAILABLE = "POINT_IN_TIME_UNAVAILABLE"
    CAPABILITY_DISABLED = "CAPABILITY_DISABLED"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    REQUIRED_PARAMETER_MISSING = "REQUIRED_PARAMETER_MISSING"


# ─── DataRequirement ──────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class DataRequirement:
    """What a research experiment needs from the system.

    Fields are optional; None means "no constraint on this dimension."
    Only constrained dimensions are evaluated during matching.
    """
    requirement_id: str
    data_kind: DataKind
    label: str = ""

    # Optional constraints — all default to None (unconstrained)
    asset_class: AssetClass | None = None
    # Sorted tuples of strings; None = no instrument constraint
    instruments: tuple[str, ...] | None = None
    resolution: Resolution | None = None
    # All of these fields must be available; None = no field constraint
    required_fields: tuple[str, ...] | None = None
    start_date: date | None = None
    end_date: date | None = None
    point_in_time_required: bool = False
    # For synthetic/parametric research: the spec parameters required
    required_parameters: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class ToolRequirement:
    """Explicit requirement for a named execution tool capability.

    Matches capabilities by capability_id.
    Separates tool availability from data availability so
    TOOL_UNAVAILABLE can be emitted independently.
    """
    requirement_id: str
    tool_name: str   # must match Capability.capability_id
    label: str = ""


# Union type for all requirement kinds
AnyRequirement = DataRequirement | ToolRequirement


# ─── Capability ───────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Capability:
    """What the system can actually provide RIGHT NOW.

    None on a set-valued field means "not declared" — NOT "unrestricted".
    When a requirement constrains a dimension, a capability with None on that
    dimension does NOT satisfy the requirement (fail-closed).
    Use an explicit tuple to declare support; use an empty tuple for nothing supported.
    """
    capability_id: str
    capability_type: str   # e.g. "EXECUTION_TOOL" or "DATA_FEED"
    data_kind: DataKind

    # Sorted tuples; empty tuple = nothing supported
    asset_classes: tuple[AssetClass, ...]
    resolutions: tuple[Resolution, ...]

    # None = NOT DECLARED (does not satisfy any constrained requirement)
    instruments: tuple[str, ...] | None = None
    available_fields: tuple[str, ...] | None = None

    # Date coverage; None = unknown / not applicable
    coverage_start: date | None = None
    coverage_end: date | None = None

    point_in_time: bool = False
    # None = NOT DECLARED (does not satisfy any required-parameter constraint)
    supported_parameters: tuple[str, ...] | None = None

    provider: str = ""
    enabled: bool = True
    version: str = "1"
    metadata: dict[str, Any] = field(default_factory=dict)


# ─── Matching / Result models ─────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class RequirementResult:
    """Feasibility verdict for a single DataRequirement or ToolRequirement."""
    requirement: DataRequirement | ToolRequirement
    satisfied: bool
    matched_capability: Capability | None
    reason_codes: tuple[FeasibilityReasonCode, ...]
    notes: str = ""


@dataclass(frozen=True, slots=True)
class FeasibilityResult:
    """Structured, machine-readable feasibility verdict for a set of requirements.

    Future usage:
        if result.status == FeasibilityStatus.TESTABLE:
            # Spec Builder may proceed
        else:
            # Record missing capabilities; do not reject hypothesis outright
            # (MISSING_REQUIREMENT != scientifically invalid hypothesis)
    """
    status: FeasibilityStatus
    requirement_results: tuple[RequirementResult, ...]
    satisfied_ids: tuple[str, ...]
    unsatisfied_ids: tuple[str, ...]
    reason_codes: tuple[FeasibilityReasonCode, ...]   # union of all unsatisfied codes
    registry_version: str
    registry_fingerprint: str
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ─── Canonical serialization for fingerprinting ───────────────────────────────

def _capability_to_canon(cap: Capability) -> dict:
    """Return a JSON-stable dict for fingerprinting (no metadata, sorted fields)."""
    return {
        "capability_id": cap.capability_id,
        "capability_type": cap.capability_type,
        "data_kind": cap.data_kind.value,
        "asset_classes": sorted(a.value for a in cap.asset_classes),
        "resolutions": sorted(r.value for r in cap.resolutions),
        "instruments": sorted(cap.instruments) if cap.instruments is not None else None,
        "available_fields": sorted(cap.available_fields) if cap.available_fields is not None else None,
        "coverage_start": cap.coverage_start.isoformat() if cap.coverage_start else None,
        "coverage_end": cap.coverage_end.isoformat() if cap.coverage_end else None,
        "point_in_time": cap.point_in_time,
        "supported_parameters": sorted(cap.supported_parameters) if cap.supported_parameters is not None else None,
        "provider": cap.provider,
        "enabled": cap.enabled,
        "version": cap.version,
    }


def compute_registry_fingerprint(capabilities: list[Capability]) -> str:
    """SHA-256 over canonical sorted JSON of capability definitions."""
    canon_list = sorted(
        [_capability_to_canon(c) for c in capabilities],
        key=lambda d: d["capability_id"],
    )
    canon = json.dumps(canon_list, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()
