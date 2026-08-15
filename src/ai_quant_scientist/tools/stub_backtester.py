"""Deterministic stub backtester used for V0 orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from ..models.research import ExperimentResult, ResearchSpec, new_id, utcnow


@dataclass(frozen=True, slots=True)
class StubBacktester:
    """Repeatable stand-in for a future real backtesting adapter."""

    name: str = "stub_backtester"

    def run(self, *, spec: ResearchSpec, attempt_id: str) -> ExperimentResult:
        if not spec.is_frozen:
            raise ValueError("StubBacktester requires a frozen ResearchSpec")

        signal_threshold = float(spec.parameters["signal_threshold"])
        lookback = int(spec.parameters["lookback"])
        score = lookback - (signal_threshold * 5.0)
        trade_count = max(1, lookback // 5)
        net_pnl = round(score * 1.25, 2)
        sharpe = round(score / 10.0, 2)
        passed = score >= 0
        metrics = {
            "signal_threshold": signal_threshold,
            "lookback": lookback,
            "trade_count": trade_count,
            "net_pnl": net_pnl,
            "sharpe": sharpe,
            "score": round(score, 4),
        }
        summary = (
            f"Deterministic stub backtest for threshold={signal_threshold} and lookback={lookback} "
            f"produced score={score:.2f}."
        )
        return ExperimentResult(
            id=new_id(),
            attempt_id=attempt_id,
            tool_name=self.name,
            metrics=metrics,
            summary=summary,
            passed=passed,
            created_at=utcnow(),
        )
