"""D6 select_subtasks tests: the given rule is applied and the input order is
preserved (segment order mirrors the trajectory's step order and callers rely
on it)."""

from expedistill.blocks.rules import get_segment_select
from expedistill.blocks.select import select_subtasks
from expedistill.core.models import Subtask


def _subtask(subtask_id, source, success):
    return Subtask(
        subtask_id=subtask_id,
        phase_label="p02:learn",
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
    kept = select_subtasks(subtasks,
                           get_segment_select("strong_all_weak_failed"))
    assert [s.subtask_id for s in kept] == ["t/seg1", "t/seg2", "t/seg3"]


def test_select_empty_input():
    assert select_subtasks([], lambda subtask: True) == []


def test_select_applies_the_given_rule():
    subtasks = [
        _subtask("t/seg0", "weak", False),
        _subtask("t/seg1", "strong", False),
    ]
    kept = select_subtasks(subtasks,
                           lambda subtask: subtask.source == "strong")
    assert [s.subtask_id for s in kept] == ["t/seg1"]
