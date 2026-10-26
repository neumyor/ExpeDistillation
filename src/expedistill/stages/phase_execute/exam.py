"""The exam phase: one exam per pending bank skill.

Orchestration:
    1. Resolve parameters and verify exam-specific config
    2. Select pending skills from bank
    3. Plan exam units (one per skill)
    4. Execute exams in parallel (three-stage workflow per exam)
    5. Generate acceptance report from exam attempts
    6. Write summary and check for failures

Components used:
    - blocks.author: exam generation
    - blocks.view: render step views for exams
    - composites.exam_once: single exam execution
    - composites.acceptance_leak: leak audit
    - composites.acceptance_skill_status: find judged attempts
    - infra.parallel: parallel execution orchestration

Three-stage exam workflow per skill:
    a) Generate exam from subtask trajectory
    b) Answer bare (control)
    c) Leak audit
    d) Answer with skill (if control not perfect and no leak)
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from expedistill.benchmarks.registry import resolve_plugin
from expedistill.blocks.author import gen_exam
from expedistill.blocks.view import render_steps_view
from expedistill.composites import (
    answer_sheets,
    coverage,
    decision,
    phase_report,
    verdicts,
)
from expedistill.composites.acceptance_leak import (
    find_leak_verdict,
    leak_verdict_for,
)
from expedistill.composites.acceptance_skill_status import (
    exam_attempts,
    find_bare,
    find_judged_attempt,
)
from expedistill.composites.exam_once import exam_once
from expedistill.composites.skill_status import stage3_candidates
from expedistill.composites.unit_ids import make_unit_id
from expedistill.core import ids, vocabulary
from expedistill.core.answer_sheet import UnitSpec
from expedistill.core.config import PHASE_ROLE_EXAM, ExecutePhaseSpec, PhaseInstance
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
from expedistill.stages.phase_execute import engine, phase_disk, resolve

__all__ = ["_run_exam"]

#: One exam unit's terminal outcome -> its coverage cause.  ``measured``
#: and a normal ``reused`` unit have no override: their sheets are read
#: from the attempts store.
_CAUSE_BY_OUTCOME = {
    "control_perfect": vocabulary.CAUSE_WEAK_ALREADY_KNOWS,
    "leaked": vocabulary.CAUSE_LEAK,
    "generation_failed": vocabulary.CAUSE_EXAM_GENERATION_FAILED,
    "infra_failed": vocabulary.CAUSE_ZERO_EXECUTION,
}


def _pending_bank_skills(bank: Sequence[Skill]) -> List[Skill]:
    """Skills still under test (pending status only)."""
    return sorted((skill for skill in bank if skill.status == "pending"),
                  key=lambda skill: skill.subtask_id)


def _run_exam(ctx: StageContext, phase: PhaseInstance,
              spec: ExecutePhaseSpec) -> Dict[str, Any]:
    """Execute exam phase: generate and answer exams for pending skills.

    Each exam has three stages:
    1. Generate exam from subtask trajectory
    2. Answer bare (control)
    3. Leak audit + answer with skill (if not skipped)

    Replay protection: judged attempts from earlier passes of THIS phase
    are reused (never re-answered).
    """
    # ========================================================================
    # 1. RESOLVE PARAMETERS AND VERIFY CONFIG
    # ========================================================================
    resolve._execution_params(spec)  # Loud check (exam has no knobs)

    rule_name = spec.acceptance or verdicts.default_verdict_rule("exam")
    if rule_name != "exam_judge":
        raise ConfigError(
            f"exam phase requires acceptance rule 'exam_judge', "
            f"got {rule_name!r}")

    delivery, delivery_params = resolve._resolve_delivery(spec)
    compare_rule = resolve._compare_rule(spec)

    # ========================================================================
    # 2. SELECT PENDING SKILLS AND PLAN UNITS
    # ========================================================================
    bank = phase_disk._bank_skills(ctx, phase)
    pending = _pending_bank_skills(bank)

    skill_by_exam = {ids.make_exam_id(skill.subtask_id): skill
                     for skill in pending}
    # The exam REPORT universe is every stage-3 candidate, not only the
    # pending ones: an already accepted skill can still have a judged v2
    # attempt from an earlier exam phase, and the learn phase must see it
    # (that is the old reader's universe).  Only the pending skills are
    # dispatched below.
    all_skill_by_exam = {
        ids.make_exam_id(skill.subtask_id): skill
        for skill in stage3_candidates(ctx.store)}
    # A pending bank artifact can carry a skill that is not (yet) in the
    # store in a seeded/refreshed fixture; it is still a plan target, so
    # the report universe is the union.
    all_skill_by_exam.update(skill_by_exam)

    rows = delivery.resolve(bank, sorted(skill_by_exam), delivery_params)
    plans = engine._plan_units(rows, None, phase_label=phase.label,
                               benchmark=ctx.config.benchmark, role="weak",
                               n_attempts=1)

    # Replay protection: unit IDs from earlier passes of THIS phase
    replayable = phase_disk._earlier_pass_unit_ids(ctx, phase)

    phase_disk._write_units(ctx, phase, [plan.unit for plan in plans])
    reused = phase_disk._reusable_units(ctx, phase,
                                        [plan.unit for plan in plans])

    # ========================================================================
    # 3. LOAD SUPPORTING DATA
    # ========================================================================
    plugin = resolve_plugin(ctx.config.benchmark)
    recorder = UnitFailureRecorder(ctx.run_dir)
    failures_path = phase_disk._phase_dir(ctx, phase) / phase_disk.GENERATION_FAILURES_NAME
    terminal_generation = {row["exam_id"]
                           for row in read_jsonl(failures_path)}

    # ========================================================================
    # 4. EXECUTE EXAMS IN PARALLEL
    # ========================================================================
    def exam_unit(plan: engine._Plan) -> Tuple[str, str]:
        """Execute one exam unit through the three-stage workflow."""
        exam_id = plan.unit.task_ref
        skill = skill_by_exam[exam_id]

        # Rule-1 reuse: entire unit already measured
        if plan.unit.unit_id in reused:
            return "reused", exam_id

        try:
            # Stage 1: Generate or load exam
            exam = _exam_or_generate(ctx, exam_id, plugin.view_rules,
                                    terminal=exam_id in terminal_generation)
            if exam is None:
                return "generation_failed", exam_id

            # Stage 2: Bare answer (control)
            bare = (find_judged_attempt(ctx.store, exam_id, "bare",
                                       None, None, 0)
                    if plan.unit.unit_id in replayable else None)

            if bare is None:
                bare = exam_once(
                    store=ctx.store, exam=exam,
                    answerer_handle=ctx.handle("answerer"),
                    judge_handle=ctx.handle("judge"), skills_block="",
                    purpose="bare", usage=ctx.usage, run_dir=ctx.run_dir)

            # Check if control is perfect (skip with-skill answer)
            if decision.control_perfect_skips_run(
                    compare_rule=compare_rule, control_valid=1,
                    control_success=1 if bare.judge_verdict else 0):
                return "control_perfect", exam_id

            # Stage 3a: Leak audit
            verdict = leak_verdict_for(
                store=ctx.store, skill_id=skill.skill_id,
                skill_version=skill.version, skill_content=skill.content,
                exam=exam, auditor_handle=ctx.handle("auditor"),
                usage=ctx.usage, run_dir=ctx.run_dir)

            if verdict["leaked"]:
                return "leaked", exam_id

            # Stage 3b: With-skill answer
            if (plan.unit.unit_id in replayable and
                find_judged_attempt(ctx.store, exam_id, "acceptance",
                                  skill.skill_id, skill.version, 0) is not None):
                return "measured", exam_id

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
            # Terminal generation/answer/judge failure
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

    # ========================================================================
    # 5. TRIAL SHEETS + COVERAGE + GRADING
    # ========================================================================
    kind_by_exam = {exam_id: kind for kind, exam_id in outcomes}
    pending_unit_by_exam = {plan.unit.task_ref: plan.unit.unit_id
                            for plan in plans}
    specs: List[UnitSpec] = []
    bare_unit_by_exam: Dict[str, str] = {}
    skill_unit_by_key: Dict[Tuple[str, int], str] = {}
    for exam_id in sorted(all_skill_by_exam):
        skill = all_skill_by_exam[exam_id]
        bare_unit = make_unit_id(
            phase_label=phase.label, benchmark=ctx.config.benchmark,
            task_ref=exam_id, role="weak", skills_injected=None)
        bare_unit_by_exam[exam_id] = bare_unit
        specs.append(UnitSpec(
            unit_id=bare_unit, phase_label=phase.label, task_ref=exam_id,
            role="weak", arm=None, expected_attempts=1,
            injected_skills=None))
        injected = [{"skill_id": skill.skill_id,
                     "version": skill.version}]
        skill_unit = pending_unit_by_exam.get(exam_id) or make_unit_id(
            phase_label=phase.label, benchmark=ctx.config.benchmark,
            task_ref=exam_id, role="weak", skills_injected=injected)
        specs.append(UnitSpec(
            unit_id=skill_unit, phase_label=phase.label, task_ref=exam_id,
            role="weak", arm=None, expected_attempts=1,
            injected_skills=injected))
        skill_unit_by_key[(skill.skill_id, skill.version)] = skill_unit

    terminal_overrides: Dict[str, str] = {}
    leak_versions: set = set()
    for plan in plans:
        exam_id = plan.unit.task_ref
        kind = kind_by_exam[exam_id]
        skill_unit = plan.unit.unit_id
        bare_unit = bare_unit_by_exam[exam_id]
        cause = _CAUSE_BY_OUTCOME.get(kind)
        if kind == "reused":
            status, reused_cause = reused[skill_unit]
            if status != vocabulary.STATUS_NORMAL:
                cause = reused_cause
        if cause:
            terminal_overrides[skill_unit] = cause
            if kind in ("generation_failed", "infra_failed"):
                terminal_overrides[bare_unit] = cause
    # A leak is a fact about (skill, version), including a candidate that
    # was accepted before this phase; read it back rather than relying only
    # on this pass's outcomes.
    for skill in all_skill_by_exam.values():
        leak = find_leak_verdict(ctx.store, skill.skill_id, skill.version)
        if leak is not None and leak.get("leaked"):
            leak_versions.add((skill.skill_id, skill.version))

    generation_failed_units: set = set()
    for exam_id in terminal_generation:
        if exam_id in all_skill_by_exam:
            generation_failed_units.add(bare_unit_by_exam[exam_id])
            skill = all_skill_by_exam[exam_id]
            unit_id = skill_unit_by_key.get((skill.skill_id, skill.version))
            if unit_id is not None:
                generation_failed_units.add(unit_id)

    coverage_records = coverage.exam_coverage(
        ctx.store, specs,
        control_perfect_units=set(),
        leak_versions=leak_versions,
        generation_failed_units=generation_failed_units,
        terminal_overrides=terminal_overrides)

    executions = []
    for exam_id in sorted(all_skill_by_exam):
        attempts = exam_attempts(ctx.store, exam_id)
        bare = find_bare(attempts)
        if bare is not None and bare.judge_verdict is not None:
            executions.append(answer_sheets.execution_from_attempt(
                bare, unit_id=bare_unit_by_exam[exam_id],
                phase_label=phase.label))
        for attempt in attempts:
            if attempt.purpose != "acceptance":
                continue
            if attempt.judge_verdict is None:
                continue
            unit_id = skill_unit_by_key.get(
                (attempt.skill_id, attempt.skill_version))
            if unit_id is None:
                continue
            executions.append(answer_sheets.execution_from_attempt(
                attempt, unit_id=unit_id, phase_label=phase.label))

    sheets = answer_sheets.assemble_answer_sheets(
        executions, coverage_records, phase_label=phase.label)
    answer_sheets.grade_answer_sheets(sheets, rule_name)

    # ========================================================================
    # 6. WRITE SUMMARY AND CHECK FAILURES
    # ========================================================================
    infra_failed_list = sorted(
        exam_id for kind, exam_id in outcomes if kind == "infra_failed")
    summary = {
        "role": PHASE_ROLE_EXAM,
        "infra_missing": infra_failed_list,
        "infra_accepted": [],
        "complete": not infra_failed_list,
        "infra_shortfall_accepted": False,
    }
    report = phase_report.build_phase_report(
        sheets, specs, phase_kind=PHASE_ROLE_EXAM,
        learning_usable=True, summary=summary)

    phase_dir = phase_disk._phase_dir(ctx, phase)
    answer_sheets.write_answer_sheets(
        report.sheets, phase_dir / phase_report.ANSWER_SHEETS_DIR)
    answer_sheets.write_coverage(
        coverage_records, phase_dir / phase_report.COVERAGE_NAME)
    phase_report.write_unit_specs(specs, phase_dir)
    phase_disk._write_reuse_index(
        ctx, phase,
        [(unit.unit_id, unit.status, unit.cause)
         for unit in report.units])

    write_json(phase_dir / phase_disk.SUMMARY_NAME, summary, indent=2)

    if summary["infra_missing"]:
        raise InfrastructureError(
            f"phase {phase.label!r}: {len(summary['infra_missing'])} exam "
            "unit(s) pending on infrastructure errors; resume to retry")

    return summary


# ============================================================================
# Supporting functions
# ============================================================================

def _exam_or_generate(ctx: StageContext, exam_id: str, view_rules: Any,
                      *, terminal: bool) -> Optional[Exam]:
    """Load stored exam or generate one at execution time.

    Terminal failures (generation budget exhausted) are never re-offered.
    """
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
