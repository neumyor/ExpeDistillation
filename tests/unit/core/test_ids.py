"""Unit tests for the deterministic ID rules."""
import pytest

from expedistill.core.config import phase_label
from expedistill.core.ids import (
    TrajectoryIdParts,
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
    make_type_id,
    parse_subtask_id,
    parse_trajectory_id,
    parse_type_id,
    phase_tag,
    raw_task_id,
)

BASELINE = phase_label(0, "baseline")
PRACTICE = phase_label(2, "practice")


def test_task_id():
    assert make_task_id("tb", "task-1") == "tb:task-1"


def test_phase_tag_replaces_the_label_colon():
    assert phase_tag(BASELINE) == "p01_baseline"
    assert phase_tag("p12:my_phase.v2") == "p12_my_phase.v2"


@pytest.mark.parametrize("bad", [
    "baseline", "p1:baseline", "p01_baseline", "p01:", "p01:a:b",
    "p01:a/b", "", None])
def test_phase_tag_rejects_non_labels(bad):
    with pytest.raises(ValueError):
        phase_tag(bad)


def test_trajectory_id_format():
    assert (make_trajectory_id(BASELINE, "tb:task-1", "strong", 0)
            == "traj/p01_baseline/tb:task-1/strong/00")
    assert (make_trajectory_id(PRACTICE, "tb:task-1", "weak", 7)
            == "traj/p03_practice/tb:task-1/weak/07")


def test_one_task_in_two_phases_gets_two_ids():
    # The reason the id carries the phase: a practice run must never be
    # mistaken for the baseline run of the same task and attempt.
    assert make_trajectory_id(BASELINE, "tb:t", "weak", 0) \
        != make_trajectory_id(PRACTICE, "tb:t", "weak", 0)


def test_subtask_id_is_parent_plus_suffix():
    traj = make_trajectory_id(BASELINE, "tb:task-1", "strong", 0)
    assert make_subtask_id(traj, 0) == f"{traj}/seg00"
    assert make_subtask_id(traj, 12) == f"{traj}/seg12"


def test_type_and_skill_ids():
    assert make_type_id(1) == "type/001"
    assert make_type_id(42) == "type/042"
    assert make_type_id(1000) == "type/1000"
    assert make_skill_id(make_type_id(3)) == "type/003/skill"


@pytest.mark.parametrize("make,args", [
    (make_trajectory_id, (BASELINE, "tb:t", "strong", -1)),
    (make_trajectory_id, (BASELINE, "tb:t", "medium", 0)),
    (make_trajectory_id, ("baseline", "tb:t", "strong", 0)),
    (make_trajectory_id, (BASELINE, "", "strong", 0)),
    (make_subtask_id, ("traj/x", -1)),
    (make_type_id, (0,)),
    (make_type_id, (-1,)),
    (make_type_id, (True,)),
    (make_skill_id, ("traj/p01_baseline/tb:t/strong/00/seg00",)),
    (make_skill_id, ("type/1",)),
])
def test_bad_arguments_raise(make, args):
    with pytest.raises(ValueError):
        make(*args)


def test_child_id_embeds_parent_id():
    task = make_task_id("tb", "task-1")
    traj = make_trajectory_id(PRACTICE, task, "weak", 3)
    seg = make_subtask_id(traj, 2)
    type_id = make_type_id(5)
    assert traj.startswith(f"traj/{phase_tag(PRACTICE)}/{task}/")
    assert seg.startswith(traj + "/")
    assert make_skill_id(type_id).startswith(type_id + "/")


def test_raw_task_id_round_trip():
    assert raw_task_id("tb:task-1", "tb") == "task-1"
    assert raw_task_id(make_task_id("tb", "task-1"), "tb") == "task-1"
    # A raw id may itself contain '/' (ALFWorld's are relative paths);
    # only the colon is this scheme's separator.
    assert raw_task_id("tb:json_2.1.1/valid/x", "tb") == "json_2.1.1/valid/x"


@pytest.mark.parametrize("bad", [
    "searchqa:q1",     # another benchmark's id
    "task-1",          # never prefixed
    "tb:",             # empty bare id
    "tb:tb:task-1",    # double prefix: built from an already-prefixed id
    "tb:searchqa:q1",  # double prefix across benchmarks
])
def test_raw_task_id_rejects_foreign_and_non_canonical(bad):
    with pytest.raises(ValueError):
        raw_task_id(bad, "tb")


@pytest.mark.parametrize("label,task_id,role,attempt", [
    (BASELINE, "tb:task-1", "weak", 3),
    (PRACTICE, "alfworld:json_2.1.1/valid/x", "strong", 0),
    ("p07:a_b", "tb:t", "weak", 100),
])
def test_parse_trajectory_id_round_trip(label, task_id, role, attempt):
    parsed = parse_trajectory_id(
        make_trajectory_id(label, task_id, role, attempt))
    assert parsed == TrajectoryIdParts(label, task_id, role, attempt)
    assert (parsed.phase_label, parsed.task_id, parsed.role,
            parsed.attempt) == (label, task_id, role, attempt)


def test_parse_subtask_id_round_trip():
    traj = make_trajectory_id(BASELINE, "tb:task-1", "strong", 0)
    assert parse_subtask_id(f"{traj}/seg04") == (traj, 4)


@pytest.mark.parametrize("bad", [
    "traj/tb:task-1/strong/00",                 # schema-2 id: no phase
    "p01_baseline/tb:task-1/strong/00",         # missing traj/ prefix
    "traj/p01:baseline/tb:task-1/strong/00",    # label instead of tag
    "traj/p01_baseline/tb:task-1/medium/00",    # unknown role
    "traj/p01_baseline/tb:task-1/strong",       # missing attempt
    "traj/p01_baseline/tb:task-1/strong/0",     # attempt not two digits
    "traj/p01_baseline//strong/00",             # empty task id
    "traj/p01_baseline/tb:task-1/strong/007",   # non-canonical attempt
    "traj/p01_baseline/tb:task-1/strong/²²",    # non-ASCII digits
])
def test_parse_trajectory_id_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_trajectory_id(bad)


@pytest.mark.parametrize("bad", [
    "traj/p01_baseline/tb:task-1/strong/00",         # no suffix
    "traj/p01_baseline/tb:task-1/strong/00/seg",     # no index
    "traj/p01_baseline/tb:task-1/strong/00/xx01",    # wrong marker
    "traj/p01_baseline/tb:task-1/strong/00/seg1",    # not two digits
    "traj/p01_baseline/tb:task-1/strong/00/seg007",  # non-canonical
    "traj/p01_baseline/tb:task-1/strong/00/seg²²",   # non-ASCII digits
])
def test_parse_subtask_id_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_subtask_id(bad)


@pytest.mark.parametrize("index", [0, 99, 100, 1000])
def test_subtask_id_round_trips_past_the_zero_padding(index):
    traj = make_trajectory_id(BASELINE, "tb:task-1", "strong", 0)
    assert parse_subtask_id(make_subtask_id(traj, index)) == (traj, index)


@pytest.mark.parametrize("serial", [1, 9, 999, 1000, 12345])
def test_type_id_round_trips_past_the_zero_padding(serial):
    assert parse_type_id(make_type_id(serial)) == serial


@pytest.mark.parametrize("bad", [
    "type/000",     # serials start at 1
    "type/01",      # fewer than three digits
    "type/0001",    # non-canonical padding: 1 is type/001
    "type/abc",
    "type/１２３",   # fullwidth digits
    "type/001/skill",
    "001",
])
def test_parse_type_id_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_type_id(bad)
