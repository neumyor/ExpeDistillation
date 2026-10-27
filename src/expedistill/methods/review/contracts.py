"""The review driver's data hand-off shapes (layer 6).

``ReviewContext`` carries the facts that cannot be derived from the
acceptance report or the skill bank: the sequence position's remaining
checks, the live round, the delivery method and the named rules.  The
review report itself reuses ``PhaseReport``; a review item reuses
``TaskListRow`` and the existing evidence packages.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Set, Union

from expedistill.composites.evidence_package import EvidencePackage
from expedistill.composites.skill_attempt_evidence import SkillAttemptEvidence
from expedistill.core.contracts import TaskListRow

__all__ = ["ReviewContext", "ReviewItem", "ReviewResult"]


@dataclass(frozen=True)
class ReviewContext:
    """The non-data inputs a review needs."""

    phase_kind: str          # "baseline" / "live" / "exam"
    phase_label: str
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
    workers: int = 1


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
