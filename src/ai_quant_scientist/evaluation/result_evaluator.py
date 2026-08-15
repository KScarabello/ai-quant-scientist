"""Deterministic scientific evaluation of experiment results."""

from __future__ import annotations

from dataclasses import replace

from ..models.evaluation import (
    EvaluationDecision,
    EvaluationReasonCode,
    EvaluationRecommendation,
    ResultEvaluationPolicy,
)
from ..models.research import ExperimentResult, ResearchAttempt, ResearchRun, new_id, record_to_state, utcnow


class ResultEvaluator:
    """Compare measured experiment outcomes against frozen evaluation criteria."""

    def evaluate(
        self,
        *,
        run: ResearchRun,
        attempt: ResearchAttempt,
        result: ExperimentResult,
        policy: ResultEvaluationPolicy,
    ) -> EvaluationDecision:
        metrics = dict(result.metrics)
        missing_metrics = [metric_name for metric_name in ("trade_count", "sharpe", "net_pnl") if metric_name not in metrics]

        reason_codes: list[str] = []
        if missing_metrics:
            reason_codes.append(EvaluationReasonCode.RESULT_MISSING_REQUIRED_METRIC.value)
            recommendation = EvaluationRecommendation.REJECT
        else:
            trade_count = int(metrics["trade_count"])
            sharpe = float(metrics["sharpe"])
            net_pnl = float(metrics["net_pnl"])

            trade_count_met = trade_count >= policy.minimum_trade_count
            sharpe_met = sharpe >= policy.minimum_sharpe
            net_pnl_met = net_pnl >= policy.minimum_net_pnl

            reason_codes.append(
                EvaluationReasonCode.MINIMUM_TRADE_COUNT_MET.value
                if trade_count_met
                else EvaluationReasonCode.MINIMUM_TRADE_COUNT_NOT_MET.value
            )
            reason_codes.append(
                EvaluationReasonCode.MINIMUM_SHARPE_MET.value
                if sharpe_met
                else EvaluationReasonCode.MINIMUM_SHARPE_NOT_MET.value
            )
            reason_codes.append(
                EvaluationReasonCode.MINIMUM_NET_PNL_MET.value
                if net_pnl_met
                else EvaluationReasonCode.MINIMUM_NET_PNL_NOT_MET.value
            )

            if net_pnl < 0:
                reason_codes.append(EvaluationReasonCode.NET_PNL_BELOW_ZERO_HARD_FAIL.value)
                recommendation = EvaluationRecommendation.REJECT
            elif trade_count_met and sharpe_met and net_pnl_met:
                recommendation = EvaluationRecommendation.PROMOTE
            else:
                recommendation = EvaluationRecommendation.ITERATE

        summary = self._summarize(
            recommendation=recommendation,
            metrics=metrics,
            policy=policy,
            reason_codes=reason_codes,
        )
        return EvaluationDecision(
            id=new_id(),
            research_run_id=run.id,
            attempt_id=attempt.id,
            result_id=result.id,
            stage=run.stage,
            recommendation=recommendation,
            reason_codes=tuple(reason_codes),
            metrics_snapshot=metrics,
            policy_snapshot=record_to_state(policy),
            summary=summary,
            created_at=utcnow(),
        )

    def _summarize(
        self,
        *,
        recommendation: EvaluationRecommendation,
        metrics: dict[str, object],
        policy: ResultEvaluationPolicy,
        reason_codes: list[str],
    ) -> str:
        return (
            f"{recommendation.value} with trade_count={metrics.get('trade_count')}, "
            f"sharpe={metrics.get('sharpe')}, net_pnl={metrics.get('net_pnl')} against "
            f"policy v{policy.version} ({policy.minimum_trade_count}, {policy.minimum_sharpe}, {policy.minimum_net_pnl}); "
            f"reasons={','.join(reason_codes)}"
        )
