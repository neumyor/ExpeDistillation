"""The live phase: one round measurement over pending groups.

Orchestration:
    1. Resolve parameters and determine round number
    2. Load pending groups and plan units
    3. Run finalization sweep (commit any hanging work)
    4. Apply entry guards (five short-circuit conditions)
    5. Execute one K1 batch for non-skipped groups
    6. Judge and finalize each group through verdicts ledger
    7. Build trial-level answer sheets
    8. Write summary

Components used:
    - composites.live_measurement: pending groups, round dirs, audit rows
    - composites.live_verdicts: judge_round, finalize_group (ledger protocol)
    - composites.decision: control_perfect_skips_run, live_injection_overflows
    - engine: K1 batch execution
    - inject: estimate_tokens for overflow check

Entry guards (short-circuit conditions):
    1. Rule-1 reuse: unit already has verdict from earlier phase
    2. Already judged: round row already exists in verdicts ledger
    3. Control perfect: weak baseline already knows, skip execution
    4. Injection overflow: skills block exceeds token budget
    5. Already stored: trajectories exist, only judgment owed
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from expedistill.benchmarks.aux import aux_wiring
from expedistill.benchmarks.registry import resolve_plugin
from expedistill.composites import (
    answer_sheets,
    coverage,
    decision,
    phase_report,
    shortfall,
    verdicts,
)
from expedistill.composites import live_measurement as lm
from expedistill.composites import live_verdicts as lv
from expedistill.composites.skill_folder import materialize_skill_folder
from expedistill.composites.unit_ids import unit_id_for_trajectory
from expedistill.core import ids, vocabulary
from expedistill.core.answer_sheet import UnitSpec
from expedistill.core.config import PHASE_ROLE_LIVE, ExecutePhaseSpec, PhaseInstance
from expedistill.core.models import Trajectory
from expedistill.infra.reliability import CodeBugError, ConfigError, InfrastructureError
from expedistill.infra.store import Store, append_jsonl, read_jsonl, write_json
from expedistill.inject import estimate_tokens
from expedistill.stages.context import StageContext
from expedistill.stages.phase_execute import engine, phase_disk, resolve

# K1 seam: dispatch goes through package attribute
import expedistill.stages.phase_execute as _package

__all__ = ["_run_live"]


def _run_live(ctx: StageContext, phase: PhaseInstance,
              spec: ExecutePhaseSpec) -> Dict[str, Any]:
    """Execute live phase: measure one round over pending groups.

    Each group goes through entry guards that may short-circuit execution.
    Groups that pass all guards are executed and judged through the
    verdicts ledger protocol (judge_round + finalize_group).
    """
    # ========================================================================
    # 1. RESOLVE PARAMETERS AND DETERMINE ROUND
    # ========================================================================
    params = resolve._execution_params(spec)
    n_attempts = (params.n_attempts if params.n_attempts is not None
                  else ctx.config.n_attempts_weak)

    if n_attempts > ctx.config.n_attempts_weak:
        raise ConfigError(
            f"phase {phase.label!r}: live depth ({n_attempts}) exceeds "
            f"n_attempts_weak ({ctx.config.n_attempts_weak})")

    round_ = _round_of(ctx, phase)
    rule_name = resolve._compare_rule(spec)
    store = ctx.store

    # Ensure ledger footprint exists (even if group universe is empty)
    lm.ledger_path(store, lm.VERDICTS_LEDGER).parent.mkdir(
        parents=True, exist_ok=True)

    _, aux_scorer = aux_wiring(ctx.config.benchmark)
    stage1_holes = shortfall.stage1_accepted_unit_ids(ctx.run_dir)

    # ========================================================================
    # 2. LOAD PENDING GROUPS AND PLAN UNITS
    # ========================================================================
    benchmark = ctx.config.benchmark
    groups = {task_id: members
              for task_id, members in lm.pending_groups(store).items()
              if ids.make_task_id(benchmark, ids.raw_task_id(task_id, benchmark))
              in {ids.make_task_id(benchmark, raw) for raw in phase.task_ids}}

    task_ids = sorted(groups)
    delivery, delivery_params = resolve._resolve_delivery(spec)
    rows = delivery.resolve(lm.pending_groups(store), task_ids,
                           delivery_params)
    if any(engine._folder_manifest_of(row) is not None for row in rows):
        if resolve_plugin(benchmark).skill_folder_prompt_name is None:
            raise ConfigError(
                f"benchmark {benchmark!r} does not support skill folders: "
                "its plugin declares no skill_folder_prompt_name, and a "
                "folder unit needs one")
    folder_dirs = _materialize_task_folders(ctx, phase, rows, groups,
                                            benchmark)

    tasks = engine._prefixed_task_map(
        ctx, sorted(ids.raw_task_id(task_id, benchmark)
                   for task_id in task_ids))

    plans = engine._plan_units(rows, tasks, phase_label=phase.label,
                               benchmark=benchmark, role="weak",
                               n_attempts=n_attempts,
                               skill_folder_dirs=folder_dirs or None)

    # Merge with earlier passes (live universe shrinks as groups are finalized)
    earlier_units = phase_disk._earlier_pass_units(ctx, phase)
    planned_ids = {plan.unit.unit_id for plan in plans}
    carried = [unit for unit in earlier_units
               if unit.unit_id not in planned_ids]

    all_units = sorted([plan.unit for plan in plans] + carried,
                      key=lambda unit: unit.unit_id)
    phase_disk._write_units(ctx, phase, all_units)

    plan_by_task = {plan.unit.task_ref: plan for plan in plans}
    reused = phase_disk._reusable_units(ctx, phase, all_units)
    phase_disk._write_carried_units(ctx, phase, reused)

    accepted = shortfall.read_unit_ids(
        lm.round_dir(store, round_) / shortfall.SHORTFALL_NAME)

    def skip_for(task_id: str) -> List[str]:
        return lm.skip_ids_for(stage1_holes, accepted, task_id)

    # ========================================================================
    # 3. FINALIZATION SWEEP
    # ========================================================================
    # Finish any commit a crash left hanging
    for task_id in sorted(lm.pending_groups(store)):
        lv.finalize_group(store, task_id)

    # ========================================================================
    # 4. APPLY ENTRY GUARDS (five short-circuits)
    # ========================================================================
    dispatch: List[engine._Plan] = []
    baselines: Dict[str, Dict[str, int]] = {}
    infra_failed: Set[str] = set()

    for task_id in task_ids:
        plan = plan_by_task[task_id]

        # Guard 1: Rule-1 reuse
        if plan.unit.unit_id in reused:
            continue

        # Guard 2: Already has verdict row
        if _round_has_row(store, task_id, round_):
            continue

        # Load baseline for control-perfect and injection checks
        _, baseline_counts = lm.load_baseline(store, task_id, n_attempts,
                                             aux_scorer=aux_scorer)
        baselines[task_id] = baseline_counts

        # Guard 3: Control perfect (weak already knows)
        if decision.control_perfect_skips_run(
                compare_rule=rule_name,
                control_valid=baseline_counts["valid"],
                control_success=baseline_counts["success"]):
            lv.judge_round(
                store=store, task_id=task_id, round_=round_,
                rule_name=rule_name, baseline_counts=baseline_counts,
                n_attempts=n_attempts, skip_ids=[],
                terminal=lv.WEAK_ALREADY_KNOWS,
                max_injection_tokens=params.max_injection_tokens)
            lv.finalize_group(store, task_id)
            continue

        # Guard 4: Injection overflow
        if decision.live_injection_overflows(
                max_injection_tokens=params.max_injection_tokens,
                injection_tokens=estimate_tokens(plan.unit.skills_block)):
            _freeze_audit_row(store, task_id, round_,
                              plan.unit.skills_block)
            lv.judge_round(
                store=store, task_id=task_id, round_=round_,
                rule_name=rule_name, baseline_counts=baseline_counts,
                n_attempts=n_attempts, skip_ids=[],
                terminal=lv.LIVE_INJECTION_OVERFLOW,
                max_injection_tokens=params.max_injection_tokens)
            lv.finalize_group(store, task_id)
            continue

        # Guard 5: Already stored (only judgment owed)
        if _round_complete(ctx, task_id, round_, n_attempts, skip_for(task_id)):
            dispatch.append(plan)
            continue

        # No guard triggered: freeze audit row and add to dispatch
        _freeze_audit_row(store, task_id, round_, plan.unit.skills_block)
        dispatch.append(plan)

    # ========================================================================
    # 5. EXECUTE ONE K1 BATCH
    # ========================================================================
    if dispatch:
        owing = [plan for plan in dispatch
                 if not _round_complete(ctx, plan.unit.task_ref, round_,
                                       n_attempts,
                                       skip_for(plan.unit.task_ref))]

        if owing:
            skip_unit_ids: Set[str] = set()
            for plan in owing:
                skip_unit_ids.update(skip_for(plan.unit.task_ref))

            by_task = {plan.task.task_id: (plan.unit.skills_injected,
                                          plan.unit.skills_block)
                      for plan in owing}

            def skills_for_task(task):
                return by_task[task.task_id]

            _package.batch_execute(
                store=Store(lm.round_dir(store, round_)),
                benchmark=benchmark,
                tasks=[plan.task for plan in owing],
                role="weak",
                model_name=ctx.config.models["executor_weak"],
                handle=ctx.handle("executor_weak"),
                n_attempts=n_attempts,
                skills_for_task=skills_for_task,
                workdir_root=(Path(ctx.workdir_root)
                             / phase.label.replace(":", "_")
                             / f"round{round_:02d}"),
                usage=ctx.usage,
                run_dir=ctx.run_dir,
                workers=ctx.run_settings.workers,
                dispatch_stagger=ctx.run_settings.k1_stagger,
                skip_unit_ids=skip_unit_ids,
            )

    # ========================================================================
    # 6. JUDGE AND FINALIZE GROUPS
    # ========================================================================
    # Handle new shortfalls first
    if ctx.run_settings.allow_infra_shortfall:
        open_holes = set()
        round_store = Store(lm.round_dir(store, round_))
        stored = set(round_store.ids("trajectories"))
        for plan in dispatch:
            if _round_has_row(store, plan.unit.task_ref, round_):
                continue
            skip = set(skip_for(plan.unit.task_ref))
            open_holes.update(
                unit for unit in _expected_live_ids(plan.unit.task_ref,
                                                   n_attempts)
                if unit not in skip and unit not in stored)

        if open_holes:
            shortfall.write_unit_ids(
                lm.round_dir(store, round_) / shortfall.SHORTFALL_NAME,
                set(accepted) | open_holes)

    accepted = shortfall.read_unit_ids(
        lm.round_dir(store, round_) / shortfall.SHORTFALL_NAME)

    # Judge each dispatched group
    for plan in dispatch:
        task_id = plan.unit.task_ref

        if _round_has_row(store, task_id, round_):
            continue

        if task_id not in baselines:
            _, baseline_counts = lm.load_baseline(
                store, task_id, n_attempts, aux_scorer=aux_scorer)
            baselines[task_id] = baseline_counts

        if _round_complete(ctx, task_id, round_, n_attempts,
                          skip_for(task_id)):
            lv.judge_round(
                store=store, task_id=task_id, round_=round_,
                rule_name=rule_name, baseline_counts=baselines[task_id],
                n_attempts=n_attempts, skip_ids=skip_for(task_id),
                aux_scorer=aux_scorer,
                max_injection_tokens=params.max_injection_tokens)
            lv.finalize_group(store, task_id)
        else:
            infra_failed.add(task_id)

    # ========================================================================
    # 7. TRIAL SHEETS + COVERAGE + GRADING
    # ========================================================================
    # The current round's universe is the planned groups; a reused unit
    # already has a previous phase's verdict and is not re-measured here.
    measured_plans = [plan for plan in plans
                      if plan.unit.unit_id not in reused]
    specs = [
        UnitSpec(
            unit_id=plan.unit.unit_id,
            phase_label=phase.label,
            task_ref=plan.unit.task_ref,
            role="weak",
            arm=None,
            expected_attempts=n_attempts,
            injected_skills=plan.unit.skills_injected,
        )
        for plan in measured_plans
    ]

    terminal_overrides: Dict[str, str] = {}
    for plan in measured_plans:
        task_id = plan.unit.task_ref
        if task_id in infra_failed:
            cause = vocabulary.CAUSE_ZERO_EXECUTION
        else:
            terminal = _round_terminal(store, task_id, round_)
            cause = _coverage_cause_for_terminal(terminal)
        if cause:
            terminal_overrides[plan.unit.unit_id] = cause

    coverage_records = coverage.live_coverage(
        store, specs, round_=round_,
        terminal_overrides=terminal_overrides,
        zero_execution_units=(),
        accepted_holes=accepted)

    round_store = Store(lm.round_dir(store, round_))
    spec_ids = {spec.unit_id for spec in specs}
    folder_by_task = {
        unit.task_ref: unit.aux["skill_folder"]
        for unit in all_units
        if unit.aux.get("skill_folder")}
    executions = []
    for trajectory_id in round_store.ids("trajectories"):
        trajectory = round_store.load(
            "trajectories", trajectory_id, Trajectory)
        unit_id = unit_id_for_trajectory(
            phase_label=phase.label, benchmark=ctx.config.benchmark,
            trajectory=trajectory)
        if unit_id not in spec_ids:
            continue
        executions.append(answer_sheets.execution_from_trajectory(
            trajectory, unit_id=unit_id, phase_label=phase.label,
            arm=None,
            aux_success=(aux_scorer(trajectory)
                         if aux_scorer is not None else None),
            folder=folder_by_task.get(trajectory.task_id)))
    sheets = answer_sheets.assemble_answer_sheets(
        executions, coverage_records, phase_label=phase.label)
    answer_sheets.grade_answer_sheets(
        sheets, verdicts.default_verdict_rule(ctx.config.benchmark))

    # ========================================================================
    # 8. WRITE SUMMARY
    # ========================================================================
    summary = {
        "role": PHASE_ROLE_LIVE,
        "infra_missing": sorted(infra_failed),
        "infra_accepted": sorted(accepted),
        "complete": not infra_failed,
        "infra_shortfall_accepted": bool(accepted),
    }
    report = phase_report.build_phase_report(
        sheets, specs, phase_kind=PHASE_ROLE_LIVE,
        learning_usable=True, summary=summary)

    phase_dir = phase_disk._phase_dir(ctx, phase)
    answer_sheets.write_answer_sheets(
        report.sheets, phase_dir / phase_report.ANSWER_SHEETS_DIR)
    answer_sheets.write_coverage(
        coverage_records, phase_dir / phase_report.COVERAGE_NAME)
    phase_report.write_unit_specs(specs, phase_dir)
    reuse_rows = [(unit.unit_id, unit.status, unit.cause)
                  for unit in report.units]
    reuse_rows.extend(
        (unit_id, status, cause)
        for unit_id, (status, cause, _summary) in reused.items()
        if unit_id not in {row[0] for row in reuse_rows})
    phase_disk._write_reuse_index(ctx, phase, reuse_rows)

    write_json(phase_dir / phase_disk.SUMMARY_NAME, summary, indent=2)

    if infra_failed:
        raise InfrastructureError(
            f"phase {phase.label!r}: {len(infra_failed)} live group(s) "
            "still have units pending on infrastructure errors "
            f"({', '.join(sorted(infra_failed))}); see summary's "
            "infra_missing, then resume to retry")

    return summary


def _coverage_cause_for_terminal(terminal: Optional[str]) -> str:
    """The live ledger terminal -> the coverage cause (empty for a judged
    normal row, whose None verdict means "no terminal")."""
    return {
        lv.WEAK_ALREADY_KNOWS: vocabulary.CAUSE_WEAK_ALREADY_KNOWS,
        lv.LIVE_INJECTION_OVERFLOW: vocabulary.CAUSE_INJECTION_OVERFLOW,
        lv.LIVE_UNDETERMINED: vocabulary.CAUSE_ALL_HOLES,
    }.get(terminal, "")


# ============================================================================
# Supporting functions
# ============================================================================



def _materialize_task_folders(ctx: StageContext, phase: PhaseInstance,
                              rows: List[Any], groups: Dict[str, List[Any]],
                              benchmark: str) -> Dict[str, str]:
    """Write each folder row's group into its own task directory.

    Empty manifests (the delivery's null state) produce no directory,
    matching the folder rule: no skills means no guidance file.
    """
    out: Dict[str, str] = {}
    for row in rows:
        manifest = engine._folder_manifest_of(row)
        if manifest is None or not manifest.get("files"):
            continue
        members = groups.get(row.task_ref, [])
        if not members:
            raise CodeBugError(
                f"folder row {row.unit_id!r} names task {row.task_ref!r} "
                "with no pending group to materialize")
        target = (phase_disk._phase_dir(ctx, phase) / "skill_folder"
                  / ids.raw_task_id(row.task_ref, benchmark))
        out[row.task_ref] = str(materialize_skill_folder(
            manifest, members, target))
    return out


def _round_of(ctx: StageContext, phase: PhaseInstance) -> int:
    """The round THIS live phase measures (k-th live phase → round k-1)."""
    earlier_live = sum(1 for record in phase_disk._earlier_phases(ctx, phase)
                      if record.get("role") == PHASE_ROLE_LIVE)
    if earlier_live >= len(lm.ROUNDS):
        raise ConfigError(
            f"phase {phase.label!r} would measure round {earlier_live}, "
            f"but rounds are pinned to {list(lm.ROUNDS)}")
    return earlier_live


def _expected_live_ids(task_id: str, n_attempts: int) -> List[str]:
    """Expected trajectory IDs for one live unit."""
    return [ids.make_trajectory_id(task_id, "weak", attempt)
            for attempt in range(n_attempts)]


def _round_complete(ctx: StageContext, task_id: str, round_: int,
                   n_attempts: int, skip: List[str]) -> bool:
    """Whether all expected units are stored or accepted holes."""
    directory = lm.round_dir(ctx.store, round_)
    if not directory.exists():
        return not [unit for unit in _expected_live_ids(task_id, n_attempts)
                   if unit not in set(skip)]

    round_store = Store(directory)
    stored = {unit for unit in round_store.ids("trajectories")
             if ids.parse_trajectory_id(unit)[0] == task_id}
    skipped = set(skip)

    return all(unit in stored or unit in skipped
              for unit in _expected_live_ids(task_id, n_attempts))


def _round_has_row(store: Store, task_id: str, round_: int) -> bool:
    """Whether the group's round already has a verdicts-ledger row."""
    return lm.first_row_from(
        lm.ledger_path(store, lm.VERDICTS_LEDGER), task_id,
        round_) is not None


def _round_terminal(store: Store, task_id: str,
                   round_: int) -> Optional[str]:
    """The group's round row terminal, if any."""
    row = lm.first_row_from(
        lm.ledger_path(store, lm.VERDICTS_LEDGER), task_id, round_)
    return row.get("verdict") if row is not None else None


def _frozen_audit_row(store: Store, round_: int,
                     task_id: str) -> Optional[Dict[str, Any]]:
    """The group's frozen injection-audit row, or None if never dispatched."""
    for row in read_jsonl(lm.round_dir(store, round_)
                         / lm.INJECTION_AUDIT_NAME):
        if row.get("task_id") == task_id:
            return row
    return None


def _freeze_audit_row(store: Store, task_id: str, round_: int,
                      block: Optional[str] = None) -> None:
    """Freeze audit row before dispatch or verify it matches re-derivation."""
    row = lm.audit_row(store, task_id, round_, block)
    frozen = _frozen_audit_row(store, round_, task_id)

    if frozen is not None:
        # Verify re-derivation matches frozen line
        if _canonical_line(frozen) != _canonical_line(row):
            raise CodeBugError(
                f"frozen injection_audit row of task {task_id!r} "
                f"round {round_} differs from re-derivation")
    else:
        # Freeze the audit row
        append_jsonl(lm.round_dir(store, round_) / lm.INJECTION_AUDIT_NAME, row)


def _canonical_line(row: Dict[str, Any]) -> str:
    """Canonical JSON line for byte-exact comparison."""
    return json.dumps(row, ensure_ascii=False, sort_keys=True)
