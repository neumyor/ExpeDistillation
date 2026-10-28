"""The exam path's evidence collector: a skill's answer-sheet package.

The third evidence-family entry (beside ``add_path`` and
the live group attribution): a revise row of the EXAM kind collects the exam, the
judged attempt's weak answer and the judge's reason as fact-only
evidence for the author driver. This assembly used to live inline in
``stages/learn_add.py / learn_apply.py / learn_exam.py`` as a disclosed deviation ("the evidence
family has no exam collector yet"); the Wave 2 integration moved it
here -- evidence gathering belongs to the evidence family (boundary doc
section 2.5).

The rewrite material is P7b's source selection, mirrored from the
retired ``acceptance_submit``: the latest judged attempt of THIS version
(the row's graded sheet ids), else the latest judged with-skill attempt of the
exam, else the bare attempt -- and a leaked version's rewrite carries
the audit report as its failure description (the voided rewrite; the
leak verdict is read back, never re-asked).
"""

from expedistill.composites import decision
from expedistill.composites.acceptance_leak import find_leak_verdict
from expedistill.composites.acceptance_skill_status import exam_attempts
from expedistill.core.answer_sheet import UnitSummary
from expedistill.core.models import Attempt, Exam, Skill
from expedistill.infra.reliability import CodeBugError
from expedistill.infra.store import Store

from expedistill.composites.evidence_package import EvidencePackage

__all__ = ["collect_exam_evidence", "injected_version"]

#: Prefix of the error description synthesized for a leak-voided rewrite
#: (acceptance_submit's wording: the audit report is the failure being
#: repaired, the judge reason the extra context).
_VOID_NOTE = "The previous skill version was voided by the leak audit: "


def injected_version(summary: UnitSummary) -> int:
    """The single skill version an exam unit injected (its unit id's
    join back to the target)."""
    injected = summary.injected_skills
    if (not isinstance(injected, list) or len(injected) != 1
            or not isinstance(injected[0], dict)
            or not isinstance(injected[0].get("version"), int)):
        raise CodeBugError(
            f"unit summary {summary.unit_id!r} injects {injected!r}; an "
            "exam-phase unit injects exactly one {skill_id, version}")
    return injected[0]["version"]


def collect_exam_evidence(store: Store, summary: UnitSummary,
                          skill: Skill) -> EvidencePackage:
    """One skill's answer-sheet package from the unit summary's refs.

    ``summary`` is the decision's basis unit (the summary that authorized
    the revision); ``skill`` is the stored target. Raises CodeBugError
    when the summary names no exam, or no judged attempt exists to
    revise from (the summary authorized a revision without answer-sheet
    material).
    """
    exam_id = summary.task_ref
    if not exam_id:
        raise CodeBugError(
            f"unit summary {summary.unit_id!r} carries no exam ref; an "
            "exam-phase unit always names its exam")
    exam = store.load("exams", exam_id, Exam)
    judged = [store.load("attempts", sheet_id, Attempt)
              for sheet_id in summary.sheet_ids]
    if judged:
        source = judged[-1]
        error = source.judge_reason
    else:
        attempts = exam_attempts(store, exam_id)
        with_skill = [attempt for attempt in attempts
                      if attempt.purpose == "acceptance"
                      and attempt.skill_id is not None
                      and attempt.judge_verdict is not None]
        if with_skill:
            source = with_skill[-1]
        else:
            bare = [attempt for attempt in attempts
                    if attempt.purpose == "bare"
                    and attempt.judge_verdict is not None]
            if not bare:
                raise CodeBugError(
                    f"exam {exam_id!r} has no judged attempt to revise "
                    f"skill {skill.skill_id!r} from: the unit summary "
                    "authorized a revision without answer-sheet material")
            source = bare[-1]
        error = source.judge_reason
        if summary.status == decision.ROW_NOT_TESTABLE \
                and summary.cause == decision.CAUSE_LEAK:
            leak = find_leak_verdict(store, skill.skill_id,
                                     injected_version(summary))
            report = leak["reason"] if leak else ""
            reason = (source.judge_reason or "").strip()
            error = (f"{reason} {_VOID_NOTE}{report}" if reason
                     else f"{_VOID_NOTE}{report}")
    selected = "\n".join(
        step.action for step in (source.steps or [])[:-1]
        if step.action and not step.action.startswith("#"))
    answer = ("\n\n".join(part for part in (
        f"SKILLS OPENED:\n{selected}" if selected else "",
        f"PLAN:\n{source.weak_answer}") if part))
    return EvidencePackage(
        target=skill.skill_id,
        facts=[{"kind": "exam_question", "text": exam.scene_text},
               {"kind": "expected_behavior",
                "text": exam.expected_behavior},
               {"kind": "weak_answer", "text": answer},
               {"kind": "judge_reason", "text": error or ""}],
        segments=[{"exam_id": exam_id, "attempt_id": source.attempt_id}],
        old_skill=skill)
