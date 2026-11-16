"""Provenance value objects: where a skill version comes from.

``TraceRef`` names one exact place in one stored execution: a phase, one
answer sheet and a step index. Nothing else is stored -- task, attempt,
trajectory and the step count are all derivable from the sheet, and storing
a unit id instead is what once let three attempts of one task share a
pointer. ``composites.trace.trace_at`` is the only constructor callers use
(it needs the sheet object and checks the step), so an ambiguous pointer
cannot be written; this module only validates the shape.

``Provenance`` is one row per skill version: which learn phase produced it,
why (``cause``), and the evidence positions behind it (``sources``).

Standard library only (layer 0).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List

__all__ = [
    "PROVENANCE_CAUSES",
    "Provenance",
    "TraceRef",
]

#: Why a skill version exists; the vocabulary the ledger accepts: a new
#: card for a gap, or a one-side rewrite driven by trigger or content
#: evidence.
PROVENANCE_CAUSES = ("add", "revise_trigger", "revise_content")


@dataclass(frozen=True)
class TraceRef:
    """One exact location: an answer sheet of an execution phase and a step."""

    phase_label: str
    sheet_id: str
    step: int

    def __post_init__(self) -> None:
        if not isinstance(self.phase_label, str) or not self.phase_label:
            raise ValueError(
                f"TraceRef.phase_label must be a non-empty string, got "
                f"{self.phase_label!r}")
        if not isinstance(self.sheet_id, str) or not self.sheet_id:
            raise ValueError(
                f"TraceRef.sheet_id must be a non-empty string, got "
                f"{self.sheet_id!r}")
        if isinstance(self.step, bool) or not isinstance(self.step, int) \
                or self.step < 0:
            raise ValueError(
                f"TraceRef.step must be a non-negative int, got "
                f"{self.step!r}")

    def to_dict(self) -> Dict[str, Any]:
        return {"phase_label": self.phase_label, "sheet_id": self.sheet_id,
                "step": self.step}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TraceRef":
        if not isinstance(data, dict):
            raise ValueError(f"a TraceRef is a mapping, got {data!r}")
        missing = [key for key in ("phase_label", "sheet_id", "step")
                   if key not in data]
        if missing:
            raise ValueError(
                f"TraceRef record is missing {missing}; got keys "
                f"{sorted(data)}")
        return cls(phase_label=data["phase_label"],
                   sheet_id=data["sheet_id"], step=data["step"])


@dataclass(frozen=True)
class Provenance:
    """The origin of one skill version (one ledger row)."""

    skill_id: str
    version: int
    phase_label: str
    cause: str
    sources: List[TraceRef] = field(default_factory=list)
    note: str = ""

    def __post_init__(self) -> None:
        if self.cause not in PROVENANCE_CAUSES:
            raise ValueError(
                f"Provenance.cause must be one of {PROVENANCE_CAUSES}, got "
                f"{self.cause!r}")
        if isinstance(self.version, bool) or not isinstance(
                self.version, int) or self.version < 1:
            raise ValueError(
                f"Provenance.version must be an int >= 1, got "
                f"{self.version!r}")
        if not all(isinstance(source, TraceRef) for source in self.sources):
            raise ValueError(
                f"Provenance of {self.skill_id!r} v{self.version}: every "
                f"source must be a TraceRef, got {self.sources!r}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "phase_label": self.phase_label,
            "cause": self.cause,
            "sources": [source.to_dict() for source in self.sources],
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Provenance":
        return cls(
            skill_id=data["skill_id"],
            version=data["version"],
            phase_label=data["phase_label"],
            cause=data["cause"],
            sources=[TraceRef.from_dict(item)
                     for item in data.get("sources") or []],
            note=data.get("note", ""))
