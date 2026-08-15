"""Deterministic spec builder for frozen research specifications."""

from __future__ import annotations

from dataclasses import dataclass

from ..models.research import Hypothesis, ResearchSpec, new_id, utcnow


@dataclass(frozen=True, slots=True)
class SpecBuilder:
    """Convert a user-supplied hypothesis and parameters into a frozen spec."""

    def build(
        self,
        *,
        research_run_id: str,
        hypothesis: Hypothesis,
        parameters: dict[str, object],
        version: int = 1,
        parent_spec_id: str | None = None,
        revision_proposal_id: str | None = None,
    ) -> ResearchSpec:
        return ResearchSpec(
            id=new_id(),
            research_run_id=research_run_id,
            version=version,
            hypothesis_id=hypothesis.id,
            parameters=dict(parameters),
            parent_spec_id=parent_spec_id,
            revision_proposal_id=revision_proposal_id,
            created_at=utcnow(),
            frozen_at=utcnow(),
            is_frozen=True,
        )
