"""The named rules for D6 segment selection and trajectory selection
(layer 4).

Both selections are replaceable behaviour selected by *name* (README 1.1
rule 7): the config names a rule, the manifest freezes the name, and
changing a definition means registering a NEW name; a registered rule's
behaviour never changes under it. The registries live at module level so a
config's rule names resolve once against a single process-wide mapping.

Rule signatures (fixed per namespace; a new rule in a namespace must accept
exactly this shape):

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
    "SEGMENT_SELECT",
    "TRAJECTORY_SELECT",
    "get_segment_select",
    "get_trajectory_select",
]

SEGMENT_SELECT = make_registry("segment_select rule")
TRAJECTORY_SELECT = make_registry("trajectory_select rule")


def get_segment_select(name: str) -> Callable:
    """Return the segment-select rule registered under ``name``."""
    try:
        return SEGMENT_SELECT.get(name)
    except KeyError as exc:
        raise ConfigError(str(exc)) from None


def get_trajectory_select(name: str) -> Callable:
    """Return the trajectory-select rule registered under ``name``."""
    try:
        return TRAJECTORY_SELECT.get(name)
    except KeyError as exc:
        raise ConfigError(str(exc)) from None


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
    """D6 rule: keep a strong segment only when the segment itself
    succeeded (procedure material), a weak segment only when it failed
    (lesson material)."""
    if subtask.source == "strong":
        return subtask.success is True
    return subtask.success is False


@SEGMENT_SELECT.register("all")
def _all_segments(subtask: Subtask) -> bool:
    """Coverage rule: keep every segment, whatever its role or verdict.

    The coverage ablation's material switch (2026-11-04): with
    ``trajectory_select: all`` it feeds the writer every segment of every
    trajectory instead of only strong successes and weak failures. The
    material's mode follows the segment's own verdict (``collect_material``
    marks a succeeded segment distill, a failed one diagnose), so a kept
    segment never contradicts its mode. The default stays
    ``strong_success_weak_failed``; this rule is chosen per experiment
    through ``rules.segment_select``.
    """
    return True


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
    successes: on a mixed task (some weak successes, some weak failures)
    the weak quota spends itself on the failures before the successes. Fewer available than the quota is
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
