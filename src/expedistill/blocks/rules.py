"""Named rules for D4 task selection, D6 segment selection, trajectory
selection and stage-4 acceptance improvement (layer 4).

README 1.1 rule 7: replaceable behaviour is selected by *name* -- the config
names a rule, the manifest freezes the name, and changing a definition means
registering a NEW name; a registered rule's behaviour never changes under it.
The registries live at module level so a config's rule names resolve
once against a single process-wide mapping.

Rule signatures (fixed per namespace; a new rule in a namespace must accept
exactly this shape):

* task_select: ``(metrics: dict, n_attempts: int) -> bool`` where ``metrics``
  is one task's ``compute_task_metrics`` entry and ``n_attempts`` the frozen
  sampling depth;
* segment_select: ``(subtask: Subtask) -> bool``;
* trajectory_select: ``(trajectories: Sequence[Trajectory]) ->
  List[Trajectory]`` -- a PER-TASK selector: the rule receives every stored
  NON-EMPTY trajectory of ONE task (both roles mixed) and returns the
  sublist to segment (stage 2, README stage 2). Per task, because the policy
  question is "which of this task's samples to segment", which needs the
  whole task's set (e.g. quotas per model role); a per-trajectory predicate
  cannot express it. 0-step trajectories never reach a rule (stage 2
  excludes them first: segmenting nothing is undefined input for G1/D7), so
  a rule sees only informative samples. A rule must be deterministic and
  must not mutate its input; the stage fixes the processing order, not the
  rule.
* acceptance improve: ``(baseline: Mapping[str, int], live:
  Mapping[str, int], n_attempts: int) -> bool`` -- stage-4 live_run's verdict
  rule: whether the live arm (the task's skill group injected, really
  executed n times) beats the stage-1 weak-arm bare baseline on ONE task.
  Both mappings are that task's ``compute_task_metrics`` counts
  (``{attempts, valid, success, infra}``; a benchmark with an auxiliary
  metric adds ``aux_success`` -- a rule reads only the keys it needs, so
  there is no strict key-set check to break), and ``n_attempts`` is the
  frozen live sampling depth, passed for rules that normalise by it (the
  two registered rules do not). The improvement rule is pre-registered
  like every named rule: the config names it, the manifest freezes it, and
  swapping the criterion is a new experiment under a new name.

Unknown names raise ConfigError (the experiment config named something that
does not exist), listing every registered rule in that namespace.
"""

from __future__ import annotations

from typing import Callable, List, Mapping, Sequence

from expedistill.core.models import Subtask, Trajectory
from expedistill.core.registry import make_registry
from expedistill.infra.reliability import ConfigError

__all__ = [
    "TASK_SELECT",
    "SEGMENT_SELECT",
    "TRAJECTORY_SELECT",
    "ACCEPTANCE_IMPROVE",
    "register_task_select",
    "get_task_select",
    "register_segment_select",
    "get_segment_select",
    "register_trajectory_select",
    "get_trajectory_select",
    "register_acceptance_improve",
    "get_acceptance_improve",
]

TASK_SELECT = make_registry("task_select rule")
SEGMENT_SELECT = make_registry("segment_select rule")
TRAJECTORY_SELECT = make_registry("trajectory_select rule")
ACCEPTANCE_IMPROVE = make_registry("acceptance improve rule")


def register_task_select(name: str) -> Callable[[Callable], Callable]:
    """Decorator filing a task-select rule under ``name``; duplicates raise."""
    return TASK_SELECT.register(name)


def get_task_select(name: str) -> Callable:
    """Return the task-select rule registered under ``name``."""
    try:
        return TASK_SELECT.get(name)
    except KeyError as exc:
        # The listing is already in the registry's KeyError message.
        raise ConfigError(str(exc)) from None


def register_segment_select(name: str) -> Callable[[Callable], Callable]:
    """Decorator filing a segment-select rule under ``name``; duplicates raise."""
    return SEGMENT_SELECT.register(name)


def get_segment_select(name: str) -> Callable:
    """Return the segment-select rule registered under ``name``."""
    try:
        return SEGMENT_SELECT.get(name)
    except KeyError as exc:
        raise ConfigError(str(exc)) from None


def register_trajectory_select(
    name: str,
) -> Callable[[Callable], Callable]:
    """Decorator filing a trajectory-select rule; duplicates raise."""
    return TRAJECTORY_SELECT.register(name)


def get_trajectory_select(name: str) -> Callable:
    """Return the trajectory-select rule registered under ``name``."""
    try:
        return TRAJECTORY_SELECT.get(name)
    except KeyError as exc:
        raise ConfigError(str(exc)) from None


def register_acceptance_improve(
    name: str,
) -> Callable[[Callable], Callable]:
    """Decorator filing an acceptance-improvement rule; duplicates raise."""
    return ACCEPTANCE_IMPROVE.register(name)


def get_acceptance_improve(name: str) -> Callable:
    """Return the acceptance-improvement rule registered under ``name``."""
    try:
        return ACCEPTANCE_IMPROVE.get(name)
    except KeyError as exc:
        # The listing is already in the registry's KeyError message.
        raise ConfigError(str(exc)) from None


@TASK_SELECT.register("all_valid_failed")
def _all_valid_failed(metrics: dict, n_attempts: int) -> bool:
    """Stage-1 F-set rule (README stage 1).

    True when the task reached the full valid sample and every valid attempt
    failed: ``valid > 0 and valid == n_attempts and success == 0``. A task
    whose retries still left it short of ``n_attempts`` valid attempts (its
    other attempts were infra errors) is NOT in F -- it belongs on the
    separate infra-deficient list, not among the tasks judged too hard for
    the weak model; and any success, however few, disqualifies the task.
    """
    return (
        metrics["valid"] > 0
        and metrics["valid"] == n_attempts
        and metrics["success"] == 0
    )


@TASK_SELECT.register("any_weak_failed")
def _any_weak_failed(metrics: dict, n_attempts: int) -> bool:
    """Stage-1 F-set rule, protocol v2: a task enters F when the weak model
    has at least one failed VALID attempt (success < valid). A task whose
    every valid attempt succeeded stays out (nothing to fix on it), and a
    task with no valid attempt at all stays out too (infra is not a model
    failure, README 1.3). A task with a partial sample (0 < valid <
    n_attempts) and a real failure DOES enter F -- and is still listed in
    infra_shortfalls; the two lists are no longer disjoint by construction."""
    return metrics["valid"] > 0 and metrics["success"] < metrics["valid"]


@SEGMENT_SELECT.register("strong_all_weak_failed")
def _strong_all_weak_failed(subtask: Subtask) -> bool:
    """Stage-3 D6 rule (README D6).

    Strong-model segments are all kept (both their successes and failures are
    distillation material); weak-model segments are kept only when the
    segment's own goal was not reached (``success is False``) -- there is
    nothing to diagnose in a segment the weak model already handled.
    """
    return subtask.source == "strong" or subtask.success is False


@SEGMENT_SELECT.register("strong_success_weak_failed")
def _strong_success_weak_failed(subtask: Subtask) -> bool:
    """Stage-3 D6 rule, protocol v2: keep a strong segment only when the
    segment itself succeeded (procedure material), a weak segment only when
    it failed (diagnosis material). The v1 rule strong_all_weak_failed stays
    registered but is INCOMPATIBLE with the v2 G2 prompts (a failed strong
    segment would hit write_skill's distill guard)."""
    if subtask.source == "strong":
        return subtask.success is True
    return subtask.success is False


@TRAJECTORY_SELECT.register("all")
def _all(trajectories: Sequence[Trajectory]) -> List[Trajectory]:
    """Stage-2 default: segment every trajectory of the task.

    The frozen default under the per-task contract (a frozen run that used
    ``all`` resumes identically); policies with quotas or preferences
    register under their own names.
    """
    return list(trajectories)


@TRAJECTORY_SELECT.register("strong2_weak2")
def _strong2_weak2(
    trajectories: Sequence[Trajectory],
) -> List[Trajectory]:
    """Stage-2 quota rule: up to 2 strong + up to 2 weak per task.

    Cost control for the full campaign (segmentation is a model call per
    trajectory) while preferring the material each arm actually distills
    from: the strong arm's successes are the success-distillation material,
    so strong trajectories are ordered successes first (stable within the
    bucket by trajectory_id), then failures; the weak arm's failures are the
    diagnosis material by design (README D6 keeps only failed weak segments
    anyway), so weak trajectories are ordered failures first, then
    successes. Under the v1 F rule every weak trajectory on an F task had
    failed, so that failure-first order never actually had to choose; under
    the protocol v2 rule ``any_weak_failed`` a mixed task (some weak
    successes, some weak failures) enters F too, and for the first time the
    failure-first ordering does real work -- the weak quota spends itself on
    the failures before the successes. Fewer available than the quota is
    fine: take what exists. An unscored trajectory (``correctness`` None,
    no scorer verdict) is never the preferred verdict, so it sorts last
    in both roles deterministically.
    """
    selected: List[Trajectory] = []
    # Materialize once: the two role passes must not re-consume the input
    # (a one-shot iterable would silently select nothing for the second role).
    available = list(trajectories)
    for role, prefer_success in (("strong", True), ("weak", False)):
        of_role = sorted(
            (trajectory for trajectory in available
             if trajectory.model_role == role),
            key=lambda trajectory: (
                trajectory.correctness is not prefer_success,
                trajectory.trajectory_id),
        )
        selected.extend(of_role[:2])
    return selected


@TRAJECTORY_SELECT.register("strong1_weak1")
def _strong1_weak1(
    trajectories: Sequence[Trajectory],
) -> List[Trajectory]:
    """Stage-2 quota rule: up to 1 strong + up to 1 weak per task.

    The one-per-role cost floor: the same correctness preference as
    ``strong2_weak2`` (strong successes first, weak failures first),
    with a quota of one per role. Fewer available than the quota is
    fine; an unscored trajectory sorts last.
    """
    selected: List[Trajectory] = []
    available = list(trajectories)
    for role, prefer_success in (("strong", True), ("weak", False)):
        of_role = sorted(
            (trajectory for trajectory in available
             if trajectory.model_role == role),
            key=lambda trajectory: (
                trajectory.correctness is not prefer_success,
                trajectory.trajectory_id),
        )
        selected.extend(of_role[:1])
    return selected


@ACCEPTANCE_IMPROVE.register("strict_rate_gain")
def _strict_rate_gain(
    baseline: Mapping[str, int],
    live: Mapping[str, int],
    n_attempts: int,
) -> bool:
    """Stage-4 live_run improvement rule (decision doc 2026-10-14, ruling
    A6): the live arm's success RATE strictly exceeds the baseline's.

    Compared by integer cross-multiplication, never a float division: exact
    in small integers, and the arms may have different valid denominators
    (an accepted infra shortfall shrinks the live side; the stage-1 baseline
    may itself be short). With equal depths counts and rates agree; where
    they differ the rate is the only unbiased reading -- live 3/5 vs
    baseline 2/3 has MORE successes but a WORSE rate, and "not better" is
    the right verdict. Strict on purpose: equality is not a gain.

    The zero branches are defense in depth, not a live_run design
    claim. The judge layer (composites/live_verdicts.judge_round)
    short-circuits a group with no valid measurement on either side --
    every expected unit an accepted hole, every stored run back without
    a scorer verdict (R2b), or a baseline whose trials never came back
    scored -- to live_undetermined before any rule runs. But the rule
    stays total: a direct caller handing it zero evidence gets "not
    better", never a crash.
    """
    if live["valid"] == 0:
        return False
    if baseline["valid"] == 0:
        return False
    return (live["success"] * baseline["valid"]
            > baseline["success"] * live["valid"])


@ACCEPTANCE_IMPROVE.register("first_success_gain")
def _first_success_gain(
    baseline: Mapping[str, int],
    live: Mapping[str, int],
    n_attempts: int,
) -> bool:
    """Stage-4 live_run improvement rule, the ablation alternative
    (decision doc 2026-10-14, ruling 1: registered, but the configs name
    strict_rate_gain): pass@N semantics -- the baseline never succeeded and
    the live arm succeeded at least once. Weaker than strict_rate_gain (it
    accepts one lucky success among n attempts), which is exactly what
    running the two rules against each other measures.
    """
    return live["success"] >= 1 and baseline["success"] == 0
