"""The practice phase: the executor with the current bank on train or val.

Orchestration:
    1. Resolve the phase (executor, depth, folder capability, playbook)
    2. Deliver the previous learn phase's ACTIVE cards as a skill folder
       (plus the evolved playbook) and materialize the folder
    3. Execute the executor's arm through K1 into the run-level store,
       re-dispatching invalid units like the baseline
    4. Build trial-level answer sheets + coverage, grade them
    5. Write the summary with the mechanism metrics; check for holes

The presentation is the final test's: the same ``folder`` delivery and
``real_folder`` execution, so a card's behaviour here is the behaviour the
test measures. An empty bank still runs as a folder unit (the folder prompt
and the playbook in their usual places, an empty catalog).

The trajectories land in the run-level store beside the baseline's: their
ids carry this phase's label, so no two phases ever share one, and the next
learn phase reads them from there. Every answer sheet's
``execution.artifacts`` carries the trajectory id and the unit's folder
manifest (``files``, ``catalog_text`` and, when shown, ``playbook_text``),
so the learn phase can recover what the executor saw and which cards it
opened (``composites/skill_use.skill_read_events``).
"""

from typing import Any, Dict, List, Set

from expedistill.benchmarks.aux import aux_wiring
from expedistill.composites import (
    answer_sheets,
    coverage,
    phase_report,
    shortfall,
    trigger_metrics,
    unit_summary,
)
from expedistill.composites.skill_use import skill_files_read
from expedistill.composites.unit_ids import unit_id_for_trajectory
from expedistill.core import vocabulary
from expedistill.core.answer_sheet import UnitSpec
from expedistill.core.config import (
    PHASE_ROLE_PRACTICE,
    ExecutePhaseSpec,
    PhaseInstance,
)
from expedistill.infra.reliability import CodeBugError, InfrastructureError
from expedistill.infra.store import write_json
from expedistill.inject import bank_reference
from expedistill.methods.delivery import units as delivery_units
from expedistill.stages.context import StageContext
from expedistill.stages.phase_execute import engine, phase_disk, resolve

__all__ = ["run_practice"]


def run_practice(ctx: StageContext, phase: PhaseInstance,
                 spec: ExecutePhaseSpec) -> Dict[str, Any]:
    """Execute one practice phase; returns its summary."""
    # ========================================================================
    # 1. RESOLVE PARAMETERS
    # ========================================================================
    resolved = resolve.resolve_phase(ctx, phase, spec)
    role = resolved.executor_role
    n_attempts = resolved.n_attempts
    if role is None or n_attempts is None:
        raise CodeBugError(
            f"phase {phase.label!r}: a practice phase resolves its executor "
            "and depth")
    benchmark = ctx.config.benchmark
    tasks = engine.task_map(ctx, list(phase.task_ids))
    bank = phase_disk.bank_skills(ctx, phase)

    # ========================================================================
    # 2. DELIVER AND MATERIALIZE THE FOLDER
    # ========================================================================
    rows = resolved.delivery_rows(bank, list(tasks))
    manifest = phase_disk.folder_manifest(rows)
    if rows and manifest is None:
        raise CodeBugError(
            f"phase {phase.label!r}: the folder delivery resolved rows "
            "without a folder manifest")
    plans = engine.plan_units(
        rows, tasks, phase_label=phase.label, benchmark=benchmark,
        role=role, n_attempts=n_attempts,
        skill_folder_dir=phase_disk.materialize_folder(
            ctx, phase, bank, rows))
    specs = [
        UnitSpec(
            unit_id=plan.unit.unit_id,
            phase_label=phase.label,
            task_ref=plan.unit.task_ref,
            role=role,
            arm=role,
            expected_attempts=n_attempts,
            injected_skills=plan.unit.skills_injected,
            no_valid_cause=vocabulary.CAUSE_ALL_HOLES,
        )
        for plan in plans]
    phase_disk.write_units(ctx, phase, [plan.unit for plan in plans])

    # ========================================================================
    # 3. EXECUTE THE ARM (run-level store, invalid redispatch)
    # ========================================================================
    arm_directory = phase_disk.arm_dir(ctx, phase, role)
    shortfall_path = arm_directory / shortfall.SHORTFALL_NAME
    accepted: Set[str] = set(shortfall.read_unit_ids(shortfall_path))
    trajectories, holes = engine.run_arm_units(
        ctx, phase, plans=plans, store=ctx.store, arm=role,
        limit=resolved.redispatch_limit, accepted=set(accepted))
    if holes and ctx.run_settings.allow_infra_shortfall:
        accepted |= holes
        shortfall.write_unit_ids(shortfall_path, accepted)
        holes = set()

    # ========================================================================
    # 4. TRIAL SHEETS + COVERAGE + GRADING
    # ========================================================================
    aux_metric, aux_scorer = aux_wiring(benchmark)
    coverage_records = coverage.store_coverage(
        ctx.store, specs, accepted_holes=accepted)
    executions = [
        answer_sheets.execution_from_trajectory(
            trajectory,
            unit_id=unit_id_for_trajectory(
                phase_label=phase.label, benchmark=benchmark,
                trajectory=trajectory),
            phase_label=phase.label,
            arm=role,
            aux_success=(aux_scorer(trajectory)
                         if aux_scorer is not None
                         and trajectory.correctness is not None else None),
            folder=manifest)
        for trajectory in trajectories]
    sheets = answer_sheets.assemble_answer_sheets(
        executions, coverage_records, phase_label=phase.label)
    answer_sheets.grade_answer_sheets(sheets, resolved.verdict_rule)

    # ========================================================================
    # 5. WRITE SUMMARY AND CHECK HOLES
    # ========================================================================
    report = phase_report.build_phase_report(
        sheets, specs, phase_kind=PHASE_ROLE_PRACTICE, learning_usable=True)
    every_hole = sorted(set(accepted) | holes)
    summary: Dict[str, Any] = {
        "role": PHASE_ROLE_PRACTICE,
        "tasks": spec.tasks,
        "executor": role,
        "n_attempts": n_attempts,
        "bank_reference": bank_reference(
            delivery_units.active_bank(bank)),
        "playbook_shown": bool(resolved.playbook),
        "metrics": unit_summary.summarize_metrics(
            unit_summary.task_metrics(report.units, n_attempts),
            n_attempts, aux_metric=aux_metric),
        "skill_use": trigger_metrics.card_use_metrics(report.sheets),
        "skill_files_read": _read_sets(trajectories, manifest),
        "infra_missing": every_hole,
        "infra_accepted": sorted(accepted),
        "complete": not every_hole,
        "infra_shortfall_accepted": bool(accepted),
    }

    phase_dir = phase_disk.execute_phase_dir(ctx, phase)
    answer_sheets.write_answer_sheets(
        report.sheets, phase_dir / phase_report.ANSWER_SHEETS_DIR)
    answer_sheets.write_coverage(
        coverage_records, phase_dir / phase_report.COVERAGE_NAME)
    phase_report.write_unit_specs(specs, phase_dir)
    write_json(phase_dir / phase_disk.SUMMARY_NAME, summary, indent=2)

    if holes:
        raise InfrastructureError(
            f"phase {phase.label!r}: {len(holes)} unit(s) exhausted their "
            "infra and redispatch budgets and stored nothing (listed in "
            "the summary under infra_missing); resume the run to retry "
            "them, or accept the shortfall with --allow-infra-shortfall")
    return summary


def _read_sets(trajectories: List[Any],
               manifest: Any) -> Dict[str, List[str]]:
    """``{trajectory id: [skill ids read, first-read order]}``."""
    if manifest is None:
        return {}
    return {trajectory.trajectory_id: skill_files_read(trajectory, manifest)
            for trajectory in trajectories}
