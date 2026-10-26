"""The final test phase: with-bank arm vs control over test set.

Orchestration:
    1. Resolve parameters and verify folder capability
    2. Prepare control arm (three options: reused/fresh/external)
    3. Execute with-bank arm
    4. Execute control arm (if fresh)
    5. Build trial-level answer sheets (execution + coverage) and grade
    6. Aggregate unit summaries, compute the delta and write the summary

Components used:
    - composites.external_control: load external campaign scores
    - composites.skill_folder: materialize folder for folder delivery
    - composites.skill_use: parse which skills were read
    - composites.answer_sheets: trial-level execution/coverage/grading views
    - composites.coverage: expected-vs-stored coverage per arm
    - composites.unit_summary: unit and task aggregation (pure functions)
    - composites.phase_report: the phase's runtime report envelope
    - decision: final_test_delta, control_arm_source

Three control arm options:
    1. reused: baseline phase's weak bare trajectories (run-level store)
    2. fresh: new bare arm executed inside this phase
    3. external: zero execution, scores from archived campaign file

Special rules:
    - Control completeness checked BEFORE with-bank spends anything
    - No learning report is written (numbers go to summary only)
    - No skip logic, no redispatch (every call re-runs everything)
    - Folder delivery: materialize skills as files + catalog
"""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from expedistill.benchmarks.aux import aux_wiring
from expedistill.benchmarks.registry import resolve_plugin
from expedistill.composites import answer_sheets as sheet_io
from expedistill.composites import coverage as coverage_io
from expedistill.composites import decision, shortfall, verdicts
from expedistill.composites import external_control
from expedistill.composites import phase_report, unit_ids, unit_summary
from expedistill.composites.skill_folder import materialize_skill_folder
from expedistill.composites.skill_use import skill_files_read
from expedistill.core import ids
from expedistill.core.answer_sheet import AnswerSheet, ExecutionRecord, UnitSpec
from expedistill.core.config import (
    EXTERNAL_CAMPAIGN,
    FOLDER_DELIVERY_NAME,
    FRESH_BARE,
    PHASE_ROLE_TEST,
    ExecutePhaseSpec,
    PhaseInstance,
)
from expedistill.core.contracts import ExecutionUnitRow
from expedistill.core.models import Skill, Trajectory
from expedistill.infra.reliability import CodeBugError, ConfigError, InfrastructureError
from expedistill.infra.store import Store, write_json
from expedistill.inject import bank_reference
from expedistill.methods import registry as method_registry
from expedistill.methods.delivery import units as delivery_units
from expedistill.stages.context import StageContext
from expedistill.stages.phase_execute import engine, phase_disk, resolve

__all__ = ["_run_test"]


def _run_test(ctx: StageContext, phase: PhaseInstance,
              spec: ExecutePhaseSpec) -> Dict[str, Any]:
    """Execute test phase: with-bank vs control over test set.

    Control arm is verified complete BEFORE with-bank spends anything.
    No learning report is written (C numbers go to summary only).
    """
    # ========================================================================
    # 1. RESOLVE PARAMETERS AND VERIFY CAPABILITY
    # ========================================================================
    resolve._execution_params(spec)
    _require_folder_capability(spec, ctx.config.benchmark)

    delivery, delivery_params = resolve._resolve_delivery(spec)
    bank = phase_disk._bank_skills(ctx, phase)
    benchmark = ctx.config.benchmark

    tasks = engine._task_objects(ctx, list(phase.task_ids))
    tasks_by_ref = engine._prefixed_task_map(ctx, [task.task_id for task in tasks])
    n_attempts = ctx.config.n_attempts_weak

    expected = {ids.make_task_id(benchmark, task.task_id): n_attempts
                for task in tasks}

    reference = bank_reference(delivery_units.accepted_bank(bank))
    aux_metric, aux_scorer = aux_wiring(benchmark)

    against = (spec.compare.against if spec.compare is not None
               else FRESH_BARE)
    control_source = decision.control_arm_source(
        reused_phase=None if against in (FRESH_BARE, EXTERNAL_CAMPAIGN)
        else against,
        external_campaign=against == EXTERNAL_CAMPAIGN)

    # ========================================================================
    # 2. PREPARE CONTROL ARM (verify completeness BEFORE any spend)
    # ========================================================================
    control_dir: Optional[Path] = None
    control_accepted: Set[str] = set()
    reused_control: Optional[List[Trajectory]] = None
    external_metrics: Optional[Dict[str, Dict[str, int]]] = None
    external_rewards: Optional[Dict[str, List[float]]] = None
    external_record: Optional[Dict[str, Any]] = None
    external_pinned = None

    if against == EXTERNAL_CAMPAIGN:
        # External campaign: load scores from file, copy to phase dir
        assert spec.compare is not None and spec.compare.external is not None
        pinned = spec.compare.external
        external_pinned = pinned

        external_metrics, external_rewards = external_control.\
            load_external_control(
                path=pinned.path, sha256=pinned.sha256,
                model_key=pinned.model_key, benchmark=benchmark,
                tasks=tasks, n_attempts=n_attempts)

        phase_directory = phase_disk._phase_dir(ctx, phase)
        phase_directory.mkdir(parents=True, exist_ok=True)

        with open(pinned.path, "rb") as source:
            payload = source.read()
        with open(phase_directory / external_control.EXTERNAL_CONTROL_NAME,
                  "wb") as target:
            target.write(payload)

        external_record = external_control.external_control_record(
            spec={"path": pinned.path, "sha256": pinned.sha256,
                  "model_key": pinned.model_key},
            n_attempts=n_attempts, rewards=external_rewards)

    elif against != FRESH_BARE:
        # Reused control: baseline's weak bare trajectories
        stage1_holes = set(shortfall.stage1_accepted_unit_ids(ctx.run_dir))
        reused_control = []
        missing_control = []

        for task_id in sorted(expected):
            for attempt in range(n_attempts):
                trajectory_id = ids.make_trajectory_id(task_id, "weak", attempt)

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
                f"phase {phase.label!r}: reused control of {against!r} "
                f"is incomplete: {len(missing_control)} weak bare unit(s) "
                f"missing ({missing_control}); resume so control phase "
                "retries them")

    # ========================================================================
    # 3. EXECUTE WITH-BANK ARM
    # ========================================================================
    with_bank_dir = phase_disk._arm_dir(ctx, phase, "with_bank")
    with_bank_store = Store(with_bank_dir / "store")
    with_bank_accepted = set(shortfall.read_unit_ids(
        with_bank_dir / shortfall.SHORTFALL_NAME))

    rows = delivery.resolve(bank, tasks, delivery_params)

    # Delivery-specific side effects
    audit_rows = getattr(delivery, "audit_rows", None)
    if audit_rows is not None:
        delivery.write_audit(ctx.run_dir,
                           audit_rows(bank, tasks, delivery_params))

    # Folder delivery: materialize skills as files
    folder_dir = _materialize_folder(ctx, phase, bank, rows)

    plans = engine._plan_units(rows, tasks_by_ref, phase_label=phase.label,
                               benchmark=benchmark, role="weak",
                               n_attempts=n_attempts,
                               skill_folder_dir=folder_dir)

    phase_disk._write_units(ctx, phase, [plan.unit for plan in plans])

    engine._dispatch_arm(ctx, phase, plans=plans, store=with_bank_store,
                        arm="with_bank", skip_unit_ids=with_bank_accepted)

    with_bank = [traj for traj in engine._arm_trajectories(with_bank_store)
                 if traj.trajectory_id not in with_bank_accepted]

    folder_reads = _folder_read_sets(rows, with_bank)

    # ========================================================================
    # 4. EXECUTE CONTROL ARM (if fresh)
    # ========================================================================
    control: Optional[List[Trajectory]]
    control_store: Optional[Store] = None

    if external_metrics is not None:
        control = None

    elif reused_control is not None:
        control = [traj for traj in reused_control
                  if traj.trajectory_id not in control_accepted]

    else:
        # Fresh bare control
        control_dir = phase_disk._arm_dir(ctx, phase, "control")
        control_accepted = set(shortfall.read_unit_ids(
            control_dir / shortfall.SHORTFALL_NAME))
        control_store = Store(control_dir / "store")

        bare_delivery, bare_params = method_registry.resolve_fixed(
            "delivery", "none")
        bare_rows = bare_delivery.resolve(None, tasks, bare_params)
        bare_plans = engine._plan_units(bare_rows, tasks_by_ref,
                                        phase_label=phase.label,
                                        benchmark=benchmark, role="weak",
                                        n_attempts=n_attempts)

        engine._dispatch_arm(ctx, phase, plans=bare_plans, store=control_store,
                            arm="control", skip_unit_ids=control_accepted)

        control = [traj for traj in engine._arm_trajectories(control_store)
                  if traj.trajectory_id not in control_accepted]

    # ========================================================================
    # 5. HANDLE MISSING UNITS
    # ========================================================================
    missing = {"with_bank": _missing_units(expected, with_bank),
               "control": ([] if external_metrics is not None
                          else _missing_units(expected, control))}

    accepted_now = {"with_bank": with_bank_accepted,
                    "control": control_accepted}

    new_holes = {arm: [unit for unit in missing[arm]
                      if unit not in accepted_now[arm]]
                 for arm in ("with_bank", "control")}

    if any(new_holes.values()) and ctx.run_settings.allow_infra_shortfall:
        for arm in ("with_bank", "control"):
            if new_holes[arm] and (reused_control is None
                                  or arm == "with_bank"):
                record = (with_bank_dir if arm == "with_bank"
                         else control_dir)
                shortfall.write_unit_ids(
                    record / shortfall.SHORTFALL_NAME,
                    accepted_now[arm] | set(new_holes[arm]))

        new_holes = {arm: [] for arm in ("with_bank", "control")}

    # ========================================================================
    # 6. BUILD AND GRADE TRIAL-LEVEL ANSWER SHEETS
    # ========================================================================
    with_bank_specs = [
        UnitSpec(unit_id=plan.unit.unit_id, phase_label=phase.label,
                 task_ref=plan.unit.task_ref, role="weak", arm="with_bank",
                 expected_attempts=n_attempts,
                 injected_skills=plan.unit.skills_injected)
        for plan in plans]
    control_specs = _control_specs(phase_label=phase.label,
                                   benchmark=benchmark, expected=expected,
                                   n_attempts=n_attempts)
    all_specs = with_bank_specs + control_specs

    executions = _with_bank_executions(
        with_bank, phase_label=phase.label, benchmark=benchmark,
        aux_scorer=aux_scorer)

    if external_rewards is not None:
        assert external_pinned is not None
        control_unit_by_task = {spec.task_ref: spec.unit_id
                                for spec in control_specs}
        executions += [
            sheet_io.execution_from_external(
                task_ref=task_id, unit_id=control_unit_by_task[task_id],
                phase_label=phase.label, reward=reward,
                sample_index=index, source=external_pinned.path,
                sha256=external_pinned.sha256, arm="control")
            for task_id in sorted(external_rewards)
            for index, reward in enumerate(external_rewards[task_id])]
    else:
        executions += _control_executions(
            control, phase_label=phase.label, benchmark=benchmark,
            aux_scorer=aux_scorer)

    stores = {"with_bank": with_bank_store}
    if reused_control is not None:
        # The reused control's trajectories live in the run-level store.
        stores["control"] = ctx.store
    elif control_store is not None:
        stores["control"] = control_store

    coverage = coverage_io.test_coverage(
        stores=stores, specs=all_specs,
        accepted_by_arm={"with_bank": with_bank_accepted,
                         "control": control_accepted},
        external_rewards=({"control": dict(external_rewards)}
                          if external_rewards is not None else None))

    sheets = sheet_io.assemble_answer_sheets(
        executions, coverage, phase_label=phase.label)
    local_rule = verdicts.default_verdict_rule(benchmark)
    local_sheets = [sheet for sheet in sheets
                    if not _is_external(sheet)]
    external_sheets = [sheet for sheet in sheets if _is_external(sheet)]
    sheet_io.grade_answer_sheets(local_sheets, local_rule)
    sheet_io.grade_answer_sheets(external_sheets, "external_reward")

    phase_directory = phase_disk._phase_dir(ctx, phase)
    sheet_io.write_answer_sheets(
        sheets, phase_directory / phase_report.ANSWER_SHEETS_DIR)
    sheet_io.write_coverage(
        coverage, phase_directory / phase_report.COVERAGE_NAME)
    phase_report.write_unit_specs(all_specs, phase_directory)

    # ========================================================================
    # 7. AGGREGATE UNIT SUMMARIES, COMPUTE DELTA, WRITE SUMMARY
    # ========================================================================
    report = phase_report.build_phase_report(
        sheets, all_specs, phase_kind="test", learning_usable=False)

    with_bank_units = [unit for unit in report.units
                       if unit.arm == "with_bank"]
    control_units = [unit for unit in report.units if unit.arm == "control"]

    with_bank_metrics = unit_summary.task_metrics(with_bank_units,
                                                  n_attempts)
    control_metrics = unit_summary.task_metrics(control_units, n_attempts)

    if set(with_bank_metrics) != set(control_metrics):
        raise CodeBugError(
            "test phase arms cover different task sets: with-bank "
            f"{sorted(with_bank_metrics)} vs control "
            f"{sorted(control_metrics)}")

    with_bank_summary = unit_summary.summarize_metrics(
        with_bank_metrics, n_attempts, aux_metric=aux_metric)
    # An external control carries campaign rewards, never the plugin's
    # auxiliary scorer, so its auxiliary column is absent by construction.
    control_summary = unit_summary.summarize_metrics(
        control_metrics, n_attempts,
        aux_metric=(None if external_metrics is not None else aux_metric))

    n_missing = sum(len(unit_ids_) for unit_ids_ in missing.values())
    n_new = sum(len(unit_ids_) for unit_ids_ in new_holes.values())

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

    if external_record is not None:
        summary["external_control"] = external_record

    report.summary = summary
    report.external = external_record

    write_json(phase_directory / phase_disk.SUMMARY_NAME,
               summary, indent=2)

    if n_new:
        raise InfrastructureError(
            f"phase {phase.label!r}: {n_new} unit(s) exhausted their infra "
            "retries and stored nothing (listed in summary under "
            "infra_missing); resume to retry, or accept with "
            "--allow-infra-shortfall")

    return summary


# ============================================================================
# Supporting functions
# ============================================================================

def _control_specs(*, phase_label: str, benchmark: str,
                   expected: Dict[str, int],
                   n_attempts: int) -> List[UnitSpec]:
    """One bare (no injection) control unit spec per test task."""
    return [
        UnitSpec(
            unit_id=unit_ids.make_unit_id(
                phase_label=phase_label, benchmark=benchmark,
                task_ref=task_id, role="weak", skills_injected=None),
            phase_label=phase_label, task_ref=task_id, role="weak",
            arm="control", expected_attempts=n_attempts,
            injected_skills=None)
        for task_id in sorted(expected)]


def _with_bank_executions(
        trajectories: List[Trajectory], *, phase_label: str,
        benchmark: str,
        aux_scorer: Optional[Callable[[Trajectory], bool]]
        ) -> List[ExecutionRecord]:
    """The with-bank arm's trial-level execution records."""
    return [
        sheet_io.execution_from_trajectory(
            trajectory,
            unit_id=unit_ids.unit_id_for_trajectory(
                phase_label=phase_label, benchmark=benchmark,
                trajectory=trajectory),
            phase_label=phase_label, arm="with_bank",
            aux_success=_aux_success(aux_scorer, trajectory))
        for trajectory in trajectories]


def _control_executions(
        trajectories: List[Trajectory], *, phase_label: str,
        benchmark: str,
        aux_scorer: Optional[Callable[[Trajectory], bool]]
        ) -> List[ExecutionRecord]:
    """The local (fresh or reused) control arm's execution records."""
    return [
        sheet_io.execution_from_trajectory(
            trajectory,
            unit_id=unit_ids.unit_id_for_trajectory(
                phase_label=phase_label, benchmark=benchmark,
                trajectory=trajectory),
            phase_label=phase_label, arm="control",
            aux_success=_aux_success(aux_scorer, trajectory))
        for trajectory in trajectories]


def _aux_success(aux_scorer: Optional[Callable[[Trajectory], bool]],
                 trajectory: Trajectory) -> Optional[bool]:
    """The plugin-declared auxiliary verdict, computed exactly where the
    old metrics block computed it (only for trials with a scorer verdict)."""
    if aux_scorer is None or trajectory.correctness is None:
        return None
    return aux_scorer(trajectory)


def _is_external(sheet: AnswerSheet) -> bool:
    """Whether a trial sheet carries an external-campaign execution."""
    return (sheet.execution is not None
            and sheet.execution.source == "external")


def _require_folder_capability(spec: ExecutePhaseSpec, benchmark: str) -> None:
    """Verify benchmark supports folder delivery if phase uses it."""
    if spec.delivery["name"] != FOLDER_DELIVERY_NAME:
        return

    if resolve_plugin(benchmark).skill_folder_prompt_name is None:
        raise ConfigError(
            f"benchmark {benchmark!r} does not support skill folders "
            "(the real_folder execution): its plugin declares no "
            "skill_folder_prompt_name")


def _materialize_folder(ctx: StageContext, phase: PhaseInstance,
                       bank: List[Skill],
                       rows: List[ExecutionUnitRow]) -> Optional[str]:
    """Materialize skill folder if delivery provides manifest."""
    manifests = [manifest for manifest in map(engine._folder_manifest_of, rows)
                if manifest is not None]
    manifest = manifests[0] if manifests else None

    if manifest is None or not manifest.get("files"):
        return None

    directory = materialize_skill_folder(
        manifest, bank, phase_disk._phase_dir(ctx, phase) / "skill_folder")

    return str(directory)


def _folder_read_sets(rows: List[ExecutionUnitRow],
                     with_bank: List[Trajectory]
                     ) -> Optional[Dict[str, List[str]]]:
    """Parse which skill files were read from trajectories (folder only)."""
    manifests = [manifest for manifest in map(engine._folder_manifest_of, rows)
                if manifest is not None]
    manifest = manifests[0] if manifests else None

    if manifest is None:
        return None

    return {traj.trajectory_id: skill_files_read(traj, manifest)
            for traj in with_bank}


def _missing_units(expected: Dict[str, int],
                  trajectories: List[Trajectory]) -> List[str]:
    """Expected unit IDs with no trajectory."""
    stored = {traj.trajectory_id for traj in trajectories}
    return [ids.make_trajectory_id(task_id, "weak", attempt)
            for task_id in sorted(expected)
            for attempt in range(expected[task_id])
            if ids.make_trajectory_id(task_id, "weak", attempt)
            not in stored]
