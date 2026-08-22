"""V1 capability registry — reflects what ai-quant-scientist ACTUALLY supports today.

Registry version: capability_registry_v1

Current reality (2026-08-20):
    Only the StubBacktester is integrated.
    It produces deterministic synthetic metrics from (signal_threshold, lookback) parameters.
    There is NO real market data, NO real data provider, NO real asset class support.

This registry is a statement of reality, not aspiration.
Future capabilities must be registered here explicitly when a real integration exists.
"""
from __future__ import annotations

from .models import AssetClass, Capability, DataKind, Resolution, ToolKind
from .registry import CapabilityRegistry

_CAPABILITIES: list[Capability] = [
    Capability(
        capability_id="stub_backtester_v1",
        capability_type="EXECUTION_TOOL",
        data_kind=DataKind.SYNTHETIC_PARAMETRIC,
        asset_classes=(AssetClass.SYNTHETIC,),
        resolutions=(Resolution.NOT_APPLICABLE,),
        # instruments=None: no real instrument concept; synthetic data
        instruments=None,
        # available_fields: what the stub outputs as metrics
        available_fields=("signal_threshold", "lookback", "trade_count", "net_pnl", "sharpe", "score"),
        # No date coverage; purely parametric, no historical market data
        coverage_start=None,
        coverage_end=None,
        point_in_time=False,
        # The two parameters this tool's research specs must have
        supported_parameters=("signal_threshold", "lookback"),
        supported_tool_kinds=(ToolKind.BACKTEST_EXECUTION,),
        provider="StubBacktester",
        enabled=True,
        version="1",
        metadata={
            "notes": (
                "Deterministic synthetic parametric simulation. "
                "No real market data. "
                "Parameters: signal_threshold (float), lookback (int). "
                "Metrics are computed from a deterministic formula, not real backtest results."
            )
        },
    ),
]


def build_v1_registry() -> CapabilityRegistry:
    """Return the authoritative V1 capability registry."""
    return CapabilityRegistry(_CAPABILITIES)


# Module-level singleton for convenience
DEFAULT_REGISTRY: CapabilityRegistry = build_v1_registry()
