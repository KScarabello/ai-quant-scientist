"""Tool protocol used by the orchestrator."""

from __future__ import annotations

from typing import Protocol

from ..models.research import ExperimentResult, ResearchSpec


class ResearchTool(Protocol):
    """Deterministic scientific tooling interface."""

    name: str

    def run(self, *, spec: ResearchSpec, attempt_id: str) -> ExperimentResult:
        """Execute the tool against a frozen spec and return a structured result."""
