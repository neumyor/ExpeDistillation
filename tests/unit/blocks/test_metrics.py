"""D3 metrics unit tests: the avg@N / pass@N definitions on hand-computed
per-task counts.

The point of the worked example is the per-task-mean-then-task-mean
discipline: avg@N must average per-task rates (success / valid), which is NOT
the pooled success share -- a task with one valid trial weighs as much as a
task with three.
"""

import pytest

from expedistill.blocks.metrics import summarize


def _counts(attempts, valid, success, infra, aux_success=None):
    counts = {"attempts": attempts, "valid": valid, "success": success,
              "infra": infra}
    if aux_success is not None:
        counts["aux_success"] = aux_success
    return counts


def test_summarize_worked_example():
    # Hand-computed:
    #   a: 1 success / 2 valid -> rate 0.5
    #   b: 0 success / 3 valid -> rate 0.0
    #   c: 1 success / 1 valid (plus 1 infra) -> rate 1.0
    #   d: 0 valid (2 infra) -> no rate, not passed, on the infra list
    # avg@N = mean(0.5, 0.0, 1.0) over the three valid tasks = 0.5
    # pass@N = 2 of 4 tasks (a and c) = 0.5
    metrics = {
        "a": _counts(2, 2, 1, 0),
        "b": _counts(3, 3, 0, 0),
        "c": _counts(2, 1, 1, 1),
        "d": _counts(2, 0, 0, 2),
    }
    summary = summarize(metrics)
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
    metrics = {"a": _counts(1, 1, 1, 0), "b": _counts(3, 3, 0, 0)}
    summary = summarize(metrics)
    assert summary["avg_at_n"] == 0.5
    assert summary["pass_at_n"] == 0.5


def test_summarize_without_valid_trials():
    summary = summarize({"a": _counts(1, 0, 0, 1)})
    assert summary["tasks"] == 1
    assert summary["avg_at_n"] is None
    assert summary["pass_at_n"] == 0.0
    assert summary["infra_tasks"] == ["a"]


def test_summarize_empty():
    summary = summarize({})
    assert summary == {
        "tasks": 0,
        "avg_at_n": None,
        "avg_at_n_conservative": 0.0,
        "avg_at_n_invalid_as_failure": 0.0,
        "pass_at_n": 0.0,
        "infra_tasks": [],
        "aux": None,
    }


def test_a_vanished_task_stays_in_every_denominator():
    # a: one stored success of 2 expected; b: nothing stored at all (every
    # attempt exhausted its infra retries) and counted as infra.
    metrics = {"a": _counts(2, 1, 1, 1), "b": _counts(2, 0, 0, 2)}
    summary = summarize(metrics)
    assert summary["tasks"] == 2
    assert summary["avg_at_n"] == 1.0
    assert summary["avg_at_n_conservative"] == 0.5
    assert summary["pass_at_n"] == 0.5
    assert summary["infra_tasks"] == ["a", "b"]


# ---------------------------------------------------------------------------
# Auxiliary metric (pre-registered, plugin-declared; always reported beside
# the primary, never chosen between after seeing results)
# ---------------------------------------------------------------------------

def test_summarize_aux_both_avg_disciplines():
    # a: aux 1/2 valid -> 0.5; b: aux 0/1 -> 0.0; c: all infra -> no rate.
    # avg_at_n = mean(0.5, 0.0) = 0.25; conservative = (0.5 + 0.0 + 0.0) / 3.
    metrics = {
        "a": _counts(2, 2, 0, 0, aux_success=1),
        "b": _counts(1, 1, 0, 0, aux_success=0),
        "c": _counts(1, 0, 0, 1, aux_success=0),
    }
    summary = summarize(metrics, aux_metric="sub_em")
    assert summary["aux"]["metric"] == "sub_em"
    assert summary["aux"]["avg_at_n"] == 0.25
    assert summary["aux"]["avg_at_n_conservative"] == pytest.approx(1 / 6)


def test_summarize_aux_is_none_without_a_declared_metric():
    summary = summarize({"a": _counts(1, 1, 0, 0)})
    assert summary["aux"] is None


def test_summarize_aux_none_when_no_task_has_a_valid_trial():
    metrics = {"a": _counts(1, 0, 0, 1, aux_success=0)}
    summary = summarize(metrics, aux_metric="sub_em")
    assert summary["aux"] == {
        "metric": "sub_em", "avg_at_n": None,
        "avg_at_n_conservative": 0.0}


def test_summarize_aux_metric_without_aux_counts_is_rejected():
    with pytest.raises(ValueError, match="aux_success"):
        summarize({"a": _counts(1, 1, 0, 0)},
                  aux_metric="sub_em")


def test_invalid_as_failure_counts_every_planned_attempt():
    # t1: 2 of 3 valid, 1 success; t2: no valid trial at all.
    summary = summarize({
        "t1": {"attempts": 3, "valid": 2, "success": 1, "infra": 1},
        "t2": {"attempts": 3, "valid": 0, "success": 0, "infra": 3},
    })
    assert summary["avg_at_n"] == pytest.approx(0.5)
    assert summary["avg_at_n_conservative"] == pytest.approx(0.25)
    assert summary["avg_at_n_invalid_as_failure"] == pytest.approx(1 / 6)
