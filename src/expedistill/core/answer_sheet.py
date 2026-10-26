"""Trial-level answer sheets and unit-level summary views (layer 0).

The acceptance module grades ONE trial at a time.  An ``AnswerSheet`` is that
trial-level envelope: execution facts (the trajectory or exam answer), the
coverage fact (was this trial expected/present/terminal and why), and the
grading result.  It never carries unit-level counts -- ``UnitSummary`` is the
separate, pure-function view used by decision/audit/test.

Nothing here is a new persisted object kind: the canonical records remain
``Trajectory`` / ``Attempt``; an AnswerSheet is their trial-level view and may
also be exported as a derived (JSON + Markdown) artifact under the phase
directory.
"""

from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Optional

from expedistill.core.models import Step

__all__ = [
    "AnswerSheet",
    "CoverageRecord",
    "ExecutionRecord",
    "GradingRecord",
    "PhaseReport",
    "UnitSpec",
    "UnitSummary",
]


def _known_kwargs(cls: Any, data: Dict[str, Any]) -> Dict[str, Any]:
    """Drop unknown keys (the repo's lenient read policy for new fields)."""
    known = {item.name for item in fields(cls)}
    return {key: value for key, value in data.items() if key in known}


@dataclass
class ExecutionRecord:
    """One trial's execution facts (the answer-sheet payload).

    ``kind`` is ``"trajectory"`` (a ReAct run), ``"attempt"`` (one exam
    answer) or ``"external"`` (one external-campaign reward).  The payload
    fields are mutually exclusive by kind: steps for a trajectory, answer for
    an exam, rewards for an external control.
    """

    sheet_id: str
    unit_id: str
    phase_label: str
    task_ref: str
    role: str
    arm: Optional[str]
    model_role: str
    model_name: str
    sample_index: int
    skills_injected: Any = None
    steps: Optional[List[Step]] = None
    answer: Optional[str] = None
    rewards: Optional[List[float]] = None
    raw_correctness: Optional[bool] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    aux: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    source: str = "local"
    kind: str = "trajectory"
    #: The benchmark-declared auxiliary scorer's verdict for this trial, when
    #: the plugin declares one; None otherwise.
    aux_success: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = dict(self.__dict__)
        payload["steps"] = (
            [(step.to_dict() if hasattr(step, "to_dict") else dict(step))
             for step in self.steps]
            if self.steps is not None else None)
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionRecord":
        payload = _known_kwargs(cls, data)
        steps = payload.get("steps")
        payload["steps"] = (
            [Step.from_dict(step) if isinstance(step, dict) else step
             for step in steps]
            if steps is not None else None)
        return cls(**payload)


@dataclass
class CoverageRecord:
    """One expected trial's coverage fact.

    A present trial has ``present=True`` and ``status="present"``.  A missing
    or terminal trial has ``present=False`` and a cause from the shared
    vocabulary; the execution record is then absent.  Coverage is computed by
    the coverage module, never by grading.
    """

    unit_id: str
    attempt_index: Optional[int]
    present: bool
    status: str
    cause: str = ""
    artifacts: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CoverageRecord":
        return cls(**_known_kwargs(cls, data))


@dataclass
class GradingRecord:
    """One trial's grading result under a named rule."""

    verdict: Optional[str]
    basis: str = ""
    rule_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GradingRecord":
        return cls(**_known_kwargs(cls, data))


@dataclass
class AnswerSheet:
    """The trial-level envelope: execution + coverage + grading.

    ``execution`` is None for a missing/terminal trial; ``grading`` is None
    when the trial was never graded (there was no execution to grade).
    """

    sheet_id: str
    phase_label: str
    coverage: CoverageRecord
    execution: Optional[ExecutionRecord] = None
    grading: Optional[GradingRecord] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sheet_id": self.sheet_id,
            "phase_label": self.phase_label,
            "coverage": self.coverage.to_dict(),
            "execution": (self.execution.to_dict()
                          if self.execution is not None else None),
            "grading": (self.grading.to_dict()
                        if self.grading is not None else None),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AnswerSheet":
        execution = data.get("execution")
        grading = data.get("grading")
        return cls(
            sheet_id=data["sheet_id"],
            phase_label=data["phase_label"],
            coverage=CoverageRecord.from_dict(data["coverage"]),
            execution=(ExecutionRecord.from_dict(execution)
                       if execution is not None else None),
            grading=(GradingRecord.from_dict(grading)
                     if grading is not None else None),
        )


@dataclass
class UnitSpec:
    """One unit's measurement specification (unit level, no trial data)."""

    unit_id: str
    phase_label: str
    task_ref: str
    role: str
    arm: Optional[str]
    expected_attempts: int
    injected_skills: Any = None
    #: The cause a unit takes when every present trial is invalid (no valid
    #: sample).  The role supplies it: baseline -> all_holes, live/exam ->
    #: zero_execution.
    no_valid_cause: str = "zero_execution"

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UnitSpec":
        return cls(**_known_kwargs(cls, data))


@dataclass
class UnitSummary:
    """One unit's aggregation view, produced by pure functions only.

    This is what decision/audit/test consume.  It is never written by the
    acceptance module and never persisted as a report card.
    """

    unit_id: str
    phase_label: str
    task_ref: str
    role: str
    arm: Optional[str]
    expected_attempts: int
    injected_skills: Any
    valid_count: int
    success_count: int
    status: str
    cause: str
    sheet_ids: List[str] = field(default_factory=list)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    aux_success_count: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UnitSummary":
        return cls(**_known_kwargs(cls, data))


@dataclass
class PhaseReport:
    """One execute phase's trial records and derived unit summaries.

    ``learning_usable`` is False for the final test: the caller must not
    feed that phase's summaries into decision.  This is a runtime envelope,
    not a persisted report card.
    """

    phase_kind: str
    learning_usable: bool
    sheets: List[AnswerSheet]
    units: List[UnitSummary]
    summary: Dict[str, Any] = field(default_factory=dict)
    external: Any = None
