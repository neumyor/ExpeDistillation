"""Per-trial review: one free-text diagnosis for each failed trial.

The analysis is intentionally concurrent and independent per trial.  It does
not choose final skill actions; the aggregation agent does that after every
finding exists.

An exam trial is a paper answer (skills opened, then a plan; nothing runs),
so it is analyzed with ``TRIAL_EXAM_ANALYSIS`` from the judge's reason and
the expected behavior, and its finding always names the skill the exam was
written for.
"""

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from expedistill.benchmarks.registry import get_plugin
from expedistill.blocks.attribution import _canonical_skill_id
from expedistill.blocks.view import render_steps_view
from expedistill.composites.skill_use import skill_files_read
from expedistill.core.answer_sheet import AnswerSheet, PhaseReport
from expedistill.core.models import Skill
from expedistill.infra.parallel import run_parallel
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    ModelOutputError,
)
from expedistill.llm.call import call_model
from expedistill.methods.review import attribution, trial_evidence
from expedistill.methods.review.contracts import ReviewContext, TrialFinding

__all__ = ["analyze_trials", "is_failed_trial"]


def is_failed_trial(sheet: AnswerSheet) -> bool:
    return (sheet.execution is not None
            and sheet.grading is not None
            and sheet.grading.verdict == "failure")


def analyze_trials(
    *,
    current: PhaseReport,
    bank: Sequence[Skill],
    context: ReviewContext,
    handle: Any,
    usage: Any = None,
    run_dir: Any = None,
    existing: Sequence[TrialFinding] = (),
) -> Tuple[List[TrialFinding], Set[str], Set[str]]:
    """Analyze every missing failed trial; return findings and failures."""
    known = {finding.sheet_id: finding for finding in existing}
    pending = [sheet for sheet in current.sheets
               if sheet.phase_label == context.source_phase_label
               and is_failed_trial(sheet) and sheet.sheet_id not in known
               and not (context.phase_kind == "exam"
                        and not _examines_a_skill(sheet))]
    view_rules = get_plugin(context.benchmark).view_rules

    def one(sheet: AnswerSheet):
        try:
            return sheet, _analyze_one(
                sheet, bank=bank, context=context,
                handle=handle, usage=usage, run_dir=run_dir,
                view_rules=view_rules), None, None
        except InfrastructureError as exc:
            return sheet, None, exc, "infra"
        except ModelOutputError as exc:
            return sheet, None, exc, "model"

    workers = max(1, min(int(context.workers or 1), len(pending))) \
        if len(pending) > 1 else 1
    if workers <= 1:
        results = [one(sheet) for sheet in pending]
    else:
        results = run_parallel(one, pending, workers, thread=True)

    failed: Set[str] = set()
    model_errors: Set[str] = set()
    for sheet, finding, exc, kind in results:
        if finding is not None:
            known[sheet.sheet_id] = finding
        if exc is not None:
            if kind == "infra":
                failed.add(sheet.sheet_id)
            else:
                model_errors.add(sheet.sheet_id)
    ordered = [known[sheet.sheet_id] for sheet in current.sheets
               if sheet.phase_label == context.source_phase_label
               and sheet.sheet_id in known]
    return ordered, failed, model_errors


def _examines_a_skill(sheet: AnswerSheet) -> bool:
    """False for a bare exam control: it answers without any skill."""
    injected = sheet.execution.skills_injected if sheet.execution else None
    return isinstance(injected, list) and bool(injected)


def _analyze_one(
    sheet: AnswerSheet,
    *,
    bank: Sequence[Skill],
    context: ReviewContext,
    handle: Any,
    usage: Any,
    run_dir: Any,
    view_rules: Any,
) -> TrialFinding:
    execution = sheet.execution
    if execution is None:
        raise ModelOutputError(
            f"trial analysis needs a present execution, got {sheet.sheet_id!r}")
    steps = list(execution.steps or [])
    exam_skill: Optional[str] = None
    if context.phase_kind == "exam":
        prompt = "TRIAL_EXAM_ANALYSIS"
        exam_skill, variables = _exam_variables(sheet, bank)
        parsed = call_model(
            handle, prompt, variables,
            output_json=True, usage=usage, run_dir=run_dir)
    else:
        prompt = "TRIAL_FAILURE_ANALYSIS"
        available = trial_evidence.available_skill_ids(sheet, bank)
        parsed = call_model(
            handle, prompt,
            {
                "task_instruction": attribution.task_instruction_for(
                    context.benchmark, execution.task_ref),
                "trial_steps": render_steps_view(steps, view_rules),
                "available_skills": _skill_ids(available),
                "all_skills": _skill_cards(bank),
            },
            output_json=True, usage=usage, run_dir=run_dir)
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"{prompt}: expected a JSON object, got "
            f"{type(parsed).__name__}")
    reason = parsed.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ModelOutputError(
            f"{prompt}: reason must be a non-empty string")
    profile_note = parsed.get("profile_note") or ""
    new_skill_note = parsed.get("new_skill_note") or ""
    if not isinstance(profile_note, str) or not isinstance(new_skill_note, str):
        raise ModelOutputError(
            f"{prompt}: profile_note and new_skill_note must "
            "be strings")
    known = {skill.skill_id for skill in bank}
    folder = (execution.artifacts or {}).get("skill_folder")
    aliases = attribution._folder_aliases(
        folder if isinstance(folder, dict) else None)
    mentioned: List[str] = []
    issues: Dict[str, str] = {}
    ranges: Dict[str, tuple] = {}
    overall_start, overall_end = len(steps), 0
    raw_skills = parsed.get("skills") or []
    if not isinstance(raw_skills, list):
        raise ModelOutputError(
            f"{prompt}: skills must be a list")
    for entry in raw_skills:
        if not isinstance(entry, dict):
            raise ModelOutputError(
                f"{prompt}: each skills entry must be an object")
        raw_skill_id = entry.get("skill_id")
        if raw_skill_id == "":
            continue
        skill_id = _canonical_skill_id(raw_skill_id, known, aliases)
        if skill_id is None:
            raise ModelOutputError(
                f"{prompt}: unknown skill_id "
                f"{raw_skill_id!r}; expected one of the current bank")
        if skill_id not in mentioned:
            mentioned.append(skill_id)
        issue = entry.get("issue") or ""
        if not isinstance(issue, str):
            raise ModelOutputError(
                f"{prompt}: skill issue must be a string")
        issues[skill_id] = issue
        lo = (max(0, min(_step_index(entry["step_start"], "step_start"),
                         len(steps)))
              if entry.get("step_start") is not None else 0)
        hi = (max(lo, min(_step_index(entry["step_end"], "step_end"),
                          len(steps)))
              if entry.get("step_end") is not None else len(steps))
        ranges[skill_id] = (lo, hi)
        overall_start = min(overall_start, lo)
        overall_end = max(overall_end, hi)
    if exam_skill is not None:
        exam_issue = parsed.get("exam_skill_issue")
        if not isinstance(exam_issue, str) or not exam_issue.strip():
            raise ModelOutputError(
                f"{prompt}: exam_skill_issue must be a non-empty "
                "string")
        if exam_skill in mentioned:
            mentioned.remove(exam_skill)
        mentioned.insert(0, exam_skill)
        issues[exam_skill] = exam_issue.strip()
        # A paper answer is one decision: every finding covers all of it.
        ranges = {skill_id: (0, len(steps)) for skill_id in mentioned}
        overall_start, overall_end = 0, len(steps)
    if not ranges:
        overall_start, overall_end = 0, len(steps)
    evidence = trial_evidence.evidence_for_skills(
        sheet, skill_ids=mentioned, issue="; ".join(
            f"{skill_id}: {issues.get(skill_id, '')}"
            for skill_id in mentioned),
        reason=reason, step_start=overall_start,
        step_end=overall_end, bank=bank, ranges=ranges)
    return TrialFinding(
        sheet_id=sheet.sheet_id,
        unit_id=sheet.coverage.unit_id,
        task_ref=execution.task_ref,
        reason=reason.strip(),
        skill_ids=mentioned,
        profile_note=profile_note.strip(),
        new_skill_note=new_skill_note.strip(),
        step_start=overall_start,
        step_end=overall_end,
        evidence=evidence)


def _exam_variables(sheet: AnswerSheet,
                    bank: Sequence[Skill]) -> Tuple[str, Dict[str, str]]:
    """The skill under examination and the TRIAL_EXAM_ANALYSIS inputs."""
    execution = sheet.execution
    metrics = execution.metrics if isinstance(execution.metrics, dict) else {}
    aux = execution.aux if isinstance(execution.aux, dict) else {}
    facts = {
        "scene_text": metrics.get("scene_text"),
        "expected_behavior": metrics.get("expected_behavior"),
        "judge_reason": aux.get("judge_reason"),
        "plan": execution.answer,
    }
    for name, value in facts.items():
        if not isinstance(value, str) or not value.strip():
            raise CodeBugError(
                f"exam answer sheet {sheet.sheet_id!r} has no {name}; the "
                "exam review reads it from the sheet the exam phase wrote")
    injected = execution.skills_injected
    if not (isinstance(injected, list) and len(injected) == 1
            and isinstance(injected[0], dict)):
        raise CodeBugError(
            f"exam answer sheet {sheet.sheet_id!r} must inject exactly the "
            f"skill under examination, got {injected!r}")
    by_id = {skill.skill_id: skill for skill in bank}
    exam_skill = injected[0].get("skill_id")
    if exam_skill not in by_id:
        raise CodeBugError(
            f"exam answer sheet {sheet.sheet_id!r} examines "
            f"{exam_skill!r}, which is not in the current bank")
    folder = (execution.artifacts or {}).get("skill_folder")
    catalog = folder.get("catalog_text") if isinstance(folder, dict) else None
    if not isinstance(catalog, str) or not catalog.strip():
        raise CodeBugError(
            f"exam answer sheet {sheet.sheet_id!r} has no skill catalog")
    opened = skill_files_read(execution, folder)
    opened_text = "\n\n".join(
        f"SKILL_ID: {skill_id}\n"
        + (by_id[skill_id].content if skill_id in by_id
           else "(not in the current bank)")
        for skill_id in opened) or "(none)"
    return exam_skill, {
        "scene_text": facts["scene_text"],
        "expected_behavior": facts["expected_behavior"],
        "judge_reason": facts["judge_reason"],
        "exam_skill_id": exam_skill,
        "exam_skill_content": by_id[exam_skill].content,
        "skill_catalog": catalog,
        "opened_skills": opened_text,
        "plan": facts["plan"],
    }


def _step_index(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ModelOutputError(
            f"TRIAL_FAILURE_ANALYSIS: {field} must be a non-negative int, "
            f"got {value!r}")
    return value


def _skill_ids(ids: Sequence[str]) -> str:
    return "\n".join(f"- {skill_id}" for skill_id in ids) or "(none)"


def _skill_cards(bank: Sequence[Skill]) -> str:
    return "\n\n".join(
        f"SKILL_ID: {skill.skill_id}\nVERSION: {skill.version}\n"
        f"SUMMARY: {skill.summary}\nCONTENT:\n{skill.content}"
        for skill in bank) or "(empty bank)"
