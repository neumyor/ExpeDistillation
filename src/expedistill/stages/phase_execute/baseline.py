"""The baseline phase: both bare arms over the train set.

Orchestration:
    1. Resolve parameters (execution, delivery, verdict rule)
    2. Plan units for both arms (strong, weak)
    3. Execute both arms through K1 (run-level store)
    4. Write the per-unit verdicts (the rule-1 reuse input)
    5. Write summary and check for holes

Components used:
    - engine: parameter resolution, unit planning, arm execution
    - composites.shortfall: stage-1 hole tracking
"""

from typing import Any, Dict, List, Set, Tuple

from expedistill.composites import shortfall
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
    """Execute baseline phase: both bare arms over train set.

    The strong and weak arms run bare at their frozen depths, stored in
    the RUN-LEVEL trajectory store. Every frozen consumer (live baseline
    loading, report reader, test phase reused control) reads them there.
    """
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
    # Note: baseline uses 'none' delivery (bare runs), but we still call
    # resolve to get the unit rows
    rows = delivery.resolve(None, list(tasks), delivery_params)

    plans_by_role: Dict[str, list] = {}
    for arm in params.arms:
        role = resolve._ROLE_OF_ARM[arm]
        plans_by_role[role] = engine._plan_units(
            rows, tasks, phase_label=phase.label,
            benchmark=ctx.config.benchmark, role=role,
            n_attempts=depths[role])

    phase_disk._write_units(
        ctx, phase,
        [plan.unit for role in ("strong", "weak")
         for plan in plans_by_role[role]])

    # ========================================================================
    # 3. EXECUTE BOTH ARMS (run-level store)
    # ========================================================================
    # Stage-1 accepted holes: never re-dispatched, never re-raised
    stage1_holes = set(shortfall.stage1_accepted_unit_ids(ctx.run_dir))
    holes: Set[str] = set()
    verdicts: List[Tuple[str, str, str]] = []

    for role in ("strong", "weak"):
        rows, arm_holes = engine._run_arm_units(
            ctx, phase, plans=plans_by_role[role], store=ctx.store,
            arm=role, rule_name=rule_name, limit=limit,
            accepted=set(stage1_holes))

        verdicts.extend((row.unit_id, row.status, row.cause)
                        for row in rows)

        if arm_holes and ctx.run_settings.allow_infra_shortfall:
            # Accept new holes and update stage-1 record
            shortfall.write_unit_ids(
                shortfall.stage1_shortfall_path(ctx.run_dir),
                stage1_holes | arm_holes)
            stage1_holes |= arm_holes
        else:
            holes |= arm_holes

    # ========================================================================
    # 4. WRITE THE PER-UNIT VERDICTS
    # ========================================================================
    phase_disk._write_verdicts(ctx, phase, verdicts)

    # ========================================================================
    # 5. WRITE SUMMARY AND CHECK HOLES
    # ========================================================================
    every_hole = set(stage1_holes) | holes

    summary = {
        "role": PHASE_ROLE_BASELINE,
        "infra_missing": sorted(every_hole),
        "infra_accepted": sorted(stage1_holes),
        "complete": not every_hole,
        "infra_shortfall_accepted": bool(stage1_holes),
    }

    write_json(phase_disk._phase_dir(ctx, phase) / phase_disk.SUMMARY_NAME,
               summary, indent=2)

    if holes:
        raise InfrastructureError(
            f"phase {phase.label!r}: {len(holes)} unit(s) exhausted their "
            "infra and redispatch budgets and stored nothing (listed in "
            "the summary under infra_missing); resume the run to retry "
            "them, or accept the shortfall with --allow-infra-shortfall")

    return summary
