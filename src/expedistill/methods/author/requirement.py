"""Write one new skill from an aggregated review requirement."""

from typing import Any, Optional

from expedistill.blocks.author import write_skill
from expedistill.core.models import Skill
from expedistill.core.ids import make_subtask_id
from expedistill.core.models import Subtask
from expedistill.infra.reliability import CodeBugError

__all__ = ["write_requirement"]


def write_requirement(
    requirement: Any,
    *,
    weak_profile: str,
    handle: Any,
    usage: Any = None,
    run_dir: Optional[Any] = None,
) -> Skill:
    """Render the requirement's evidence into one pending skill."""
    experiences = list(requirement.evidence.source_experiences)
    if not experiences:
        raise CodeBugError(
            f"requirement {requirement.requirement_id!r} carries no source "
            "experience")
    subtasks = sorted(
        (experience["subtask"] for experience in experiences),
        key=lambda subtask: subtask.subtask_id)
    anchor_id = make_subtask_id(requirement.requirement_id, 0)
    anchor = Subtask(
        subtask_id=anchor_id,
        task_id=requirement.requirement_id,
        trajectory_id=requirement.requirement_id,
        source="weak",
        trajectory_success=False,
        segment_index=0,
        step_range=(0, 0),
        label=requirement.title,
        description=requirement.description,
        success=False,
        context_summary=requirement.trigger,
    )
    steps_view = "\n\n".join(
        str(experience.get("steps_view") or "")
        for experience in experiences)
    skill = write_skill(
        anchor,
        steps_view,
        weak_profile,
        "diagnose",
        handle,
        segment_position=requirement.trigger,
        prompt_name="REQUIREMENT_SKILL",
        usage=usage,
        run_dir=run_dir)
    skill.source_subtasks = sorted({subtask.subtask_id for subtask in subtasks})
    return skill
