"""The final test phase: with-bank arm vs control over test set.

Orchestration:
    1. Resolve the phase (the folder capability included)
    2. Load the external control (verified complete BEFORE any spend)
    3. Execute the with-bank arm
    4. Execute the fresh bare control arm (unless the control is external)
    5. Settle the holes, build trial-level answer sheets and grade them
    6. Aggregate unit summaries, compute the delta and write the summary

Components used:
    - composites.external_control: load external campaign scores
    - composites.skill_materialize: materialize folder for folder delivery
    - composites.skill_use: parse which skills were read
    - composites.answer_sheets: trial-level execution/coverage/grading views
    - composites.coverage: expected-vs-stored coverage per arm
    - composites.unit_summary: unit and task aggregation (pure functions)
    - composites.phase_report: the phase's runtime report envelope
    - decision: final_test_delta, control_arm_source

Two control arm options:
    1. fresh: new bare arm executed inside this phase
    2. external: zero execution, scores from archived campaign file

Special rules:
    - Control completeness checked BEFORE with-bank spends anything
    - No learning report is written (numbers go to summary only)
    - No skip logic, no redispatch (every call re-runs everything)
    - Only the bank's ACTIVE cards are delivered
    - Folder delivery: materialize skills as files + catalog; an empty
      bank still runs as a folder unit (folder prompt, playbook, empty
      catalog -- the practice phase's presentation)
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from expedistill.benchmarks.aux import aux_wiring
from expedistill.composites import answer_sheets as sheet_io
from expedistill.composites import coverage as coverage_io
from expedistill.composites import decision, shortfall
from expedistill.composites import external_control
from expedistill.composites import phase_report, unit_ids, unit_summary
from expedistill.composites.skill_use import skill_files_read
from expedistill.core import ids
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
    UnitSpec,
)
from expedistill.core.config import (
    EXTERNAL_CAMPAIGN,
    FRESH_BARE,
    PHASE_ROLE_TEST,
    ExecutePhaseSpec,
    ExternalControlSpec,
    PhaseInstance,
)
from expedistill.core.contracts import ExecutionUnitRow
from expedistill.core.models import Trajectory
from expedistill.infra.reliability import CodeBugError, InfrastructureError
from expedistill.infra.store import Store, write_json
from expedistill.inject import bank_reference
from expedistill.methods.delivery import units as delivery_units
from expedistill.stages.context import StageContext
from expedistill.stages.phase_execute import engine, phase_disk, resolve

__all__ = ["run_test"]

_ARMS = ("with_bank", "control")


@dataclass
class _Arm:
    """One locally executed arm: its directory, store and durable
    accepted holes, and the trials it stored outside those holes."""

    name: str
    directory: Path
    store: Store
    accepted: Set[str]
    trajectories: List[Trajectory] = field(default_factory=list)


@dataclass(frozen=True)
class _ExternalControl:
    """The pinned campaign's control scores and the summary's record."""

    pinned: ExternalControlSpec
    rewards: Dict[str, List[float]]
    record: Dict[str, Any]


def run_test(ctx: StageContext, phase: PhaseInstance,
             spec: ExecutePhaseSpec) -> Dict[str, Any]:
    """Execute test phase: with-bank vs control over test set.

    Control arm is verified complete BEFORE with-bank spends anything.
    No learning report is written (C numbers go to summary only).
    """
    resolved = resolve.resolve_phase(ctx, phase, spec)
    role = resolved.executor_role
    bank = phase_disk.bank_skills(ctx, phase)
    benchmark = ctx.config.benchmark

    tasks = engine.task_objects(ctx, list(phase.task_ids))
    tasks_by_ref = engine.task_map(ctx, [task.task_id for task in tasks])
    n_attempts = resolved.n_attempts
    expected = {ids.make_task_id(benchmark, task.task_id): n_attempts
                for task in tasks}

    reference = bank_reference(delivery_units.active_bank(bank))
    aux_metric, aux_scorer = aux_wiring(benchmark)

    against = (spec.compare.against if spec.compare is not None
               else FRESH_BARE)
    control_source = decision.control_arm_source(
        external_campaign=against == EXTERNAL_CAMPAIGN)
    external = (_load_external_control(ctx, phase, spec, tasks, n_attempts)
                if against == EXTERNAL_CAMPAIGN else None)

    with_bank = _open_arm(ctx, phase, "with_bank")
    rows = resolved.delivery_rows(bank, tasks)
    plans = engine.plan_units(
        rows, tasks_by_ref, phase_label=phase.label, benchmark=benchmark,
        role=role, n_attempts=n_attempts,
        skill_folder_dir=phase_disk.materialize_folder(
            ctx, phase, bank, rows))
    phase_disk.write_units(ctx, phase, [plan.unit for plan in plans])
    _run_arm(ctx, phase, with_bank, plans)
    folder_reads = _folder_read_sets(rows, with_bank.trajectories)

    control = (None if external is not None
               else _run_fresh_control(ctx, phase, tasks, tasks_by_ref,
                                       role=role, n_attempts=n_attempts))
    arms = {"with_bank": with_bank, "control": control}
    accepted = {name: (arm.accepted if arm is not None else set())
                for name, arm in arms.items()}
    missing = {name: (_missing_units(phase.label, expected,
                                     arm.trajectories, role)
                      if arm is not None else [])
               for name, arm in arms.items()}
    new_holes = _new_holes(ctx, arms, missing)

    specs = ([UnitSpec(unit_id=plan.unit.unit_id, phase_label=phase.label,
                       task_ref=plan.unit.task_ref, role=role,
                       arm="with_bank", expected_attempts=n_attempts,
                       injected_skills=plan.unit.skills_injected)
              for plan in plans]
             + _control_specs(phase_label=phase.label, benchmark=benchmark,
                              expected=expected, n_attempts=n_attempts,
                              role=role))
    coverage, sheets = _graded_sheets(
        phase.label, benchmark, specs, with_bank, control, external,
        aux_scorer=aux_scorer, local_rule=resolved.verdict_rule, role=role)

    phase_directory = phase_disk.execute_phase_dir(ctx, phase)
    sheet_io.write_answer_sheets(
        sheets, phase_directory / phase_report.ANSWER_SHEETS_DIR)
    sheet_io.write_coverage(
        coverage, phase_directory / phase_report.COVERAGE_NAME)
    phase_report.write_unit_specs(specs, phase_directory)

    report = phase_report.build_phase_report(
        sheets, specs, phase_kind="test", learning_usable=False)
    summary = {
        "role": PHASE_ROLE_TEST,
        "bank_reference": reference,
        "control_source": control_source,
        **_arm_summaries(report.units, n_attempts, aux_metric,
                         external_control_arm=external is not None),
        "infra_missing": missing,
        "infra_accepted": {arm: sorted(accepted[arm]) for arm in _ARMS},
        "complete": not sum(len(trials) for trials in missing.values()),
        "infra_shortfall_accepted": any(accepted.values()),
    }
    if folder_reads is not None:
        summary["skill_files_read"] = folder_reads
    if external is not None:
        summary["external_control"] = external.record
    write_json(phase_directory / phase_disk.SUMMARY_NAME,
               summary, indent=2)

    n_new = sum(len(trials) for trials in new_holes.values())
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

def _load_external_control(ctx: StageContext, phase: PhaseInstance,
                           spec: ExecutePhaseSpec, tasks: Sequence[Any],
                           n_attempts: int) -> _ExternalControl:
    """The pinned campaign's scores, checked against the phase's tasks and
    depth, with a byte copy of the score file in the phase directory."""
    pinned = spec.compare.external
    if pinned is None:
        raise CodeBugError(
            f"phase {phase.label!r} compares against an external campaign "
            "but pins no score file")
    _, rewards = external_control.load_external_control(
        path=pinned.path, sha256=pinned.sha256,
        model_key=pinned.model_key, benchmark=ctx.config.benchmark,
        tasks=tasks, n_attempts=n_attempts)

    phase_directory = phase_disk.execute_phase_dir(ctx, phase)
    phase_directory.mkdir(parents=True, exist_ok=True)
    with open(pinned.path, "rb") as source:
        payload = source.read()
    with open(phase_directory / external_control.EXTERNAL_CONTROL_NAME,
              "wb") as target:
        target.write(payload)

    record = external_control.external_control_record(
        spec={"path": pinned.path, "sha256": pinned.sha256,
              "model_key": pinned.model_key},
        n_attempts=n_attempts, rewards=rewards)
    return _ExternalControl(pinned=pinned, rewards=rewards, record=record)


def _open_arm(ctx: StageContext, phase: PhaseInstance, name: str) -> _Arm:
    """An arm's directory, store and durable accepted holes."""
    directory = phase_disk.arm_dir(ctx, phase, name)
    return _Arm(name=name, directory=directory,
                store=Store(directory / "store"),
                accepted=set(shortfall.read_unit_ids(
                    directory / shortfall.SHORTFALL_NAME)))


def _run_arm(ctx: StageContext, phase: PhaseInstance, arm: _Arm,
             plans: Sequence[engine.Plan]) -> None:
    """One K1 batch over the arm's plans; the arm keeps its stored trials
    outside its accepted holes."""
    engine.dispatch_arm(ctx, phase, plans=plans, store=arm.store,
                        arm=arm.name, skip_unit_ids=arm.accepted)
    arm.trajectories = [
        traj for traj in engine.arm_trajectories(arm.store)
        if traj.trajectory_id not in arm.accepted]


def _run_fresh_control(ctx: StageContext, phase: PhaseInstance,
                       tasks: Sequence[Any],
                       tasks_by_ref: Dict[str, Any], *,
                       role: str, n_attempts: int) -> _Arm:
    """The bare control arm executed inside this phase."""
    arm = _open_arm(ctx, phase, "control")
    plans = engine.plan_units(
        resolve.bare_delivery_rows(tasks), tasks_by_ref,
        phase_label=phase.label, benchmark=ctx.config.benchmark,
        role=role, n_attempts=n_attempts)
    _run_arm(ctx, phase, arm, plans)
    return arm


def _new_holes(ctx: StageContext, arms: Dict[str, Optional[_Arm]],
               missing: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Per arm, the missing trials not yet accepted as holes.

    With ``allow_infra_shortfall`` they are accepted durably (the arm's
    shortfall record) and none stay new.
    """
    new_holes = {name: [unit for unit in missing[name]
                        if unit not in arm.accepted]
                 if arm is not None else []
                 for name, arm in arms.items()}
    if any(new_holes.values()) and ctx.run_settings.allow_infra_shortfall:
        for name, arm in arms.items():
            if new_holes[name]:
                shortfall.write_unit_ids(
                    arm.directory / shortfall.SHORTFALL_NAME,
                    arm.accepted | set(new_holes[name]))
        new_holes = {name: [] for name in _ARMS}
    return new_holes


def _graded_sheets(phase_label: str, benchmark: str,
                   specs: List[UnitSpec], with_bank: _Arm,
                   control: Optional[_Arm],
                   external: Optional[_ExternalControl], *,
                   aux_scorer: Optional[Callable[[Trajectory], bool]],
                   local_rule: str, role: str
                   ) -> Tuple[List[CoverageRecord], List[AnswerSheet]]:
    """Both arms' coverage and graded trial sheets: local trials under the
    phase's rule, external campaign rewards under ``external_reward``."""
    executions = _executions(with_bank.trajectories, arm="with_bank",
                             phase_label=phase_label, benchmark=benchmark,
                             aux_scorer=aux_scorer)
    stores = {"with_bank": with_bank.store}
    if external is not None:
        control_unit_by_task = {spec.task_ref: spec.unit_id
                                for spec in specs if spec.arm == "control"}
        executions += [
            sheet_io.execution_from_external(
                task_ref=task_id, unit_id=control_unit_by_task[task_id],
                phase_label=phase_label, reward=reward,
                sample_index=index, source=external.pinned.path,
                sha256=external.pinned.sha256, arm="control", role=role)
            for task_id in sorted(external.rewards)
            for index, reward in enumerate(external.rewards[task_id])]
    else:
        executions += _executions(control.trajectories, arm="control",
                                  phase_label=phase_label,
                                  benchmark=benchmark,
                                  aux_scorer=aux_scorer)
        stores["control"] = control.store

    coverage = coverage_io.test_coverage(
        stores=stores, specs=specs,
        accepted_by_arm={
            "with_bank": with_bank.accepted,
            "control": control.accepted if control is not None else set()},
        external_rewards=({"control": dict(external.rewards)}
                          if external is not None else None))
    sheets = sheet_io.assemble_answer_sheets(
        executions, coverage, phase_label=phase_label)
    sheet_io.grade_answer_sheets(
        [sheet for sheet in sheets if not _is_external(sheet)], local_rule)
    sheet_io.grade_answer_sheets(
        [sheet for sheet in sheets if _is_external(sheet)], "external_reward")
    return coverage, sheets


def _arm_summaries(units: Sequence[Any], n_attempts: int,
                   aux_metric: Optional[str], *,
                   external_control_arm: bool) -> Dict[str, Any]:
    """The summary's ``with_bank`` / ``control`` / ``delta`` entries."""
    with_bank_metrics = unit_summary.task_metrics(
        [unit for unit in units if unit.arm == "with_bank"], n_attempts)
    control_metrics = unit_summary.task_metrics(
        [unit for unit in units if unit.arm == "control"], n_attempts)
    if set(with_bank_metrics) != set(control_metrics):
        raise CodeBugError(
            "test phase arms cover different task sets: with-bank "
            f"{sorted(with_bank_metrics)} vs control "
            f"{sorted(control_metrics)}")

    with_bank = unit_summary.summarize_metrics(
        with_bank_metrics, n_attempts, aux_metric=aux_metric)
    # An external control carries campaign rewards, never the plugin's
    # auxiliary scorer, so its auxiliary column is absent by construction.
    control = unit_summary.summarize_metrics(
        control_metrics, n_attempts,
        aux_metric=None if external_control_arm else aux_metric)
    return {"with_bank": with_bank, "control": control,
            "delta": decision.final_test_delta(with_bank, control)}


def _control_specs(*, phase_label: str, benchmark: str,
                   expected: Dict[str, int], n_attempts: int,
                   role: str) -> List[UnitSpec]:
    """One bare (no injection) control unit spec per test task."""
    return [
        UnitSpec(
            unit_id=unit_ids.make_unit_id(
                phase_label=phase_label, benchmark=benchmark,
                task_ref=task_id, role=role, skills_injected=None),
            phase_label=phase_label, task_ref=task_id, role=role,
            arm="control", expected_attempts=n_attempts,
            injected_skills=None)
        for task_id in sorted(expected)]


def _executions(
        trajectories: List[Trajectory], *, arm: str, phase_label: str,
        benchmark: str,
        aux_scorer: Optional[Callable[[Trajectory], bool]]
        ) -> List[ExecutionRecord]:
    """One locally executed arm's trial-level execution records."""
    return [
        sheet_io.execution_from_trajectory(
            trajectory,
            unit_id=unit_ids.unit_id_for_trajectory(
                phase_label=phase_label, benchmark=benchmark,
                trajectory=trajectory),
            phase_label=phase_label, arm=arm,
            aux_success=_aux_success(aux_scorer, trajectory))
        for trajectory in trajectories]


def _aux_success(aux_scorer: Optional[Callable[[Trajectory], bool]],
                 trajectory: Trajectory) -> Optional[bool]:
    """The plugin-declared auxiliary verdict (only for trials with a
    scorer verdict)."""
    if aux_scorer is None or trajectory.correctness is None:
        return None
    return aux_scorer(trajectory)


def _is_external(sheet: AnswerSheet) -> bool:
    """Whether a trial sheet carries an external-campaign execution."""
    return (sheet.execution is not None
            and sheet.execution.source == "external")


def _folder_read_sets(rows: List[ExecutionUnitRow],
                      with_bank: List[Trajectory]
                      ) -> Optional[Dict[str, List[str]]]:
    """Parse which skill files were read from trajectories (folder only)."""
    manifest = phase_disk.folder_manifest(rows)
    if manifest is None:
        return None
    return {traj.trajectory_id: skill_files_read(traj, manifest)
            for traj in with_bank}


def _missing_units(phase_label: str, expected: Dict[str, int],
                   trajectories: List[Trajectory],
                   role: str) -> List[str]:
    """Expected trajectory ids with no trajectory."""
    stored = {traj.trajectory_id for traj in trajectories}
    planned = (ids.make_trajectory_id(phase_label, task_id, role, attempt)
               for task_id in sorted(expected)
               for attempt in range(expected[task_id]))
    return [trajectory_id for trajectory_id in planned
            if trajectory_id not in stored]
