"""Unit tests for the deterministic ID rules."""
import pytest

from expedistill.core.ids import (
    make_attempt_id,
    make_exam_id,
    make_merged_skill_id,
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
    parse_attempt_id,
    parse_subtask_id,
    parse_trajectory_id,
    task_id_of,
)


def test_task_id():
    assert make_task_id("tb", "task-1") == "tb:task-1"


def test_trajectory_id_format():
    assert (make_trajectory_id("tb:task-1", "strong", 0)
            == "traj/tb:task-1/strong/00")
    assert (make_trajectory_id("tb:task-1", "weak", 7)
            == "traj/tb:task-1/weak/07")


def test_subtask_id_is_parent_plus_suffix():
    traj = "traj/tb:task-1/strong/00"
    assert make_subtask_id(traj, 0) == f"{traj}/seg00"
    assert make_subtask_id(traj, 12) == f"{traj}/seg12"


def test_skill_and_exam_ids_share_parent():
    seg = "traj/tb:task-1/strong/00/seg01"
    assert make_skill_id(seg) == f"{seg}/skill"
    assert make_exam_id(seg) == f"{seg}/exam"


def test_attempt_id_serial_is_two_digits():
    exam = "traj/tb:task-1/strong/00/seg01/exam"
    assert make_attempt_id(exam, 0) == f"{exam}/att00"
    assert make_attempt_id(exam, 13) == f"{exam}/att13"


def test_merged_skill_id():
    assert make_merged_skill_id(0) == "bank/merge/0000"
    assert make_merged_skill_id(42) == "bank/merge/0042"


@pytest.mark.parametrize("make,args", [
    (make_trajectory_id, ("tb:t", "strong", -1)),
    (make_trajectory_id, ("tb:t", "medium", 0)),
    (make_subtask_id, ("traj/x", -1)),
    (make_attempt_id, ("e", -1)),
    (make_merged_skill_id, (-1,)),
])
def test_negative_indices_raise(make, args):
    with pytest.raises(ValueError):
        make(*args)


def test_child_id_embeds_parent_id():
    task = make_task_id("tb", "task-1")
    traj = make_trajectory_id(task, "weak", 3)
    seg = make_subtask_id(traj, 2)
    skill = make_skill_id(seg)
    exam = make_exam_id(seg)
    att = make_attempt_id(exam, 0)
    # The trajectory id is the one child that wraps its parent in a prefix.
    assert traj.startswith(f"traj/{task}/")
    for parent, child in [(traj, seg), (seg, skill), (seg, exam),
                          (exam, att)]:
        assert child.startswith(parent + "/")


def test_parse_trajectory_id_round_trip():
    task_id = "tb:task-1"
    assert parse_trajectory_id("traj/tb:task-1/weak/03") == (task_id, "weak", 3)
    assert task_id_of("traj/tb:task-1/strong/00") == task_id


def test_parse_subtask_id_round_trip():
    traj = "traj/tb:task-1/strong/00"
    assert parse_subtask_id(f"{traj}/seg04") == (traj, 4)


def test_parse_attempt_id_round_trip():
    exam = "traj/tb:task-1/strong/00/seg01/exam"
    assert parse_attempt_id(f"{exam}/att02") == (exam, 2)


@pytest.mark.parametrize("bad", [
    "tb:task-1/strong/00",          # missing traj/ prefix
    "traj/tb:task-1/medium/00",     # unknown role
    "traj/tb:task-1/strong",       # missing attempt
    "traj/tb:task-1/strong/0",     # attempt not two digits
    "traj//strong/00",              # empty task id
])
def test_parse_trajectory_id_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_trajectory_id(bad)


@pytest.mark.parametrize("bad", [
    "traj/tb:task-1/strong/00",       # no suffix
    "traj/tb:task-1/strong/00/seg",   # no index
    "traj/tb:task-1/strong/00/xx01",  # wrong marker
    "traj/tb:task-1/strong/00/seg1",  # index not two digits
])
def test_parse_subtask_id_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_subtask_id(bad)


@pytest.mark.parametrize("bad", [
    "traj/tb:task-1/strong/00/seg01/exam",  # no suffix
    "x/exam/att",                           # no serial
    "x/exam/att1",                          # serial not two digits
])
def test_parse_attempt_id_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_attempt_id(bad)


@pytest.mark.parametrize("serial", [0, 9, 99, 100, 1000, 12345])
def test_attempt_id_round_trips_past_the_zero_padding(serial):
    # The formatter pads to two digits but does not cap: the 100th attempt on
    # one exam is att100, and the parser must accept it (otherwise every later
    # serial scan over the store raises).
    exam = "traj/tb:task-1/strong/00/seg01/exam"
    attempt_id = make_attempt_id(exam, serial)
    assert parse_attempt_id(attempt_id) == (exam, serial)


def test_attempt_id_formatting_unchanged_below_100():
    exam = "e/exam"
    assert make_attempt_id(exam, 7) == "e/exam/att07"
    assert make_attempt_id(exam, 99) == "e/exam/att99"
    assert make_attempt_id(exam, 100) == "e/exam/att100"


@pytest.mark.parametrize("bad", [
    "x/exam/att",        # no serial
    "x/exam/att1",       # fewer than two digits
    "x/exam/att1a",      # non-digit
    "x/exam/att-1",      # sign
    "x/exam/att 12",     # whitespace
    "x/exam/att²²",  # unicode superscript digits (str.isdigit is True)
    "x/exam/att１２",  # fullwidth digits
    "x/exam/att007",     # non-canonical padding: 7 is att07
    "x/exam/att0100",    # non-canonical padding: 100 is att100
])
def test_parse_attempt_id_rejects_bad_serials(bad):
    with pytest.raises(ValueError):
        parse_attempt_id(bad)


@pytest.mark.parametrize("index", [0, 99, 100, 1000])
def test_subtask_id_round_trips_past_the_zero_padding(index):
    traj = "traj/tb:task-1/strong/00"
    assert parse_subtask_id(make_subtask_id(traj, index)) == (traj, index)


@pytest.mark.parametrize("attempt", [0, 99, 100, 1000])
def test_trajectory_id_round_trips_past_the_zero_padding(attempt):
    trajectory_id = make_trajectory_id("tb:task-1", "weak", attempt)
    assert parse_trajectory_id(trajectory_id) == ("tb:task-1", "weak", attempt)


@pytest.mark.parametrize("bad", [
    "traj/tb:task-1/strong/007",
    "traj/tb:task-1/strong/²²",
])
def test_parse_trajectory_id_rejects_non_canonical_attempt(bad):
    with pytest.raises(ValueError):
        parse_trajectory_id(bad)


@pytest.mark.parametrize("bad", [
    "traj/tb:task-1/strong/00/seg007",
    "traj/tb:task-1/strong/00/seg²²",
])
def test_parse_subtask_id_rejects_non_canonical_index(bad):
    with pytest.raises(ValueError):
        parse_subtask_id(bad)
