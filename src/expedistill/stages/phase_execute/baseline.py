"""The baseline phase: both bare arms over the train set.

Orchestration:
    1. Resolve parameters (execution, delivery, verdict rule)
    2. Plan units for both arms (strong, weak)
    3. Execute both arms through K1 (run-level store)
    4. Build trial-level answer sheets + coverage, grade them
    5. Write the reuse index and summary; check for holes

The execute driver no longer builds unit-level report cards: coverage
and unit summaries are derived by pure functions from the trial sheets.
"""

from typing import Any, Dict, List, Set

from expedistill.composites import (
    answer_sheets,
    coverage,
    phase_report,
    shortfall,
)
from expedistill.composites.unit_ids import unit_id_for_trajectory
from expedistill.core import vocabulary
from expedistill.core.answer_sheet import UnitSpec
from expedistill.core.config import (
    PHASE_ROLE_BASELINE,
    ExecutePhaseSpec,
    PhaseInstance,
)
from expedistill.infra.reliability import InfrastructureError
from expedistill.infra.store import write_json
from expedistill.stages.context import StageContext
from expedistill.stages.phase_execute import engine, phase_disk, resolve

__all__ = ["_run_baseline"]


def _run_baseline(ctx: StageContext, phase: PhaseInstance,
                  spec: ExecutePhaseSpec) -> Dict[str, Any]:
    """Execute baseline: both bare arms, then derive trial/unit views."""
    # ========================================================================
    # 1. RESOLVE PARAMETERS
    # ========================================================================
    params = resolve._execution_params(spec)
    delivery, delivery_params = resolve._resolve_delivery(spec)
    rule_name = resolve._resolve_verdict_rule(spec, ctx.config.benchmark)
    limit = resolve._redispatch_limit(ctx)

    tasks = engine._prefixed_task_map(ctx, list(phase.task_ids))
    depths = {"strong": ctx.config.n_attempts_strong,
              "weak": ctx.config.n_attempts_weak}

    # ========================================================================
    # 2. PLAN UNITS FOR BOTH ARMS
    # ========================================================================
    rows = delivery.resolve(None, list(tasks), delivery_params)

    plans_by_role: Dict[str, list] = {}
    specs: List[UnitSpec] = []
    for arm in params.arms:
        role = resolve._ROLE_OF_ARM[arm]
        plans = engine._plan_units(
            rows, tasks, phase_label=phase.label,
            benchmark=ctx.config.benchmark, role=role,
            n_attempts=depths[role])
        plans_by_role[role] = plans
        specs.extend(
            UnitSpec(
                unit_id=plan.unit.unit_id,
                phase_label=phase.label,
                task_ref=plan.unit.task_ref,
                role=role,
                arm=role,
                expected_attempts=plan.n_attempts,
                injected_skills=plan.unit.skills_injected,
                no_valid_cause=vocabulary.CAUSE_ALL_HOLES,
            )
            for plan in plans)

    phase_disk._write_units(
        ctx, phase,
        [plan.unit for role in ("strong", "weak")
         for plan in plans_by_role[role]])

    # ========================================================================
    # 3. EXECUTE BOTH ARMS (run-level store)
    # ========================================================================
    stage1_holes = set(shortfall.stage1_accepted_unit_ids(ctx.run_dir))
    holes: Set[str] = set()
    trajectories = []

    for role in ("strong", "weak"):
        arm_trajectories, arm_holes = engine._run_arm_units(
            ctx, phase, plans=plans_by_role[role], store=ctx.store,
            arm=role, limit=limit, accepted=set(stage1_holes))
        trajectories.extend(arm_trajectories)

        if arm_holes and ctx.run_settings.allow_infra_shortfall:
            shortfall.write_unit_ids(
                shortfall.stage1_shortfall_path(ctx.run_dir),
                stage1_holes | arm_holes)
            stage1_holes |= arm_holes
        else:
            holes |= arm_holes

    # ========================================================================
    # 4. TRIAL SHEETS + COVERAGE + GRADING
    # ========================================================================
    coverage_records = coverage.baseline_coverage(
        ctx.store, specs, accepted_holes=stage1_holes)
    executions = [
        answer_sheets.execution_from_trajectory(
            trajectory,
            unit_id=unit_id_for_trajectory(
                phase_label=phase.label,
                benchmark=ctx.config.benchmark,
                trajectory=trajectory),
            phase_label=phase.label,
            arm=trajectory.model_role)
        for trajectory in trajectories
    ]
    sheets = answer_sheets.assemble_answer_sheets(
        executions, coverage_records, phase_label=phase.label)
    answer_sheets.grade_answer_sheets(sheets, rule_name)

    every_hole = set(stage1_holes) | holes
    summary = {
        "role": PHASE_ROLE_BASELINE,
        "infra_missing": sorted(every_hole),
        "infra_accepted": sorted(stage1_holes),
        "complete": not every_hole,
        "infra_shortfall_accepted": bool(stage1_holes),
    }
    report = phase_report.build_phase_report(
        sheets, specs, phase_kind=PHASE_ROLE_BASELINE,
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

    # ========================================================================
    # 5. WRITE SUMMARY AND CHECK HOLES
    # ========================================================================
    write_json(phase_dir / phase_disk.SUMMARY_NAME, summary, indent=2)

    if holes:
        raise InfrastructureError(
            f"phase {phase.label!r}: {len(holes)} unit(s) exhausted their "
            "infra and redispatch budgets and stored nothing (listed in "
            "the summary under infra_missing); resume the run to retry "
            "them, or accept the shortfall with --allow-infra-shortfall")

    return summary
