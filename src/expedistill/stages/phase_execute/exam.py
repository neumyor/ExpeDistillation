"""The exam phase: one generalization exam per changed skill version.

The old exam injected one skill and asked a text question.  The
generalization exam reconstructs the scene a skill was written for, gives
the weak model the whole bank catalog, lets it choose skills and write a
plan, and lets an LLM judge decide whether the choice and plan are right.

The phase never executes a real task.  ``EXAM_SCENE`` builds the scene from
the source trajectory's recorded prefix; ``EXAM_TRIGGER`` selects from the
catalog; ``EXAM_PLAN`` writes the plan; ``EXAM_JUDGE`` grades the episode.
"""

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from expedistill.benchmarks.registry import resolve_plugin
from expedistill.blocks.exam import write_scene
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
from expedistill.composites import trace
from expedistill.composites.provenance import (
    check_provenance,
    find_provenance,
)
from expedistill.composites.unit_ids import make_unit_id
from expedistill.core import ids, vocabulary
from expedistill.core.answer_sheet import UnitSpec
from expedistill.core.config import PHASE_ROLE_EXAM, ExecutePhaseSpec, PhaseInstance
from expedistill.core.models import Exam, Skill
from expedistill.core.trace import Provenance, TraceContext
from expedistill.infra.parallel import run_units
from expedistill.infra.reliability import (
    CodeBugError,
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import (
    append_jsonl, read_jsonl, write_json)
from expedistill.methods.review import attribution
from expedistill.stages.context import StageContext
from expedistill.stages.phase_execute import engine, phase_disk, resolve

__all__ = ["_run_exam"]

_CAUSE_BY_OUTCOME = {
    "control_perfect": vocabulary.CAUSE_WEAK_ALREADY_KNOWS,
    "leaked": vocabulary.CAUSE_LEAK,
    "generation_failed": vocabulary.CAUSE_EXAM_GENERATION_FAILED,
    "infra_failed": vocabulary.CAUSE_ZERO_EXECUTION,
}


def _changed_skills(ctx: StageContext, previous: Sequence[Skill],
                    current: Sequence[Skill]) -> List[Skill]:
    """New, version-increased, or never-examined pending skills."""
    old = {skill.skill_id: skill.version for skill in previous}
    out = []
    for skill in current:
        if skill.skill_id.startswith("bank/merge/"):
            continue
        changed = (skill.skill_id not in old
                   or skill.version > old[skill.skill_id])
        pending_unexamined = (
            skill.status == "pending"
            and not ctx.store.exists(
                "exams", ids.make_exam_id(skill.skill_id, skill.version)))
        if changed or pending_unexamined:
            out.append(skill)
    return sorted(out, key=lambda skill: skill.skill_id)


def _scene_context(ctx: StageContext, provenance: Provenance,
                   view_rules: Any) -> TraceContext:
    """The real situation a skill version's exam is cut from.

    One exact lookup of ``Provenance.scene``; nothing is searched or
    guessed. The task instruction comes from the benchmark when it can
    load the task, else from the sheet's recorded initial prompt.
    """
    benchmark = ctx.config.benchmark

    def instruction_for(task_ref: str) -> str:
        return attribution.task_instruction_for(benchmark, task_ref)

    return trace.resolve(
        ctx.run_dir, provenance.scene,
        instruction_for=instruction_for, view_rules=view_rules)


def _exam_or_generate(ctx: StageContext, exam_id: str, skill: Skill,
                      provenance: Provenance, view_rules: Any,
                      terminal: bool) -> Optional[Exam]:
    """Load an exam or generate it from the skill version's provenance."""
    if ctx.store.exists("exams", exam_id):
        return ctx.store.load("exams", exam_id, Exam)
    if terminal:
        return None
    if provenance.scene is None:
        raise ModelOutputError(
            f"skill {skill.skill_id!r} version {skill.version} has no "
            "scene to cut an exam from")
    context = _scene_context(ctx, provenance, view_rules)
    scene = write_scene(
        context.task_instruction, context.before, context.after,
        skill.content, provenance.note, ctx.handle("examiner"),
        usage=ctx.usage, run_dir=ctx.run_dir)
    exam = Exam(
        exam_id=exam_id,
        skill_id=skill.skill_id,
        skill_version=skill.version,
        scene_text=scene["scene_text"],
        expected_behavior=scene["expected_behavior"],
        reference_answer=scene["reference_answer"],
        scene=provenance.scene,
    )
    ctx.store.save(exam, "exams")
    return exam


def _folder_of(plan: engine._Plan) -> Optional[Dict[str, Any]]:
    """The exam catalog manifest attached to a plan row."""
    manifest = plan.unit.aux.get("skill_folder")
    return manifest if isinstance(manifest, dict) else None


def _run_exam(ctx: StageContext, phase: PhaseInstance,
              spec: ExecutePhaseSpec) -> Dict[str, Any]:
    """Run the generalization exam phase."""
    params = resolve._execution_params(spec)
    delivery, delivery_params = resolve._resolve_delivery(spec)
    rule_name = spec.acceptance or verdicts.default_verdict_rule("exam")
    if rule_name != "exam_judge":
        raise ConfigError(
            f"exam phase requires acceptance rule 'exam_judge', "
            f"got {rule_name!r}")
    compare_rule = resolve._compare_rule(spec)

    bank = phase_disk._bank_skills(ctx, phase)
    previous = phase_disk._bank_before_previous(ctx, phase)
    changed = _changed_skills(ctx, previous, bank)
    skill_by_exam = {
        ids.make_exam_id(skill.skill_id, skill.version): skill
        for skill in changed}
    exam_ids = sorted(skill_by_exam)

    # Fail before any model call: a version whose scene cannot be followed
    # to a real execution would otherwise surface as one generation
    # failure per exam deep into the phase.
    unexamined = [
        skill for exam_id, skill in skill_by_exam.items()
        if not ctx.store.exists("exams", exam_id)]
    problems = check_provenance(ctx.run_dir, unexamined)
    if problems:
        raise CodeBugError(
            f"phase {phase.label!r}: {len(problems)} skill version(s) have "
            "no usable provenance, so their exam cannot be cut from a real "
            f"scene: {problems[:5]}")

    rows = delivery.resolve(bank, exam_ids, delivery_params)
    plans = engine._plan_units(
        rows, None, phase_label=phase.label,
        benchmark=ctx.config.benchmark, role="weak", n_attempts=1,
        attach_folder=False)
    plan_by_exam = {plan.unit.task_ref: plan for plan in plans}

    replayable = phase_disk._earlier_pass_unit_ids(ctx, phase)
    phase_disk._write_units(ctx, phase, [plan.unit for plan in plans])
    reused = phase_disk._reusable_units(
        ctx, phase, [plan.unit for plan in plans])
    phase_disk._write_carried_units(ctx, phase, reused)

    plugin = resolve_plugin(ctx.config.benchmark)
    recorder = UnitFailureRecorder(ctx.run_dir)
    failures_path = (
        phase_disk._phase_dir(ctx, phase)
        / phase_disk.GENERATION_FAILURES_NAME)
    terminal_generation = {
        row["exam_id"] for row in read_jsonl(failures_path)
        if isinstance(row, dict) and row.get("exam_id")}

    def exam_unit(plan: engine._Plan) -> Tuple[str, str]:
        exam_id = plan.unit.task_ref
        skill = skill_by_exam[exam_id]
        if plan.unit.unit_id in reused:
            return "reused", exam_id
        try:
            provenance = find_provenance(
                ctx.run_dir, skill.skill_id, skill.version)
            if provenance is None:
                raise ModelOutputError(
                    f"no provenance for {skill.skill_id!r} "
                    f"version {skill.version}")
            exam = _exam_or_generate(
                ctx, exam_id, skill, provenance, plugin.view_rules,
                terminal=exam_id in terminal_generation)
            if exam is None:
                return "generation_failed", exam_id

            if getattr(params, "bare_control", False):
                bare = (find_judged_attempt(
                    ctx.store, exam_id, "bare", None, None, 0)
                    if plan.unit.unit_id in replayable else None)
                if bare is None:
                    bare = exam_once(
                        store=ctx.store, exam=exam,
                        answerer_handle=ctx.handle("answerer"),
                        judge_handle=ctx.handle("judge"),
                        skill_folder=None, skills_block=None,
                        purpose="bare", skill_id=None, skill_version=None,
                        max_skill_reads=getattr(params, "max_skill_reads", 3),
                        usage=ctx.usage, run_dir=ctx.run_dir)
                if decision.control_perfect_skips_run(
                        compare_rule=compare_rule, control_valid=1,
                        control_success=1 if bare.judge_verdict else 0):
                    return "control_perfect", exam_id

            if getattr(params, "leak_audit", False):
                verdict = leak_verdict_for(
                    store=ctx.store, skill_id=skill.skill_id,
                    skill_version=skill.version,
                    skill_content=skill.content, exam=exam,
                    auditor_handle=ctx.handle("auditor"),
                    usage=ctx.usage, run_dir=ctx.run_dir)
                if verdict["leaked"]:
                    return "leaked", exam_id

            if (plan.unit.unit_id in replayable and
                    find_judged_attempt(
                        ctx.store, exam_id, "acceptance",
                        skill.skill_id, skill.version, 0) is not None):
                return "measured", exam_id

            exam_once(
                store=ctx.store, exam=exam,
                answerer_handle=ctx.handle("answerer"),
                judge_handle=ctx.handle("judge"),
                skill_folder=_folder_of(plan),
                purpose="acceptance", skill_id=skill.skill_id,
                skill_version=skill.version,
                max_skill_reads=getattr(params, "max_skill_reads", 3),
                usage=ctx.usage, run_dir=ctx.run_dir)
            return "measured", exam_id
        except ModelOutputError as exc:
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

    kind_by_exam = {exam_id: kind for kind, exam_id in outcomes}
    bare_unit_by_exam: Dict[str, str] = {}
    skill_unit_by_key: Dict[Tuple[str, int], str] = {}
    specs: List[UnitSpec] = []
    for exam_id in exam_ids:
        skill = skill_by_exam[exam_id]
        plan = plan_by_exam[exam_id]
        if getattr(params, "bare_control", False):
            bare_unit = make_unit_id(
                phase_label=phase.label, benchmark=ctx.config.benchmark,
                task_ref=exam_id, role="weak", skills_injected=None)
            bare_unit_by_exam[exam_id] = bare_unit
            specs.append(UnitSpec(
                unit_id=bare_unit, phase_label=phase.label,
                task_ref=exam_id, role="weak", arm=None,
                expected_attempts=1, injected_skills=None))
        injected = [{"skill_id": skill.skill_id,
                     "version": skill.version}]
        skill_unit = plan.unit.unit_id
        skill_unit_by_key[(skill.skill_id, skill.version)] = skill_unit
        specs.append(UnitSpec(
            unit_id=skill_unit, phase_label=phase.label,
            task_ref=exam_id, role="weak", arm=None,
            expected_attempts=1, injected_skills=injected))

    terminal_overrides: Dict[str, str] = {}
    generation_failed_units: Set[str] = set()
    for exam_id in exam_ids:
        kind = kind_by_exam.get(exam_id, "infra_failed")
        plan = plan_by_exam[exam_id]
        skill_unit = plan.unit.unit_id
        cause = _CAUSE_BY_OUTCOME.get(kind)
        if kind == "reused":
            status, reused_cause, _summary = reused[skill_unit]
            if status != vocabulary.STATUS_NORMAL:
                cause = reused_cause
        if cause:
            terminal_overrides[skill_unit] = cause
            if kind in ("generation_failed", "infra_failed") \
                    and exam_id in bare_unit_by_exam:
                terminal_overrides[bare_unit_by_exam[exam_id]] = cause
        if kind == "generation_failed":
            generation_failed_units.add(skill_unit)
            if exam_id in bare_unit_by_exam:
                generation_failed_units.add(bare_unit_by_exam[exam_id])

    leak_versions: Set[Tuple[str, int]] = set()
    for skill in changed:
        leak = find_leak_verdict(ctx.store, skill.skill_id, skill.version)
        if leak is not None and leak.get("leaked"):
            leak_versions.add((skill.skill_id, skill.version))

    coverage_records = coverage.exam_coverage(
        ctx.store, specs,
        control_perfect_units=set(),
        leak_versions=leak_versions,
        generation_failed_units=generation_failed_units,
        terminal_overrides=terminal_overrides)

    executions = []
    for exam_id in exam_ids:
        attempts = exam_attempts(ctx.store, exam_id)
        bare = find_bare(attempts)
        if bare is not None and exam_id in bare_unit_by_exam:
            executions.append(answer_sheets.execution_from_attempt(
                bare, unit_id=bare_unit_by_exam[exam_id],
                phase_label=phase.label,
                exam=ctx.store.load("exams", exam_id, Exam)))
        folder = _folder_of(plan_by_exam[exam_id])
        for attempt in attempts:
            if attempt.purpose != "acceptance" or attempt.judge_verdict is None:
                continue
            unit_id = skill_unit_by_key.get(
                (attempt.skill_id, attempt.skill_version))
            if unit_id is None:
                continue
            executions.append(answer_sheets.execution_from_attempt(
                attempt, unit_id=unit_id, phase_label=phase.label,
                folder=folder,
                exam=ctx.store.load("exams", exam_id, Exam)))

    sheets = answer_sheets.assemble_answer_sheets(
        executions, coverage_records, phase_label=phase.label)
    answer_sheets.grade_answer_sheets(sheets, rule_name)

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
