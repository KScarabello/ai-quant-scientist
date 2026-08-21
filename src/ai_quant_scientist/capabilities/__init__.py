"""Capabilities & Data Requirements package (V0.9/V0.10)."""
from .models import (
    AnyRequirement,
    AssetClass,
    Capability,
    DataKind,
    DataRequirement,
    FeasibilityReasonCode,
    FeasibilityResult,
    FeasibilityStatus,
    RequirementResult,
    Resolution,
    ToolRequirement,
    compute_registry_fingerprint,
)
from .registry import CapabilityRegistry, REGISTRY_VERSION
from .v1_registry import DEFAULT_REGISTRY, build_v1_registry
from .gate import (
    GATE_VERSION,
    GateDecision,
    ResearchCandidate,
    ResearchFeasibilityDecision,
    ResearchFeasibilityGate,
)
from .intake import (
    GovernedResearchIntake,
    IntakeResult,
    StoredFeasibilityDecision,
)
from .serialization import (
    compute_candidate_fingerprint,
    feasibility_result_to_dict,
    requirements_from_json,
    requirements_to_json,
)

__all__ = [
    "AnyRequirement",
    "AssetClass",
    "Capability",
    "CapabilityRegistry",
    "DataKind",
    "DataRequirement",
    "DEFAULT_REGISTRY",
    "FeasibilityReasonCode",
    "FeasibilityResult",
    "FeasibilityStatus",
    "GATE_VERSION",
    "GateDecision",
    "REGISTRY_VERSION",
    "RequirementResult",
    "ResearchCandidate",
    "ResearchFeasibilityDecision",
    "ResearchFeasibilityGate",
    "Resolution",
    "ToolRequirement",
    "build_v1_registry",
    "compute_registry_fingerprint",
]
