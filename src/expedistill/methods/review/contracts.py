"""The review driver's data hand-off shapes (layer 6).

``ReviewContext`` carries the facts that cannot be derived from the
acceptance report or the skill bank: the sequence position's remaining
checks, the live round, the delivery method and the named rules.  The
review report itself reuses ``PhaseReport``; a review item reuses
``TaskListRow`` and the existing evidence packages.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Union

from expedistill.composites.evidence_package import EvidencePackage
from expedistill.composites.skill_attempt_evidence import (
    SkillAttemptEvidence,
    WindowEvidence,
)
from expedistill.core.contracts import TaskListRow

__all__ = [
    "NewSkillRequirement",
    "ReviewContext",
    "ReviewItem",
    "ReviewResult",
    "TrialFinding",
]


@dataclass(frozen=True)
class ReviewContext:
    """The non-data inputs a review needs."""

    phase_kind: str          # "baseline" / "live" / "exam"
    phase_label: str
    source_phase_label: str
    checks_left: int
    round: int
    delivery: str
    compare_rule: str
    n_attempts_weak: int
    benchmark: str
    weak_profile: str
    task_select_rule: str
    trajectory_select: str
    segment_select: str
    exam_control: bool = True
    workers: int = 1


@dataclass
class TrialFinding:
    """One failed trial's free-text diagnosis and its evidence."""

    sheet_id: str
    unit_id: str
    task_ref: str
    reason: str
    skill_ids: List[str] = field(default_factory=list)
    profile_note: str = ""
    new_skill_note: str = ""
    step_start: int = 0
    step_end: int = 0
    evidence: Dict[str, SkillAttemptEvidence] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "sheet_id": self.sheet_id,
            "unit_id": self.unit_id,
            "task_ref": self.task_ref,
            "reason": self.reason,
            "skill_ids": list(self.skill_ids),
            "profile_note": self.profile_note,
            "new_skill_note": self.new_skill_note,
            "step_start": self.step_start,
            "step_end": self.step_end,
            "evidence": {
                skill_id: evidence.to_dict()
                for skill_id, evidence in self.evidence.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "TrialFinding":
        evidence = {}
        for skill_id, raw in (data.get("evidence") or {}).items():
            raw = dict(raw)
            windows = [WindowEvidence(**window) for window in raw.get(
                "windows") or []]
            evidence[skill_id] = SkillAttemptEvidence(
                skill_id=raw.get("skill_id", skill_id),
                skill_version=raw.get("skill_version", 1),
                windows=windows)
        return cls(
            sheet_id=str(data.get("sheet_id", "")),
            unit_id=str(data.get("unit_id", "")),
            task_ref=str(data.get("task_ref", "")),
            reason=str(data.get("reason", "")),
            skill_ids=list(data.get("skill_ids") or []),
            profile_note=str(data.get("profile_note") or ""),
            new_skill_note=str(data.get("new_skill_note") or ""),
            step_start=int(data.get("step_start", 0) or 0),
            step_end=int(data.get("step_end", 0) or 0),
            evidence=evidence)


@dataclass
class NewSkillRequirement:
    """One aggregated capability gap; it is independent of task and trial."""

    requirement_id: str
    title: str
    description: str
    trigger: str
    evidence: EvidencePackage
    source_sheet_ids: List[str] = field(default_factory=list)
    source_phase_label: str = ""


@dataclass
class ReviewItem:
    """One decided row and, when needed, the evidence for writing it."""

    row: TaskListRow
    evidence: Optional[Union[EvidencePackage, SkillAttemptEvidence]] = None


@dataclass
class ReviewResult:
    """The phase's review output, including per-target failure facts."""

    items: List[ReviewItem] = field(default_factory=list)
    failed_targets: Set[str] = field(default_factory=set)
    model_errors: Set[str] = field(default_factory=set)
    superseded_units: List[str] = field(default_factory=list)
    trial_findings: List[TrialFinding] = field(default_factory=list)
    new_skill_requirements: List[NewSkillRequirement] = field(
        default_factory=list)
    profile_update: Optional[str] = None
