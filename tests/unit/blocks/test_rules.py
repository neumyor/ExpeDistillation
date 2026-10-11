"""Named-rule registry tests: the three namespaces, their frozen signatures,
and the named-rule discipline (a new behaviour = a new name; an existing name
never changes and never registers twice).
"""

import pytest

import expedistill.blocks
from expedistill.blocks.rules import (
    SEGMENT_SELECT,
    TASK_SELECT,
    TRAJECTORY_SELECT,
    get_segment_select,
    get_task_select,
    get_trajectory_select,
    register_task_select,
)
from expedistill.core.models import Subtask, Trajectory
from expedistill.infra.reliability import ConfigError


def _subtask(source, success):
    return Subtask(
        subtask_id="t/seg0",
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


def _trajectory(status):
    return Trajectory(
        trajectory_id="t#0",
        task_id="t",
        benchmark="tb",
        model_role="weak",
        model_name="test-model",
        attempt_index=0,
        status=status,
    )


def _metrics(valid, success, infra=0):
    return {"attempts": valid + infra, "valid": valid,
            "success": success, "infra": infra}


def test_task_select_all_valid_failed_truth_table():
    rule = get_task_select("all_valid_failed")
    assert rule(_metrics(3, 0), 3) is True  # full sample, all failed
    assert rule(_metrics(0, 0), 0) is False  # no valid trial at all
    # Infra-deficient sample: excluded from F (listed separately instead).
    assert rule(_metrics(2, 0, infra=1), 3) is False
    # More valid attempts than the frozen depth: not the defined situation.
    assert rule(_metrics(3, 0), 2) is False
    # Any success disqualifies.
    assert rule(_metrics(3, 1), 3) is False


def test_segment_select_strong_all_weak_failed():
    rule = get_segment_select("strong_all_weak_failed")
    assert rule(_subtask("strong", True)) is True
    assert rule(_subtask("strong", False)) is True
    assert rule(_subtask("weak", False)) is True
    assert rule(_subtask("weak", True)) is False


def test_trajectory_select_all_accepts_everything():
    rule = get_trajectory_select("all")
    assert rule(_trajectory("success")) is True
    assert rule(_trajectory("failure")) is True
    assert rule(_trajectory("infra_error")) is True


def test_unknown_names_raise_config_error_listing_known():
    with pytest.raises(ConfigError) as exc:
        get_task_select("success_rate_below")
    assert "all_valid_failed" in str(exc.value)
    with pytest.raises(ConfigError) as exc:
        get_segment_select("weak_all")
    assert "strong_all_weak_failed" in str(exc.value)
    with pytest.raises(ConfigError) as exc:
        get_trajectory_select("best_only")
    assert "all" in str(exc.value)


def test_duplicate_registration_is_rejected():
    with pytest.raises(ValueError, match="new name"):
        register_task_select("all_valid_failed")(_metrics_and_true)


def _metrics_and_true(metrics, n_attempts):
    return True


def test_registering_a_new_name_is_the_extension_path():
    # The named-rule discipline: a new behaviour arrives as a new name, and
    # the existing names are untouched.
    @register_task_select("test_rate_below_half")
    def _rate_below_half(metrics, n_attempts):
        return metrics["valid"] > 0 and metrics["success"] / metrics["valid"] < 0.5

    assert "test_rate_below_half" in TASK_SELECT.names()
    assert get_task_select("test_rate_below_half")(_metrics(2, 0), 2) is True
    assert get_task_select("test_rate_below_half")(_metrics(2, 1), 2) is False
    assert get_task_select("test_rate_below_half")(_metrics(2, 2), 2) is False
    # The original rule is unaffected.
    assert get_task_select("all_valid_failed")(_metrics(2, 1), 2) is False


def test_registries_are_module_level_and_disjoint_namespaces():
    assert "all_valid_failed" in TASK_SELECT
    assert "all" in TRAJECTORY_SELECT
    assert "all" not in TASK_SELECT and "all" not in SEGMENT_SELECT


def test_block_exports_expose_rule_api():
    # The package-level re-exports let stages resolve config names without
    # importing submodules directly.
    for name in (
        "TASK_SELECT", "SEGMENT_SELECT", "TRAJECTORY_SELECT",
        "get_task_select", "get_segment_select", "get_trajectory_select",
        "register_task_select", "register_segment_select",
        "register_trajectory_select",
        "compute_task_metrics", "summarize", "failed_tasks",
        "select_subtasks", "load_tasks", "split_tasks",
    ):
        assert hasattr(expedistill.blocks, name), name
