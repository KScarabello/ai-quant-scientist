"""Deterministic Revision Planner — resolves RevisionIntent to an exact parameter value.

Policy version: revision_planner_v1

AI decides scientific intent.
This planner decides the exact execution value.

Same inputs (intent + current spec + constraints + lineage) always produce the same output.
No LLM calls. No randomness. Fail closed.
"""
from __future__ import annotations

from typing import Any

from ai_quant_scientist.models.revision import (
    ExperimentType,
    PlannerResult,
    RevisionDirection,
    RevisionIntent,
)

PLANNER_VERSION = "revision_planner_v1"

_MAX_CANDIDATES = 2000  # safety ceiling to prevent infinite loops


class PlannerRejectionError(Exception):
    """The planner could not materialize a legal non-redundant experiment.

    This is a domain outcome, not an infrastructure failure.
    It is distinct from NO_USEFUL_REVISION (which is a scientific AI decision).
    """
    def __init__(self, reason: str, result: PlannerResult):
        super().__init__(reason)
        self.reason = reason
        self.result = result


class RevisionPlanner:
    """Deterministic single-step revision planner (V1 policy).

    V1 policy: smallest informative legal perturbation in the requested direction.
    Generates a candidate grid from constraints, filters by lineage, selects nearest.
    """

    def _generate_candidates(
        self,
        current_value: Any,
        direction: RevisionDirection,
        constraint: dict,
    ) -> tuple[list[Any], str | None]:
        """Return (ordered_candidates, error_reason). Candidates empty on error."""
        c_type = constraint.get("type")
        step = constraint.get("step")
        min_val = constraint.get("min")
        max_val = constraint.get("max")

        if c_type == "float":
            if step is None:
                # Architectural gap: continuous parameter with no step policy.
                return [], "no_step_policy_for_continuous_parameter"
            step_f = float(step)
            if step_f <= 0:
                return [], "invalid_step_must_be_positive"
            cur = float(current_value)

            if direction == RevisionDirection.INCREASE:
                candidates: list[float] = []
                v = round(cur + step_f, 10)
                while (max_val is None or v <= max_val) and len(candidates) < _MAX_CANDIDATES:
                    candidates.append(round(v, 10))
                    v = round(v + step_f, 10)
                return candidates, None

            if direction == RevisionDirection.DECREASE:
                candidates = []
                v = round(cur - step_f, 10)
                while (min_val is None or v >= min_val) and len(candidates) < _MAX_CANDIDATES:
                    candidates.append(round(v, 10))
                    v = round(v - step_f, 10)
                return candidates, None

            # PERTURB — interleave nearest first (prefer INCREASE on tie)
            up: list[float] = []
            v = round(cur + step_f, 10)
            while (max_val is None or v <= max_val) and len(up) < _MAX_CANDIDATES // 2:
                up.append(round(v, 10)); v = round(v + step_f, 10)
            down: list[float] = []
            v = round(cur - step_f, 10)
            while (min_val is None or v >= min_val) and len(down) < _MAX_CANDIDATES // 2:
                down.append(round(v, 10)); v = round(v - step_f, 10)
            return self._interleave_nearest(up, down, cur), None

        if c_type == "int":
            step_i = int(step) if step is not None else 1
            if step_i <= 0:
                return [], "invalid_step_must_be_positive"
            cur_i = int(current_value)
            min_i = int(min_val) if min_val is not None else None
            max_i = int(max_val) if max_val is not None else None

            if direction == RevisionDirection.INCREASE:
                candidates_i: list[int] = []
                v_i = cur_i + step_i
                while (max_i is None or v_i <= max_i) and len(candidates_i) < _MAX_CANDIDATES:
                    candidates_i.append(v_i); v_i += step_i
                return candidates_i, None

            if direction == RevisionDirection.DECREASE:
                candidates_i = []
                v_i = cur_i - step_i
                while (min_i is None or v_i >= min_i) and len(candidates_i) < _MAX_CANDIDATES:
                    candidates_i.append(v_i); v_i -= step_i
                return candidates_i, None

            # PERTURB
            up_i: list[int] = []
            v_i = cur_i + step_i
            while (max_i is None or v_i <= max_i) and len(up_i) < _MAX_CANDIDATES // 2:
                up_i.append(v_i); v_i += step_i
            down_i: list[int] = []
            v_i = cur_i - step_i
            while (min_i is None or v_i >= min_i) and len(down_i) < _MAX_CANDIDATES // 2:
                down_i.append(v_i); v_i -= step_i
            return self._interleave_nearest(up_i, down_i, cur_i), None

        return [], f"unsupported_parameter_type: {c_type}"

    @staticmethod
    def _interleave_nearest(up: list, down: list, current: Any) -> list:
        """Merge two ascending-from-current lists, nearest first (INCREASE preferred on tie)."""
        result: list = []
        ui, di = 0, 0
        while ui < len(up) or di < len(down):
            pick_up = ui < len(up) and (
                di >= len(down)
                or abs(up[ui] - current) <= abs(down[di] - current)
            )
            if pick_up:
                result.append(up[ui]); ui += 1
            else:
                result.append(down[di]); di += 1
        return result

    @staticmethod
    def _is_tested(candidate_params: dict, lineage: list[dict], current_params: dict) -> bool:
        if candidate_params == current_params:
            return True
        for prior in lineage:
            if prior.get("parameters") == candidate_params:
                return True
        return False

    def plan(
        self,
        intent: RevisionIntent,
        current_spec: dict[str, Any],
        constraints: dict[str, Any],
        lineage: list[dict[str, Any]],
    ) -> PlannerResult:
        """Resolve intent to an exact planned change. Deterministic. No API calls.

        Returns PlannerResult with planned_change={param: value} on success,
        or planned_change=None and rejection_reason set on failure.
        """
        param = intent.parameter
        constraint = constraints.get(param)

        if constraint is None:
            return PlannerResult(
                intent_id=intent.id,
                planned_change=None,
                rejection_reason=f"parameter_not_in_constraints: {param}",
                planner_version=PLANNER_VERSION,
                candidates_considered=[],
                tested_values_skipped=[],
                selected_value=None,
            )

        current_params = current_spec.get("parameters", {})
        current_value = current_params.get(param)

        if current_value is None:
            return PlannerResult(
                intent_id=intent.id,
                planned_change=None,
                rejection_reason=f"parameter_not_in_current_spec: {param}",
                planner_version=PLANNER_VERSION,
                candidates_considered=[],
                tested_values_skipped=[],
                selected_value=None,
            )

        candidates, error = self._generate_candidates(current_value, intent.direction, constraint)
        if error:
            return PlannerResult(
                intent_id=intent.id,
                planned_change=None,
                rejection_reason=error,
                planner_version=PLANNER_VERSION,
                candidates_considered=[],
                tested_values_skipped=[],
                selected_value=None,
            )

        tested_skipped: list = []
        for candidate in candidates:
            candidate_params = {**current_params, param: candidate}
            if self._is_tested(candidate_params, lineage, current_params):
                tested_skipped.append(candidate)
                continue
            # First untested legal candidate — deterministic selection
            return PlannerResult(
                intent_id=intent.id,
                planned_change={param: candidate},
                rejection_reason=None,
                planner_version=PLANNER_VERSION,
                candidates_considered=candidates,
                tested_values_skipped=tested_skipped,
                selected_value=candidate,
            )

        return PlannerResult(
            intent_id=intent.id,
            planned_change=None,
            rejection_reason="no_legal_untested_candidate",
            planner_version=PLANNER_VERSION,
            candidates_considered=candidates,
            tested_values_skipped=tested_skipped,
            selected_value=None,
        )
