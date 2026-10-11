"""D6 select_subtasks tests: the named rule is applied and the input order is
preserved (segment order mirrors the trajectory's step order and callers rely
on it)."""

import pytest

from expedistill.blocks.rules import register_segment_select
from expedistill.blocks.select import select_subtasks
from expedistill.core.models import Subtask
from expedistill.infra.reliability import ConfigError


def _subtask(subtask_id, source, success):
    return Subtask(
        subtask_id=subtask_id,
        task_id="t",
        trajectory_id="t#0",
        source=source,
        trajectory_success=False,
        segment_index=int(subtask_id.rsplit("seg", 1)[1]),
        step_range=(0, 2),
        label="l",
        description="d",
        success=success,
    )


def test_select_preserves_input_order():
    subtasks = [
        _subtask("t/seg0", "weak", True),    # dropped: weak and succeeded
        _subtask("t/seg1", "strong", True),  # kept
        _subtask("t/seg2", "weak", False),   # kept: weak, goal missed
        _subtask("t/seg3", "strong", False),  # kept
    ]
    kept = select_subtasks(subtasks, "strong_all_weak_failed")
    assert [s.subtask_id for s in kept] == ["t/seg1", "t/seg2", "t/seg3"]


def test_select_empty_input():
    assert select_subtasks([], "strong_all_weak_failed") == []


def test_select_unknown_rule_name():
    with pytest.raises(ConfigError) as exc:
        select_subtasks([], "keep_nothing_registered")
    assert "strong_all_weak_failed" in str(exc.value)


def test_select_applies_whichever_rule_the_name_resolves_to():
    # A newly registered name changes the outcome without touching the block:
    # named rules, not code branches.
    @register_segment_select("test_strong_only")
    def _strong_only(subtask):
        return subtask.source == "strong"

    subtasks = [
        _subtask("t/seg0", "weak", False),
        _subtask("t/seg1", "strong", False),
    ]
    kept = select_subtasks(subtasks, "test_strong_only")
    assert [s.subtask_id for s in kept] == ["t/seg1"]
