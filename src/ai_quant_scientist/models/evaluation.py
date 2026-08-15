"""Immutable result-evaluation records for AI Quant Scientist."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from .enums import ResearchStage
from .research import utcnow


class EvaluationRecommendation(str, Enum):
    """Structured evaluator outcomes."""

    PROMOTE = "PROMOTE"
    ITERATE = "ITERATE"
    REJECT = "REJECT"


class EvaluationReasonCode(str, Enum):
    """Machine-readable reason codes used by deterministic evaluation."""

    MINIMUM_TRADE_COUNT_MET = "MINIMUM_TRADE_COUNT_MET"
    MINIMUM_TRADE_COUNT_NOT_MET = "MINIMUM_TRADE_COUNT_NOT_MET"
    MINIMUM_SHARPE_MET = "MINIMUM_SHARPE_MET"
    MINIMUM_SHARPE_NOT_MET = "MINIMUM_SHARPE_NOT_MET"
    MINIMUM_NET_PNL_MET = "MINIMUM_NET_PNL_MET"
    MINIMUM_NET_PNL_NOT_MET = "MINIMUM_NET_PNL_NOT_MET"
    NET_PNL_BELOW_ZERO_HARD_FAIL = "NET_PNL_BELOW_ZERO_HARD_FAIL"
    RESULT_MISSING_REQUIRED_METRIC = "RESULT_MISSING_REQUIRED_METRIC"


@dataclass(frozen=True, slots=True)
class ResultEvaluationPolicy:
    """Frozen stub research criteria used to judge discovery evidence.

    These thresholds are intentionally simple infrastructure defaults, not
    validated trading rules.
    """

    stage: ResearchStage = ResearchStage.DISCOVERY
    version: int = 1
    minimum_trade_count: int = 4
    minimum_sharpe: float = 1.0
    minimum_net_pnl: float = 10.0


@dataclass(frozen=True, slots=True)
class EvaluationDecision:
    """Historical judgment over a specific experiment result."""

    id: str
    research_run_id: str
    attempt_id: str
    result_id: str
    stage: ResearchStage
    recommendation: EvaluationRecommendation
    reason_codes: tuple[str, ...]
    metrics_snapshot: dict[str, Any]
    policy_snapshot: dict[str, Any]
    summary: str
    created_at: datetime = field(default_factory=utcnow)
