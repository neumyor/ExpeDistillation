"""B3 Store: layout, save/load by ID, atomic writes, JSONL bank."""

import json

import pytest

from expedistill.core.ids import (
    make_attempt_id, make_exam_id, make_skill_id, make_subtask_id,
    make_task_id, make_trajectory_id,
)
from expedistill.core.models import Attempt, Exam, Skill, Step, Subtask, Trajectory
from expedistill.infra import store as store_mod
from expedistill.infra.store import (
    KIND_DIRS, Store, append_jsonl, kind_for, object_id, read_json, read_jsonl,
    write_json,
)

ALL_DIRS = sorted(set(KIND_DIRS.values())
                  | {"stage5_curation", "stage6_test", "llm_requests"})


def a_trajectory(task="tb:task-1", role="strong", attempt=0):
    return Trajectory(
        trajectory_id=make_trajectory_id(make_task_id("tb", task), role, attempt),
        task_id=task, benchmark="tb", model_role=role, model_name="m",
        attempt_index=attempt,
        steps=[Step(action="ls", observation="a b")])


def a_subtask(trajectory="traj/tb:task-1/strong/00", index=1):
    return Subtask(
        subtask_id=make_subtask_id(trajectory, index), task_id="tb:task-1",
        trajectory_id=trajectory, source="strong", trajectory_success=True,
        segment_index=index, step_range=(0, 1), label="l", description="d",
        success=True)


def a_skill(subtask="traj/tb:task-1/strong/00/seg01"):
    return Skill(skill_id=make_skill_id(subtask), content="do the thing",
                 subtask_id=subtask, source_subtasks=[subtask])


def an_exam(subtask="traj/tb:task-1/strong/00/seg01"):
    return Exam(exam_id=make_exam_id(subtask), question_text="q?",
                reference_answer="a", subtask_id=subtask,
                skill_id=make_skill_id(subtask))


def an_attempt(exam="traj/tb:task-1/strong/00/seg01/exam"):
    return Attempt(attempt_id=make_attempt_id(exam, 0), exam_id=exam,
                   purpose="bare", weak_answer="answer")


EXAMPLES = [
    ("trajectories", a_trajectory()),
    ("subtasks", a_subtask()),
    ("skills", a_skill()),
    ("exams", an_exam()),
    ("attempts", an_attempt()),
]


def test_init_creates_full_layout(tmp_path):
    Store(tmp_path / "run")
    # Every leaf directory plus its stage parents must exist.
    found = {p.relative_to(tmp_path / "run").as_posix()
             for p in (tmp_path / "run").rglob("*") if p.is_dir()}
    expected = set(ALL_DIRS)
    for relative in ALL_DIRS:
        parent = relative.rsplit("/", 1)[0]
        if "/" in relative:
            expected.add(parent)
    assert found == expected
    # No stray files, no stray top-level directories.
    assert not [p for p in (tmp_path / "run").rglob("*") if not p.is_dir()]


@pytest.mark.parametrize("kind,obj", EXAMPLES)
def test_save_load_roundtrip(tmp_path, kind, obj):
    s = Store(tmp_path)
    s.save(obj, kind)
    obj_id = object_id(obj, kind)
    assert s.exists(kind, obj_id)
    loaded = s.load(kind, obj_id, type(obj))
    assert loaded == obj


def test_ids_are_decoded_and_sorted(tmp_path):
    s = Store(tmp_path)
    objs = [a_trajectory(task=f"tb:t{index}") for index in (2, 0, 1)]
    for obj in objs:
        s.save(obj, "trajectories")
    assert s.ids("trajectories") == [object_id(obj) for obj in
                                      sorted(objs, key=object_id)]


def test_filenames_are_percent_encoded(tmp_path):
    # IDs contain '/' and ':' which must not leak into path segments.
    s = Store(tmp_path)
    obj = a_subtask()
    s.save(obj, "subtasks")
    encoded = store_mod.urllib.parse.quote(object_id(obj, "subtasks"), safe="") + ".json"
    assert (s.dir_for("subtasks") / encoded).exists()
    assert "%" in encoded and "/" not in encoded


def test_load_missing_raises(tmp_path):
    s = Store(tmp_path)
    with pytest.raises(FileNotFoundError):
        s.load("trajectories", "nope", Trajectory)


def test_unknown_kind_rejected(tmp_path):
    s = Store(tmp_path)
    with pytest.raises(ValueError):
        s.exists("nonsense", "x")
    with pytest.raises(ValueError):
        s.save(a_skill(), "nonsense")


def test_kind_for_and_object_id():
    for kind, obj in EXAMPLES:
        assert kind_for(obj) == kind
    with pytest.raises(ValueError):
        kind_for(object())
    with pytest.raises(ValueError):
        object_id(object())


def test_write_json_is_atomic_when_replace_fails(tmp_path):
    """Simulated interruption between temp write and rename: the target keeps
    its previous content and no partial file or temp debris is left."""
    path = tmp_path / "data.json"
    write_json(path, {"version": 1})
    original = path.read_text()

    real_replace = store_mod.os.replace

    def failing_replace(src, dst):
        raise OSError("simulated crash before rename")

    store_mod.os.replace = failing_replace
    try:
        with pytest.raises(OSError):
            write_json(path, {"version": 2})
    finally:
        store_mod.os.replace = real_replace
    assert path.read_text() == original
    assert json.loads(path.read_text()) == {"version": 1}
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_json_is_atomic_when_serialization_fails(tmp_path):
    """A crash mid-dump leaves the previous content intact and no temp file."""
    path = tmp_path / "data.json"
    write_json(path, {"version": 1})
    with pytest.raises(TypeError):
        write_json(path, {"version": 2, "bad": object()})
    assert json.loads(path.read_text()) == {"version": 1}
    assert list(tmp_path.glob("*.tmp")) == []


def test_read_json_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_json(tmp_path / "absent.json")


def test_append_bank_appends_lines(tmp_path):
    s = Store(tmp_path)
    s.append_bank({"skill_id": "a"})
    s.append_bank({"skill_id": "b"})
    assert s.bank() == [{"skill_id": "a"}, {"skill_id": "b"}]


def test_append_bank_repairs_missing_newline(tmp_path):
    # A previous interrupted append may leave no trailing newline; the next
    # append must not glue two records onto one line.
    s = Store(tmp_path)
    path = tmp_path / "skill_bank.jsonl"
    path.write_text('{"skill_id": "a"}')  # no trailing newline
    s.append_bank({"skill_id": "b"})
    assert len(s.bank()) == 2


def test_append_jsonl_creates_parent(tmp_path):
    path = tmp_path / "sub" / "ledger.jsonl"
    append_jsonl(path, {"n": 1})
    assert read_jsonl(path) == [{"n": 1}]


def test_read_jsonl_tolerates_interrupted_tail_but_not_middle(tmp_path):
    path = tmp_path / "ledger.jsonl"
    good = '{"n": 1}\n{"n": 2}\n'
    path.write_text(good + '{"n": 3')  # interrupted final line
    assert read_jsonl(path) == [{"n": 1}, {"n": 2}]
    path.write_text('{"n": 1}\n{"broken\n{"n": 3}\n')
    with pytest.raises(ValueError):
        read_jsonl(path)


def test_read_jsonl_absent_file_is_empty(tmp_path):
    assert read_jsonl(tmp_path / "absent.jsonl") == []


# --- torn writes must not become permanent corruption ----------------------

def test_torn_tail_survives_two_more_appends(tmp_path):
    # A crash mid-append leaves a partial final line. Before the fix the next
    # append wrote "\n" after it, turning the fragment into a corrupt *middle*
    # line, and every later read_jsonl raised -- permanently.
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b'{"n": 1}\n{"n": 2}\n{"n": 3, "pa')
    append_jsonl(path, {"n": 4})
    append_jsonl(path, {"n": 5})
    assert read_jsonl(path) == [{"n": 1}, {"n": 2}, {"n": 4}, {"n": 5}]
    assert path.read_bytes() == (
        b'{"n": 1}\n{"n": 2}\n{"n": 4}\n{"n": 5}\n')


def test_torn_tail_is_kept_as_evidence(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b'{"n": 1}\n{"n": 2, "pa')
    append_jsonl(path, {"n": 3})
    tail = path.with_name(path.name + ".interrupted-tail")
    assert tail.read_bytes() == b'{"n": 2, "pa\n'
    # A second torn write appends to the evidence file, never overwrites it.
    with path.open("ab") as stream:
        stream.write(b'{"n": 4')
    append_jsonl(path, {"n": 5})
    assert tail.read_bytes() == b'{"n": 2, "pa\n{"n": 4\n'
    assert read_jsonl(path) == [{"n": 1}, {"n": 3}, {"n": 5}]


def test_torn_first_line_is_dropped(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b'{"n": 1')
    append_jsonl(path, {"n": 2})
    assert path.read_bytes() == b'{"n": 2}\n'


def test_complete_record_missing_newline_is_kept(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b'{"n": 1}\n{"n": 2}')
    append_jsonl(path, {"n": 3})
    append_jsonl(path, {"n": 4})
    assert read_jsonl(path) == [{"n": i} for i in (1, 2, 3, 4)]
    assert not path.with_name(path.name + ".interrupted-tail").exists()


@pytest.mark.parametrize("fragment", [
    b'{"n": 2, "s": "\xe4\xb8',  # torn inside a multi-byte UTF-8 character
    b'[1, 2',                    # torn non-object
    b'42',                       # parses, but is not a record (not an object)
    b'   ',                      # whitespace only
])
def test_non_record_fragments_are_truncated(tmp_path, fragment):
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b'{"n": 1}\n' + fragment)
    append_jsonl(path, {"n": 3})
    append_jsonl(path, {"n": 4})
    assert read_jsonl(path) == [{"n": 1}, {"n": 3}, {"n": 4}]


def test_bank_survives_torn_tail(tmp_path):
    s = Store(tmp_path)
    s.append_bank({"skill_id": "a"})
    with (tmp_path / "skill_bank.jsonl").open("ab") as stream:
        stream.write(b'{"skill_id": "b", "con')
    s.append_bank({"skill_id": "c"})
    s.append_bank({"skill_id": "d"})
    assert [r["skill_id"] for r in s.bank()] == ["a", "c", "d"]


@pytest.mark.parametrize("block", [1, 3, 7, 64 * 1024])
def test_tail_scan_across_block_boundaries(tmp_path, monkeypatch, block):
    # The backwards scan for the last newline reads in blocks; the result
    # must not depend on where block boundaries fall.
    monkeypatch.setattr(store_mod, "_TAIL_BLOCK", block)
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b'{"n": 1}\n{"n": 2, "long": "' + b"x" * 50)
    append_jsonl(path, {"n": 3})
    path.write_bytes(path.read_bytes() + b'{"n": 4}')  # newline lost
    append_jsonl(path, {"n": 5})
    assert read_jsonl(path) == [{"n": 1}, {"n": 3}, {"n": 4}, {"n": 5}]
