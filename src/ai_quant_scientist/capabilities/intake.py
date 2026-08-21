"""Governed Research Intake service (V0.11).

This is the durable, auditable boundary between candidate proposals
and ResearchSpec construction.

Future Hypothesis Scientist → ResearchCandidate → GovernedResearchIntake
    → persist candidate
    → evaluate via ResearchFeasibilityGate
    → persist FeasibilityDecision
    → READY_FOR_SPEC or BLOCKED_CAPABILITY (never auto-creates a spec)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING
from uuid import uuid4

from .gate import GATE_VERSION, GateDecision, ResearchCandidate, ResearchFeasibilityDecision, ResearchFeasibilityGate
from .models import FeasibilityReasonCode
from .registry import CapabilityRegistry
from .serialization import (
    compute_candidate_fingerprint,
    feasibility_result_to_dict,
    requirements_from_json,
    requirements_to_json,
)

if TYPE_CHECKING:
    pass


def _new_id() -> str:
    return str(uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ─── Stored decision ──────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class StoredFeasibilityDecision:
    """A persisted feasibility evaluation — historical, immutable evidence.

    The snapshot remains interpretable even after registry_v1 changes.
    """
    id: str
    candidate_id: str
    gate_decision: GateDecision
    gate_version: str
    registry_version: str
    registry_fingerprint: str
    satisfied_ids: tuple[str, ...]
    unsatisfied_ids: tuple[str, ...]
    reason_codes: tuple[FeasibilityReasonCode, ...]
    feasibility_snapshot: dict   # full JSON snapshot for historical audit
    evaluated_at: datetime


# ─── Intake result ────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class IntakeResult:
    """Result of a GovernedResearchIntake.submit() or .re_evaluate() call."""
    candidate: ResearchCandidate
    feasibility_decision: ResearchFeasibilityDecision
    is_ready: bool      # True → READY_FOR_SPEC
    is_blocked: bool    # True → BLOCKED_CAPABILITY


# ─── GovernedResearchIntake ───────────────────────────────────────────────────

class GovernedResearchIntake:
    """Durable, auditable entry point for research candidates.

    The production/autonomous path must use this service.
    Low-level constructors (SQLiteStore, Orchestrator) remain available for
    unit tests and internal tooling, but this class is the governed entry point.

    BLOCKED_CAPABILITY does not create a ResearchSpec or a ResearchRun.
    AI cannot override feasibility decisions.
    """

    def __init__(self, store, registry: CapabilityRegistry) -> None:
        self._store = store
        self._registry = registry
        self._gate = ResearchFeasibilityGate()

    def submit(self, candidate: ResearchCandidate) -> IntakeResult:
        """Persist a candidate, evaluate feasibility, persist the decision.

        Idempotent for the candidate itself (same id → no duplicate row).
        Each call always produces a new feasibility decision for the audit trail.
        """
        self._store.save_research_candidate(candidate)
        gate_decision = self._gate.evaluate(candidate, self._registry)
        self._store.save_feasibility_decision(gate_decision)
        return IntakeResult(
            candidate=candidate,
            feasibility_decision=gate_decision,
            is_ready=gate_decision.is_ready,
            is_blocked=gate_decision.is_blocked,
        )

    def re_evaluate(self, candidate_id: str) -> IntakeResult:
        """Load an existing candidate and re-evaluate against the current registry.

        Adds a new feasibility decision to the history without overwriting prior ones.
        Raises KeyError if candidate_id is not found.
        """
        candidate = self._store.get_research_candidate(candidate_id)
        if candidate is None:
            raise KeyError(f"Research candidate not found: {candidate_id!r}")
        gate_decision = self._gate.evaluate(candidate, self._registry)
        self._store.save_feasibility_decision(gate_decision)
        return IntakeResult(
            candidate=candidate,
            feasibility_decision=gate_decision,
            is_ready=gate_decision.is_ready,
            is_blocked=gate_decision.is_blocked,
        )
