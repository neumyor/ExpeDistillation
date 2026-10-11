"""Stage 6: test the weak model with the whole bank against its control arm.

README stage 6, pinned: the strong model is fully OUT (no handle of the
strong role is ever touched), the weak model reads the WHOLE bank, and the
control is -- in ``full`` mode -- stage 1's stored weak bare trajectories
(reused, never re-run), or -- in ``split`` mode -- a fresh weak bare run on
the test set. The improvement is with-bank minus control, from D3.

Storage layout (the stage's own call, AGENTS.md section 4 rule 3):

Trajectory ids are ``traj/<task>/<role>/<attempt>`` and carry no arm
information, so the with-bank arm, the bare arm, and stage 1's weak bare runs
would all map to the SAME ids inside one store: K1's resume check would then
silently "skip" the second arm and hand back the first arm's trajectories as
its own -- a zero-delta result that looks like "the bank does nothing". The
stage therefore gives each stage-6 arm its own Store rooted under
``stage6_test/`` (``bank/`` and ``bare/``; README section 2 already places
"test 轨迹与成绩" under ``stage6_test/``), keeping ctx.store as the stage-0..5
store. The arm stores repeat B3's fixed sub-layout (``stage1_baseline/`` ...)
because the layout is hard-coded in Store; that nesting is the price of not
touching layer 1, and the summary records each arm's directory so downstream
analysis never has to guess.

One denominator for both arms. K1 never stores an infra-exhausted unit, so
such a unit is ABSENT from what K1 returns; counting only returned
trajectories would let a task vanish from one arm's denominators and compare
arms over different task sets (a reviewer probe: with-bank tasks=1 vs
control tasks=2, delta -1.0, exit 0). D3 is therefore given the full expected
unit set (every test task x the frozen ``n_attempts_weak``) for EACH arm, so
absent units count as infra and both summaries cover the identical task set
(asserted).

Holes never pass silently. The summary records ``infra_missing`` per arm
(``{"with_bank": [...], "control": [...]}``: trajectory ids carry no arm, so
a flat list would be ambiguous), ``complete`` and
``infra_shortfall_accepted``; it is written, and then -- if any unit is
missing -- the stage raises InfrastructureError (the disposition of stages
1-4): re-running the same command resumes and retries only the holes. The
run setting ``allow_infra_shortfall`` (read from the context,
default False) accepts the holes instead: they stay counted as infra and the
summary says ``infra_shortfall_accepted: true``.

An accepted shortfall is DURABLE (as in stage 1): the accepted unit ids are
written per arm to ``stage6_test/accepted_infra_shortfall.json`` before the
summary, and every later call -- with or without the flag -- passes them to
K1 as ``skip_unit_ids`` and scores them as holes, so a resume never moves
the measured numbers. Only holes outside the accepted set take the
InfrastructureError path.

The full-mode control is loaded by deterministic id, one trajectory per
(task, attempt) unit of the frozen sampling depth. Its holes are stage-1
holes, so the accepted ones are exactly stage 1's durable accepted set
(``stage1_baseline.accepted_infra_shortfall``); stage 6 never accepts a
control hole on its own, because stage 1 would not know and a later resume
of stage 1 could fill it (moving F and the bank). Any other missing control
unit raises InfrastructureError BEFORE executing anything, flag or not:
re-running the same command resumes stage 1 first, which retries the hole
(or, under the flag, accepts it durably there), and filling the hole can
change F and hence the bank, so with-bank runs made now could be measured
against the wrong bank.
"""

from pathlib import Path
from typing import Any, Collection, Dict, List, Sequence

from expedistill.benchmarks.base import Task
from expedistill.blocks.metrics import compute_task_metrics, summarize
from expedistill.composites.batch_execute import batch_execute
from expedistill.core.ids import make_task_id, make_trajectory_id
from expedistill.core.models import Trajectory
from expedistill.infra.reliability import CodeBugError, InfrastructureError
from expedistill.infra.store import Store, read_json, write_json
from expedistill.inject import bank_reference, format_skills_block
from expedistill.stages import stage1_baseline

__all__ = ["run"]

#: Directory inside the run for everything stage 6 produces (README section 2).
TEST_DIR = "stage6_test"
SUMMARY_NAME = "stage6_summary.json"
#: The durable per-arm accepted-hole record (module docstring).
ACCEPTED_SHORTFALL_NAME = "accepted_infra_shortfall.json"
_ARMS = ("with_bank", "control")


def _accepted_path(ctx: Any) -> Path:
    return Path(ctx.run_dir) / TEST_DIR / ACCEPTED_SHORTFALL_NAME


def _read_accepted(ctx: Any) -> Dict[str, List[str]]:
    """Stage 6's own accepted holes per arm (empty lists when none)."""
    path = _accepted_path(ctx)
    if not path.exists():
        return {arm: [] for arm in _ARMS}
    try:
        record = read_json(path)
        accepted = {arm: sorted(record[arm]) for arm in _ARMS}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CodeBugError(
            f"unreadable accepted-shortfall record {path}: {exc}") from None
    if not all(isinstance(unit_id, str)
               for ids in accepted.values() for unit_id in ids):
        raise CodeBugError(f"invalid accepted-shortfall record {path}")
    return accepted


def _arm_store(ctx: Any, arm: str) -> Store:
    """A per-arm B3 store under ``stage6_test/`` (see module docstring)."""
    return Store(Path(ctx.run_dir) / TEST_DIR / arm)


def _workers(ctx: Any) -> int:
    """Parallelism from the config/context if either carries it, else serial
    (same lookup stage 1 uses)."""
    workers = getattr(ctx.config, "workers", None)
    if workers is None:
        workers = getattr(ctx, "workers", 1)
    return int(workers)


def _infra_breaker(ctx: Any) -> int:
    """K1's circuit-breaker run setting from the context (the pipeline sets
    it), else K1's own default."""
    return int(getattr(ctx, "infra_breaker", 5))


def _allow_infra_shortfall(ctx: Any) -> bool:
    """The run setting accepting infra holes (module docstring); the
    pipeline sets it on the context, default False."""
    return bool(getattr(ctx, "allow_infra_shortfall", False))


def _expected_units(ctx: Any, test_tasks: Sequence[Task]) -> Dict[str, int]:
    """The arm-independent expected unit set: prefixed task id -> frozen
    ``n_attempts_weak``."""
    return {make_task_id(ctx.config.benchmark, task.task_id):
            ctx.config.n_attempts_weak for task in test_tasks}


def _missing_units(expected: Dict[str, int],
                   trajectories: Sequence[Trajectory]) -> List[str]:
    """Expected unit ids with no trajectory (sorted)."""
    stored = {traj.trajectory_id for traj in trajectories}
    return [make_trajectory_id(task_id, "weak", attempt)
            for task_id in sorted(expected)
            for attempt in range(expected[task_id])
            if make_trajectory_id(task_id, "weak", attempt) not in stored]


def _bare_stage1_trajectories(ctx: Any, test_tasks: Sequence[Task],
                              accepted: Collection[str]) -> List[Trajectory]:
    """Stage 1's weak bare trajectories for the test tasks (full-mode control).

    Deterministic-id lookup over every (task, attempt) unit of the frozen
    ``n_attempts_weak``; stage 1's accepted holes are skipped (and stay
    holes), any other missing unit raises InfrastructureError, flag or not
    (module docstring).
    """
    store = ctx.store
    n_attempts = ctx.config.n_attempts_weak
    trajectories: List[Trajectory] = []
    missing = []
    for task in sorted(test_tasks, key=lambda t: t.task_id):
        task_id = make_task_id(ctx.config.benchmark, task.task_id)
        for attempt in range(n_attempts):
            trajectory_id = make_trajectory_id(task_id, "weak", attempt)
            if trajectory_id in accepted:
                continue
            if store.exists("trajectories", trajectory_id):
                trajectories.append(
                    store.load("trajectories", trajectory_id, Trajectory))
            else:
                missing.append(trajectory_id)
    if missing:
        raise InfrastructureError(
            "stage 6: full-mode control is incomplete: stage 1 weak bare "
            f"trajectories missing for {len(missing)} unit(s): {missing}; "
            "resume the run to retry them (stage 1 resumes first; a hole "
            "can only be accepted there, with --allow-infra-shortfall)")
    return trajectories


def _delta(with_bank: Dict[str, Any],
           control: Dict[str, Any]) -> Dict[str, Any]:
    """With-bank minus control for both avg@N disciplines and pass@N.

    D3 pre-registers two avg@N disciplines (conditional ``avg_at_n`` and the
    TB21 ``avg_at_n_conservative``); both deltas are reported side by side so
    nobody picks the friendlier one after seeing the numbers.

    ``avg_at_n`` is None when an arm has no valid trial at all; a delta
    between a number and nothing is not zero (that would claim "no
    improvement" from absent evidence), it is None -- recorded honestly
    rather than coerced.
    """
    with_avg = with_bank["avg_at_n"]
    control_avg = control["avg_at_n"]
    return {
        "avg_at_n": (with_avg - control_avg
                     if with_avg is not None and control_avg is not None
                     else None),
        # summarize() never yields None for these two (0.0 with no valid
        # trial / no tasks), so their deltas are always numbers.
        "avg_at_n_conservative": (with_bank["avg_at_n_conservative"]
                                  - control["avg_at_n_conservative"]),
        "pass_at_n": with_bank["pass_at_n"] - control["pass_at_n"],
    }


def run(ctx: Any, *, test_tasks: Sequence[Task]) -> Dict[str, Any]:
    """Run the with-bank and control arms and write the stage-6 summary.

    Returns ``{"mode", "bank_reference", "control_source", "with_bank",
    "control", "delta", "infra_missing", "infra_accepted", "complete",
    "infra_shortfall_accepted"}`` (the two infra lists per arm) where the arm entries are
    ``blocks.metrics`` D3 aggregates over the shared expected unit set (both
    avg@N disciplines / pass@N / infra task list) and ``delta`` is the
    with-bank minus control difference of each (the conditional avg@N None
    when either arm has no valid trial). Raises InfrastructureError after
    writing the summary when a unit outside the accepted set is missing and
    the shortfall is not accepted now. The strong model is never invoked: only the weak executor
    handle leaves ``ctx.handles``.
    """
    bank = ctx.store.bank()
    skills_block = format_skills_block(bank)
    reference = bank_reference(bank)
    model_name = ctx.config.models["executor_weak"]
    handle = ctx.handle("executor_weak")
    n_attempts = ctx.config.n_attempts_weak
    expected = _expected_units(ctx, test_tasks)
    accepted = {arm: set(ids) for arm, ids in _read_accepted(ctx).items()}
    if ctx.config.mode == "full":
        # The reused control's accepted holes are stage 1's, never stage 6's.
        expected_ids = {make_trajectory_id(task_id, "weak", attempt)
                        for task_id, n in expected.items()
                        for attempt in range(n)}
        accepted["control"] = expected_ids & set(
            stage1_baseline.accepted_infra_shortfall(ctx.run_dir))

    # Full mode: check the reused control first, so a stage-1 hole stops the
    # stage before any with-bank execution (module docstring).
    stage1_control = (
        _bare_stage1_trajectories(ctx, test_tasks, accepted["control"])
        if ctx.config.mode == "full" else None)

    # -- with-bank arm: the weak model reads the WHOLE bank -----------------
    bank_store = _arm_store(ctx, "bank")
    with_bank = batch_execute(
        store=bank_store,
        benchmark=ctx.config.benchmark,
        tasks=test_tasks,
        role="weak",
        model_name=model_name,
        handle=handle,
        n_attempts=n_attempts,
        skills_injected=reference,
        skills_block=skills_block,
        workdir_root=Path(ctx.workdir_root) / "stage6_bank",
        usage=ctx.usage,
        run_dir=ctx.run_dir,
        workers=_workers(ctx),
        infra_breaker=_infra_breaker(ctx),
        skip_unit_ids=frozenset(accepted["with_bank"]),
    )

    # -- control arm --------------------------------------------------------
    if ctx.config.mode == "full":
        # Stage 1 already measured the weak model bare on these tasks
        # (full mode distils on the whole task set); re-running it would be
        # a different sample, not a control.
        control = stage1_control
        control_source = "stage1_reuse"
    else:
        bare_store = _arm_store(ctx, "bare")
        control = batch_execute(
            store=bare_store,
            benchmark=ctx.config.benchmark,
            tasks=test_tasks,
            role="weak",
            model_name=model_name,
            handle=handle,
            n_attempts=n_attempts,
            skills_injected=None,
            skills_block="",
            workdir_root=Path(ctx.workdir_root) / "stage6_bare",
            usage=ctx.usage,
            run_dir=ctx.run_dir,
            workers=_workers(ctx),
            infra_breaker=_infra_breaker(ctx),
            skip_unit_ids=frozenset(accepted["control"]),
        )
        control_source = "fresh_bare_run"

    # A durable hole stays a hole even if a trajectory for it appears.
    with_bank = [traj for traj in with_bank
                 if traj.trajectory_id not in accepted["with_bank"]]
    control = [traj for traj in control
               if traj.trajectory_id not in accepted["control"]]
    missing = {"with_bank": _missing_units(expected, with_bank),
               "control": _missing_units(expected, control)}
    new_holes = {arm: [unit_id for unit_id in missing[arm]
                       if unit_id not in accepted[arm]] for arm in _ARMS}
    if any(new_holes.values()) and _allow_infra_shortfall(ctx):
        # Durable from here on, recorded BEFORE the summary. Full-mode
        # control holes never get here (the pre-check raised).
        for arm in _ARMS:
            accepted[arm].update(new_holes[arm])
        own = dict(accepted)
        if ctx.config.mode == "full":
            own["control"] = set()  # stage 1's record owns those
        write_json(_accepted_path(ctx),
                   {arm: sorted(own[arm]) for arm in _ARMS}, indent=2)
        new_holes = {arm: [] for arm in _ARMS}

    with_bank_metrics = compute_task_metrics(with_bank, expected=expected)
    control_metrics = compute_task_metrics(control, expected=expected)
    if set(with_bank_metrics) != set(control_metrics):
        # Unreachable while both share ``expected``; a delta over different
        # task sets is exactly the bug this guards against.
        raise CodeBugError(
            "stage 6 arms cover different task sets: with-bank "
            f"{sorted(with_bank_metrics)} vs control {sorted(control_metrics)}")
    with_bank_summary = summarize(with_bank_metrics, n_attempts)
    control_summary = summarize(control_metrics, n_attempts)
    n_missing = sum(len(ids) for ids in missing.values())
    n_new = sum(len(ids) for ids in new_holes.values())
    summary = {
        "mode": ctx.config.mode,
        "bank_reference": reference,
        "control_source": control_source,
        "with_bank": with_bank_summary,
        "control": control_summary,
        "delta": _delta(with_bank_summary, control_summary),
        "infra_missing": missing,
        "infra_accepted": {arm: sorted(accepted[arm]) for arm in _ARMS},
        "complete": not n_missing,
        "infra_shortfall_accepted": any(accepted.values()),
    }
    write_json(Path(ctx.run_dir) / TEST_DIR / SUMMARY_NAME, summary, indent=2)
    if n_new:
        raise InfrastructureError(
            f"stage 6: {n_new} unit(s) exhausted their infra retries "
            f"and stored nothing (listed in {TEST_DIR}/{SUMMARY_NAME} under "
            "infra_missing); resume the run to retry them, or accept the "
            "shortfall with --allow-infra-shortfall")
    return summary
