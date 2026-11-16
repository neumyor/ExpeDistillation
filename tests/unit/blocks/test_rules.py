"""Named-rule tests: the segment and trajectory selection rules the
configs use, and the unknown-name error."""

import pytest

from expedistill.blocks.rules import get_segment_select, get_trajectory_select
from expedistill.core.ids import make_task_id, make_trajectory_id
from expedistill.core.models import Subtask, Trajectory
from expedistill.infra.reliability import ConfigError


#: The phase the test trajectories and segments come from.
BASELINE = "p01:baseline"
LEARN = "p02:learn"


def _subtask(source, success):
    return Subtask(
        subtask_id="t/seg0",
        phase_label=LEARN,
        task_id="t",
        trajectory_id="t#0",
        source=source,
        trajectory_success=False,
        segment_index=0,
        step_range=(0, 2),
        label="l",
        description="d",
        success=success,
    )


def _id(role, attempt):
    return make_trajectory_id(BASELINE, make_task_id("tb", "t"), role,
                              attempt)


def _trajectory(correctness, role="weak", attempt=0):
    task_id = make_task_id("tb", "t")
    return Trajectory(
        trajectory_id=_id(role, attempt),
        task_id=task_id,
        benchmark="tb",
        model_role=role,
        model_name="test-model",
        attempt_index=attempt,
        correctness=correctness,
    )


# (source, success) quadrants of one segment, in the order the tables below
# list their expected selection.
_QUADRANTS = [("strong", True), ("strong", False),
              ("weak", False), ("weak", True)]


@pytest.mark.parametrize("name, expected", [
    ("strong_all_weak_failed", [True, True, True, False]),
    # Protocol v2 default: strong segments are procedure material only when
    # they succeeded; weak segments are diagnosis material only when they
    # failed.
    ("strong_success_weak_failed", [True, False, True, False]),
    # The coverage switch: role and segment verdict select nothing.
    ("all", [True, True, True, True]),
])
def test_segment_select_rules_over_the_four_quadrants(name, expected):
    rule = get_segment_select(name)
    assert [rule(_subtask(source, success))
            for source, success in _QUADRANTS] == expected


def test_trajectory_select_all_returns_the_whole_task():
    # The per-task contract: one call sees every stored trajectory of ONE
    # task (both roles mixed) and returns the sublist to segment.
    rule = get_trajectory_select("all")
    trajectories = [
        _trajectory(True, role="strong"),
        _trajectory(False, role="weak", attempt=0),
        _trajectory(None, role="weak", attempt=1),
    ]
    assert rule(trajectories) == trajectories
    assert rule([]) == []


def test_strong1_weak1_takes_one_per_role_with_correctness_preference():
    rule = get_trajectory_select("strong1_weak1")
    strong = [
        _trajectory(False, role="strong", attempt=0),
        _trajectory(True, role="strong", attempt=1),
        _trajectory(True, role="strong", attempt=2),
    ]
    weak = [
        _trajectory(True, role="weak", attempt=0),
        _trajectory(False, role="weak", attempt=1),
        _trajectory(False, role="weak", attempt=2),
    ]
    selected = rule(strong + weak)
    # Strong arm: the first success in id order; weak arm: the first
    # failure in id order.
    assert [t.trajectory_id for t in selected] == [
        _id("strong", 1), _id("weak", 1)]


def test_strong1_weak1_clamps_to_one_and_is_total():
    rule = get_trajectory_select("strong1_weak1")
    one_strong_three_weak = [
        _trajectory(True, role="strong", attempt=0),
        _trajectory(False, role="weak", attempt=0),
        _trajectory(False, role="weak", attempt=1),
        _trajectory(False, role="weak", attempt=2),
    ]
    assert [t.trajectory_id for t in rule(one_strong_three_weak)] == [
        _id("strong", 0), _id("weak", 0)]
    # Only weak trajectories: one weak, no strong.
    only_weak = [_trajectory(False, role="weak", attempt=i)
                 for i in range(3)]
    assert [t.trajectory_id for t in rule(only_weak)] == [
        _id("weak", 0)]
    # A weak arm with no failures (impossible on F, but the rule must be
    # total): the quota falls back to the first weak success.
    weak_successes = [_trajectory(True, role="weak", attempt=i)
                      for i in range(3)]
    assert [t.trajectory_id for t in rule(weak_successes)] == [
        _id("weak", 0)]
    assert rule([]) == []
    # A one-shot iterable is materialized once for both role passes.
    trajectories = [
        _trajectory(False, role="weak", attempt=2),
        _trajectory(True, role="strong", attempt=0),
        _trajectory(True, role="weak", attempt=1),
        _trajectory(False, role="strong", attempt=1),
    ]
    first = rule(trajectories)
    assert rule(reversed(trajectories)) == first


def test_unknown_names_raise_config_error_listing_known():
    with pytest.raises(ConfigError) as exc:
        get_segment_select("weak_all")
    assert "strong_all_weak_failed" in str(exc.value)
    with pytest.raises(ConfigError) as exc:
        get_trajectory_select("best_only")
    assert "all" in str(exc.value)
