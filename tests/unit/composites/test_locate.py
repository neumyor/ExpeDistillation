"""K3 (composites.locate): typing in chunks, the append-only ledgers and
their resume, and the segmentation helpers."""

import json
import re

import pytest

from expedistill.composites import locate
from expedistill.core.ids import make_subtask_id, make_trajectory_id
from expedistill.core.models import Step, Subtask, Trajectory
from expedistill.infra.reliability import (
    InfrastructureError, ModelOutputError, UnitFailureRecorder)
from expedistill.infra.store import Store, read_jsonl
from expedistill.llm.fake import json_script, make_fake_handle

LEARN = "p02:learn"


def _trajectory(store, task, role="weak", n_steps=2, correct=False):
    trajectory = Trajectory(
        trajectory_id=make_trajectory_id("p01:baseline", task, role, 0),
        task_id=task, benchmark="tb", model_role=role, model_name="m",
        attempt_index=0, correctness=correct,
        steps=[Step(action=f"{task}-a{i}", observation="o")
               for i in range(n_steps)])
    store.save(trajectory, "trajectories")
    return trajectory


def _segments(store, trajectory, labels, phase=LEARN):
    out = []
    for index, label in enumerate(labels):
        subtask = Subtask(
            subtask_id=make_subtask_id(trajectory.trajectory_id, index),
            phase_label=phase, task_id=trajectory.task_id,
            trajectory_id=trajectory.trajectory_id,
            source=trajectory.model_role, trajectory_success=False,
            segment_index=index, step_range=(index, index + 1), label=label,
            description=f"{label} it", success=False)
        store.save(subtask, "subtasks")
        out.append(subtask)
    return out


def _by_label(system, user):
    """A typing reply: one new type per label, existing names reused."""
    table = dict(re.findall(r"^(type/\d+) \| ([^|]+) \|", user, re.M))
    names = {name.strip(): type_id for type_id, name in table.items()}
    pairs = re.findall(r"^SUBTASK_ID: (.+)\n(?:.*\n)*?LABEL: (.+)$", user,
                       re.M)
    keys, new_types, assignments = {}, [], []
    for subtask_id, label in pairs:
        if label in names:
            ref = names[label]
        else:
            if label not in keys:
                keys[label] = f"k{len(keys)}"
                new_types.append({"key": keys[label], "name": label,
                                  "entry_state": f"about to {label}"})
            ref = keys[label]
        assignments.append({"subtask_id": subtask_id, "type": ref})
    return json_script({"new_types": new_types, "assignments": assignments})


def _type(store, tmp_path, handle, chunk_size=100):
    return locate.type_pending(store=store, run_dir=tmp_path,
                               phase_label=LEARN, chunk_size=chunk_size,
                               max_chars=500, view_rules=None, handle=handle)


@pytest.fixture()
def store(tmp_path):
    store = Store(tmp_path)
    _segments(store, _trajectory(store, "tb:t1"), ["find", "heat"])
    _segments(store, _trajectory(store, "tb:t2"), ["find", "cool"])
    return store


def test_every_new_segment_gets_a_type_in_subtask_order(store, tmp_path):
    handle = make_fake_handle(script=_by_label)

    counts = _type(store, tmp_path, handle)

    assert counts == {"typed": 4, "new_types": 3, "given_up": 0}
    table = locate.load_type_table(tmp_path)
    assert [(row.type_id, row.name, row.phase_label) for row in table] == [
        ("type/001", "find", LEARN), ("type/002", "heat", LEARN),
        ("type/003", "cool", LEARN)]
    rows = read_jsonl(tmp_path / locate.SEGMENT_TYPES_PATH)
    assert [row["subtask_id"] for row in rows] == sorted(
        row["subtask_id"] for row in rows)
    assert {row["phase_label"] for row in rows} == {LEARN}
    assignments = locate.load_assignments(tmp_path)
    assert assignments[rows[0]["subtask_id"]] == "type/001"


def test_chunks_carry_the_types_earlier_chunks_added(store, tmp_path):
    handle = make_fake_handle(script=_by_label)

    _type(store, tmp_path, handle, chunk_size=2)

    calls = handle.client.calls
    assert len(calls) == 2
    assert "(none)" in calls[0]["user"]
    assert "type/001 | find |" in calls[1]["user"]
    assert len(locate.load_type_table(tmp_path)) == 3


def test_a_resume_types_only_the_untyped_segments(store, tmp_path):
    _type(store, tmp_path, make_fake_handle(script=_by_label), chunk_size=2)
    _segments(store, _trajectory(store, "tb:t3"), ["find", "wash"])
    handle = make_fake_handle(script=_by_label)

    counts = _type(store, tmp_path, handle)

    assert counts["typed"] == 2 and counts["new_types"] == 1
    prompt = handle.client.calls[0]["user"]
    assert prompt.count("SUBTASK_ID: ") == 2 and "tb:t3" in prompt
    assert _type(store, tmp_path, handle)["typed"] == 0
    assert len(handle.client.calls) == 1


def test_segments_of_other_phases_are_not_typed(store, tmp_path):
    _segments(store, _trajectory(store, "tb:t9"), ["late"],
              phase="p04:learn")
    handle = make_fake_handle(script=_by_label)

    _type(store, tmp_path, handle)

    assert "tb:t9" not in handle.client.calls[0]["user"]


def test_types_a_crash_left_unused_are_dropped_first(store, tmp_path):
    handle = make_fake_handle(script=_by_label)
    _type(store, tmp_path, handle, chunk_size=2)
    straight = (tmp_path / locate.SUBTASK_TYPES_PATH).read_bytes()
    # The crash: the second chunk's types landed, its assignments did not.
    rows = read_jsonl(tmp_path / locate.SEGMENT_TYPES_PATH)
    path = tmp_path / locate.SEGMENT_TYPES_PATH
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n"
                            for row in rows[:2]), encoding="utf-8")

    _type(store, tmp_path, make_fake_handle(script=_by_label),
          chunk_size=2)

    assert (tmp_path / locate.SUBTASK_TYPES_PATH).read_bytes() == straight
    assert len(locate.load_assignments(tmp_path)) == 4


def test_an_unusable_reply_is_a_terminal_record_per_segment(store, tmp_path):
    bad = json_script({"new_types": [], "assignments": []})
    handle = make_fake_handle(script=[bad] * (locate.TYPING_REPAIRS + 1))

    counts = _type(store, tmp_path, handle)

    assert counts == {"typed": 0, "new_types": 0, "given_up": 4}
    assert "rejected" in handle.client.calls[1]["user"]
    failures = UnitFailureRecorder(tmp_path).list_failures()
    assert len(failures) == 4
    assert {row["error_class"] for row in failures} == {
        ModelOutputError.__name__}
    again = make_fake_handle(script=[])
    assert _type(store, tmp_path, again)["given_up"] == 4
    assert again.client.calls == []


def test_an_infrastructure_failure_is_recorded_and_raised(store, tmp_path):
    handle = make_fake_handle(script=[InfrastructureError("down")])

    with pytest.raises(InfrastructureError):
        _type(store, tmp_path, handle)

    failures = UnitFailureRecorder(tmp_path).list_failures()
    assert [row["error_class"] for row in failures] == [
        InfrastructureError.__name__]
    # Not terminal: the resume types the chunk.
    assert _type(store, tmp_path,
                 make_fake_handle(script=_by_label))["typed"] == 4


def test_an_empty_trajectory_is_skipped_once_without_a_call(tmp_path):
    store = Store(tmp_path)
    empty = _trajectory(store, "tb:t1", n_steps=0)
    handle = make_fake_handle(script=[])
    for _ in range(2):
        assert locate.segment_one(
            store=store, trajectory=empty, phase_label=LEARN, K=3,
            retry_limit=0, view_rules=None, handle=handle,
            run_dir=tmp_path) is None
    records = read_jsonl(tmp_path / "stage2_segments"
                         / "segmentation_audit.jsonl")
    assert records == [{"trajectory_id": empty.trajectory_id,
                        "status": "skipped", "reason": "empty trajectory"}]


def test_strong_trajectories_follow_the_rule_per_task(tmp_path):
    store = Store(tmp_path)
    kept = [_trajectory(store, task, role="strong", correct=True)
            for task in ("tb:t2", "tb:t1")]
    _trajectory(store, "tb:t1", role="weak")
    _trajectory(store, "tb:t3", role="strong", n_steps=0)
    extra = Trajectory(**{**kept[0].to_dict(), "trajectory_id":
                          make_trajectory_id("p01:baseline", "tb:t2",
                                             "strong", 1),
                          "correctness": False})
    store.save(extra, "trajectories")

    every = locate.strong_trajectories(store, "p01:baseline", "all")
    one = locate.strong_trajectories(store, "p01:baseline", "strong1_weak1")

    assert [item.trajectory_id for item in every] == sorted(
        [kept[0].trajectory_id, kept[1].trajectory_id,
         extra.trajectory_id])
    assert extra.trajectory_id not in {item.trajectory_id for item in one}
    assert len(one) == 2
    assert locate.strong_trajectories(store, "p03:practice", "all") == []
