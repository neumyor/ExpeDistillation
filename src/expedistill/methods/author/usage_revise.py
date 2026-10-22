"""The usage-aware group author.

The decision module authorizes a group revision; ``usage_group`` supplies
one fact-only package per member, including whether the model read the
skill and how the reading attempts ended.  This driver turns those facts
into one of five outcomes per skill:

* ``kept`` -- no action;
* ``retriggered`` -- the summary and [When to use] section were rewritten,
  the body kept byte-identical;
* ``revised`` -- the whole skill body was rewritten;
* ``deleted`` -- the skill is discarded;
* ``observed`` -- the evidence is insufficient, the skill stays as is.

The policy is deliberately conservative on the two ends: a delivery
failure or a round where no skill was read never rewrites individual
triggers, and an unread skill is retriggered once before a later round
may delete it.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Union

from expedistill.blocks.attribution import retrigger_skill, revise_skill
from expedistill.blocks.author import has_skill_sections
from expedistill.composites.decision import LIVE_REVISION_DELETED, REVISE
from expedistill.core.contracts import TaskListRow
from expedistill.core.models import Skill
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    ModelOutputError,
)
from expedistill.llm.call import ModelHandle
from expedistill.methods.author import UsageReviseParams

__all__ = ["UsageRevisionOutcome", "revise_group"]

_REVISION_ATTEMPTS = 2
_DECISIONS = ("keep", "retrigger", "revise", "delete", "observe")


@dataclass(frozen=True)
class UsageRevisionOutcome:
    """One skill's usage-aware revision result (the landing contract)."""

    skill_id: str
    decision: str
    outcome: str
    content: Optional[str] = None
    failure_category: Optional[str] = None
    revision_reason: Optional[str] = None
    discard_reason: Optional[str] = None
    summary: Optional[str] = None


def _previous_trigger_rewrite(skill: Skill) -> bool:
    return any(entry.get("error_category") == "wrong_trigger"
               for entry in skill.rewrite_history)


def _decision(package: Any) -> str:
    """The ``usage_v1`` policy over one package's facts."""
    delivery = package.fact("delivery_status") or "ok"
    selection = package.fact("selection_status") or "some_read"
    if delivery != "ok" or selection == "none_read":
        return "observe"
    if not package.has_fact("skill_read_attempts"):
        return "observe"
    relevance = package.fact("skill_relevance") or "unclear"
    application = package.fact("skill_application") or "unclear"
    try:
        read_attempts = int(package.fact("skill_read_attempts") or 0)
    except ValueError:
        raise ModelOutputError(
            f"usage revision facts for {package.target!r} carry a "
            "non-integer skill_read_attempts") from None
    if relevance == "relevant" and read_attempts == 0:
        return ("delete" if _previous_trigger_rewrite(package.old_skill)
                else "retrigger")
    if relevance == "irrelevant" and read_attempts == 0:
        return "keep"
    if application == "contradicted":
        return "delete"
    if application in ("applied", "partially_applied", "not_applied"):
        return "revise"
    return "observe"


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


def _retrigger_once(
    package: Any,
    task_instruction: str,
    weak_profile: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Optional[dict]:
    skill: Skill = package.old_skill
    result = retrigger_skill(
        skill.content, skill.summary,
        package.fact("sibling_descriptions") or "",
        task_instruction,
        package.fact("first_actions") or "",
        package.fact("skill_usage") or "",
        weak_profile, handle, usage=usage, run_dir=run_dir)
    if result["content"] == skill.content:
        return None
    return {
        "content": result["content"],
        "failure_category": result["failure_category"],
        "revision_reason": result["reason"],
        "summary": result["summary"],
    }


def _revise_once(
    package: Any,
    weak_profile: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Optional[dict]:
    skill: Skill = package.old_skill
    failure_view = package.fact("failure_view")
    if failure_view is None:
        raise CodeBugError(
            f"the evidence package of {package.target!r} authorizes a "
            "revision but carries no failure_view fact")
    result = revise_skill(
        skill.content, _attribution_report(package), failure_view,
        weak_profile, handle, usage=usage, run_dir=run_dir)
    new_text = result["skill_text"]
    if has_skill_sections(skill.content) and not has_skill_sections(new_text):
        raise ModelOutputError(
            "the revision must keep the four bracket sections")
    if new_text == skill.content:
        return None
    return {
        "content": new_text,
        "failure_category": result["failure_category"],
        "revision_reason": result["revision_reason"],
        "summary": result["summary"],
    }


def _with_budget(
    package: Any,
    decision: str,
    task_instruction: str,
    weak_profile: str,
    handle: ModelHandle,
    attempts: int,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> UsageRevisionOutcome:
    skill: Skill = package.old_skill
    for _ in range(attempts):
        try:
            if decision == "retrigger":
                result = _retrigger_once(
                    package, task_instruction, weak_profile, handle,
                    usage=usage, run_dir=run_dir)
            else:
                result = _revise_once(
                    package, weak_profile, handle,
                    usage=usage, run_dir=run_dir)
        except InfrastructureError:
            return UsageRevisionOutcome(
                skill_id=skill.skill_id, decision=decision,
                outcome="infra_failed")
        except ModelOutputError:
            continue
        if result is None:
            return UsageRevisionOutcome(
                skill_id=skill.skill_id, decision=decision,
                outcome="unchanged")
        return UsageRevisionOutcome(
            skill_id=skill.skill_id, decision=decision,
            outcome=("retriggered" if decision == "retrigger"
                     else "revised"),
            content=result["content"],
            failure_category=result["failure_category"],
            revision_reason=result["revision_reason"],
            summary=result["summary"])
    return UsageRevisionOutcome(
        skill_id=skill.skill_id, decision=decision, outcome="unchanged")


def revise_group(
    *,
    task_row: TaskListRow,
    packages: List[Any],
    rewriter_handle: ModelHandle,
    weak_profile: str,
    task_instruction: str = "",
    decide: Optional[Callable[[Any], str]] = None,
    revision_attempts: int = _REVISION_ATTEMPTS,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
    params: Any = None,
) -> List[UsageRevisionOutcome]:
    """Apply the usage policy to one authorized group revision."""
    if task_row.action != REVISE:
        raise ValueError(
            "usage_revise needs a task list row with action 'revise', "
            f"got {task_row.action!r} on target {task_row.target_id!r}")
    if revision_attempts < 1:
        raise ValueError(
            f"revision_attempts must be >= 1, got {revision_attempts}")
    if params is None:
        params = UsageReviseParams()
    if not isinstance(params, UsageReviseParams):
        raise CodeBugError(
            "usage_revise received params of type "
            f"{type(params).__name__}; expected UsageReviseParams")
    outcomes: List[UsageRevisionOutcome] = []
    for package in packages:
        if package.old_skill is None:
            raise CodeBugError(
                f"the evidence package of {package.target!r} carries no "
                "old_skill: a revision package must name the current skill")
        decision = (decide(package) if decide is not None
                    else _decision(package))
        if decision not in _DECISIONS:
            raise CodeBugError(
                f"the usage policy decided {decision!r} for "
                f"{package.target!r}; expected one of {_DECISIONS}")
        if decision == "keep":
            outcomes.append(UsageRevisionOutcome(
                skill_id=package.target, decision="keep", outcome="kept"))
        elif decision == "observe":
            outcomes.append(UsageRevisionOutcome(
                skill_id=package.target, decision="observe",
                outcome="observed"))
        elif decision == "delete":
            outcomes.append(UsageRevisionOutcome(
                skill_id=package.target, decision="delete",
                outcome="deleted", discard_reason=LIVE_REVISION_DELETED))
        else:
            outcomes.append(_with_budget(
                package, decision, task_instruction, weak_profile,
                rewriter_handle, revision_attempts,
                usage=usage, run_dir=run_dir))
    return outcomes
