"""The live phase (B): see ``_run_live``'s docstring and the package
entry's module docstring (the role list, the frozen internal order and
the resume model)."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from expedistill.benchmarks.aux import aux_wiring
from expedistill.composites import decision, shortfall
from expedistill.composites import live_measurement as lm
from expedistill.composites import live_verdicts as lv
from expedistill.composites import reports
from expedistill.core import ids
from expedistill.core.config import (
    PHASE_ROLE_LIVE,
    ExecutePhaseSpec,
    PhaseInstance,
)
from expedistill.core.models import Skill
from expedistill.infra.reliability import (
    CodeBugError,
    ConfigError,
    InfrastructureError,
)
from expedistill.infra.store import Store, append_jsonl, read_jsonl, write_json
from expedistill.inject import estimate_tokens
from expedistill.stages.context import StageContext
from expedistill.stages.phase_execute.engine import (
    _Plan,
    _plan_units,
    _prefixed_task_map,
)
from expedistill.stages.phase_execute.phase_disk import (
    SUMMARY_NAME,
    _earlier_phases,
    _phase_dir,
    _rebased_row,
    _reused_rows,
    _write_report,
    _write_units,
)
from expedistill.stages.phase_execute.resolve import (
    _compare_rule,
    _execution_params,
    _resolve_delivery,
)

# The K1 seam (see engine.py): dispatch goes through the package attribute.
import expedistill.stages.phase_execute as _package


def _live_phase_groups(ctx: StageContext, task_ids: Set[str]
                       ) -> Dict[str, List[Skill]]:
    """The live phase's group universe: the store's pending groups (the
    same derivation the frozen audit line is built from), filtered to
    the phase's task set."""
    return {task_id: members
            for task_id, members in lm.pending_groups(ctx.store).items()
            if task_id in task_ids}


def _round_of(ctx: StageContext, phase: PhaseInstance) -> int:
    """The round THIS live phase measures: the k-th live phase of the
    sequence measures round ``k-1`` (the rounds are pinned to {0, 1})."""
    earlier_live = sum(1 for record in _earlier_phases(ctx, phase)
                       if record.get("role") == PHASE_ROLE_LIVE)
    if earlier_live >= len(lm.ROUNDS):
        raise ConfigError(
            f"phase {phase.label!r} would measure live round "
            f"{earlier_live}, but the rounds are pinned to "
            f"{list(lm.ROUNDS)} (user ruling 8): a sequence carries at "
            "most two live phases")
    return earlier_live


def _round_complete(ctx: StageContext, task_id: str, round_: int,
                    n_attempts: int, skip: Sequence[str]) -> bool:
    """Ruling A10's completeness half: every expected unit is stored or
    an accepted hole (the all-holes round is complete without a
    trajectory)."""
    directory = lm.round_dir(ctx.store, round_)
    if not directory.exists():
        return not [unit for unit in _expected_live_ids(task_id,
                                                         n_attempts)
                    if unit not in set(skip)]
    round_store = Store(directory)
    stored = {unit for unit in round_store.ids("trajectories")
              if ids.parse_trajectory_id(unit)[0] == task_id}
    skipped = set(skip)
    return all(unit in stored or unit in skipped
               for unit in _expected_live_ids(task_id, n_attempts))


def _expected_live_ids(task_id: str, n_attempts: int) -> List[str]:
    return [ids.make_trajectory_id(task_id, "weak", attempt)
            for attempt in range(n_attempts)]


def _frozen_audit_row(store: Store, round_: int,
                      task_id: str) -> Optional[Dict[str, Any]]:
    """The group's frozen injection-audit row of one round, or None
    when the round was never dispatched for it."""
    for row in read_jsonl(lm.round_dir(store, round_)
                          / lm.INJECTION_AUDIT_NAME):
        if row.get("task_id") == task_id:
            return row
    return None


def _canonical_line(row: Dict[str, Any]) -> str:
    """The row as append_jsonl writes it: the frozen line and the
    re-derivation compare as these exact bytes."""
    return json.dumps(row, ensure_ascii=False, sort_keys=True)


def _run_live(ctx: StageContext, phase: PhaseInstance,
              spec: ExecutePhaseSpec) -> Dict[str, Any]:
    """One live round over every group that still owes it, measured
    through the frozen verdicts-ledger protocol (``judge_round`` /
    ``finalize_group`` -- the ledger rows are the resume protocol; the
    coordinator's 2026-10-16 ruling keeps this call shape)."""
    params = _execution_params(spec)
    n_attempts = (params.n_attempts if params.n_attempts is not None
                  else ctx.config.n_attempts_weak)
    if n_attempts > ctx.config.n_attempts_weak:
        raise ConfigError(
            f"phase {phase.label!r}: the live depth ({n_attempts}) exceeds "
            f"n_attempts_weak ({ctx.config.n_attempts_weak}); the baseline "
            "would not cover the live executions (the old cross-check, "
            "restated here because the config validation is bypassed "
            "when a spec is constructed directly)")
    round_ = _round_of(ctx, phase)
    rule_name = _compare_rule(spec)
    store = ctx.store
    _, aux_scorer = aux_wiring(ctx.config.benchmark)
    stage1_holes = shortfall.stage1_accepted_unit_ids(ctx.run_dir)

    # -- finalization sweep: finish any commit a crash left hanging -----
    for task_id in sorted(lm.pending_groups(store)):
        lv.finalize_group(store, task_id)

    benchmark = ctx.config.benchmark
    groups = _live_phase_groups(
        ctx, {ids.make_task_id(benchmark, raw) for raw in phase.task_ids})
    task_ids = sorted(groups)
    delivery, delivery_params = _resolve_delivery(spec)
    rows = delivery.resolve(lm.pending_groups(store), task_ids,
                           delivery_params)
    tasks = _prefixed_task_map(
        ctx, sorted(ids.raw_task_id(task_id, benchmark)
                    for task_id in task_ids))
    plans = _plan_units(rows, tasks, phase_label=phase.label,
                        benchmark=benchmark, role="weak",
                        n_attempts=n_attempts)
    _write_units(ctx, phase, [plan.unit for plan in plans])
    plan_by_task = {plan.unit.task_ref: plan for plan in plans}
    reused = _reused_rows(ctx, phase, [plan.unit for plan in plans])

    accepted = shortfall.read_unit_ids(
        lm.round_dir(store, round_) / shortfall.SHORTFALL_NAME)

    def skip_for(task_id: str) -> List[str]:
        return lm.skip_ids_for(stage1_holes, accepted, task_id)

    # -- entry guards and the dispatch set -------------------------------
    # Baselines load before a single execution is spent; the zero-
    # execution short-circuits write their terminal ledger rows (the
    # coordinator's ruling: judge_round + finalize_group, the old
    # driver's shape).
    dispatch: List[_Plan] = []
    baselines: Dict[str, Dict[str, int]] = {}
    infra_failed: Set[str] = set()
    for task_id in task_ids:
        plan = plan_by_task[task_id]
        if plan.unit.unit_id in reused:
            continue  # rule 1: the last verdict stands, nothing re-runs
        if _round_has_row(store, task_id, round_):
            # A resumed call already judged this group's round, and the
            # entry sweep finalized it; it owes this phase nothing.
            continue
        _, baseline_counts = lm.load_baseline(store, task_id, n_attempts,
                                              aux_scorer=aux_scorer)
        baselines[task_id] = baseline_counts
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
        if decision.live_injection_overflows(
                max_injection_tokens=params.max_injection_tokens,
                injection_tokens=estimate_tokens(plan.unit.skills_block)):
            # The overflow verdict must carry the frozen audit row's own
            # evidence: freeze it first (the old driver's order).
            _freeze_audit_row(store, task_id, round_)
            lv.judge_round(
                store=store, task_id=task_id, round_=round_,
                rule_name=rule_name, baseline_counts=baseline_counts,
                n_attempts=n_attempts, skip_ids=[],
                terminal=lv.LIVE_INJECTION_OVERFLOW,
                max_injection_tokens=params.max_injection_tokens)
            lv.finalize_group(store, task_id)
            continue
        if _round_complete(ctx, task_id, round_, n_attempts,
                           skip_for(task_id)):
            dispatch.append(plan)  # stored already; owes only judgment
            continue
        _freeze_audit_row(store, task_id, round_)
        dispatch.append(plan)

    # -- one K1 batch for the round (ruling A7) ---------------------------
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

    # -- shortfall first, then the judgment on the shrunken sets ---------
    if ctx.run_settings.allow_infra_shortfall:
        open_holes = set()
        for plan in dispatch:
            if _round_has_row(store, plan.unit.task_ref, round_):
                continue
            skip = set(skip_for(plan.unit.task_ref))
            open_holes.update(
                unit for unit in _expected_live_ids(plan.unit.task_ref,
                                                    n_attempts)
                if unit not in skip
                and not _round_stores(store, plan.unit.task_ref, round_))
        if open_holes:
            shortfall.write_unit_ids(
                lm.round_dir(store, round_) / shortfall.SHORTFALL_NAME,
                set(accepted) | open_holes)
    accepted = shortfall.read_unit_ids(
        lm.round_dir(store, round_) / shortfall.SHORTFALL_NAME)

    for plan in dispatch:
        task_id = plan.unit.task_ref
        if _round_has_row(store, task_id, round_):
            continue  # a terminal fired above, or a resume already judged
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

    # -- the report: the frozen live reader over this round, plus the
    # rule-1 rows of the groups that never re-ran ------------------------
    # A zero-execution terminal is finalized before the reader runs, so
    # the reader's fallback (the pending members) sees an emptied group;
    # the delivery row is the truth about what the group was at the skip
    # decision, so its injected refs and id replace the reader's for the
    # groups this phase never dispatched.
    report = reports.acceptance_report(
        store, kind="live", benchmark=benchmark, phase_label=phase.label,
        round_=round_)
    by_id = {row.unit_id: row for row in report}
    for plan in plans:
        for row in report:
            if (decision.parse_unit_id(row.unit_id)[0]
                    == plan.unit.task_ref
                    and row.unit_id != plan.unit.unit_id):
                del by_id[row.unit_id]
                by_id[plan.unit.unit_id] = replace(
                    row, unit_id=plan.unit.unit_id,
                    injected_skills=plan.unit.skills_injected)
    for plan in plans:
        if plan.unit.unit_id in reused:
            by_id.setdefault(
                plan.unit.unit_id,
                _rebased_row(reused[plan.unit.unit_id],
                             unit_id=plan.unit.unit_id,
                             phase_label=phase.label))
    report = sorted(by_id.values(), key=lambda row: row.unit_id)
    _write_report(ctx, phase, report)

    summary = {
        "role": PHASE_ROLE_LIVE,
        "round": round_,
        "n_attempts": n_attempts,
        "improvement_rule": rule_name,
        "units": [plan.unit.unit_id for plan in plans],
        "dispatched": [plan.unit.task_ref for plan in dispatch],
        "skipped_reused": sorted(
            plan.unit.unit_id for plan in plans
            if plan.unit.unit_id in reused),
        "skipped_control_perfect": sorted(
            plan.unit.unit_id for plan in plans
            if _round_terminal(store, plan.unit.task_ref, round_)
            == lv.WEAK_ALREADY_KNOWS),
        "skipped_injection_overflow": sorted(
            plan.unit.unit_id for plan in plans
            if _round_terminal(store, plan.unit.task_ref, round_)
            == lv.LIVE_INJECTION_OVERFLOW),
        "infra_missing": sorted(infra_failed),
        "infra_accepted": sorted(accepted),
        "report_rows": len(report),
    }
    summary["complete"] = not summary["infra_missing"]
    summary["infra_shortfall_accepted"] = bool(accepted)
    write_json(_phase_dir(ctx, phase) / SUMMARY_NAME, summary, indent=2)
    if infra_failed:
        raise InfrastructureError(
            f"phase {phase.label!r}: {len(infra_failed)} live group(s) "
            "still have units pending on infrastructure errors "
            f"({', '.join(sorted(infra_failed))}); see the summary's "
            "infra_missing, then resume the run to retry them")
    return summary


def _round_has_row(store: Store, task_id: str, round_: int) -> bool:
    """Whether the group's round already carries a verdicts-ledger row."""
    return lm.first_row_from(
        lm.ledger_path(store, lm.VERDICTS_LEDGER), task_id,
        round_) is not None


def _round_terminal(store: Store, task_id: str,
                    round_: int) -> Optional[str]:
    """The group's round row terminal, if it wrote one."""
    row = lm.first_row_from(
        lm.ledger_path(store, lm.VERDICTS_LEDGER), task_id, round_)
    return row.get("verdict") if row is not None else None


def _round_stores(store: Store, task_id: str, round_: int) -> bool:
    """Whether the group's round store holds ANY trajectory of the
    task (False means nothing was stored for it at all)."""
    directory = lm.round_dir(store, round_)
    if not directory.exists():
        return False
    round_store = Store(directory)
    return any(ids.parse_trajectory_id(unit)[0] == task_id
               for unit in round_store.ids("trajectories"))


def _freeze_audit_row(store: Store, task_id: str, round_: int) -> None:
    """Ruling A10: freeze the audit row BEFORE the round's dispatch (the
    first write), or compare the re-derivation byte for byte against the
    frozen line (a resumed dispatch)."""
    row = lm.audit_row(store, task_id, round_)
    frozen = _frozen_audit_row(store, round_, task_id)
    if frozen is not None:
        if _canonical_line(frozen) != _canonical_line(row):
            raise CodeBugError(
                f"the frozen injection_audit row of task {task_id!r} "
                f"round {round_} differs from the re-derivation:\nstored: "
                f"{_canonical_line(frozen)}\nrecomputed: "
                f"{_canonical_line(row)}; the group's pending members "
                "changed under the frozen line -- refusing to re-dispatch")
    else:
        append_jsonl(lm.round_dir(store, round_)
                     / lm.INJECTION_AUDIT_NAME, row)
