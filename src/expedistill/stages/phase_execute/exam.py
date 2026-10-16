"""The exam phase (A): one exam per pending bank skill.

The exam is generated at execution time, answered bare, audited for
leaks, then answered with the skill injected. The attempts and the
leak-verdict ledger live in the run-level shared stores (their ids are
exam-anchored and never collide across phases); the invalid-redispatch
loop does not apply here -- an answer run is either judged, or the
pipeline's own repair budget died (the terminal
``exam_generation_failed`` cause).
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from expedistill.benchmarks.registry import resolve_plugin
from expedistill.blocks.author import gen_exam
from expedistill.blocks.view import render_steps_view
from expedistill.composites import decision, verdicts
from expedistill.composites.acceptance_leak import (
    find_leak_verdict,
    leak_verdict_for,
)
from expedistill.composites.acceptance_skill_status import (
    _exam_attempts,
    _find_bare,
)
from expedistill.composites.exam_grading import judge_budget_exhausted
from expedistill.composites.exam_once import exam_once
from expedistill.composites.reports import _run_record_from_attempt
from expedistill.core import ids
from expedistill.core.config import (
    PHASE_ROLE_EXAM,
    ExecutePhaseSpec,
    PhaseInstance,
)
from expedistill.core.contracts import AcceptanceReportRow
from expedistill.core.models import Exam, Skill, Subtask, Trajectory
from expedistill.infra.parallel import run_units
from expedistill.infra.reliability import (
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import append_jsonl, read_jsonl, write_json
from expedistill.stages.context import StageContext
from expedistill.stages.phase_execute.engine import _Plan, _plan_units
from expedistill.stages.phase_execute.phase_disk import (
    GENERATION_FAILURES_NAME,
    SUMMARY_NAME,
    _bank_skills,
    _phase_dir,
    _rebased_row,
    _reused_rows,
    _write_report,
    _write_units,
)
from expedistill.stages.phase_execute.resolve import (
    _compare_rule,
    _execution_params,
    _resolve_delivery,
)

__all__ = ["_pending_bank_skills", "_run_exam"]


def _pending_bank_skills(bank: Sequence[Skill]) -> List[Skill]:
    """The bank members still under test (the A-phase universe is one
    exam per PENDING skill: accepted / merged / discarded members have
    their verdicts already)."""
    return sorted((skill for skill in bank if skill.status == "pending"),
                  key=lambda skill: skill.subtask_id)


def _run_exam(ctx: StageContext, phase: PhaseInstance,
              spec: ExecutePhaseSpec) -> Dict[str, Any]:
    """One exam per pending bank skill: generate (at execution time, the
    R8 ruling), answer bare, audit for leaks, answer with the skill.

    An exam's control unit is its own bare answer, so the frozen order
    holds WITHIN each unit: the control runs and is judged first, then
    :func:`decision.control_perfect_skips_run` decides whether the
    with-skill answer is skipped, then the with-skill answer runs --
    after the D8 leak audit (boundaries doc C1: the audit checks before
    the with-skill call, so a leaked version never spends one).

    The rewrite loop is NOT here: a revise decision belongs to the next
    learn phase, which re-measures the new version in the following A
    phase (rule 1 reuses every unchanged unit). Attempts and the
    leak-verdict ledger live in the run-level shared stores (their ids
    are exam-anchored and never collide across phases). The invalid
    redispatch loop does not apply to the exam role: an answer run is
    either judged, or the pipeline's own repair budget died -- the
    terminal ``exam_generation_failed`` cause, never a re-dispatch."""
    _execution_params(spec)  # the exam execution has no knobs; loud check
    rule_name = spec.acceptance or verdicts.default_verdict_rule("exam")
    if rule_name != "exam_judge":
        raise ConfigError(
            "the exam phase's runs are model-judged attempts; its "
            f"acceptance rule must be 'exam_judge', got {rule_name!r}")
    delivery, delivery_params = _resolve_delivery(spec)
    bank = _bank_skills(ctx, phase)
    pending = _pending_bank_skills(bank)
    skill_by_exam = {ids.make_exam_id(skill.subtask_id): skill
                     for skill in pending}
    rows = delivery.resolve(bank, sorted(skill_by_exam), delivery_params)
    plans = _plan_units(rows, None, phase_label=phase.label,
                        benchmark=ctx.config.benchmark, role="weak",
                        n_attempts=1)
    _write_units(ctx, phase, [plan.unit for plan in plans])
    reused = _reused_rows(ctx, phase, [plan.unit for plan in plans])
    plugin = resolve_plugin(ctx.config.benchmark)
    recorder = UnitFailureRecorder(ctx.run_dir)
    failures_path = _phase_dir(ctx, phase) / GENERATION_FAILURES_NAME
    terminal_generation = {row["exam_id"]
                           for row in read_jsonl(failures_path)}
    compare_rule = _compare_rule(spec)

    def exam_unit(plan: _Plan) -> Tuple[str, str]:
        """One exam unit's disposition; an expected failure stays here,
        anything escaping is fatal to the batch (run_units' rule)."""
        exam_id = plan.unit.task_ref
        skill = skill_by_exam[exam_id]
        if plan.unit.unit_id in reused:
            return "reused", exam_id
        try:
            exam = _exam_or_generate(ctx, exam_id, plugin.view_rules,
                                     terminal=exam_id in
                                     terminal_generation)
            if exam is None:
                return "generation_failed", exam_id
            # -- the control first: the bare answer, judged ---------------
            bare = exam_once(
                store=ctx.store, exam=exam,
                answerer_handle=ctx.handle("answerer"),
                judge_handle=ctx.handle("judge"), skills_block="",
                purpose="bare", usage=ctx.usage, run_dir=ctx.run_dir)
            if decision.control_perfect_skips_run(
                    compare_rule=compare_rule, control_valid=1,
                    control_success=1 if bare.judge_verdict else 0):
                return "control_perfect", exam_id
            # -- the D8 leak audit BEFORE the with-skill call (C1) -------
            verdict = leak_verdict_for(
                store=ctx.store, skill_id=skill.skill_id,
                skill_version=skill.version, skill_content=skill.content,
                exam=exam, auditor_handle=ctx.handle("auditor"),
                usage=ctx.usage, run_dir=ctx.run_dir)
            if verdict["leaked"]:
                return "leaked", exam_id
            # -- the with-skill answer, judged --------------------------
            exam_once(
                store=ctx.store, exam=exam,
                answerer_handle=ctx.handle("answerer"),
                judge_handle=ctx.handle("judge"),
                skills_block=plan.unit.skills_block,
                purpose="acceptance", skill_id=skill.skill_id,
                skill_version=skill.version, usage=ctx.usage,
                run_dir=ctx.run_dir)
            return "measured", exam_id
        except ModelOutputError as exc:
            # The pipeline's own repair budget died (generation, answer or
            # judge): terminal within this run, first failure row wins.
            if exam_id not in terminal_generation:
                append_jsonl(failures_path, {
                    "exam_id": exam_id,
                    "error_class": type(exc).__name__,
                    "message": " ".join(str(exc).split())[:1000]})
                terminal_generation.add(exam_id)
            return "generation_failed", exam_id
        except InfrastructureError as exc:
            recorder.record(plan.unit.unit_id, phase.label, exc, exc)
            return "infra_failed", exam_id

    outcomes = run_units(exam_unit, plans, ctx.run_settings.workers)
    report = _exam_report(ctx, phase, pending, rule_name, reused)
    _write_report(ctx, phase, report)

    counts: Dict[str, List[str]] = {}
    for kind, exam_id in outcomes:
        counts.setdefault(kind, []).append(exam_id)
    summary = {
        "role": PHASE_ROLE_EXAM,
        "universe": [plan.unit.task_ref for plan in plans],
        "measured": sorted(counts.get("measured", [])),
        "reused": sorted(counts.get("reused", [])),
        "control_perfect": sorted(counts.get("control_perfect", [])),
        "leaked": sorted(counts.get("leaked", [])),
        "generation_failed": sorted(counts.get("generation_failed", [])),
        "infra_failed": sorted(counts.get("infra_failed", [])),
        "report_rows": len(report),
    }
    write_json(_phase_dir(ctx, phase) / SUMMARY_NAME, summary, indent=2)
    if summary["infra_failed"]:
        raise InfrastructureError(
            f"phase {phase.label!r}: {len(summary['infra_failed'])} exam "
            "unit(s) are pending on infrastructure errors, recorded in the "
            "unit-failure ledger; resume the run to retry them")
    return summary


def _exam_or_generate(ctx: StageContext, exam_id: str, view_rules: Any,
                      *, terminal: bool) -> Optional[Exam]:
    """The stored exam, or one G3 generation call at execution time (the
    R8 ruling: the call moved from the authoring phase to here). A
    generation whose repair budget already died is never re-offered --
    the unit's row is its ``exam_generation_failed`` cause."""
    if ctx.store.exists("exams", exam_id):
        return ctx.store.load("exams", exam_id, Exam)
    if terminal:
        return None
    subtask_id = exam_id[: -len("/exam")]
    subtask = ctx.store.load("subtasks", subtask_id, Subtask)
    trajectory = ctx.store.load("trajectories", subtask.trajectory_id,
                                Trajectory)
    start, end = subtask.step_range
    exam = gen_exam(subtask,
                    render_steps_view(trajectory.steps[start:end],
                                      view_rules),
                    ctx.handle("examiner"),
                    usage=ctx.usage, run_dir=ctx.run_dir)
    ctx.store.save(exam, "exams")
    return exam


def _exam_report(ctx: StageContext, phase: PhaseInstance,
                 pending: Sequence[Skill], rule_name: str,
                 reused: Dict[str, AcceptanceReportRow]
                 ) -> List[AcceptanceReportRow]:
    """The A phase's report rows from the shared stores' on-disk state:
    one bare control row per exam plus one row per measured (skill,
    version) -- the frozen reader's shape, over THIS phase's universe,
    with rule-1 reuse rows joined in."""
    rows: List[AcceptanceReportRow] = []
    for skill in pending:
        exam_id = ids.make_exam_id(skill.subtask_id)
        injected = [{"skill_id": skill.skill_id,
                     "version": skill.version}]
        unit_id = verdicts.make_unit_id(
            phase_label=phase.label, benchmark="exam", task_ref=exam_id,
            role="weak", skills_injected=injected)
        if not ctx.store.exists("exams", exam_id):
            rows.append(verdicts.summarize_unit(
                unit_id=verdicts.make_unit_id(
                    phase_label=phase.label, benchmark="exam",
                    task_ref=exam_id, role="weak", skills_injected=injected),
                phase_label=phase.label, role="weak",
                injected_skills=injected, runs=[],
                status=verdicts.STATUS_NOT_TESTABLE,
                cause=verdicts.CAUSE_EXAM_GENERATION_FAILED,
                artifacts={"skill_id": skill.skill_id}))
            continue
        attempts = _exam_attempts(ctx.store, exam_id)

        # -- the bare control row ----------------------------------------
        bare = _find_bare(attempts)
        bare_runs: List[Dict[str, Any]] = []
        if bare is not None and bare.judge_verdict is not None:
            verdict, basis = verdicts.verdict_from_run(
                _run_record_from_attempt(bare), rule_name=rule_name)
            bare_runs.append({"run": bare.attempt_id, "verdict": verdict,
                              "basis": basis})
        if bare_runs:
            bare_status, bare_cause = verdicts.STATUS_NORMAL, ""
        elif any(judge_budget_exhausted(attempt)
                 for attempt in attempts
                 if attempt.purpose == "bare"
                 and attempt.skill_id is None):
            bare_status = verdicts.STATUS_NOT_TESTABLE
            bare_cause = verdicts.CAUSE_EXAM_GENERATION_FAILED
        else:
            bare_status = verdicts.STATUS_NOT_TESTABLE
            bare_cause = verdicts.CAUSE_ZERO_EXECUTION
        rows.append(verdicts.summarize_unit(
            unit_id=verdicts.make_unit_id(
                phase_label=phase.label, benchmark="exam",
                task_ref=exam_id, role="weak", skills_injected=None),
            phase_label=phase.label, role="weak", injected_skills=None,
            runs=bare_runs, status=bare_status, cause=bare_cause,
            artifacts={"exam_id": exam_id,
                       "attempt_ids": [run["run"] for run in bare_runs]}))

        # -- the with-skill row: reuse, leak, skip, judged, or owed -------
        reused_row = reused.get(unit_id)
        if reused_row is not None:
            rows.append(_rebased_row(reused_row, unit_id=unit_id,
                                     phase_label=phase.label))
            continue
        leak = find_leak_verdict(ctx.store, skill.skill_id, skill.version)
        if leak is not None and leak.get("leaked"):
            rows.append(verdicts.summarize_unit(
                unit_id=unit_id, phase_label=phase.label, role="weak",
                injected_skills=injected, runs=[],
                status=verdicts.STATUS_NOT_TESTABLE,
                cause=verdicts.CAUSE_LEAK,
                artifacts={"exam_id": exam_id}))
            continue
        judged = sorted(
            (attempt for attempt in attempts
             if attempt.purpose == "acceptance"
             and attempt.skill_id == skill.skill_id
             and attempt.skill_version == skill.version
             and attempt.judge_verdict is not None),
            key=lambda attempt: ids.parse_attempt_id(attempt.attempt_id)[1])
        runs: List[Dict[str, Any]] = []
        for attempt in judged:
            verdict, basis = verdicts.verdict_from_run(
                _run_record_from_attempt(attempt), rule_name=rule_name)
            runs.append({"run": attempt.attempt_id, "verdict": verdict,
                         "basis": basis})
        if runs:
            rows.append(verdicts.summarize_unit(
                unit_id=unit_id, phase_label=phase.label, role="weak",
                injected_skills=injected, runs=runs,
                status=verdicts.STATUS_NORMAL, cause="",
                artifacts={"exam_id": exam_id,
                           "attempt_ids": [run["run"] for run in runs]}))
            continue
        if bare is not None and bare.judge_verdict:
            # The control was perfect, so the with-skill answer was
            # skipped (rule 3): zero executions, no headroom.
            rows.append(verdicts.summarize_unit(
                unit_id=unit_id, phase_label=phase.label, role="weak",
                injected_skills=injected, runs=[],
                status=verdicts.STATUS_NOT_RUN_CONTROL_PERFECT,
                cause=verdicts.CAUSE_WEAK_ALREADY_KNOWS,
                artifacts={"exam_id": exam_id}))
            continue
        status, cause = (
            (verdicts.STATUS_NOT_TESTABLE,
             verdicts.CAUSE_EXAM_GENERATION_FAILED)
            if any(judge_budget_exhausted(attempt)
                   for attempt in attempts
                   if attempt.purpose == "acceptance"
                   and attempt.skill_id == skill.skill_id
                   and attempt.skill_version == skill.version)
            else (verdicts.STATUS_NOT_TESTABLE,
                  verdicts.CAUSE_ZERO_EXECUTION))
        rows.append(verdicts.summarize_unit(
            unit_id=unit_id, phase_label=phase.label, role="weak",
            injected_skills=injected, runs=[], status=status, cause=cause,
            artifacts={"exam_id": exam_id}))
    return sorted(rows, key=lambda row: row.unit_id)
