"""Per-trial review: one free-text diagnosis for each failed trial.

The analysis is intentionally concurrent and independent per trial.  It does
not choose final skill actions; the aggregation agent does that after every
finding exists.
"""

from typing import Any, Dict, List, Sequence, Set, Tuple

from expedistill.benchmarks.registry import get_plugin
from expedistill.blocks.attribution import _canonical_skill_id
from expedistill.blocks.view import render_steps_view
from expedistill.core.answer_sheet import AnswerSheet, PhaseReport
from expedistill.core.models import Skill
from expedistill.infra.parallel import run_parallel
from expedistill.infra.reliability import (
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
               and is_failed_trial(sheet) and sheet.sheet_id not in known]
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
    available = trial_evidence.available_skill_ids(sheet, bank)
    parsed = call_model(
        handle, "TRIAL_FAILURE_ANALYSIS",
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
            "TRIAL_FAILURE_ANALYSIS: expected a JSON object, got "
            f"{type(parsed).__name__}")
    reason = parsed.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ModelOutputError(
            "TRIAL_FAILURE_ANALYSIS: reason must be a non-empty string")
    profile_note = parsed.get("profile_note") or ""
    new_skill_note = parsed.get("new_skill_note") or ""
    if not isinstance(profile_note, str) or not isinstance(new_skill_note, str):
        raise ModelOutputError(
            "TRIAL_FAILURE_ANALYSIS: profile_note and new_skill_note must "
            "be strings")
    known = {skill.skill_id for skill in bank}
    folder = (execution.artifacts or {}).get("skill_folder")
    aliases = attribution._folder_aliases(
        folder if isinstance(folder, dict) else None)
    mentioned: List[str] = []
    issues: Dict[str, str] = {}
    start, end = 0, len(steps)
    raw_skills = parsed.get("skills") or []
    if not isinstance(raw_skills, list):
        raise ModelOutputError(
            "TRIAL_FAILURE_ANALYSIS: skills must be a list")
    for entry in raw_skills:
        if not isinstance(entry, dict):
            raise ModelOutputError(
                "TRIAL_FAILURE_ANALYSIS: each skills entry must be an object")
        raw_skill_id = entry.get("skill_id")
        if raw_skill_id == "":
            continue
        skill_id = _canonical_skill_id(raw_skill_id, known, aliases)
        if skill_id is None:
            raise ModelOutputError(
                "TRIAL_FAILURE_ANALYSIS: unknown skill_id "
                f"{raw_skill_id!r}; expected one of the current bank")
        if skill_id not in mentioned:
            mentioned.append(skill_id)
        issue = entry.get("issue") or ""
        if not isinstance(issue, str):
            raise ModelOutputError(
                "TRIAL_FAILURE_ANALYSIS: skill issue must be a string")
        issues[skill_id] = issue
        if entry.get("step_start") is not None:
            start = max(0, min(_step_index(
                entry["step_start"], "step_start"), len(steps)))
        if entry.get("step_end") is not None:
            end = max(start, min(_step_index(
                entry["step_end"], "step_end"), len(steps)))
    evidence = trial_evidence.evidence_for_skills(
        sheet, skill_ids=mentioned, issue="; ".join(
            f"{skill_id}: {issues.get(skill_id, '')}"
            for skill_id in mentioned),
        reason=reason, step_start=start, step_end=end, bank=bank)
    return TrialFinding(
        sheet_id=sheet.sheet_id,
        unit_id=sheet.coverage.unit_id,
        task_ref=execution.task_ref,
        reason=reason.strip(),
        skill_ids=mentioned,
        profile_note=profile_note.strip(),
        new_skill_note=new_skill_note.strip(),
        step_start=start,
        step_end=end,
        evidence=evidence)


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
