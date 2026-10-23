"""The author family's driver: write skills from task list + evidence (R6).

The author module takes what the decision module authorized (a
TaskListRow) and what the evidence module gathered (evidence
packages), and produces skill CONTENT: a revision's new text, or a new
skill distilled from source experiences. The P04/P06 per-skill decision
lives in ``composites/decision_strategies.py``; this module's
``revise_group`` is the exam-compatible group writer and always revises
each package. It dispatches by the EVIDENCE PACKAGE's shape, never by the
report kind -- a package carrying an exam answer sheet goes to the exam
rewrite block (G6's REWRITE prompt), one carrying failure views to the
live revision block (G11's LIVE_SKILL_REVISION); the caller never says
which. For the same reason this module does not import the evidence
family (methods modules never import each other): it consumes the
package's duck-typed shape -- ``target``, ``facts``, ``old_skill``,
``source_experiences`` and the ``fact``/``has_fact`` readers.

The old per-skill keep/delete policy is gone: delete is a decision row,
landed by ``stages.learn_ledger``. The shared P04/P06 writer is
``methods/author/revise.py``.

Budget exhaustion deliberately does NOT discard anything: the C8
ruling (repair plan section 3) settles the old failure-disposition
row -- a rewrite that produced no new version counts as NO CHANGE
(boundaries section 4.3 rule 2: the skill keeps its version, is not
re-measured, is retried next learning round and deleted by decision's
table only at the last one). The same "unchanged" outcome covers a
revision that comes back byte-identical (section 4.3 rule 1) and a
budget that ran out. The caller persists: this driver writes nothing
to disk.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Union

from expedistill.blocks.attribution import revise_skill
from expedistill.blocks.author import (
    has_skill_sections,
    rewrite_skill,
    write_skill,
)
from expedistill.composites.decision import ADD, LIVE_REVISION_DELETED, REVISE
from expedistill.core.contracts import TaskListRow
from expedistill.core.models import SKILL_MARKERS, Skill
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    ModelOutputError,
)
from expedistill.llm.call import ModelHandle

__all__ = [
    "RevisionOutcome",
    "revise_group",
    "write_new",
]

#: The per-skill decisions the author may take inside one authorized
#: group revision (boundaries section 2.6). "add" is not among them: a
#: new skill inside a revision needs source experiences, which a
#: revision package does not carry; adds come through the add path.
_DECISIONS = ("keep", "revise", "delete")

#: Composite-level repair budget of one revision, the same value K4 and
#: K6 give their rewrites. Call_model's own format repairs ride on top.
_REVISION_ATTEMPTS = 2


@dataclass(frozen=True)
class RevisionOutcome:
    """One skill's revision result, for the task list applier.

    ``decision`` is the author's call (keep / revise / delete);
    ``outcome`` is what happened: ``kept``, ``revised`` (new content in
    ``content``, with the model's ``failure_category``,
    ``revision_reason`` and fresh ``summary``), ``deleted``
    (``discard_reason`` is live_revision_deleted), ``unchanged`` (no new
    version: exhausted budget or identical output -- the applier leaves
    the skill as is) or ``infra_failed`` (the work is valid, the service
    was not; the skill stays for a retry).
    """

    skill_id: str
    decision: str
    outcome: str
    content: Optional[str] = None
    failure_category: Optional[str] = None
    revision_reason: Optional[str] = None
    discard_reason: Optional[str] = None
    #: The revised skill's fresh one-liner (schema v3); None unless the
    #: outcome is "revised".
    summary: Optional[str] = None


def _always_revise(package: Any) -> str:
    """The legacy group writer has no policy: revise every package."""
    return "revise"


def _attribution_report(package: Any) -> str:
    """The revision prompt's attribution report, composed the same way
    the legacy K6 path composes it (group diagnosis plus this skill's
    problem), so the two paths render the same request bytes."""
    diagnosis = package.fact("group_diagnosis") or ""
    problem = package.fact("skill_problem") or ""
    report = diagnosis
    if problem:
        report = f"{report}\n\nThis skill's problem: {problem}" if report \
            else f"This skill's problem: {problem}"
    return report


def _revise_once(
    package: Any,
    weak_profile: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Optional[dict]:
    """Run ONE revision attempt for one package; None means no new
    version came out of it (exhausted budget -- see the module
    docstring) and an InfrastructureError propagates.

    Dispatch is by the package's evidence shape (module docstring): an
    exam answer sheet (question, weak answer, judge reason) goes to
    blocks.author.rewrite_skill; failure views go to
    blocks.attribution.revise_skill with the composite-level marker
    check (the K5 lesson: a structural failure rides the same
    ModelOutputError budget as a format failure).
    """
    skill: Skill = package.old_skill
    exam_question = package.fact("exam_question")
    if exam_question is not None:
        weak_answer = package.fact("weak_answer") or ""
        judge_reason = package.fact("judge_reason") or ""
        result = rewrite_skill(
            skill, exam_question, weak_answer, judge_reason,
            weak_profile, handle, usage=usage, run_dir=run_dir)
        return {"content": result["content"],
                "failure_category": result["failure_category"],
                "revision_reason": result["rewrite_reason"],
                "summary": result["summary"]}
    failure_view = package.fact("failure_view")
    if failure_view is None:
        raise CodeBugError(
            f"the evidence package of {package.target!r} authorizes a "
            "revision but carries no failure material: expected an exam "
            "answer sheet (exam_question) or failure views (failure_view)")
    result = revise_skill(
        skill.content, _attribution_report(package), failure_view,
        weak_profile, handle, usage=usage, run_dir=run_dir)
    new_text = result["skill_text"]
    if has_skill_sections(skill.content) and not has_skill_sections(new_text):
        # Same budget as a format failure; a structural raise outside
        # the budget's scope kills whole units (the K5 lesson).
        raise ModelOutputError(
            "the revision must keep the four bracket sections "
            f"({' / '.join(SKILL_MARKERS)})")
    return {"content": new_text,
            "failure_category": result["failure_category"],
            "revision_reason": result["revision_reason"],
            "summary": result["summary"]}


def _revise_with_budget(
    package: Any,
    weak_profile: str,
    handle: ModelHandle,
    attempts: int,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> RevisionOutcome:
    skill: Skill = package.old_skill
    for _ in range(attempts):
        try:
            result = _revise_once(package, weak_profile, handle,
                                  usage=usage, run_dir=run_dir)
        except InfrastructureError:
            # Not a budget item: the work is valid, the service was
            # not. The caller records it and the skill stays as is.
            return RevisionOutcome(skill_id=skill.skill_id,
                                   decision="revise",
                                   outcome="infra_failed")
        except ModelOutputError:
            continue
        if result["content"] == skill.content:
            # Byte-identical output is no change (boundaries section
            # 4.3 rule 1): no new version, no discard.
            return RevisionOutcome(skill_id=skill.skill_id,
                                   decision="revise", outcome="unchanged")
        return RevisionOutcome(
            skill_id=skill.skill_id, decision="revise", outcome="revised",
            content=result["content"],
            failure_category=result["failure_category"],
            revision_reason=result["revision_reason"],
            summary=result["summary"])
    # Budget exhausted: NO discard -- the C8 ruling; the skill keeps
    # its version and the next learning round tries again.
    return RevisionOutcome(skill_id=skill.skill_id, decision="revise",
                           outcome="unchanged")


def revise_group(
    *,
    task_row: TaskListRow,
    packages: List[Any],
    rewriter_handle: ModelHandle,
    weak_profile: str,
    decide: Optional[Callable[[Any], str]] = None,
    revision_attempts: int = _REVISION_ATTEMPTS,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
    params: Any = None,
    task_instruction: str = "",
) -> List[RevisionOutcome]:
    """Revise one authorized target from its evidence packages.

    ``task_row`` is the decision module's authorization and must carry
    action ``revise``; ``packages`` are the evidence module's per-skill
    packages (each with its ``old_skill``). Every package is revised
    (``decide`` defaults to always-revise; the exam path passes its own);
    outcomes come back in package order for the task-list applier:
    nothing is written to disk here. A revision's new version fields are
    the caller's to persist (version bump plus rewrite_history entry). ``params`` is the registry's
    ``AuthoringParams`` (empty); it is accepted so the phase driver can
    call every group-author method through one signature.
    """
    if task_row.action != REVISE:
        raise ValueError(
            "revise_group needs a task list row with action 'revise', "
            f"got {task_row.action!r} on target {task_row.target_id!r}")
    if revision_attempts < 1:
        raise ValueError(
            f"revision_attempts must be >= 1, got {revision_attempts}")
    decide = decide or _always_revise
    outcomes: List[RevisionOutcome] = []
    for package in packages:
        if package.old_skill is None:
            raise CodeBugError(
                f"the evidence package of {package.target!r} carries no "
                "old_skill: a revision package must name the current "
                "skill")
        decision = decide(package)
        if decision not in _DECISIONS:
            raise CodeBugError(
                f"the revision policy decided {decision!r} for "
                f"{package.target!r}; expected one of {_DECISIONS}")
        if decision == "keep":
            outcomes.append(RevisionOutcome(
                skill_id=package.target, decision="keep", outcome="kept"))
        elif decision == "delete":
            outcomes.append(RevisionOutcome(
                skill_id=package.target, decision="delete",
                outcome="deleted", discard_reason=LIVE_REVISION_DELETED))
        else:
            outcomes.append(_revise_with_budget(
                package, weak_profile, rewriter_handle, revision_attempts,
                usage=usage, run_dir=run_dir))
    return outcomes


def write_new(
    *,
    task_row: TaskListRow,
    package: Any,
    author_handle: ModelHandle,
    weak_profile: str,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> List[Skill]:
    """Write new skills for one authorized add, from its evidence.

    ``task_row`` must carry action ``add`` (its target is the task the
    decision module picked); ``package`` is the add-path evidence
    package whose ``source_experiences`` each name a subtask, its
    rendered steps view and the write mode. One skill is written per
    source experience (the stage-3 grain: one segment, one skill),
    through the same G2 block stage 3 uses. A ModelOutputError that
    survives the block's own format repairs propagates: the caller
    dispositions the unit (the stage-3 rule -- terminal within the
    run directory, never a partial save). Nothing is stored here.
    """
    if task_row.action != ADD:
        raise ValueError(
            "write_new needs a task list row with action 'add', got "
            f"{task_row.action!r} on target {task_row.target_id!r}")
    if not package.source_experiences:
        raise CodeBugError(
            f"the evidence package of {package.target!r} carries no "
            "source_experiences: an add package must name the segments "
            "to learn from")
    skills: List[Skill] = []
    for experience in package.source_experiences:
        skills.append(write_skill(
            experience["subtask"], experience["steps_view"], weak_profile,
            experience["mode"], author_handle,
            segment_position=experience.get("segment_position", ""),
            usage=usage, run_dir=run_dir))
    return skills
