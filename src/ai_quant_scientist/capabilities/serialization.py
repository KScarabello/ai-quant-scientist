"""Lossless, deterministic JSON serialization for capabilities domain objects.

Used by SQLiteStore to persist and reconstruct ResearchCandidate and
ResearchFeasibilityDecision without loss of type information or field values.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from .models import (
    AssetClass,
    DataKind,
    DataRequirement,
    FeasibilityReasonCode,
    FeasibilityResult,
    Resolution,
    ToolKind,
    ToolRequirement,
    AnyRequirement,
)


def _resolution_from_value(v: str) -> Resolution:
    for r in Resolution:
        if r.value == v:
            return r
    raise ValueError(f"Unknown Resolution value: {v!r}")


# ─── requirements ─────────────────────────────────────────────────────────────

def requirements_to_json(requirements: tuple[AnyRequirement, ...]) -> str:
    """Deterministic JSON string preserving DataRequirement vs ToolRequirement distinction."""
    items: list[dict[str, Any]] = []
    for req in requirements:
        if isinstance(req, DataRequirement):
            items.append({
                "type": "DataRequirement",
                "requirement_id": req.requirement_id,
                "data_kind": req.data_kind.value,
                "label": req.label,
                "asset_class": req.asset_class.value if req.asset_class is not None else None,
                "instruments": sorted(req.instruments) if req.instruments is not None else None,
                "resolution": req.resolution.value if req.resolution is not None else None,
                "required_fields": sorted(req.required_fields) if req.required_fields is not None else None,
                "start_date": req.start_date.isoformat() if req.start_date is not None else None,
                "end_date": req.end_date.isoformat() if req.end_date is not None else None,
                "point_in_time_required": req.point_in_time_required,
                "required_parameters": sorted(req.required_parameters) if req.required_parameters is not None else None,
            })
        elif isinstance(req, ToolRequirement):
            if req.tool_kind is not None:
                items.append({
                    "type": "ToolRequirement",
                    "requirement_id": req.requirement_id,
                    "tool_kind": req.tool_kind.value,
                    "label": req.label,
                })
            else:
                items.append({
                    "type": "ToolRequirement",
                    "requirement_id": req.requirement_id,
                    "tool_name": req.tool_name,
                    "label": req.label,
                })
        else:
            raise TypeError(f"Unknown requirement type: {type(req)}")
    return json.dumps(items, sort_keys=True, separators=(",", ":"))


def requirements_from_json(s: str) -> tuple[AnyRequirement, ...]:
    """Reconstruct requirements from JSON string (fail closed on unknown types)."""
    result: list[AnyRequirement] = []
    for d in json.loads(s):
        req_type = d.get("type")
        if req_type == "DataRequirement":
            result.append(DataRequirement(
                requirement_id=d["requirement_id"],
                data_kind=DataKind[d["data_kind"]],
                label=d.get("label", ""),
                asset_class=AssetClass[d["asset_class"]] if d.get("asset_class") else None,
                instruments=tuple(d["instruments"]) if d.get("instruments") is not None else None,
                resolution=_resolution_from_value(d["resolution"]) if d.get("resolution") else None,
                required_fields=tuple(d["required_fields"]) if d.get("required_fields") is not None else None,
                start_date=date.fromisoformat(d["start_date"]) if d.get("start_date") else None,
                end_date=date.fromisoformat(d["end_date"]) if d.get("end_date") else None,
                point_in_time_required=d.get("point_in_time_required", False),
                required_parameters=tuple(d["required_parameters"]) if d.get("required_parameters") is not None else None,
            ))
        elif req_type == "ToolRequirement":
            if d.get("tool_kind"):
                result.append(ToolRequirement(
                    requirement_id=d["requirement_id"],
                    tool_kind=ToolKind(d["tool_kind"]),
                    label=d.get("label", ""),
                ))
            else:
                result.append(ToolRequirement(
                    requirement_id=d["requirement_id"],
                    legacy_tool_name=d["tool_name"],
                    label=d.get("label", ""),
                ))
        else:
            raise ValueError(f"Unknown requirement type in persisted JSON: {req_type!r}")
    return tuple(result)


# ─── feasibility result snapshot ──────────────────────────────────────────────

def feasibility_result_to_dict(result: FeasibilityResult) -> dict:
    """JSON-stable snapshot of FeasibilityResult for historical audit.

    Persists capability_id of the matched capability so decisions remain
    interpretable even after the registry changes.
    """
    req_results = [
        {
            "requirement_id": rr.requirement.requirement_id,
            "requirement_type": type(rr.requirement).__name__,
            "satisfied": rr.satisfied,
            "matched_capability_id": rr.matched_capability.capability_id if rr.matched_capability else None,
            "reason_codes": [r.value for r in rr.reason_codes],
            "notes": rr.notes,
        }
        for rr in result.requirement_results
    ]
    return {
        "status": result.status.value,
        "satisfied_ids": list(result.satisfied_ids),
        "unsatisfied_ids": list(result.unsatisfied_ids),
        "reason_codes": [r.value for r in result.reason_codes],
        "registry_version": result.registry_version,
        "registry_fingerprint": result.registry_fingerprint,
        "requirement_results": req_results,
    }


# ─── candidate fingerprint ────────────────────────────────────────────────────

def compute_candidate_fingerprint(hypothesis_statement: str, hypothesis_rationale: str,
                                   requirements: tuple[AnyRequirement, ...]) -> str:
    """SHA-256 over canonical scientific content only.

    Excludes id, created_at, source — those are incidental to the science.
    Same hypothesis + rationale + requirements → same fingerprint.
    """
    canon = json.dumps({
        "hypothesis_statement": hypothesis_statement,
        "hypothesis_rationale": hypothesis_rationale,
        "requirements": json.loads(requirements_to_json(requirements)),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()
