"""D3 score statistics and the D4 rule application (layer 4).

Pure counting over Trajectory objects; no model, no disk. The metric
definitions follow the TB campaign's ``scripts/tb21/tb_stats.py`` so stage-1
and stage-6 numbers stay comparable with the reference pipeline:

* ``avg@N`` -- per task, average over that task's VALID trials first
  (success / valid), then average over tasks. Tasks whose trials were all
  infrastructure errors have no valid trial and contribute no rate; with no
  valid task at all the aggregate is ``None`` rather than a fake zero.
  (Deviation from tb_stats.py, which divides the rate sum by ALL tasks and
  thereby scores infra-only tasks as 0; the per-task-mean-then-task-mean over
  valid tasks is the definition this repo froze.)
* ``pass@N`` -- the share of tasks with at least one success among their
  valid trials; an infra-only task counts in the denominator but can never
  pass, matching tb_stats.py.
* the AUXILIARY metric -- an optional, plugin-declared second success
  notion (e.g. SearchQA's sub_EM substring match beside the strict-EM
  primary) reported ALWAYS alongside the primary under both avg@N
  disciplines, never swapped in or chosen between after seeing results.
  The metric stays benchmark-blind here: the caller passes the scorer as a
  callable and the metric's frozen NAME; the plugin contract
  (``benchmarks/base.py``) maps trajectories to auxiliary success. An
  infra/missing unit is never an auxiliary success, and the auxiliary
  averages use the same denominators as the primary.

``infra_error`` is never a model failure (README 1.3, E1 row): it is counted
separately and excluded from valid/success everywhere.

The expected unit set: K1 never stores an infra-exhausted unit, so on real
data an infra error usually shows up as a MISSING trajectory, not as an
``infra_error`` one. Counting only what came back would let a task whose
every attempt exhausted infra vanish from all denominators (the conservative
avg@N would equal the conditional one, ``infra_tasks`` would always be empty,
and two arms could be compared over different task sets). Callers that know
which units they asked for pass ``expected`` (task_id -> n_attempts) and every
expected unit without a trajectory is counted as one infra attempt.

``failed_tasks`` applies a *named* task_select rule (``blocks/rules.py``) to
each task's metrics -- D4 is the application, the rule is the definition, so
redefining "failed" means registering a new named rule, not editing code here.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Set

from expedistill.core.models import Trajectory

__all__ = ["compute_task_metrics", "summarize", "failed_tasks"]

#: Statuses that count as a real attempt of the model (README 1.3: infra
#: errors are excluded from scoring, never read as "the model failed").
VALID_STATUSES = ("success", "failure")


def compute_task_metrics(
    trajectories: List[Trajectory],
    expected: Optional[Mapping[str, int]] = None,
    aux_scorer: Optional[Callable[[Trajectory], bool]] = None,
) -> Dict[str, Dict[str, int]]:
    """Count per-task attempts / valid / success / infra, keyed by raw task_id.

    Uses the task_id exactly as given on each Trajectory (benchmark-native,
    no prefixing). The four counts satisfy ``attempts == valid + infra``
    because Trajectory statuses are exactly success / failure / infra_error.

    ``expected`` (task_id -> n_attempts) is the unit set the caller asked
    for: every expected task gets an entry, and each of its attempts with no
    trajectory counts as ``infra`` (module docstring). A trajectory of a task
    outside ``expected`` is a ValueError -- the caller's unit set and its
    trajectories disagree, and silently widening the denominator would hide
    it. ``expected=None`` counts exactly the given trajectories.

    ``aux_scorer`` (plugin-declared auxiliary metric, module docstring)
    adds an ``aux_success`` count per task: valid trajectories the scorer
    accepts. An infra trajectory and an absent expected unit are never
    auxiliary successes. Without a scorer no key is added, so the per-task
    dicts keep their exact four-field shape.
    """
    if expected is not None:
        unexpected = sorted({traj.task_id for traj in trajectories}
                            - set(expected))
        if unexpected:
            raise ValueError(
                f"trajectories for tasks outside the expected unit set: "
                f"{unexpected}")
    metrics: Dict[str, Dict[str, int]] = {}

    def new_counts() -> Dict[str, int]:
        counts = {"attempts": 0, "valid": 0, "success": 0, "infra": 0}
        if aux_scorer is not None:
            counts["aux_success"] = 0
        return counts

    seen: Dict[str, Set[int]] = {}
    for traj in trajectories:
        seen.setdefault(traj.task_id, set()).add(traj.attempt_index)
        per_task = metrics.setdefault(traj.task_id, new_counts())
        per_task["attempts"] += 1
        if traj.status in VALID_STATUSES:
            per_task["valid"] += 1
            if aux_scorer is not None and aux_scorer(traj):
                per_task["aux_success"] += 1
        if traj.status == "success":
            per_task["success"] += 1
        if traj.status == "infra_error":
            per_task["infra"] += 1
    for task_id, n_attempts in (expected or {}).items():
        per_task = metrics.setdefault(task_id, new_counts())
        present = {i for i in seen.get(task_id, ()) if 0 <= i < n_attempts}
        missing = n_attempts - len(present)
        per_task["attempts"] += missing
        per_task["infra"] += missing
    return metrics


def summarize(
    task_metrics: Dict[str, Dict[str, int]],
    n_attempts: int,
    aux_metric: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate per-task metrics into avg@N / pass@N (definitions above).

    ``n_attempts`` is the frozen sampling depth of the run. It is not used by
    the two aggregate definitions themselves; it is part of the signature so
    callers pass the frozen value alongside the metrics and future aggregates
    that depend on it (e.g. "share of tasks with a full valid sample") need no
    signature change. ``avg_at_n`` is None when no task has a valid trial.

    Two avg@N disciplines are reported side by side (pre-registered, never
    picked between after seeing results):
    - ``avg_at_n`` (conditional): mean over tasks with >=1 valid trial; tasks
      whose sampling collapsed entirely into infra errors are excluded.
    - ``avg_at_n_conservative`` (conservative): tb_stats.py's denominator —
      every task counts, a task with no valid trial contributes 0. Use this
      one when comparing against the TB21 campaign numbers; report both.

    ``aux_metric`` names the plugin-declared auxiliary metric whose per-task
    ``aux_success`` counts ``task_metrics`` carries (pass the same plugin's
    scorer to ``compute_task_metrics``); the summary then reports ``aux`` =
    ``{"metric": <name>, "avg_at_n": ..., "avg_at_n_conservative": ...}``
    under the SAME two disciplines as the primary. Without a name ``aux`` is
    None -- the field is always present, never silently dropped, and a name
    over counts without ``aux_success`` is a ValueError (the wiring forgot
    the scorer, and a silent zero would fake a measured number).
    """
    n_tasks = len(task_metrics)
    rates = [
        per_task["success"] / per_task["valid"]
        for per_task in task_metrics.values()
        if per_task["valid"] > 0
    ]
    conservative = sum(rates) / n_tasks if n_tasks else 0.0
    passed = [
        task_id for task_id, per_task in task_metrics.items()
        if per_task["success"] >= 1
    ]
    aux: Optional[Dict[str, Any]] = None
    if aux_metric is not None:
        without_counts = sorted(
            task_id for task_id, per_task in task_metrics.items()
            if "aux_success" not in per_task)
        if without_counts:
            raise ValueError(
                f"summarize(aux_metric={aux_metric!r}) got task metrics "
                f"without aux_success counts for {without_counts}; pass the "
                "plugin's auxiliary scorer to compute_task_metrics")
        aux_rates = [
            per_task["aux_success"] / per_task["valid"]
            for per_task in task_metrics.values()
            if per_task["valid"] > 0
        ]
        aux = {
            "metric": aux_metric,
            "avg_at_n": (sum(aux_rates) / len(aux_rates)
                         if aux_rates else None),
            "avg_at_n_conservative": (sum(aux_rates) / n_tasks
                                      if n_tasks else 0.0),
        }
    return {
        "tasks": n_tasks,
        "avg_at_n": sum(rates) / len(rates) if rates else None,
        "avg_at_n_conservative": conservative,
        "pass_at_n": len(passed) / n_tasks if n_tasks else 0.0,
        "infra_tasks": sorted(
            task_id for task_id, per_task in task_metrics.items()
            if per_task["infra"] > 0),
        "aux": aux,
    }


def failed_tasks(
    task_metrics: Dict[str, Dict[str, int]],
    rule: Callable[[Dict[str, int], int], bool],
    n_attempts: int,
) -> List[str]:
    """Return the sorted task_ids for which ``rule(metrics, n_attempts)`` holds.

    D4: the caller resolves the named rule from ``blocks.rules`` (config names
    are identity there) and passes the callable here, so this block stays
    agnostic of which rule -- or which definition of "failed" -- is in effect.
    """
    return sorted(
        task_id for task_id, per_task in task_metrics.items()
        if rule(per_task, n_attempts))
