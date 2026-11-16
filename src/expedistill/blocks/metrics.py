"""D3 score statistics (layer 4).

Pure aggregation over per-task counts (``attempts`` / ``valid`` / ``success`` /
``infra``, built by ``composites/unit_summary.task_metrics``); no model, no
disk. The metric definitions follow the TB campaign's
``scripts/tb21/tb_stats.py`` so baseline and test numbers stay comparable with
the reference pipeline:

* ``avg@N`` -- per task, average over that task's SCORED trials first
  (success / valid), then average over tasks. Tasks whose trials were all
  unscored have no valid trial and contribute no rate; with no
  valid task at all the aggregate is ``None`` rather than a fake zero.
  (Deviation from tb_stats.py, which divides the rate sum by ALL tasks and
  thereby scores infra-only tasks as 0; the per-task-mean-then-task-mean over
  valid tasks is the definition this repo froze.)
* ``pass@N`` -- the share of tasks with at least one success among their
  valid trials; a task with no scored trial counts in the denominator but
  can never pass, matching tb_stats.py.
* the AUXILIARY metric -- an optional, plugin-declared second success
  notion (e.g. SearchQA's sub_EM substring match beside the strict-EM
  primary) reported ALWAYS alongside the primary under both avg@N
  disciplines, never swapped in or chosen between after seeing results.
  The metric stays benchmark-blind here: the caller supplies the per-task
  ``aux_success`` counts and the metric's frozen NAME; the plugin contract
  (``benchmarks/base.py``) maps trajectories to auxiliary success. An
  unscored/missing unit is never an auxiliary success, and the auxiliary
  averages use the same denominators as the primary.

An attempt without a scorer verdict is never a model failure:
``correctness=None`` (a run that came back with no scorer verdict) and an
absent unit (a run that never came back) both count as ``infra``, separately
from valid/success everywhere. The caller counts every expected unit, so a
task whose every attempt exhausted infra still appears in the denominators
instead of vanishing.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

__all__ = ["summarize"]


def summarize(
    task_metrics: Dict[str, Dict[str, int]],
    aux_metric: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate per-task metrics into avg@N / pass@N (definitions above).

    ``avg_at_n`` is None when no task has a valid trial.

    Two avg@N disciplines are reported side by side (pre-registered, never
    picked between after seeing results):
    - ``avg_at_n`` (conditional): mean over tasks with >=1 valid trial; tasks
      whose sampling collapsed entirely into infra errors are excluded.
    - ``avg_at_n_conservative`` (conservative): tb_stats.py's denominator —
      every task counts, a task with no valid trial contributes 0. Use this
      one when comparing against the TB21 campaign numbers; report both.

    ``aux_metric`` names the plugin-declared auxiliary metric whose per-task
    ``aux_success`` counts ``task_metrics`` carries; the summary then reports ``aux`` =
    ``{"metric": <name>, "avg_at_n": ..., "avg_at_n_conservative": ...}``
    under the SAME two disciplines as the primary. Without a name ``aux`` is
    None -- the field is always present, never silently dropped, and a name
    over counts without ``aux_success`` is a ValueError (the wiring forgot
    the counts, and a silent zero would fake a measured number).
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
                "auxiliary success counts")
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
