"""Stage 1: dual-model bare baseline execution (README 3, stage 1).

Wiring only (layer 6): K1 runs each arm (per-unit persistence and resume are
K1's -- this stage never stores a trajectory itself), D3 aggregates the
returned trajectories, D4 applies the frozen task_select rule
(``config.rules["task_select"]``; core.config fills the README default) to
the weak arm's metrics. Both arms execute the train set: the test set is stage 6's
job in split mode (and equals the train set in full mode), so ``test_tasks``
is accepted for pipeline uniformity and not used here.

``train_tasks`` / ``test_tasks`` are task-ID LISTS (raw benchmark ids, the
``"train"`` / ``"test"`` values stage 0 froze), never Task objects, so every
stage-to-stage value stays JSON-serialisable. The Task objects K1 needs are
rebuilt through D1 (``blocks.tasks.load_tasks``), which is deterministic from
the frozen config: the plugin resolves the same ids into the same tasks, and
an id the frozen split named but the plugin no longer knows is a ConfigError
at the D1 boundary, before any execution starts.

The failure set ``F`` is a pure function of the weak arm's metrics and the
frozen rule, so a rerun of this stage is a pure re-aggregation over stored
trajectories: K1 resumes (already-stored units are skipped, no model calls),
and the metrics/F/shortfall columns are recomputed from the same store.

Auxiliary metric. When the benchmark plugin declares one (stage 0 froze its
name into the manifest as ``aux_metric``), both arms additionally report it
under D3's ``aux`` section -- same expected-unit denominator, same two
avg@N disciplines as the primary, never swapped in or picked between after
seeing results (README stage 1). Without a declaration the section is null
and no scorer ever runs. The scorer is plugin code and the trajectory meta
it reads is unit-level evidence, so a rerun re-derives the aux numbers from
the same stored trajectories.

Infra holes. K1 never stores an ``infra_error`` trajectory, so a unit whose
infra retries ran out is simply ABSENT from what K1 returns. D3 is therefore
given each arm's full expected unit set (prefixed task id -> frozen
n_attempts): every absent unit counts as one infra attempt, so a task whose
every attempt exhausted infra stays in every all-task denominator instead of
vanishing. ``infra_shortfalls`` lists the tasks whose weak valid sample came
up short (README stage 1: they are listed separately and stay out of F).

Holes never pass silently. After both arms ran, the summary records
``infra_missing`` (the absent unit ids of both arms), ``complete`` and
``infra_shortfall_accepted``, is written, and then -- if any unit is
missing -- the stage raises InfrastructureError, the same disposition stages
2-4 use: re-running the same command resumes and retries only the holes. The
run setting ``allow_infra_shortfall`` (read from the context, default False)
is the escape hatch for permanently broken tasks: the stage proceeds with the
holes counted as infra and the summary says ``infra_shortfall_accepted:
true``.

Accepting a shortfall changes what is measured (F is derived from the
sample with holes, and stages 2-5 build the bank from that F), so it is a
DURABLE per-run decision, not a per-invocation flag: the accepted unit ids
are written to ``stage1_baseline/accepted_infra_shortfall.json`` before F is
derived, and every later call -- with or without the flag -- passes them to
K1 as ``skip_unit_ids`` (never re-executed) and scores them as holes even if
a trajectory for one appears later. Otherwise a resume without the flag
would fill the holes, move F, and leave the run directory with a bank that
no longer matches its acceptance results. Only holes outside the accepted
set take the InfrastructureError path. Stage 6 reads the same file: in full
mode its control inherits these holes.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Callable, Collection, Dict, List, Optional, Sequence, Tuple

from expedistill.blocks import metrics as metrics_block
from expedistill.blocks import tasks as tasks_block
from expedistill.blocks.rules import get_task_select
from expedistill.benchmarks.registry import get_plugin
from expedistill.composites.batch_execute import batch_execute
from expedistill.core.ids import make_task_id, make_trajectory_id
from expedistill.core.models import Trajectory
from expedistill.infra.reliability import (
    CodeBugError,
    ConfigError,
    InfrastructureError,
)
from expedistill.infra.store import read_json, write_json
from expedistill.stages.context import StageContext

__all__ = [
    "accepted_infra_shortfall",
    "accepted_shortfall_path",
    "aux_wiring",
    "run",
    "write_accepted_infra_shortfall",
]

_SUMMARY_NAME = "stage1_summary.json"

#: The durable accepted-hole record (module docstring), beside B3's
#: ``stage1_baseline/trajectories/``.
ACCEPTED_SHORTFALL_PATH = ("stage1_baseline", "accepted_infra_shortfall.json")


def accepted_shortfall_path(run_dir: Path) -> Path:
    """Where stage 1's accepted infra holes are recorded."""
    return Path(run_dir).joinpath(*ACCEPTED_SHORTFALL_PATH)


def accepted_infra_shortfall(run_dir: Path) -> List[str]:
    """Stage 1's accepted hole unit ids (sorted; empty when none)."""
    path = accepted_shortfall_path(run_dir)
    if not path.exists():
        return []
    try:
        unit_ids = read_json(path)["unit_ids"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CodeBugError(
            f"unreadable accepted-shortfall record {path}: {exc}") from None
    if not isinstance(unit_ids, list) or not all(
            isinstance(unit_id, str) for unit_id in unit_ids):
        raise CodeBugError(
            f"invalid accepted-shortfall record {path}: {unit_ids!r}")
    return sorted(unit_ids)


def write_accepted_infra_shortfall(run_dir: Path,
                                   unit_ids: Collection[str]) -> None:
    """Persist the accepted hole set (atomic write, sorted)."""
    write_json(accepted_shortfall_path(run_dir),
               {"unit_ids": sorted(set(unit_ids))}, indent=2)


def _workers(ctx: StageContext) -> int:
    """Parallelism from the config/context if either carries it, else serial.

    Neither ExperimentConfig nor StageContext has a ``workers`` field today;
    the lookup stays so a later addition needs no stage change.
    """
    workers = getattr(ctx.config, "workers", None)
    if workers is None:
        workers = getattr(ctx, "workers", 1)
    return int(workers)


def _infra_breaker(ctx: Any) -> int:
    """K1's circuit-breaker run setting from the context (the pipeline sets
    it), else K1's own default."""
    return int(getattr(ctx, "infra_breaker", 5))


def _allow_infra_shortfall(ctx: StageContext) -> bool:
    """The run setting accepting infra holes (module docstring); the
    pipeline sets it on the context, default False."""
    return bool(getattr(ctx, "allow_infra_shortfall", False))


def _train_task_objects(ctx: StageContext,
                        train_ids: Sequence[str]) -> Sequence[Any]:
    """Rebuild the train set's Task objects from the frozen id list (D1).

    The plugin resolves ids deterministically; a listed id it does not know
    is a ConfigError from the plugin itself, and a task that comes back
    under an unexpected id is checked here so the arms can never silently
    run a different set than the frozen split named.
    """
    tasks = tasks_block.load_tasks(ctx.config.benchmark, sorted(train_ids))
    unexpected = sorted({task.task_id for task in tasks} - set(train_ids))
    if unexpected:
        raise ConfigError(
            f"benchmark {ctx.config.benchmark!r} loaded tasks outside the "
            f"requested train set: {unexpected}")
    return tasks


def _run_arm(ctx: StageContext, *, tasks: Sequence[Any], role: str,
             n_attempts: int, accepted: Collection[str],
             ) -> Tuple[List[Any], Dict[str, int], List[str]]:
    """One K1 arm over its full unit set, accepted holes skipped.

    Returns ``(trajectories, expected, missing)``: the arm's trajectories
    minus any accepted hole (a durable hole stays a hole), the expected unit
    set for D3, and the absent unit ids (accepted ones included).
    """
    accepted = frozenset(accepted)
    trajectories = batch_execute(
        store=ctx.store,
        benchmark=ctx.config.benchmark,
        tasks=tasks,
        role=role,
        model_name=ctx.config.models[f"executor_{role}"],
        handle=ctx.handle(f"executor_{role}"),
        n_attempts=n_attempts,
        workdir_root=ctx.workdir_root / "stage1",
        usage=ctx.usage,
        run_dir=ctx.run_dir,
        workers=_workers(ctx),
        infra_breaker=_infra_breaker(ctx),
        skip_unit_ids=accepted,
    )
    trajectories = [traj for traj in trajectories
                    if traj.trajectory_id not in accepted]
    expected = {make_task_id(ctx.config.benchmark, task.task_id): n_attempts
                for task in tasks}
    stored = {traj.trajectory_id for traj in trajectories}
    missing = [make_trajectory_id(task_id, role, attempt)
               for task_id in sorted(expected)
               for attempt in range(n_attempts)
               if make_trajectory_id(task_id, role, attempt) not in stored]
    return trajectories, expected, missing


def aux_wiring(
    benchmark: str,
) -> Tuple[Optional[str], Optional[Callable[[Trajectory], bool]]]:
    """(aux metric name, strict scorer) the benchmark's plugin declares.

    Imports the plugin package first (registration at import, D1's
    discipline), so a pure-resume re-aggregation that executes nothing
    still resolves the plugin. A plugin that declares no auxiliary metric
    yields ``(None, None)`` -- the summary's ``aux`` is null and no scorer
    ever runs. A plugin that declares a name must answer True / False for
    every valid trajectory; the default method's None leaking through a
    declared name is a plugin contract violation and raises CodeBugError
    here (never silently a zero). Stage 6 reuses this wiring so both
    scoring stages read the auxiliary metric the same way.
    """
    importlib.import_module("expedistill.benchmarks." + benchmark)
    plugin = get_plugin(benchmark)
    name = plugin.aux_metric_name
    if name is None:
        return None, None

    def scorer(traj: Trajectory) -> bool:
        value = plugin.aux_success(traj)
        if not isinstance(value, bool):
            raise CodeBugError(
                f"benchmark {benchmark!r} declares auxiliary metric "
                f"{name!r} but aux_success answered {value!r} for "
                f"{traj.trajectory_id}; a declared scorer must answer "
                "True/False for every valid trajectory")
        return value

    return name, scorer


def _arm_summary(trajectories: List[Any], expected: Dict[str, int],
                 n_attempts: int,
                 aux: Optional[Tuple[Optional[str],
                                    Optional[Callable[[Trajectory], bool]]]],
                 ) -> Dict[str, Any]:
    """D3 over the arm's full unit set (absent units counted as infra).

    ``aux`` is ``aux_wiring``'s (name, scorer) pair; the name is None for
    benchmarks without an auxiliary metric, and then the scorer is never
    passed on (no extra work happens).
    """
    aux_metric, aux_scorer = aux if aux is not None else (None, None)
    task_metrics = metrics_block.compute_task_metrics(
        trajectories, expected=expected, aux_scorer=aux_scorer)
    return {
        "metrics": task_metrics,
        "summary": metrics_block.summarize(
            task_metrics, n_attempts, aux_metric=aux_metric),
    }


def run(ctx: StageContext, *, train_tasks: Sequence[str],
        test_tasks: Sequence[str]) -> Dict[str, Any]:
    """Run both bare arms on the train set and derive the failure set ``F``.

    ``train_tasks`` / ``test_tasks`` are task-ID lists (stage 0's frozen
    ``"train"`` / ``"test"``); the Task objects are rebuilt via D1. Returns
    and writes ``stage1_summary.json`` (JSON-safe) with ``strong`` / ``weak``
    (each ``{"metrics": per-task counts, "summary": avg@N / pass@N}``),
    ``F`` (sorted prefixed task ids), ``infra_shortfalls`` (sorted
    prefixed task ids of tasks whose weak valid sample fell short),
    ``infra_missing`` (sorted absent unit ids of both arms),
    ``infra_accepted`` (the durable accepted holes), ``complete`` and
    ``infra_shortfall_accepted``. Raises InfrastructureError after writing
    the summary when a unit outside the accepted set is missing and the
    shortfall is not accepted now (module docstring).
    """
    config = ctx.config
    tasks = _train_task_objects(ctx, train_tasks)
    accepted = set(accepted_infra_shortfall(ctx.run_dir))
    strong_trajs, strong_expected, strong_missing = _run_arm(
        ctx, tasks=tasks, role="strong",
        n_attempts=config.n_attempts_strong, accepted=accepted)
    weak_trajs, weak_expected, weak_missing = _run_arm(
        ctx, tasks=tasks, role="weak",
        n_attempts=config.n_attempts_weak, accepted=accepted)

    missing = sorted(strong_missing + weak_missing)
    new_holes = [unit_id for unit_id in missing if unit_id not in accepted]
    if new_holes and _allow_infra_shortfall(ctx):
        # Durable from here on, and recorded BEFORE F is derived from it.
        accepted.update(new_holes)
        write_accepted_infra_shortfall(ctx.run_dir, accepted)
        new_holes = []

    aux = aux_wiring(config.benchmark)
    strong = _arm_summary(strong_trajs, strong_expected,
                          config.n_attempts_strong, aux)
    weak = _arm_summary(weak_trajs, weak_expected, config.n_attempts_weak,
                        aux)

    # config.rules is complete by construction (core.config DEFAULT_RULES)
    # and stage 0 already resolved the name, so this lookup cannot miss.
    rule = get_task_select(config.rules["task_select"])
    failed = metrics_block.failed_tasks(
        weak["metrics"], rule, n_attempts=config.n_attempts_weak)

    # The metrics cover the whole expected unit set, so every train task
    # has an entry (absent units counted as infra).
    shortfalls = [
        task_id for task_id, per_task in weak["metrics"].items()
        if per_task["valid"] < config.n_attempts_weak
    ]

    result = {
        "strong": strong,
        "weak": weak,
        "F": failed,
        "infra_shortfalls": sorted(shortfalls),
        "infra_missing": missing,
        "infra_accepted": sorted(accepted),
        "complete": not missing,
        "infra_shortfall_accepted": bool(accepted),
    }
    write_json(ctx.run_dir / _SUMMARY_NAME, result, indent=2)
    if new_holes:
        raise InfrastructureError(
            f"stage 1: {len(new_holes)} unit(s) (executions) exhausted their "
            f"infra retries and stored nothing (listed in {_SUMMARY_NAME} "
            "under infra_missing); resume the run to retry them, or accept "
            "the shortfall with --allow-infra-shortfall")
    return result
