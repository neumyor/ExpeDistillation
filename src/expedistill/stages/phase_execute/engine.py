"""The real-execution engine: plan, dispatch, re-dispatch.

The shared machinery every K1-driven arm runs on: the delivery rows
become :class:`Plan` objects (full frozen unit ids attached), K1
dispatches them, and a unit whose dispatch died with nothing stored is
re-offered up to the durable per-unit budget. The roles grade the stored
trials themselves. All three execute roles (baseline, practice, test) go
through this engine.
"""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from expedistill.benchmarks.base import Task
from expedistill.blocks import tasks as tasks_block
from expedistill.composites.batch_execute import batch_execute
from expedistill.composites.unit_ids import make_unit_id
from expedistill.core import ids
from expedistill.core.config import PhaseInstance
from expedistill.core.contracts import ExecutionUnitRow
from expedistill.core.models import Trajectory
from expedistill.infra.reliability import (
    CodeBugError,
    ConfigError,
)
from expedistill.infra.store import Store, read_json, write_json
from expedistill.stages.context import StageContext
from expedistill.stages.phase_execute.phase_disk import (
    REDISPATCH_NAME,
    execute_phase_dir,
    folder_manifest_of,
)

__all__ = [
    "Plan",
    "arm_trajectories",
    "dispatch_arm",
    "plan_units",
    "planned_trajectory_ids",
    "run_arm_units",
    "task_map",
    "task_objects",
]


def task_objects(ctx: StageContext, raw_ids: Sequence[str]) -> List[Task]:
    """Rebuild the phase's Task objects through D1 (deterministic from
    the frozen config); a plugin answering outside the requested ids is
    a ConfigError before any execution starts."""
    tasks = tasks_block.load_tasks(ctx.config.benchmark, sorted(raw_ids))
    unexpected = sorted({task.task_id for task in tasks} - set(raw_ids))
    if unexpected:
        raise ConfigError(
            f"benchmark {ctx.config.benchmark!r} loaded tasks outside the "
            f"requested set: {unexpected}")
    return sorted(tasks, key=lambda task: task.task_id)


def task_map(ctx: StageContext,
             raw_ids: Sequence[str]) -> Dict[str, Task]:
    """``{prefixed task id: Task}`` over the raw id list (the join of
    delivery rows, K1's Task objects and the trajectory ids). An empty
    list is an empty map WITHOUT a plugin call: a plugin may legitimately
    refuse an empty task list (TB does -- empty means "the whole 89-task
    set by accident" is never allowed)."""
    if not raw_ids:
        return {}
    return {ids.make_task_id(ctx.config.benchmark, task.task_id): task
            for task in task_objects(ctx, raw_ids)}


@dataclass
class Plan:
    """One real-execution unit: the delivery row with its FULL frozen
    unit id attached, its role and its Task object."""

    unit: ExecutionUnitRow
    role: str
    task: Task
    n_attempts: int


def plan_units(rows: Sequence[ExecutionUnitRow],
               tasks: Dict[str, Task], *,
               phase_label: str, benchmark: str, role: str,
               n_attempts: int,
               skill_folder_dir: Optional[str] = None,
               ) -> List[Plan]:
    """Rebuild each delivery row's full frozen unit id (unit_ids' frozen
    rule; the row's task ref and sha8 already carry the pieces -- the
    P2 ruling's one rebuild site) and pair it with its Task object.

    The row's task ref is normalized to the PREFIXED task id: delivery
    answers whatever identity its tasks carried (a Task object's raw
    id or a prefixed id), while the frozen unit id, the trajectory ids
    and the reports all speak the prefixed form.

    A row whose aux carries the folder delivery's manifest (the
    real_folder execution; presence read through
    ``phase_disk.folder_manifest_of``, the one folder-row predicate) gets it
    attached to its plan's Task meta together with ``skill_folder_dir``
    -- the materialized folder's local path, which the CALLER
    materialized before dispatch (the phase driver owns the bank the
    manifest resolves against). A manifest with no files (an empty
    bank) is attached all the same: the unit runs with the folder prompt
    and the manifest's ``playbook_text`` in the same place as a with-bank
    unit, only the catalog is empty -- the bank is the one difference
    between such a unit and a with-bank one, never the presentation. A
    folder row without a directory is a wiring bug, not a configuration
    problem."""
    plans: List[Plan] = []
    for row in rows:
        ref = row.task_ref
        if ref not in tasks:
            prefixed = ids.make_task_id(benchmark, ref)
            if prefixed in tasks:
                ref = prefixed
            else:
                raise CodeBugError(
                    f"delivery resolved unit {row.unit_id!r} outside the "
                    "phase's task set; the driver and the delivery "
                    "disagree")
        task = tasks[ref]
        manifest = folder_manifest_of(row)
        if manifest is not None:
            folder_dir = skill_folder_dir
            if folder_dir is None:
                raise CodeBugError(
                    f"delivery resolved unit {row.unit_id!r} with a skill "
                    "folder but the phase passes no materialized "
                    "directory: materialize the folder before dispatch "
                    "(the manifest resolves against the phase bank)")
            task = replace(task, meta={**task.meta,
                                       "skill_folder": manifest,
                                       "skill_folder_dir": folder_dir})
        plans.append(Plan(
            unit=replace(
                row,
                task_ref=ref,
                unit_id=make_unit_id(
                    phase_label=phase_label, benchmark=benchmark,
                    task_ref=ref, role=role,
                    skills_injected=row.skills_injected)),
            role=role, task=task,
            n_attempts=n_attempts))
    plans.sort(key=lambda plan: plan.unit.unit_id)
    return plans


def _skills_for_task(plans: Sequence[Plan]
                     ) -> Callable[[Task], Tuple[Any, str]]:
    """K1's per-task injection resolver over the planned rows. When every
    unit is bare (``skills_injected`` None) every task resolves to the
    empty pair, whatever block its row carries."""
    if all(plan.unit.skills_injected is None for plan in plans):
        return lambda task: (None, "")
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


def dispatch_arm(ctx: StageContext, phase: PhaseInstance, *,
                 plans: Sequence[Plan], store: Store, arm: str,
                 skip_unit_ids: Set[str]) -> None:
    """One K1 batch over the arm's plans at the arm's frozen depth.

    K1 is called through this module's ``batch_execute`` name, so the
    tests wrap it at ``engine.batch_execute``."""
    if not plans:
        return
    role = plans[0].role
    batch_execute(
        store=store,
        phase_label=phase.label,
        benchmark=ctx.config.benchmark,
        tasks=[plan.task for plan in plans],
        role=role,
        model_name=ctx.config.models[f"executor_{role}"],
        handle=ctx.handle(f"executor_{role}"),
        n_attempts=plans[0].n_attempts,
        skills_for_task=_skills_for_task(plans),
        workdir_root=(Path(ctx.workdir_root)
                      / ids.phase_tag(phase.label) / arm),
        usage=ctx.usage,
        run_dir=ctx.run_dir,
        workers=ctx.run_settings.rollout,
        dispatch_stagger=ctx.run_settings.k1_stagger,
        skip_unit_ids=skip_unit_ids,
    )


def planned_trajectory_ids(phase_label: str, plan: Plan) -> List[str]:
    """The trajectory ids one plan expects, in attempt order."""
    return [ids.make_trajectory_id(phase_label, plan.unit.task_ref,
                                   plan.role, attempt)
            for attempt in range(plan.n_attempts)]


def _missing_attempts(store: Store, phase_label: str, plan: Plan, *,
                      accepted: Set[str]) -> List[str]:
    """Expected trajectory ids that are neither stored nor accepted holes."""
    missing: List[str] = []
    for trajectory_id in planned_trajectory_ids(phase_label, plan):
        if trajectory_id in accepted:
            continue
        if not store.exists("trajectories", trajectory_id):
            missing.append(trajectory_id)
    return missing


def run_arm_units(ctx: StageContext, phase: PhaseInstance, *,
                  plans: Sequence[Plan], store: Store, arm: str,
                  limit: int, accepted: Set[str]
                  ) -> Tuple[List[Trajectory], Set[str]]:
    """Dispatch and re-dispatch one arm's units; return stored trials.

    ``accepted`` is the CALLER's durable accepted-hole set: accepted holes
    are never re-dispatched and never re-raised.  Every call first offers
    every unit to K1 (K1 skips stored trials, so a resumed phase dispatches
    exactly its holes: each run of the phase is one more pass over them).
    The re-dispatch loop is the frozen rule 6 on top of that: a unit whose
    dispatch died with nothing stored is re-offered while its budget lasts,
    all such units together in one K1 batch per round.  The budget is
    durable, so a resumed phase continues the same budget.

    This function deliberately builds no unit summaries: it returns the
    stored trajectories and the unaccepted hole ids.  Coverage and unit
    summaries are derived later by the pure functions.
    """
    state_path = execute_phase_dir(ctx, phase, arm) / REDISPATCH_NAME
    state = _redispatch_state(state_path)
    dispatch_arm(ctx, phase, plans=plans, store=store, arm=arm,
                 skip_unit_ids=accepted)
    for plan in plans:
        state.setdefault(plan.unit.unit_id, {"passes": 0})
    while True:
        due = [plan for plan in plans
               if state[plan.unit.unit_id]["passes"] < limit
               and _missing_attempts(store, phase.label, plan,
                                     accepted=accepted)]
        if not due:
            break
        for plan in due:
            state[plan.unit.unit_id]["passes"] += 1  # one more K1 offer
        _save_redispatch_state(state_path, state)
        dispatch_arm(ctx, phase, plans=due, store=store, arm=arm,
                     skip_unit_ids=accepted)
    _save_redispatch_state(state_path, state)
    holes: Set[str] = set()
    for plan in plans:
        holes.update(_missing_attempts(store, phase.label, plan,
                                       accepted=accepted))

    planned_ids = {trajectory_id for plan in plans
                   for trajectory_id in planned_trajectory_ids(
                       phase.label, plan)}
    trajectories = [
        store.load("trajectories", trajectory_id, Trajectory)
        for trajectory_id in sorted(planned_ids)
        if trajectory_id not in accepted
        and store.exists("trajectories", trajectory_id)
    ]
    return trajectories, holes


def arm_trajectories(store: Store) -> List[Trajectory]:
    """Every trajectory in one arm store, sorted by id."""
    return [store.load("trajectories", trajectory_id, Trajectory)
            for trajectory_id in store.ids("trajectories")]
