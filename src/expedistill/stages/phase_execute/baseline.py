"""The baseline phase (D): both bare arms over the train set.

The strong and weak arms run bare (nothing injected) at their frozen
depths, stored in the RUN-LEVEL trajectory store with the stage-1
accepted-holes record beside them -- every frozen consumer of a bare
baseline (live baseline loading, the report reader, the test phase's
reused control) reads them there.
"""

from typing import Any, Dict, Set

from expedistill.benchmarks.aux import aux_wiring
from expedistill.blocks import metrics as metrics_block
from expedistill.composites import reports, shortfall
from expedistill.core.config import (
    PHASE_ROLE_BASELINE,
    ExecutePhaseSpec,
    PhaseInstance,
)
from expedistill.infra.reliability import InfrastructureError
from expedistill.infra.store import write_json
from expedistill.stages.context import StageContext
from expedistill.stages.phase_execute.engine import (
    _arm_trajectories,
    _plan_units,
    _prefixed_task_map,
    _run_arm_units,
)
from expedistill.stages.phase_execute.phase_disk import (
    SUMMARY_NAME,
    _phase_dir,
    _write_report,
    _write_units,
)
from expedistill.stages.phase_execute.resolve import (
    _ROLE_OF_ARM,
    _execution_params,
    _redispatch_limit,
    _resolve_delivery,
    _resolve_verdict_rule,
)

__all__ = ["_run_baseline"]


def _run_baseline(ctx: StageContext, phase: PhaseInstance,
                  spec: ExecutePhaseSpec) -> Dict[str, Any]:
    """Both bare arms over the phase's task set, stored in the RUN-LEVEL
    trajectory store with the stage-1 accepted-holes record beside them
    -- every frozen consumer of a bare baseline (live baseline loading,
    the report reader, the test phase's reused control) reads them there.
    One report row per (task, role) (the baseline phases' role split); the
    failure-set derivation is NOT here (G3/R3: decision applies the
    named task-select rule to the weak rows)."""
    params = _execution_params(spec)
    delivery, delivery_params = _resolve_delivery(spec)
    tasks = _prefixed_task_map(ctx, list(phase.task_ids))
    rows = delivery.resolve(None, list(tasks),
                            delivery_params)  # the none driver ignores bank
    rule_name = _resolve_verdict_rule(spec, ctx.config.benchmark)
    limit = _redispatch_limit(ctx)
    depths = {"strong": ctx.config.n_attempts_strong,
              "weak": ctx.config.n_attempts_weak}

    plans_by_role: Dict[str, list] = {}
    for arm in params.arms:
        role = _ROLE_OF_ARM[arm]
        plans_by_role[role] = _plan_units(
            rows, tasks, phase_label=phase.label,
            benchmark=ctx.config.benchmark, role=role,
            n_attempts=depths[role])
    _write_units(ctx, phase, [plan.unit for role in ("strong", "weak")
                              for plan in plans_by_role[role]])

    # The run-level store holds ONLY bare trajectories (the with-skill
    # arms of every other phase live in their own stores), so the bare
    # arms keep their historical home and no id ever collides.
    # The accepted set comes from the stage-1 record and is passed INTO
    # the arm run: an accepted hole is never re-dispatched and never
    # re-raised, whatever later calls' flags say (the durable
    # acceptance contract, stage 1's own rule).
    stage1_holes = set(shortfall.stage1_accepted_unit_ids(ctx.run_dir))
    holes: Set[str] = set()
    for role in ("strong", "weak"):
        _, arm_holes = _run_arm_units(
            ctx, phase, plans=plans_by_role[role], store=ctx.store,
            arm=role, rule_name=rule_name, limit=limit,
            accepted=set(stage1_holes))
        if arm_holes and ctx.run_settings.allow_infra_shortfall:
            # The durable record is the stage-1 path: load_baseline, the
            # report reader and the C control read it from there.
            shortfall.write_unit_ids(
                shortfall.stage1_shortfall_path(ctx.run_dir),
                stage1_holes | arm_holes)
            stage1_holes |= arm_holes
        else:
            holes |= arm_holes

    # The frozen report reader builds the rows over exactly this store
    # and record (baseline kind; the same status/cause mapping the
    # driver's own judging produced).
    all_rows = reports.acceptance_report(
        ctx.store, kind="baseline", benchmark=ctx.config.benchmark,
        phase_label=phase.label,
        n_attempts_strong=ctx.config.n_attempts_strong,
        n_attempts_weak=ctx.config.n_attempts_weak)
    _write_report(ctx, phase, all_rows)

    aux_metric, aux_scorer = aux_wiring(ctx.config.benchmark)
    arm_summaries: Dict[str, Dict[str, Any]] = {}
    weak_metrics: Dict[str, Dict[str, Any]] = {}
    for role in ("strong", "weak"):
        plans = plans_by_role[role]
        trajectories = [traj for traj in _arm_trajectories(ctx.store)
                        if traj.model_role == role
                        and traj.trajectory_id not in stage1_holes]
        expected = {plan.unit.task_ref: plan.n_attempts for plan in plans}
        metrics = metrics_block.compute_task_metrics(
            trajectories, expected=expected, aux_scorer=aux_scorer)
        if role == "weak":
            weak_metrics = metrics
        arm_summaries[role] = metrics_block.summarize(
            metrics, depths[role], aux_metric=aux_metric)
    every_hole = set(stage1_holes) | holes
    summary = {
        "role": PHASE_ROLE_BASELINE,
        "strong": arm_summaries["strong"],
        "weak": arm_summaries["weak"],
        "infra_shortfalls": sorted(
            task_id for task_id, counts in weak_metrics.items()
            if counts["valid"] < ctx.config.n_attempts_weak),
        "infra_missing": sorted(every_hole),
        "infra_accepted": sorted(stage1_holes),
        "report_rows": len(all_rows),
        "complete": not every_hole,
    }
    summary["infra_shortfall_accepted"] = bool(stage1_holes)
    write_json(_phase_dir(ctx, phase) / SUMMARY_NAME, summary, indent=2)
    if holes:
        raise InfrastructureError(
            f"phase {phase.label!r}: {len(holes)} unit(s) exhausted their "
            "infra and redispatch budgets and stored nothing (listed in "
            "the summary under infra_missing); resume the run to retry "
            "them, or accept the shortfall with --allow-infra-shortfall")
    return summary
