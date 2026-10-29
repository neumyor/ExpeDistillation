"""Provenance value objects: where a skill version and its exam come from.

``TraceRef`` names one exact place in one stored execution: a phase, one
answer sheet and a step index. Nothing else is stored -- task, attempt,
trajectory and the step count are all derivable from the sheet, and storing
a unit id instead is what once let three attempts of one task share a
pointer. ``composites.trace.trace_at`` is the only constructor callers use
(it needs the sheet object and checks the step), so an ambiguous pointer
cannot be written; this module only validates the shape.

``Provenance`` is one row per skill version: which learn phase produced it,
why, where an exam of it should be cut (``scene``) and the evidence behind
it (``sources``, audit only). ``TraceContext`` is what ``resolve`` returns:
the rendered text a prompt may read about a ``TraceRef``.

Standard library only (layer 0).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = [
    "PROVENANCE_CAUSES",
    "Provenance",
    "TraceContext",
    "TraceRef",
]

#: Why a skill version exists; the vocabulary the ledger accepts.
PROVENANCE_CAUSES = ("add", "revise", "requirement", "merge")


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
    scene: Optional[TraceRef]
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
        if self.scene is not None and not isinstance(self.scene, TraceRef):
            raise ValueError("Provenance.scene must be a TraceRef or None")
        if self.cause != "merge" and self.scene is None:
            raise ValueError(
                f"Provenance of {self.skill_id!r} v{self.version} "
                f"(cause={self.cause!r}) needs a scene: only a merge "
                "product is exempt from an exam")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "phase_label": self.phase_label,
            "cause": self.cause,
            "scene": self.scene.to_dict() if self.scene else None,
            "sources": [source.to_dict() for source in self.sources],
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Provenance":
        scene = data.get("scene")
        return cls(
            skill_id=data["skill_id"],
            version=data["version"],
            phase_label=data["phase_label"],
            cause=data["cause"],
            scene=TraceRef.from_dict(scene) if scene is not None else None,
            sources=[TraceRef.from_dict(item)
                     for item in data.get("sources") or []],
            note=data.get("note", ""))


@dataclass(frozen=True)
class TraceContext:
    """The text a prompt may read about a ``TraceRef`` (from ``resolve``)."""

    ref: TraceRef
    task_ref: str
    task_instruction: str
    before: str
    after: str
    outcome: Optional[bool]
