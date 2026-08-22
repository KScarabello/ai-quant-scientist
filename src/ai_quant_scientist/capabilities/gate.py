"""ResearchCandidate and deterministic candidate-feasibility gate (V0.10).

Policy version: research_feasibility_gate_v1

The future Hypothesis Scientist will produce ResearchCandidate objects.
The gate deterministically decides whether the candidate can proceed to Spec creation.
It does not validate exact frozen-spec executability.

Same candidate + same registry → same logical gate decision.
Makes no LLM calls. AI cannot override feasibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from .models import AnyRequirement, DataRequirement, FeasibilityResult, ToolRequirement
from .registry import CapabilityRegistry

GATE_VERSION = "research_feasibility_gate_v1"


def _new_id() -> str:
    return str(uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ─── GateDecision vocabulary ─────────────────────────────────────────────────

class GateDecision(str, Enum):
    READY_FOR_SPEC = "READY_FOR_SPEC"
    # Hypothesis may be scientifically valid; current capabilities are insufficient.
    # Does NOT mean the hypothesis is rejected.
    BLOCKED_CAPABILITY = "BLOCKED_CAPABILITY"


# ─── ResearchCandidate ───────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ResearchCandidate:
    """A scientifically proposed piece of research awaiting feasibility authorization.

    Pre-spec.  Requirements must be explicitly supplied — not inferred from prose.
    Future: produced by the Hypothesis Scientist.
    Current: constructed manually in tests and CLI.
    """
    id: str
    hypothesis_statement: str
    hypothesis_rationale: str
    requirements: tuple[AnyRequirement, ...]
    # Provenance: who/what produced this candidate
    source: str = "manual"
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if not self.hypothesis_statement or not self.hypothesis_statement.strip():
            raise ValueError("ResearchCandidate requires non-empty hypothesis_statement")
        if not self.requirements:
            raise ValueError(
                "ResearchCandidate requires explicit requirements — "
                "requirements must not be empty; they are not inferred from prose"
            )

    @classmethod
    def create(
        cls,
        hypothesis_statement: str,
        hypothesis_rationale: str,
        requirements: list[AnyRequirement],
        source: str = "manual",
    ) -> "ResearchCandidate":
        return cls(
            id=_new_id(),
            hypothesis_statement=hypothesis_statement,
            hypothesis_rationale=hypothesis_rationale,
            requirements=tuple(requirements),
            source=source,
        )


# ─── ResearchFeasibilityDecision ─────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ResearchFeasibilityDecision:
    """Structured gate verdict for a ResearchCandidate.

    Records registry provenance so the decision is auditable even after registry changes.
    BLOCKED_CAPABILITY does not mean the hypothesis is scientifically invalid.
    It means the current system cannot test it.
    READY_FOR_SPEC means broad prerequisites are present so deterministic design may begin.
    """
    candidate_id: str
    decision: GateDecision
    feasibility_result: FeasibilityResult
    gate_version: str
    registry_version: str
    registry_fingerprint: str
    evaluated_at: datetime = field(default_factory=_utcnow)
    # auto-generated DB identity; last field so it does not break positional construction
    id: str = field(default_factory=_new_id)

    @property
    def is_ready(self) -> bool:
        return self.decision == GateDecision.READY_FOR_SPEC

    @property
    def is_blocked(self) -> bool:
        return self.decision == GateDecision.BLOCKED_CAPABILITY


# ─── ResearchFeasibilityGate ─────────────────────────────────────────────────

class ResearchFeasibilityGate:
    """Deterministic candidate-feasibility gate — the boundary between candidate and spec.

    AI cannot override its decisions.
    Same candidate + registry + gate policy → same logical decision.
    Exact implementation compatibility is deferred until a future frozen-spec stage.
    """

    def evaluate(
        self,
        candidate: ResearchCandidate,
        registry: CapabilityRegistry,
    ) -> ResearchFeasibilityDecision:
        """Evaluate a ResearchCandidate against a CapabilityRegistry.

        Returns READY_FOR_SPEC if all broad candidate requirements are satisfied.
        Returns BLOCKED_CAPABILITY if any requirement is not met.
        Never constructs a ResearchSpec.  Never authorizes execution.  Never calls an LLM.
        """
        feasibility = registry.evaluate(list(candidate.requirements))

        from .models import FeasibilityStatus
        decision = (
            GateDecision.READY_FOR_SPEC
            if feasibility.status == FeasibilityStatus.TESTABLE
            else GateDecision.BLOCKED_CAPABILITY
        )

        return ResearchFeasibilityDecision(
            candidate_id=candidate.id,
            decision=decision,
            feasibility_result=feasibility,
            gate_version=GATE_VERSION,
            registry_version=registry.version,
            registry_fingerprint=registry.fingerprint,
        )
