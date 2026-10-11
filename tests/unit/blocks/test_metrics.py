"""D3/D4 metrics unit tests: infra separation and the avg@N / pass@N
definitions, on a hand-computed worked example.

The point of the worked example is the per-task-mean-then-task-mean
discipline: avg@N must average per-task rates (success / valid), which is NOT
the pooled success share -- a task with one valid trial weighs as much as a
task with three.
"""

from expedistill.blocks.metrics import (
    compute_task_metrics,
    failed_tasks,
    summarize,
)
from expedistill.blocks.rules import get_task_select
from expedistill.core.models import Trajectory


def _traj(task_id, attempt_index, status, benchmark="tb", model_role="weak"):
    return Trajectory(
        trajectory_id=f"{task_id}#{attempt_index}",
        task_id=task_id,
        benchmark=benchmark,
        model_role=model_role,
        model_name="test-model",
        attempt_index=attempt_index,
        status=status,
    )


def test_compute_task_metrics_counts_and_infra_separation():
    # a: one success, one failure; b: two failures; c: one infra only.
    trajectories = [
        _traj("a", 0, "success"),
        _traj("a", 1, "failure"),
        _traj("b", 0, "failure"),
        _traj("b", 1, "failure"),
        _traj("c", 0, "infra_error"),
    ]
    assert compute_task_metrics(trajectories) == {
        "a": {"attempts": 2, "valid": 2, "success": 1, "infra": 0},
        "b": {"attempts": 2, "valid": 2, "success": 0, "infra": 0},
        "c": {"attempts": 1, "valid": 0, "success": 0, "infra": 1},
    }


def test_compute_task_metrics_uses_raw_task_id_and_groups_loose_order():
    trajectories = [
        _traj("z-1", 0, "failure", benchmark="other"),
        _traj("a", 0, "success"),
        _traj("z-1", 1, "failure"),
    ]
    metrics = compute_task_metrics(trajectories)
    assert set(metrics) == {"z-1", "a"}
    assert metrics["z-1"] == {
        "attempts": 2, "valid": 2, "success": 0, "infra": 0}


def test_summarize_worked_example():
    # Hand-computed:
    #   a: 1 success / 2 valid -> rate 0.5
    #   b: 0 success / 3 valid -> rate 0.0
    #   c: 1 success / 1 valid (plus 1 infra) -> rate 1.0
    #   d: 0 valid (2 infra) -> no rate, not passed, on the infra list
    # avg@N = mean(0.5, 0.0, 1.0) over the three valid tasks = 0.5
    # pass@N = 2 of 4 tasks (a and c) = 0.5
    trajectories = [
        _traj("a", 0, "success"),
        _traj("a", 1, "failure"),
        _traj("b", 0, "failure"),
        _traj("b", 1, "failure"),
        _traj("b", 2, "failure"),
        _traj("c", 0, "success"),
        _traj("c", 1, "infra_error"),
        _traj("d", 0, "infra_error"),
        _traj("d", 1, "infra_error"),
    ]
    summary = summarize(compute_task_metrics(trajectories), n_attempts=3)
    assert summary["tasks"] == 4
    assert summary["avg_at_n"] == 0.5
    # Conservative discipline: infra-only tasks score 0 with all tasks in
    # the denominator (matches scripts/tb21/tb_stats.py).
    assert summary["avg_at_n_conservative"] < summary["avg_at_n"]
    assert summary["pass_at_n"] == 0.5
    assert summary["infra_tasks"] == ["c", "d"]


def test_avg_is_per_task_mean_not_pooled_share():
    # Pooled success share would be 1/4 = 0.25; the per-task mean of
    # (1/1, 0/3) is 0.5. The definitions (tb_stats.py) require 0.5.
    trajectories = [
        _traj("a", 0, "success"),
        _traj("b", 0, "failure"),
        _traj("b", 1, "failure"),
        _traj("b", 2, "failure"),
    ]
    summary = summarize(compute_task_metrics(trajectories), n_attempts=3)
    assert summary["avg_at_n"] == 0.5
    assert summary["pass_at_n"] == 0.5


def test_summarize_without_valid_trials():
    trajectories = [_traj("a", 0, "infra_error")]
    summary = summarize(compute_task_metrics(trajectories), n_attempts=1)
    assert summary["tasks"] == 1
    assert summary["avg_at_n"] is None
    assert summary["pass_at_n"] == 0.0
    assert summary["infra_tasks"] == ["a"]


def test_summarize_empty():
    summary = summarize({}, n_attempts=1)
    assert summary == {
        "tasks": 0,
        "avg_at_n": None,
        "avg_at_n_conservative": 0.0,
        "pass_at_n": 0.0,
        "infra_tasks": [],
    }


def test_failed_tasks_all_valid_failed_rule():
    rule = get_task_select("all_valid_failed")
    trajectories = [
        # f1: full valid sample, all failed -> in F.
        _traj("f1", 0, "failure"),
        _traj("f1", 1, "failure"),
        # f2: infra errors left it short of the sample -> NOT in F.
        _traj("f2", 0, "failure"),
        _traj("f2", 1, "infra_error"),
        # ok: one success disqualifies.
        _traj("ok", 0, "success"),
        _traj("ok", 1, "failure"),
    ]
    assert failed_tasks(
        compute_task_metrics(trajectories), rule, n_attempts=2) == ["f1"]


def test_failed_tasks_output_is_sorted():
    rule = get_task_select("all_valid_failed")
    trajectories = [
        _traj("z", 0, "failure"),
        _traj("a", 0, "failure"),
    ]
    assert failed_tasks(
        compute_task_metrics(trajectories), rule, n_attempts=1) == ["a", "z"]


# ---------------------------------------------------------------------------
# Expected unit set: K1 never stores an infra-exhausted unit, so a hole in
# the returned trajectories IS the infra error and must be counted as one.
# ---------------------------------------------------------------------------

def test_expected_units_without_trajectory_count_as_infra():
    # a: one stored success of 2 expected; b: nothing stored at all (every
    # attempt exhausted its infra retries, so K1 returned no trajectory).
    trajectories = [_traj("a", 0, "success")]
    metrics = compute_task_metrics(trajectories, expected={"a": 2, "b": 2})
    assert metrics == {
        "a": {"attempts": 2, "valid": 1, "success": 1, "infra": 1},
        "b": {"attempts": 2, "valid": 0, "success": 0, "infra": 2},
    }
    summary = summarize(metrics, n_attempts=2)
    # The vanished task stays in every denominator that counts all tasks.
    assert summary["tasks"] == 2
    assert summary["avg_at_n"] == 1.0
    assert summary["avg_at_n_conservative"] == 0.5
    assert summary["pass_at_n"] == 0.5
    assert summary["infra_tasks"] == ["a", "b"]


def test_expected_counts_stored_infra_trajectory_once():
    # A stored infra_error trajectory (another producer than K1) fills its
    # unit; only genuinely absent units are added on top.
    trajectories = [_traj("a", 0, "infra_error"), _traj("a", 1, "failure")]
    assert compute_task_metrics(trajectories, expected={"a": 3}) == {
        "a": {"attempts": 3, "valid": 1, "success": 0, "infra": 2}}


def test_expected_without_holes_matches_plain_counting():
    trajectories = [_traj("a", 0, "success"), _traj("a", 1, "failure")]
    assert compute_task_metrics(trajectories, expected={"a": 2}) == \
        compute_task_metrics(trajectories)


def test_trajectory_outside_expected_set_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="b"):
        compute_task_metrics([_traj("b", 0, "success")], expected={"a": 1})
