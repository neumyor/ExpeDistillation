"""The shared P04/P06 revision writer: one decision row, one skill.

The decision module has already chosen ``revise``. This module only
writes the new content. It has no keep/delete policy: the old
``usage_revise`` decision table is gone.

A revise may be full (the whole skill body) or trigger-only. The latter
is the internal sub-mode for a skill that was relevant but never read:
the author rewrites only its summary and ``[When to use]`` section, and
the body stays byte-identical.
"""

from pathlib import Path
from typing import Any, Optional, Union

from expedistill.blocks.attribution import retrigger_skill, revise_skill
from expedistill.blocks.author import has_skill_sections
from expedistill.core.models import SKILL_MARKERS, Skill
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    ModelOutputError,
)
from expedistill.llm.call import ModelHandle
from expedistill.methods.author.write import RevisionOutcome

__all__ = ["revise_one"]

_REVISION_ATTEMPTS = 2


def _attribution_report(package: Any) -> str:
    parts = []
    diagnosis = package.fact("group_diagnosis") or ""
    if diagnosis:
        parts.append(diagnosis)
    problem = package.fact("skill_problem") or ""
    if problem:
        parts.append(f"This skill's problem: {problem}")
    usage = package.fact("skill_usage") or ""
    if usage:
        parts.append(f"Recorded usage: {usage}")
    return "\n\n".join(parts)


def _trigger_only(package: Any) -> bool:
    """Relevant but never read: the trigger is the thing to fix."""
    return (package.fact("skill_relevance") == "relevant"
            and package.fact("skill_read_attempts") == "0"
            and package.fact("skill_application") in ("", "unclear"))


def _retrigger(package: Any, weak_profile: str, handle: ModelHandle,
               *, usage: Any, run_dir: Optional[Union[str, Path]]
               ) -> dict:
    skill: Skill = package.old_skill
    result = retrigger_skill(
        skill.content, skill.summary,
        package.fact("sibling_descriptions") or "",
        package.fact("task_instruction") or "",
        package.fact("first_actions") or "",
        package.fact("skill_usage") or "",
        weak_profile, handle, usage=usage, run_dir=run_dir)
    return {"content": result["content"],
            "failure_category": result["failure_category"],
            "revision_reason": result["reason"],
            "summary": result["summary"]}


def _full_revise(package: Any, weak_profile: str, handle: ModelHandle,
                 *, usage: Any, run_dir: Optional[Union[str, Path]]
                 ) -> dict:
    skill: Skill = package.old_skill
    failure_view = package.fact("failure_view")
    if failure_view is None:
        raise CodeBugError(
            f"the evidence package of {package.target!r} authorizes a "
            "revision but carries no failure_view")
    result = revise_skill(
        skill.content, _attribution_report(package), failure_view,
        weak_profile, handle, usage=usage, run_dir=run_dir)
    new_text = result["skill_text"]
    if has_skill_sections(skill.content) and not has_skill_sections(new_text):
        raise ModelOutputError(
            "the revision must keep the four bracket sections "
            f"({' / '.join(SKILL_MARKERS)})")
    return {"content": new_text,
            "failure_category": result["failure_category"],
            "revision_reason": result["revision_reason"],
            "summary": result["summary"]}


def revise_one(package: Any, *, weak_profile: str, handle: ModelHandle,
               attempts: int = _REVISION_ATTEMPTS,
               usage: Any = None,
               run_dir: Optional[Union[str, Path]] = None
               ) -> RevisionOutcome:
    """Write one skill's revision; no action policy is applied here."""
    skill: Skill = package.old_skill
    if skill is None:
        raise CodeBugError(
            f"the evidence package of {package.target!r} carries no "
            "old_skill")
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts}")
    for _ in range(attempts):
        try:
            if _trigger_only(package):
                result = _retrigger(package, weak_profile, handle,
                                    usage=usage, run_dir=run_dir)
            else:
                result = _full_revise(package, weak_profile, handle,
                                      usage=usage, run_dir=run_dir)
        except InfrastructureError:
            return RevisionOutcome(
                skill_id=skill.skill_id, decision="revise",
                outcome="infra_failed")
        except ModelOutputError:
            continue
        if result["content"] == skill.content:
            return RevisionOutcome(
                skill_id=skill.skill_id, decision="revise",
                outcome="unchanged")
        return RevisionOutcome(
            skill_id=skill.skill_id, decision="revise",
            outcome=("retriggered" if _trigger_only(package) else "revised"),
            content=result["content"],
            failure_category=result["failure_category"],
            revision_reason=result["revision_reason"],
            summary=result["summary"])
    return RevisionOutcome(skill_id=skill.skill_id, decision="revise",
                           outcome="unchanged")
