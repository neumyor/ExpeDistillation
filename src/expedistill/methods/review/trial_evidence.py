"""Turn trial AnswerSheets into the review's existing evidence objects.

A live trial has no stored ``Subtask``.  This adapter creates deterministic
synthetic subtasks for the failure window, so the requirement writer can keep
using the existing ``EvidencePackage`` and ``SkillAttemptEvidence`` shapes.
"""

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from expedistill.blocks.view import render_steps_view
from expedistill.composites.evidence_package import EvidencePackage
from expedistill.composites.skill_attempt_evidence import (
    SkillAttemptEvidence,
    WindowEvidence,
)
from expedistill.core.answer_sheet import AnswerSheet
from expedistill.core.ids import make_subtask_id
from expedistill.core.models import Skill, Subtask

__all__ = [
    "available_skill_ids",
    "evidence_for_skills",
    "requirement_evidence",
]


def available_skill_ids(sheet: AnswerSheet, bank: Sequence[Skill]) -> List[str]:
    """The skills this trial actually delivered, in manifest order."""
    execution = sheet.execution
    if execution is None:
        return []
    folder = (execution.artifacts or {}).get("skill_folder")
    if isinstance(folder, dict):
        ids = [entry.get("skill_id") for entry in folder.get("files") or []
               if isinstance(entry, dict)]
        if ids:
            return [skill_id for skill_id in ids if isinstance(skill_id, str)]
    injected = execution.skills_injected
    if isinstance(injected, list):
        ids = [entry.get("skill_id") for entry in injected
               if isinstance(entry, dict)]
        return [skill_id for skill_id in ids if isinstance(skill_id, str)]
    if isinstance(injected, str):
        # The whole-bank digest form names no individual skills.
        return [skill.skill_id for skill in bank]
    return []


def evidence_for_skills(
    sheet: AnswerSheet,
    *,
    skill_ids: Sequence[str],
    issue: str,
    reason: str,
    step_start: int,
    step_end: int,
    bank: Sequence[Skill],
    ranges: Optional[Mapping[str, Tuple[int, int]]] = None,
) -> Dict[str, SkillAttemptEvidence]:
    """One SkillAttemptEvidence per mentioned skill."""
    execution = sheet.execution
    if execution is None:
        return {}
    versions = {skill.skill_id: skill.version for skill in bank}
    out: Dict[str, SkillAttemptEvidence] = {}
    for skill_id in skill_ids:
        lo, hi = (ranges or {}).get(skill_id, (step_start, step_end))
        window = WindowEvidence(
            task_id=execution.task_ref,
            attempt=execution.sample_index,
            success=False,
            kind="trial",
            step_start=lo,
            step_end=hi,
            trigger_context=issue,
            behavior_after_trigger="",
            improvement=reason,
        )
        out[skill_id] = SkillAttemptEvidence(
            skill_id=skill_id,
            skill_version=versions.get(skill_id, 1),
            windows=[window])
    return out


def requirement_evidence(
    sheet: AnswerSheet,
    *,
    requirement_id: str,
    reason: str,
    step_start: int,
    step_end: int,
    view_rules: Any,
    index: int = 0,
) -> EvidencePackage:
    """Build the EvidencePackage for one aggregated requirement from a trial."""
    execution = sheet.execution
    if execution is None:
        raise ValueError(
            f"requirement evidence needs a present trial, got {sheet.sheet_id!r}")
    steps = list(execution.steps or [])
    start = max(0, min(step_start, len(steps)))
    end = max(start, min(step_end, len(steps)))
    trajectory_id = (execution.artifacts or {}).get("trajectory_id")
    if not isinstance(trajectory_id, str) or not trajectory_id:
        trajectory_id = f"trial/{sheet.sheet_id}"
    subtask = Subtask(
        subtask_id=make_subtask_id(trajectory_id, index),
        task_id=execution.task_ref,
        trajectory_id=trajectory_id,
        source="weak",
        trajectory_success=False,
        segment_index=index,
        step_range=(start, end),
        label="review failure window",
        description=reason,
        success=False,
        context_summary=reason,
    )
    steps_view = render_steps_view(steps[start:end], view_rules,
                                   start_index=start)
    return EvidencePackage(
        target=requirement_id,
        facts=[{"kind": "trial_failure", "text": reason}],
        segments=[{
            "sheet_id": sheet.sheet_id,
            "step_range": [start, end],
        }],
        source_experiences=[{
            "subtask": subtask,
            "steps_view": steps_view,
            "mode": "diagnose",
            "segment_position": f"trial window steps {start} to {end - 1}",
        }],
    )
