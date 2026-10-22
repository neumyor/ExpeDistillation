"""The real-execution engine: plan, dispatch, judge, re-dispatch.

The shared machinery every K1-driven arm runs on: the delivery rows
become :class:`_Plan` objects (full frozen unit ids attached), K1
dispatches them, each attempt is judged under the phase's named rule,
and a unit whose dispatch died with nothing stored is re-offered up to
the durable per-unit budget. The baseline and final-test roles go
through this engine; the exam and live roles run their own protocols.
"""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from expedistill.benchmarks.base import Task
from expedistill.blocks import tasks as tasks_block
from expedistill.composites import verdicts
from expedistill.composites.reports import (
    run_record_from_trajectory,
)
from expedistill.core import ids
from expedistill.core.config import PhaseInstance
from expedistill.core.contracts import AcceptanceReportRow, ExecutionUnitRow
from expedistill.core.models import Trajectory
from expedistill.infra.reliability import (
    CodeBugError,
    ConfigError,
)
from expedistill.infra.store import Store, read_json, write_json
from expedistill.stages.context import StageContext

# The K1 seam: the tests wrap the package-level ``batch_execute`` (the
# entry module re-exports it), so every dispatch in this package goes
# through the package attribute at call time, never a module-local name.
import expedistill.stages.phase_execute as _package

from expedistill.stages.phase_execute.phase_disk import (
    REDISPATCH_NAME,
    _phase_dir,
)

__all__ = [
    "_Plan",
    "_arm_trajectories",
    "_dispatch_arm",
    "_folder_manifest_of",
    "_plan_units",
    "_prefixed_task_map",
    "_redispatch_state",
    "_run_arm_units",
    "_save_redispatch_state",
    "_skills_for_task",
    "_task_objects",
    "_unit_row_for",
    "_unit_runs",
]


def _task_objects(ctx: StageContext, raw_ids: Sequence[str]) -> List[Task]:
    """Rebuild the phase's Task objects through D1 (deterministic from
    the frozen config); a plugin answering outside the requested ids is
    a ConfigError before any execution starts (the stage-1 rule)."""
    tasks = tasks_block.load_tasks(ctx.config.benchmark, sorted(raw_ids))
    unexpected = sorted({task.task_id for task in tasks} - set(raw_ids))
    if unexpected:
        raise ConfigError(
            f"benchmark {ctx.config.benchmark!r} loaded tasks outside the "
            f"requested set: {unexpected}")
    return sorted(tasks, key=lambda task: task.task_id)


def _prefixed_task_map(ctx: StageContext,
                       raw_ids: Sequence[str]) -> Dict[str, Task]:
    """``{prefixed task id: Task}`` over the raw id list (the join of
    delivery rows, K1's Task objects and the trajectory ids). An empty
    list is an empty map WITHOUT a plugin call: a live phase whose
    pending-group universe is empty (an assembled run with a fully
    accepted bank) resolves zero tasks, and a plugin may legitimately
    refuse an empty task list (TB does -- empty means "the whole
    89-task set by accident" is never allowed)."""
    if not raw_ids:
        return {}
    return {ids.make_task_id(ctx.config.benchmark, task.task_id): task
            for task in _task_objects(ctx, raw_ids)}


@dataclass
class _Plan:
    """One real-execution unit: the delivery row with its FULL frozen
    unit id attached, its role and its Task object."""

    unit: ExecutionUnitRow
    role: str
    task: Optional[Task]
    n_attempts: int


def _folder_manifest_of(row: ExecutionUnitRow) -> Optional[Any]:
    """The row's folder manifest, or None when it carries none (the one
    folder-row predicate: the folder delivery attaches ONE phase-level
    manifest to every row, every other delivery's rows carry none, so
    presence alone is the question -- the engine's attachment, the
    phase driver's materialization and its read-set pass all ask it
    here). A manifest with no ``files`` is the empty accepted bank's
    null state; what that means (no materialization, no attachment, but
    the summary still reports the read sets) is each caller's frozen
    rule, not this predicate's."""
    return row.aux.get("skill_folder")


def _plan_units(rows: Sequence[ExecutionUnitRow],
                tasks: Optional[Dict[str, Task]], *,
                phase_label: str, benchmark: str, role: str,
                n_attempts: int,
                skill_folder_dir: Optional[str] = None,
                skill_folder_dirs: Optional[Mapping[str, str]] = None
                ) -> List[_Plan]:
    """Rebuild each delivery row's full frozen unit id (verdicts' frozen
    rule; the row's task ref and sha8 already carry the pieces -- the
    P2 ruling's one rebuild site) and pair it with its Task object when
    the phase dispatches K1 (the exam role pairs none).

    The row's task ref is normalized to the PREFIXED task id: delivery
    answers whatever identity its tasks carried (a Task object's raw
    id, a prefixed id, an exam ref), while the frozen unit id, the
    trajectory ids and the reports all speak the prefixed form. Exam
    refs pass through unchanged (they are not task ids).

    A row whose aux carries the folder delivery's manifest (the
    real_folder execution, M3; presence read through
    ``_folder_manifest_of``, the one folder-row predicate) gets it
    attached to its plan's Task meta together with ``skill_folder_dir``
    -- the materialized folder's local path, which the CALLER
    materialized before dispatch (the phase driver owns the bank the
    manifest resolves against). ``skill_folder_dirs`` maps each task ref
    to its own materialized directory (the live phase's per-task
    folders); when it is absent, the single phase-level
    ``skill_folder_dir`` applies (the test phase). The manifest with no
    files is the delivery's null state (an empty accepted bank): no
    attachment, the unit runs bare -- the D9 empty-injection lesson,
    never a guidance claiming skills that do not exist. A manifest with
    files but no directory is a wiring bug, not a configuration
    problem."""
    plans: List[_Plan] = []
    for row in rows:
        ref = row.task_ref
        if tasks is not None and ref not in tasks:
            prefixed = ids.make_task_id(benchmark, ref)
            if prefixed in tasks:
                ref = prefixed
            else:
                raise CodeBugError(
                    f"delivery resolved unit {row.unit_id!r} outside the "
                    "phase's task set; the driver and the delivery "
                    "disagree")
        task = tasks[ref] if tasks is not None else None
        manifest = _folder_manifest_of(row)
        if manifest is not None and manifest.get("files"):
            folder_dir = (skill_folder_dirs.get(ref)
                          if skill_folder_dirs is not None
                          else skill_folder_dir)
            if folder_dir is None or task is None:
                raise CodeBugError(
                    f"delivery resolved unit {row.unit_id!r} with a skill "
                    "folder but the phase passes no materialized "
                    "directory: materialize the folder before dispatch "
                    "(the manifest resolves against the phase bank)")
            task = replace(task, meta={**task.meta,
                                       "skill_folder": manifest,
                                       "skill_folder_dir": folder_dir})
        plans.append(_Plan(
            unit=replace(
                row,
                task_ref=ref,
                unit_id=verdicts.make_unit_id(
                    phase_label=phase_label, benchmark=benchmark,
                    task_ref=ref, role=role,
                    skills_injected=row.skills_injected)),
            role=role, task=task,
            n_attempts=n_attempts))
    plans.sort(key=lambda plan: plan.unit.unit_id)
    return plans


def _skills_for_task(plans: Sequence[_Plan]
                     ) -> Optional[Callable[[Task], Tuple[Any, str]]]:
    """K1's per-task injection resolver over the planned rows (None when
    every unit is a bare scalar pair -- the frozen whole-bank and bare
    paths, so K1 records the same scalar bookkeeping it always did)."""
    if all(plan.unit.skills_injected is None for plan in plans):
        return None
    by_task = {plan.task.task_id: (plan.unit.skills_injected,
                                   plan.unit.skills_block)
               for plan in plans}

    def resolve(task: Task) -> Tuple[Any, str]:
        try:
            return by_task[task.task_id]
        except KeyError:
            raise CodeBugError(
                f"task {task.task_id!r} has no delivery resolution: the "
                "per-task mapping was built over a different task set"
            ) from None

    return resolve


def _redispatch_state(path: Path) -> Dict[str, Dict[str, int]]:
    """The durable per-unit re-dispatch bookkeeping: ``{unit_id:
    {"passes": n}}`` -- how many times the phase re-offered the unit's
    holes to K1 (counted against the config's budget)."""
    if not path.exists():
        return {}
    try:
        state = read_json(path)
    except (OSError, ValueError) as exc:
        raise CodeBugError(
            f"unreadable redispatch record {path}: {exc}") from None
    if not isinstance(state, dict):
        raise CodeBugError(f"invalid redispatch record {path}")
    return state


def _save_redispatch_state(path: Path,
                           state: Dict[str, Dict[str, int]]) -> None:
    write_json(path, state, indent=2)


def _dispatch_arm(ctx: StageContext, phase: PhaseInstance, *,
                  plans: Sequence[_Plan], store: Store, arm: str,
                  skip_unit_ids: Set[str],
                  skills_for_task: Optional[Callable] = None
                  ) -> None:
    """One K1 batch over the arm's plans at the arm's frozen depth."""
    if not plans:
        return
    role = plans[0].role
    _package.batch_execute(
        store=store,
        benchmark=ctx.config.benchmark,
        tasks=[plan.task for plan in plans],
        role=role,
        model_name=ctx.config.models[f"executor_{role}"],
        handle=ctx.handle(f"executor_{role}"),
        n_attempts=plans[0].n_attempts,
        skills_for_task=(skills_for_task if skills_for_task is not None
                         else _skills_for_task(plans)),
        workdir_root=(Path(ctx.workdir_root)
                      / phase.label.replace(":", "_") / arm),
        usage=ctx.usage,
        run_dir=ctx.run_dir,
        workers=ctx.run_settings.workers,
        dispatch_stagger=ctx.run_settings.k1_stagger,
        skip_unit_ids=skip_unit_ids,
    )


def _unit_runs(store: Store, plan: _Plan, *, accepted: Set[str],
               rule_name: str
               ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """One unit's judged runs: every expected attempt either judged
    under the named rule, an accepted hole (no run at all) or a missing
    invalid run. Returns ``(runs, missing_ids)`` -- the frozen reader's
    shape (the run key is the trajectory id, verdict and basis)."""
    runs: List[Dict[str, Any]] = []
    missing: List[str] = []
    for attempt in range(plan.n_attempts):
        trajectory_id = ids.make_trajectory_id(
            plan.unit.task_ref, plan.role, attempt)
        if trajectory_id in accepted:
            continue
        if store.exists("trajectories", trajectory_id):
            trajectory = store.load("trajectories", trajectory_id,
                                    Trajectory)
            verdict, basis = verdicts.verdict_from_run(
                run_record_from_trajectory(trajectory),
                rule_name=rule_name)
        else:
            missing.append(trajectory_id)
            verdict = verdicts.RUN_INVALID
            basis = ("missing: dispatched but never completed (infra "
                     "retries exhausted)")
        runs.append({"run": trajectory_id, "verdict": verdict,
                     "basis": basis})
    return runs, missing


def _unit_row_for(plan: _Plan, *, runs: List[Dict[str, Any]],
                  missing: int, phase_label: str) -> AcceptanceReportRow:
    """The unit's report row over its judged runs (the frozen reader's
    status mapping: any valid run -> normal; every unit a hole or every
    stored run unscored -> all_holes; a dispatch that died mid-unit ->
    invalid_redispatch_exhausted)."""
    if any(run["verdict"] != verdicts.RUN_INVALID for run in runs):
        status, cause = verdicts.STATUS_NORMAL, ""
    elif not runs or missing == 0:
        status, cause = (verdicts.STATUS_NOT_TESTABLE,
                         verdicts.CAUSE_ALL_HOLES)
    else:
        status, cause = (verdicts.STATUS_NOT_TESTABLE,
                         verdicts.CAUSE_INVALID_REDISPATCH_EXHAUSTED)
    return verdicts.summarize_unit(
        unit_id=plan.unit.unit_id, phase_label=phase_label,
        role=plan.role, injected_skills=plan.unit.skills_injected,
        runs=runs, status=status, cause=cause,
        artifacts={"trajectory_ids": [run["run"] for run in runs]})


def _run_arm_units(ctx: StageContext, phase: PhaseInstance, *,
                   plans: Sequence[_Plan], store: Store, arm: str,
                   rule_name: str, limit: int, accepted: Set[str]
                   ) -> Tuple[List[AcceptanceReportRow], Set[str]]:
    """Dispatch, judge and re-dispatch one arm's units.

    ``accepted`` is the CALLER's durable accepted-hole set (the baseline
    phase passes the stage-1 record's): accepted holes are never
    re-dispatched, never re-raised -- the durable-acceptance contract
    every later call honours with or without the flag.

    Returns ``(rows, unaccepted_holes)``. The re-dispatch loop is the
    frozen rule 6: a unit whose dispatch died with nothing stored is
    re-offered to K1 while the budget lasts. The bookkeeping is durable,
    so a resumed phase continues the same budget instead of restarting
    it, and a unit whose budget is already spent never re-enters K1
    (the initial batch excludes it; its row reports the cause and the
    phase raises for the operator's shortfall decision)."""
    state_path = _phase_dir(ctx, phase, arm) / REDISPATCH_NAME
    state = _redispatch_state(state_path)
    open_budget = [plan for plan in plans
                   if state.get(plan.unit.unit_id,
                                {}).get("passes", 0) < limit]
    _dispatch_arm(ctx, phase, plans=open_budget, store=store, arm=arm,
                  skip_unit_ids=accepted)
    rows: List[AcceptanceReportRow] = []
    holes: Set[str] = set()
    for plan in plans:
        entry = state.setdefault(plan.unit.unit_id, {"passes": 0})
        while True:
            runs, missing = _unit_runs(store, plan, accepted=accepted,
                                       rule_name=rule_name)
            if not missing or entry["passes"] >= limit:
                break  # nothing to re-offer, or the budget is spent
            entry["passes"] += 1  # one more K1 offer of the holes
            _save_redispatch_state(state_path, state)
            _dispatch_arm(ctx, phase, plans=[plan], store=store, arm=arm,
                          skip_unit_ids=accepted)
        _save_redispatch_state(state_path, state)
        holes.update(missing)
        rows.append(_unit_row_for(plan, runs=runs, missing=len(missing),
                                  phase_label=phase.label))
    return rows, holes


def _arm_trajectories(store: Store) -> List[Trajectory]:
    """Every trajectory in one arm store, sorted by id."""
    return [store.load("trajectories", trajectory_id, Trajectory)
            for trajectory_id in store.ids("trajectories")]
