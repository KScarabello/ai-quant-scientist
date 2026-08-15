"""Enumerations for research stages and run status."""

from __future__ import annotations

from enum import Enum


class ResearchStage(str, Enum):
    IDEA = "IDEA"
    DISCOVERY = "DISCOVERY"
    REPLICATION = "REPLICATION"
    HOLDOUT = "HOLDOUT"
    PAPER = "PAPER"
    SHADOW_LIVE = "SHADOW_LIVE"
    TINY_CAPITAL = "TINY_CAPITAL"
    PRODUCTION = "PRODUCTION"
    REJECTED = "REJECTED"


class RunStatus(str, Enum):
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"


class ResearchAction(str, Enum):
    NONE = "NONE"
    REVISION_REQUIRED = "REVISION_REQUIRED"


class SpecRevisionProposalStatus(str, Enum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
