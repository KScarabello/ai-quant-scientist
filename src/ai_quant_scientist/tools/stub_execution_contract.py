"""Authoritative deterministic execution-parameter contract for the stub backtester."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class StubParameterDefinition:
    name: str
    type_name: str
    required: bool = True


STUB_PARAMETER_DEFINITIONS: tuple[StubParameterDefinition, ...] = (
    StubParameterDefinition(name="signal_threshold", type_name="numeric"),
    StubParameterDefinition(name="lookback", type_name="integer"),
)


def supported_parameter_names() -> tuple[str, ...]:
    return tuple(definition.name for definition in STUB_PARAMETER_DEFINITIONS)


def validate_stub_execution_parameters(parameters: Mapping[str, Any]) -> list[str]:
    """Return deterministic validation notes for the stub execution payload."""
    required = {definition.name for definition in STUB_PARAMETER_DEFINITIONS if definition.required}
    allowed = set(supported_parameter_names())
    keys = set(parameters.keys())

    notes: list[str] = []
    missing = sorted(required - keys)
    extra = sorted(keys - allowed)
    if missing:
        notes.append(f"missing:{missing}")
    if extra:
        notes.append(f"unsupported:{extra}")

    if "signal_threshold" in parameters:
        threshold = parameters["signal_threshold"]
        if not (isinstance(threshold, (int, float)) and not isinstance(threshold, bool)):
            notes.append("type:signal_threshold:numeric")

    if "lookback" in parameters:
        lookback = parameters["lookback"]
        if not (isinstance(lookback, int) and not isinstance(lookback, bool)):
            notes.append("type:lookback:integer")

    return notes
