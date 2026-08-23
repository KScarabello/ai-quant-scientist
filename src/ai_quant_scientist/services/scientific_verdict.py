"""Deterministic precommitted scientific verdict evaluation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..models.design import (
    ExpectedDirection,
    OutcomeContrast,
    OutcomeScientificVerdict,
    ParameterSensitivityContrastResult,
    PredictionVerdictResult,
    ResearchPredictionPlan,
    ScientificVerdict,
    ScientificVerdictStatus,
)
from ..models.research import new_id


SCIENTIFIC_VERDICT_POLICY_VERSION = "directional_scientific_verdict_policy_v1"


@dataclass(frozen=True, slots=True)
class DirectionalScientificVerdictPolicy:
    version: str = SCIENTIFIC_VERDICT_POLICY_VERSION
    aggregation_rule: str = "ALL_PREDICTIONS_REQUIRED"
    zero_delta_rule: str = "EXACT_ZERO_IS_NO_CHANGE"
    comparator_relation_rule: str = "COMPARATOR_MUST_EXCEED_BASELINE"

    def fingerprint(self) -> str:
        payload = {
            "version": self.version,
            "aggregation_rule": self.aggregation_rule,
            "zero_delta_rule": self.zero_delta_rule,
            "comparator_relation_rule": self.comparator_relation_rule,
        }
        canon = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def build_directional_scientific_verdict_policy() -> DirectionalScientificVerdictPolicy:
    return DirectionalScientificVerdictPolicy()


class ScientificVerdictEvaluator:
    """Deterministically evaluate a precommitted prediction plan against a persisted contrast."""

    def __init__(self, *, store, policy: DirectionalScientificVerdictPolicy | None = None) -> None:
        self._store = store
        self._policy = policy or build_directional_scientific_verdict_policy()

    def evaluate_plan(self, plan_id: str) -> ScientificVerdict:
        plan = _require_present(
            self._store.get_initial_experiment_plan(plan_id),
            f"InitialExperimentPlan not found: {plan_id!r}",
        )
        if not plan.research_prediction_plan_id:
            raise ValueError(
                "InitialExperimentPlan does not carry a precommitted ResearchPredictionPlan; "
                "historical V0.14 plans must not receive retrospective V0.15 verdicts"
            )
        contrast = _require_present(
            self._store.get_parameter_sensitivity_contrast_result(plan.id),
            f"ParameterSensitivityContrastResult not found for plan: {plan.id!r}",
        )
        return self.evaluate(
            prediction_plan_id=plan.research_prediction_plan_id,
            experiment_plan_id=plan.id,
            contrast_result_id=contrast.id,
        )

    def evaluate(
        self,
        *,
        prediction_plan_id: str,
        experiment_plan_id: str,
        contrast_result_id: str,
    ) -> ScientificVerdict:
        existing = self._store.get_scientific_verdict_by_prediction_plan_and_contrast_result(
            prediction_plan_id=prediction_plan_id,
            contrast_result_id=contrast_result_id,
        )
        if existing is not None:
            return existing

        prediction_plan = _require_present(
            self._store.get_research_prediction_plan(prediction_plan_id),
            f"ResearchPredictionPlan not found: {prediction_plan_id!r}",
        )
        experiment_plan = _require_present(
            self._store.get_initial_experiment_plan(experiment_plan_id),
            f"InitialExperimentPlan not found: {experiment_plan_id!r}",
        )
        contrast_result = _require_present(
            self._store.get_parameter_sensitivity_contrast_result_by_id(contrast_result_id),
            f"ParameterSensitivityContrastResult not found: {contrast_result_id!r}",
        )

        self._validate_exact_linkage(
            prediction_plan=prediction_plan,
            experiment_plan=experiment_plan,
            contrast_result=contrast_result,
        )

        verdict = self._compute_verdict(
            prediction_plan=prediction_plan,
            experiment_plan=experiment_plan,
            contrast_result=contrast_result,
        )
        self._store.save_scientific_verdict(verdict)
        return verdict

    def _validate_exact_linkage(
        self,
        *,
        prediction_plan: ResearchPredictionPlan,
        experiment_plan,
        contrast_result: ParameterSensitivityContrastResult,
    ) -> None:
        if experiment_plan.research_prediction_plan_id != prediction_plan.id:
            raise ValueError("InitialExperimentPlan does not point to the requested ResearchPredictionPlan")
        if prediction_plan.design_intent_id != experiment_plan.design_intent_id:
            raise ValueError("ResearchPredictionPlan does not belong to the InitialExperimentPlan design intent")
        if prediction_plan.candidate_id != experiment_plan.candidate_id:
            raise ValueError("ResearchPredictionPlan does not belong to the InitialExperimentPlan candidate")
        if prediction_plan.independent_variable != experiment_plan.independent_variable:
            raise ValueError("ResearchPredictionPlan independent variable does not match the InitialExperimentPlan")
        predicted_outcomes = {item.outcome for item in prediction_plan.predictions}
        if predicted_outcomes != set(experiment_plan.dependent_outcomes):
            raise ValueError(
                "ResearchPredictionPlan predicted outcomes do not match the InitialExperimentPlan dependent outcomes"
            )
        if contrast_result.plan_id != experiment_plan.id:
            raise ValueError("ParameterSensitivityContrastResult does not belong to the requested InitialExperimentPlan")
        if contrast_result.independent_variable != experiment_plan.independent_variable:
            raise ValueError("Contrast independent variable does not match the InitialExperimentPlan")
        if contrast_result.baseline_condition_id != experiment_plan.ordered_conditions[0].id:
            raise ValueError("Contrast baseline condition does not match the InitialExperimentPlan baseline")
        if contrast_result.comparator_condition_id != experiment_plan.ordered_conditions[1].id:
            raise ValueError("Contrast comparator condition does not match the InitialExperimentPlan comparator")
        if contrast_result.comparator_parameter_value <= contrast_result.baseline_parameter_value:
            raise ValueError(
                "Contrast comparator parameter value must be greater than the baseline value for "
                "directional verdict evaluation"
            )

    def _compute_verdict(
        self,
        *,
        prediction_plan: ResearchPredictionPlan,
        experiment_plan,
        contrast_result: ParameterSensitivityContrastResult,
    ) -> ScientificVerdict:
        outcomes_by_name = {item.outcome: item for item in contrast_result.outcomes}
        per_outcome: list[OutcomeScientificVerdict] = []
        saw_indeterminate = False

        for prediction in prediction_plan.predictions:
            outcome = outcomes_by_name.get(prediction.outcome)
            if outcome is None:
                saw_indeterminate = True
                per_outcome.append(
                    OutcomeScientificVerdict(
                        outcome=prediction.outcome,
                        expected_direction=prediction.expected_direction,
                        observed_direction=None,
                        baseline_value=None,
                        comparator_value=None,
                        delta=None,
                        result=PredictionVerdictResult.INDETERMINATE,
                    )
                )
                continue

            observed_direction = _direction_from_contrast(outcome)
            result = (
                PredictionVerdictResult.PASS
                if observed_direction == prediction.expected_direction
                else PredictionVerdictResult.FAIL
            )
            per_outcome.append(
                OutcomeScientificVerdict(
                    outcome=prediction.outcome,
                    expected_direction=prediction.expected_direction,
                    observed_direction=observed_direction,
                    baseline_value=outcome.baseline_value,
                    comparator_value=outcome.comparator_value,
                    delta=outcome.delta,
                    result=result,
                )
            )

        if saw_indeterminate:
            overall_status = ScientificVerdictStatus.INDETERMINATE
        elif all(item.result == PredictionVerdictResult.PASS for item in per_outcome):
            overall_status = ScientificVerdictStatus.SUPPORTED
        else:
            overall_status = ScientificVerdictStatus.FALSIFIED

        return ScientificVerdict(
            id=new_id(),
            prediction_plan_id=prediction_plan.id,
            design_intent_id=prediction_plan.design_intent_id,
            experiment_plan_id=experiment_plan.id,
            contrast_result_id=contrast_result.id,
            verdict_policy_version=self._policy.version,
            verdict_policy_fingerprint=self._policy.fingerprint(),
            overall_status=overall_status,
            per_outcome_verdicts=tuple(per_outcome),
        )


def _direction_from_contrast(outcome: OutcomeContrast) -> ExpectedDirection:
    if outcome.delta > 0:
        return ExpectedDirection.INCREASE
    if outcome.delta < 0:
        return ExpectedDirection.DECREASE
    return ExpectedDirection.NO_CHANGE


def _require_present(value, message: str):
    if value is None:
        raise KeyError(message)
    return value
