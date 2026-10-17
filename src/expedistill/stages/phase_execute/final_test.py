"""The final-test phase (C): the with-bank arm over the test set
against its control arm.

Frozen C rules: no skip logic and no redispatch loop -- every unit
re-runs on every call; no report is written (the C numbers go to the
summary alone); the comparison is the final summary's own statistic,
never a decision input.
"""

from typing import Any, Dict, List, Optional, Sequence, Set

from expedistill.benchmarks.aux import aux_wiring
from expedistill.benchmarks.base import Task
from expedistill.blocks import metrics as metrics_block
from expedistill.composites import decision, shortfall
from expedistill.composites.skill_folder import materialize_skill_folder
from expedistill.composites.skill_reads import skill_files_read
from expedistill.core import ids
from expedistill.core.config import (
    FRESH_BARE,
    PHASE_ROLE_TEST,
    ExecutePhaseSpec,
    PhaseInstance,
)
from expedistill.core.contracts import ExecutionUnitRow
from expedistill.core.models import Skill, Trajectory
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
)
from expedistill.infra.store import Store, write_json
from expedistill.inject import bank_reference
from expedistill.methods import registry as method_registry
from expedistill.methods.delivery import units as delivery_units
from expedistill.stages.context import StageContext
from expedistill.stages.phase_execute.engine import (
    _arm_trajectories,
    _dispatch_arm,
    _plan_units,
    _prefixed_task_map,
    _task_objects,
)
from expedistill.stages.phase_execute.phase_disk import (
    SUMMARY_NAME,
    _arm_dir,
    _bank_skills,
    _phase_dir,
    _write_units,
)
from expedistill.stages.phase_execute.resolve import (
    _execution_params,
    _resolve_delivery,
)

__all__ = ["_run_test"]


def _expected_units(ctx: StageContext,
                    tasks: Sequence[Task]) -> Dict[str, int]:
    """The arm-independent expected unit set: prefixed task id -> the
    frozen weak sampling depth (one denominator for both arms)."""
    return {ids.make_task_id(ctx.config.benchmark, task.task_id):
            ctx.config.n_attempts_weak for task in tasks}


def _missing_units(expected: Dict[str, int],
                   trajectories: Sequence[Trajectory]) -> List[str]:
    """Expected unit ids with no trajectory (sorted)."""
    stored = {traj.trajectory_id for traj in trajectories}
    return [ids.make_trajectory_id(task_id, "weak", attempt)
            for task_id in sorted(expected)
            for attempt in range(expected[task_id])
            if ids.make_trajectory_id(task_id, "weak", attempt)
            not in stored]


def _materialize_folder(ctx: StageContext, phase: PhaseInstance,
                        bank: Sequence[Skill],
                        rows: Sequence[ExecutionUnitRow]
                        ) -> Optional[str]:
    """The phase's skill folder materialized under its phase directory
    (the folder delivery's rows share one manifest), or None when the
    rows carry no folder or the accepted bank is empty.

    The composite's fingerprint gate holds the bank version: a bank that
    moved under the manifest is a CodeBugError, never a silently wrong
    folder."""
    manifest = next(
        (row.aux.get("skill_folder") for row in rows
         if row.aux.get("skill_folder")), None)
    if manifest is None or not manifest.get("files"):
        return None
    directory = materialize_skill_folder(
        manifest, bank, _phase_dir(ctx, phase) / "skill_folder")
    return str(directory)


def _folder_read_sets(rows: Sequence[ExecutionUnitRow],
                      with_bank: Sequence[Trajectory]
                      ) -> Optional[Dict[str, List[str]]]:
    """The folder phase's per-unit read sets (M4): ``None`` when this
    phase's delivery is not folder -- the old execution versions'
    summaries carry no read statistics -- else
    ``{trajectory_id: [skill_id, ...]}`` over the with-bank arm's stored
    units, each list what :func:`skill_files_read` derives from the
    stored trajectory (the read record is a post-hoc parse, zero
    interception; an empty accepted bank leaves every unit bare, so
    every read set is empty). The dict covers the with-bank arm only:
    the control arm's units are bare by construction and have no folder
    to read. The manifest is the delivery rows' own (all rows share one,
    the M3 ruling) -- the same manifest the units ran with.

    The read set is a FACT about what the test phase executed, reported
    for audit (the scripts/audit consumption lands later); it is not a
    verdict, not a comparison and not a decision input -- the C summary
    sits outside decision's universe by the frozen rule that test
    results never flow into learning, which is exactly why it may carry
    it.
    """
    manifest = next(
        (row.aux.get("skill_folder") for row in rows
         if row.aux.get("skill_folder") is not None), None)
    if manifest is None:
        return None
    return {traj.trajectory_id: skill_files_read(traj, manifest)
            for traj in with_bank}


def _run_test(ctx: StageContext, phase: PhaseInstance,
              spec: ExecutePhaseSpec) -> Dict[str, Any]:
    """The with-bank arm over the test set against its control arm.

    Frozen C rules: no skip logic and no redispatch loop -- every unit
    re-runs on every call (only a crash resume inside the phase keeps
    its stored units, K1's own resume); no report is written (the C
    numbers go to the summary alone, boundary doc section 3 rule 9);
    the comparison is the final summary's own statistic
    (:func:`decision.final_test_delta`), never a decision input.

    The control is the baseline phase's stored weak bare trajectories
    (the run-level store, deterministic-id reuse -- full mode) or a
    fresh bare arm inside this phase (split mode). A missing control
    unit the baseline phase never accepted stops the phase BEFORE the
    with-bank arm spends anything: filling that hole can move the bank,
    so with-bank runs made now could be measured against the wrong
    bank."""
    _execution_params(spec)
    delivery, delivery_params = _resolve_delivery(spec)
    bank = _bank_skills(ctx, phase)
    benchmark = ctx.config.benchmark
    tasks = _task_objects(ctx, list(phase.task_ids))
    tasks_by_ref = _prefixed_task_map(ctx, [task.task_id for task in tasks])
    n_attempts = ctx.config.n_attempts_weak
    expected = _expected_units(ctx, tasks)
    # The injected bank is the accepted set (whole_bank / retrieval
    # filter inside the drivers); the reference digests the same set
    # the old stage-6 summary's bank_reference named.
    reference = bank_reference(delivery_units.accepted_bank(bank))
    aux_metric, aux_scorer = aux_wiring(benchmark)
    against = (spec.compare.against if spec.compare is not None
               else FRESH_BARE)
    control_source = decision.control_arm_source(
        reused_phase=None if against == FRESH_BARE else against)

    # -- the control first, and its completeness BEFORE any spend --------
    control_dir = _arm_dir(ctx, phase, "control")
    control_accepted: Set[str] = set()
    reused_control: Optional[List[Trajectory]] = None
    if against != FRESH_BARE:
        stage1_holes = set(shortfall.stage1_accepted_unit_ids(ctx.run_dir))
        reused_control = []
        missing_control = []
        for task_id in sorted(expected):
            for attempt in range(n_attempts):
                trajectory_id = ids.make_trajectory_id(task_id, "weak",
                                                      attempt)
                if trajectory_id in stage1_holes:
                    control_accepted.add(trajectory_id)
                    continue
                if ctx.store.exists("trajectories", trajectory_id):
                    reused_control.append(ctx.store.load(
                        "trajectories", trajectory_id, Trajectory))
                else:
                    missing_control.append(trajectory_id)
        if missing_control:
            raise InfrastructureError(
                f"phase {phase.label!r}: the reused control of {against!r} "
                f"is incomplete: {len(missing_control)} weak bare unit(s) "
                f"missing ({missing_control}); resume the run so the "
                "control phase retries them (a hole there can only be "
                "accepted in that phase)")

    # -- with-bank arm -----------------------------------------------------
    with_bank_dir = _arm_dir(ctx, phase, "with_bank")
    with_bank_store = Store(with_bank_dir / "store")
    with_bank_accepted = set(shortfall.read_unit_ids(
        with_bank_dir / shortfall.SHORTFALL_NAME))
    rows = delivery.resolve(bank, tasks, delivery_params)
    audit_rows = getattr(delivery, "audit_rows", None)
    if audit_rows is not None:
        # R5's side channel: the audit names the injection this dispatch is
        # about to render, written in full before dispatch (decision 15);
        # retrieval keeps the frozen stage-6 location the audit tooling
        # reads.
        delivery.write_audit(ctx.run_dir,
                             audit_rows(bank, tasks, delivery_params))
    # The folder delivery's manifest (the real_folder execution, M3):
    # every row shares one phase-level manifest, so ONE materialization
    # before dispatch serves every unit (idempotent across resumes --
    # unchanged bytes are not rewritten). The materialized directory is
    # what the capable plugin carries into its run environment; an empty
    # accepted bank has no files, so nothing materializes and the units
    # run bare (the D9 empty-injection lesson, honoured at the engine's
    # attach step).
    folder_dir = _materialize_folder(ctx, phase, bank, rows)
    plans = _plan_units(rows, tasks_by_ref, phase_label=phase.label,
                        benchmark=benchmark, role="weak",
                        n_attempts=n_attempts,
                        skill_folder_dir=folder_dir)
    _write_units(ctx, phase, [plan.unit for plan in plans])
    _dispatch_arm(ctx, phase, plans=plans, store=with_bank_store,
                  arm="with_bank", skip_unit_ids=with_bank_accepted)
    with_bank = [traj for traj in _arm_trajectories(with_bank_store)
                 if traj.trajectory_id not in with_bank_accepted]
    folder_reads = _folder_read_sets(rows, with_bank)

    # -- control arm -------------------------------------------------------
    if reused_control is not None:
        control = [traj for traj in reused_control
                   if traj.trajectory_id not in control_accepted]
    else:
        control_accepted = set(shortfall.read_unit_ids(
            control_dir / shortfall.SHORTFALL_NAME))
        control_store = Store(control_dir / "store")
        bare_delivery, bare_params = method_registry.resolve_fixed(
            "delivery", "none")
        bare_rows = bare_delivery.resolve(None, tasks, bare_params)
        bare_plans = _plan_units(bare_rows, tasks_by_ref,
                                 phase_label=phase.label,
                                 benchmark=benchmark, role="weak",
                                 n_attempts=n_attempts)
        _dispatch_arm(ctx, phase, plans=bare_plans, store=control_store,
                      arm="control", skip_unit_ids=control_accepted)
        control = [traj for traj in _arm_trajectories(control_store)
                   if traj.trajectory_id not in control_accepted]

    missing = {"with_bank": _missing_units(expected, with_bank),
               "control": _missing_units(expected, control)}
    accepted_now = {"with_bank": with_bank_accepted,
                    "control": control_accepted}
    new_holes = {arm: [unit for unit in missing[arm]
                       if unit not in accepted_now[arm]]
                 for arm in ("with_bank", "control")}
    if any(new_holes.values()) and ctx.run_settings.allow_infra_shortfall:
        # Durable per arm, written BEFORE the summary reads the shrunken
        # sample (the stage-6 rule); the reused control's holes stay the
        # baseline phase's own record, never accepted here.
        for arm in ("with_bank", "control"):
            if new_holes[arm] and (reused_control is None
                                   or arm == "with_bank"):
                record = (with_bank_dir if arm == "with_bank"
                          else control_dir)
                shortfall.write_unit_ids(
                    record / shortfall.SHORTFALL_NAME,
                    accepted_now[arm] | set(new_holes[arm]))
        new_holes = {arm: [] for arm in ("with_bank", "control")}

    with_bank_metrics = metrics_block.compute_task_metrics(
        with_bank, expected=expected, aux_scorer=aux_scorer)
    control_metrics = metrics_block.compute_task_metrics(
        control, expected=expected, aux_scorer=aux_scorer)
    if set(with_bank_metrics) != set(control_metrics):
        raise CodeBugError(
            "the test phase's arms cover different task sets: with-bank "
            f"{sorted(with_bank_metrics)} vs control "
            f"{sorted(control_metrics)} (a delta over different task sets "
            "is exactly the bug this guard exists for)")
    with_bank_summary = metrics_block.summarize(
        with_bank_metrics, n_attempts, aux_metric=aux_metric)
    control_summary = metrics_block.summarize(
        control_metrics, n_attempts, aux_metric=aux_metric)
    n_missing = sum(len(unit_ids) for unit_ids in missing.values())
    n_new = sum(len(unit_ids) for unit_ids in new_holes.values())
    summary = {
        "role": PHASE_ROLE_TEST,
        "mode": ctx.config.mode,
        "bank_reference": reference,
        "control_source": control_source,
        "with_bank": with_bank_summary,
        "control": control_summary,
        "delta": decision.final_test_delta(with_bank_summary,
                                            control_summary),
        "infra_missing": missing,
        "infra_accepted": {arm: sorted(accepted_now[arm])
                           for arm in ("with_bank", "control")},
        "complete": not n_missing,
        "infra_shortfall_accepted": any(accepted_now.values()),
    }
    if folder_reads is not None:
        summary["skill_files_read"] = folder_reads
    write_json(_phase_dir(ctx, phase) / SUMMARY_NAME, summary, indent=2)
    if n_new:
        raise InfrastructureError(
            f"phase {phase.label!r}: {n_new} unit(s) exhausted their infra "
            "retries and stored nothing (listed in the summary under "
            "infra_missing); resume the run to retry them, or accept the "
            "shortfall with --allow-infra-shortfall")
    return summary
