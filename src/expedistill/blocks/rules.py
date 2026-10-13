"""Named rules for D4 task selection, D6 segment selection and trajectory
selection (layer 4).

README 1.1 rule 7: replaceable behaviour is selected by *name* -- the config
names a rule, the manifest freezes the name, and changing a definition means
registering a NEW name; a registered rule's behaviour never changes under it.
The three registries live at module level so a config's rule names resolve
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

Unknown names raise ConfigError (the experiment config named something that
does not exist), listing every registered rule in that namespace.
"""

from __future__ import annotations

from typing import Callable, List, Sequence

from expedistill.core.models import Subtask, Trajectory
from expedistill.core.registry import make_registry
from expedistill.infra.reliability import ConfigError

__all__ = [
    "TASK_SELECT",
    "SEGMENT_SELECT",
    "TRAJECTORY_SELECT",
    "register_task_select",
    "get_task_select",
    "register_segment_select",
    "get_segment_select",
    "register_trajectory_select",
    "get_trajectory_select",
]

TASK_SELECT = make_registry("task_select rule")
SEGMENT_SELECT = make_registry("segment_select rule")
TRAJECTORY_SELECT = make_registry("trajectory_select rule")


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
    fine: take what exists. ``infra_error`` trajectories are never stored
    (K1), so "not the preferred status" covers them deterministically.
    """
    selected: List[Trajectory] = []
    # Materialize once: the two role passes must not re-consume the input
    # (a one-shot iterable would silently select nothing for the second role).
    available = list(trajectories)
    for role, prefer_success in (("strong", True), ("weak", False)):
        of_role = sorted(
            (trajectory for trajectory in available
             if trajectory.model_role == role),
            key=lambda trajectory: (trajectory.success != prefer_success,
                                    trajectory.trajectory_id),
        )
        selected.extend(of_role[:2])
    return selected
