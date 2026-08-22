"""Domain models for Capabilities & Data Requirements (V0.9)."""
from __future__ import annotations

import hashlib
import json
import re
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


class ToolKind(str, Enum):
    """Canonical semantic tool kinds that AI may request."""
    BACKTEST_EXECUTION = "BACKTEST_EXECUTION"
    SYNTHETIC_DATA_GENERATION = "SYNTHETIC_DATA_GENERATION"
    STATISTICAL_ANALYSIS = "STATISTICAL_ANALYSIS"
    MARKET_DATA_RESEARCH = "MARKET_DATA_RESEARCH"


_FIELD_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

CANONICAL_FIELDS_BY_DATA_KIND: dict[DataKind, tuple[str, ...]] = {
    DataKind.OHLCV: (
        "instrument_id", "trade_date", "timestamp", "open", "high", "low",
        "close", "adjusted_close", "volume",
    ),
    DataKind.TRADES: (
        "timestamp", "instrument_id", "trade_price", "trade_size", "exchange_or_venue",
    ),
    DataKind.QUOTES: (
        "timestamp", "instrument_id", "bid_price", "ask_price", "bid_size",
        "ask_size", "exchange_or_venue", "contract_expiry",
    ),
    DataKind.ORDER_BOOK: (
        "timestamp", "instrument_id", "best_bid_price", "best_ask_price",
        "best_bid_size", "best_ask_size", "exchange_or_venue", "contract_expiry",
    ),
    DataKind.FUNDAMENTALS: (
        "instrument_id", "trade_date", "security_type", "primary_listing_flag",
        "exchange", "listing_status", "market_cap",
    ),
    DataKind.CORPORATE_ACTIONS: (
        "instrument_id", "ex_date", "action_type", "split_ratio", "cash_dividend",
        "delisting_date", "delisting_return",
    ),
    DataKind.EARNINGS: (
        "instrument_id", "report_date", "fiscal_period", "eps_actual", "eps_estimate",
    ),
    DataKind.BORROW: (
        "instrument_id", "trade_date", "borrow_rate", "borrow_availability",
    ),
    DataKind.ALTERNATIVE: (
        "timestamp", "instrument_id", "feature_value",
    ),
    DataKind.SYNTHETIC_PARAMETRIC: (
        "timestamp",
        "signal_value",
        "synthetic_price",
        "synthetic_return",
        "regime_label",
        "volatility_regime_label",
        "latent_equilibrium_value",
        "contemporaneous_volatility",
        "one_step_forward_change",
    ),
}


def validate_required_field_names(data_kind: DataKind, fields: tuple[str, ...] | None) -> None:
    """Validate canonical primitive field identifiers for new authoritative requirements."""
    if fields is None:
        return
    allowed = set(CANONICAL_FIELDS_BY_DATA_KIND.get(data_kind, ()))
    for field_name in fields:
        if not field_name or not field_name.strip():
            raise ValueError("required_fields entries must be non-empty")
        if not _FIELD_IDENTIFIER_RE.match(field_name):
            raise ValueError(f"required_fields entry is not a primitive identifier: {field_name!r}")
        if "_or_" in field_name.lower():
            raise ValueError(f"required_fields entry encodes logical alternatives: {field_name!r}")
        if field_name not in allowed:
            raise ValueError(
                f"required_fields entry {field_name!r} is not registered for data_kind={data_kind.value}"
            )


def validate_required_parameter_names(parameters: tuple[str, ...] | None) -> None:
    """Validate explicit parameter identifiers without inferring semantics."""
    if parameters is None:
        return
    for parameter_name in parameters:
        if not parameter_name or not parameter_name.strip():
            raise ValueError("required_parameters entries must be non-empty")
        if not _FIELD_IDENTIFIER_RE.match(parameter_name):
            raise ValueError(
                f"required_parameters entry is not a canonical identifier: {parameter_name!r}"
            )
        if "_or_" in parameter_name.lower():
            raise ValueError(
                f"required_parameters entry encodes logical alternatives: {parameter_name!r}"
            )


# ─── DataRequirement ──────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class DataRequirement:
    """Prerequisite input data a research experiment needs from the system.

    Fields are optional; None means "no constraint on this dimension."
    Only constrained dimensions are evaluated during matching.
    This model describes inputs required before deterministic tool execution.
    It does not represent generated backtest outputs or experiment evidence.
    New AI-authored candidates should keep this broad and pre-spec.
    Historical/manual paths may still use required_parameters for capability-detail checks.
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
    # Historical/manual capability-detail constraint.
    # New AI-authored candidates should normally leave this unset so pre-spec
    # candidate feasibility stays broad and exact design compatibility can be
    # validated later against a frozen ResearchSpec.
    required_parameters: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.instruments is not None:
            object.__setattr__(self, "instruments", tuple(sorted(self.instruments)))
        if self.required_fields is not None:
            object.__setattr__(self, "required_fields", tuple(sorted(self.required_fields)))
        if self.required_parameters is not None:
            object.__setattr__(self, "required_parameters", tuple(sorted(self.required_parameters)))


@dataclass(frozen=True, slots=True)
class ToolRequirement:
    """Explicit requirement for a semantic tool capability.

    New authoritative requirements use tool_kind.
    Historical snapshots may still carry a legacy_tool_name string and are kept
    readable without being silently reinterpreted.
    """
    requirement_id: str
    tool_kind: ToolKind | None = None
    label: str = ""
    legacy_tool_name: str | None = None

    def __post_init__(self) -> None:
        if self.tool_kind is None and not self.legacy_tool_name:
            raise ValueError("ToolRequirement requires tool_kind or legacy_tool_name")
        if self.tool_kind is not None and self.legacy_tool_name is not None:
            raise ValueError("ToolRequirement cannot carry both tool_kind and legacy_tool_name")

    @property
    def tool_name(self) -> str:
        """Backward-compatible display/accessor used by older tests and artifacts."""
        return self.tool_kind.value if self.tool_kind is not None else (self.legacy_tool_name or "")

    @property
    def is_legacy(self) -> bool:
        return self.legacy_tool_name is not None


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
    # None = NOT DECLARED (does not satisfy semantic tool requirements)
    supported_tool_kinds: tuple[ToolKind, ...] | None = None

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
        "supported_tool_kinds": (
            sorted(kind.value for kind in cap.supported_tool_kinds)
            if cap.supported_tool_kinds is not None
            else None
        ),
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
