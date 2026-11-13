"""Named-rule registry tests: the two namespaces, their frozen
signatures, and the named-rule discipline (a new behaviour = a new name;
an existing name never changes and never registers twice).
"""

import pytest

import expedistill.blocks
from expedistill.blocks.rules import (
    SEGMENT_SELECT,
    TRAJECTORY_SELECT,
    get_segment_select,
    get_trajectory_select,
)
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


def test_segment_select_strong_all_weak_failed():
    rule = get_segment_select("strong_all_weak_failed")
    assert rule(_subtask("strong", True)) is True
    assert rule(_subtask("strong", False)) is True
    assert rule(_subtask("weak", False)) is True
    assert rule(_subtask("weak", True)) is False


def test_segment_select_strong_success_weak_failed_four_quadrants():
    # Protocol v2 default: strong segments are procedure material only
    # when they succeeded; weak segments are diagnosis material only when
    # they failed.
    rule = get_segment_select("strong_success_weak_failed")
    assert rule(_subtask("strong", True)) is True
    assert rule(_subtask("strong", False)) is False
    assert rule(_subtask("weak", False)) is True
    assert rule(_subtask("weak", True)) is False


def test_segment_select_all_keeps_every_quadrant():
    # The coverage switch (2026-11-04): role and segment verdict select
    # nothing; every segment of every trajectory is material.
    rule = get_segment_select("all")
    for source in ("strong", "weak"):
        for success in (True, False):
            assert rule(_subtask(source, success)) is True


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


def test_strong2_weak2_takes_two_per_role_with_correctness_preference():
    rule = get_trajectory_select("strong2_weak2")
    # 3 strong (a failure, then two successes) and 3 weak (a success, then
    # two failures): enough for every branch of both preferences.
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
    # Strong arm: successes first (stable by trajectory_id within the
    # bucket), so the failure at attempt 0 is dropped; weak arm: failures
    # first, so the success at attempt 0 is dropped.
    assert [t.trajectory_id for t in selected] == [
        _id("strong", 1), _id("strong", 2),
        _id("weak", 1), _id("weak", 2),
    ]


def test_strong2_weak2_clamps_to_what_exists():
    rule = get_trajectory_select("strong2_weak2")
    # One strong and three weak: 1 + 2.
    selected = rule([
        _trajectory(True, role="strong", attempt=0),
        _trajectory(False, role="weak", attempt=0),
        _trajectory(False, role="weak", attempt=1),
        _trajectory(False, role="weak", attempt=2),
    ])
    assert [t.trajectory_id for t in selected] == [
        _id("strong", 0), _id("weak", 0), _id("weak", 1)]
    # No strong trajectories at all: still the first two weak ones.
    only_weak = [_trajectory(False, role="weak", attempt=i)
                 for i in range(3)]
    assert rule(only_weak) == only_weak[:2]
    # A weak arm with no failures (impossible on F, but the rule must be
    # total): the quota falls back to the weak successes.
    weak_successes = [_trajectory(True, role="weak", attempt=i)
                      for i in range(3)]
    assert rule(weak_successes) == weak_successes[:2]
    # Nothing stored: nothing selected.
    assert rule([]) == []


def test_strong2_weak2_is_deterministic_under_input_order():
    rule = get_trajectory_select("strong2_weak2")
    trajectories = [
        _trajectory(False, role="weak", attempt=2),
        _trajectory(True, role="strong", attempt=0),
        _trajectory(False, role="strong", attempt=1),
        _trajectory(True, role="weak", attempt=1),
        _trajectory(True, role="strong", attempt=2),
        _trajectory(False, role="weak", attempt=0),
    ]
    first = rule(trajectories)
    assert rule(list(reversed(trajectories))) == first
    assert rule(trajectories) == first
    # A one-shot iterable is materialized once, not consumed by the first
    # role's pass (the second role would silently select nothing).
    assert rule(reversed(trajectories)) == first


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


def test_duplicate_registration_is_rejected():
    with pytest.raises(ValueError, match="new name"):
        SEGMENT_SELECT.register("all")(lambda subtask: True)


def test_registering_a_new_name_is_the_extension_path():
    # The named-rule discipline: a new behaviour arrives as a new name, and
    # the existing names are untouched.
    @SEGMENT_SELECT.register("test_strong_only")
    def _strong_only(subtask):
        return subtask.source == "strong"

    assert "test_strong_only" in SEGMENT_SELECT.names()
    assert get_segment_select("test_strong_only")(_subtask("weak", False)) \
        is False
    assert get_segment_select("all")(_subtask("weak", False)) is True


def test_registries_are_module_level_and_separate_namespaces():
    # The two registries are separate namespaces, not one shared table.
    assert "all" in TRAJECTORY_SELECT
    assert "all" in SEGMENT_SELECT
    assert "strong1_weak1" not in SEGMENT_SELECT


def test_block_exports_expose_rule_api():
    # The package-level re-exports let stages resolve config names without
    # importing submodules directly.
    for name in (
        "SEGMENT_SELECT", "TRAJECTORY_SELECT",
        "get_segment_select", "get_trajectory_select",
        "compute_task_metrics", "summarize",
        "select_subtasks", "load_tasks", "split_tasks",
    ):
        assert hasattr(expedistill.blocks, name), name
