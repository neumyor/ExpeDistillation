"""The P04/P06 evidence package: per-skill, per-window facts.

This shape is intentionally separate from ``EvidencePackage`` (still used
by the P02 add path and the exam path). P04's writer reads only this
shape: one skill, a list of attempt windows, each with the attribution
model's trigger-context / behavior / improvement text.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from expedistill.core.trace import TraceRef

__all__ = ["SkillAttemptEvidence", "WindowEvidence"]


@dataclass
class WindowEvidence:
    """One attribution window of one attempt for one skill.

    ``ref`` is the exact place the window was cut at: its own answer sheet
    and the trigger step (or the window start), so the step number and the
    attempt can never come from different executions.
    """

    task_id: str
    attempt: int
    success: Optional[bool]
    kind: str                 # "invocation" / "start" / "end" / "trial"
    step_start: int
    step_end: int
    ref: TraceRef
    trigger_steps: List[int] = field(default_factory=list)
    trigger_context: str = ""
    behavior_after_trigger: str = ""
    improvement: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["ref"] = self.ref.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WindowEvidence":
        data = dict(data)
        data["ref"] = TraceRef.from_dict(data["ref"])
        return cls(**data)


@dataclass
class SkillAttemptEvidence:
    """All windows of one skill, across attempts and invocations."""

    skill_id: str
    skill_version: int
    windows: List[WindowEvidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": "skill-attempt-evidence-v1",
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "windows": [window.to_dict() for window in self.windows],
        }
