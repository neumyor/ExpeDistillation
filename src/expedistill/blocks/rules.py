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
* trajectory_select: ``(trajectory: Trajectory) -> bool`` (the stage-2
  "which trajectories of a task to segment" question is still open; ``all``
  is the frozen default a config can rely on today).

Unknown names raise ConfigError (the experiment config named something that
does not exist), listing every registered rule in that namespace.
"""

from __future__ import annotations

from typing import Callable

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


@SEGMENT_SELECT.register("strong_all_weak_failed")
def _strong_all_weak_failed(subtask: Subtask) -> bool:
    """Stage-3 D6 rule (README D6).

    Strong-model segments are all kept (both their successes and failures are
    distillation material); weak-model segments are kept only when the
    segment's own goal was not reached (``success is False``) -- there is
    nothing to diagnose in a segment the weak model already handled.
    """
    return subtask.source == "strong" or subtask.success is False


@TRAJECTORY_SELECT.register("all")
def _all(trajectory: Trajectory) -> bool:
    """Stage-2 default: segment every trajectory of the task.

    The real policy (all samples vs. a fixed number per model) is still an
    open question in README stage 2; ``all`` is the frozen default, and any
    future policy registers under a new name.
    """
    return True
