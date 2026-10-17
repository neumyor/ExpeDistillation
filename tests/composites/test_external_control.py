"""The external-campaign control loader (composites/external_control):
the sha256-pinned score file, the model-key coverage and the aligned
reward slice -- every identity failure a loud ConfigError, never a
silently widened or missing task.

The C-phase wiring (control_source, the phase-directory copy, the
summary's audit record) is covered by the driver tests in
tests/unit/test_w2a_phase_execute.py; these tests pin the loader's own
contract on a small fixture file in the real campaign shape.
"""

import hashlib
import json

import pytest

from expedistill.composites import external_control
from expedistill.core.ids import make_task_id
from expedistill.infra.reliability import ConfigError

BENCHMARK = "fakebench"
TASKS = ("t1", "t2", "t3")
MODEL_KEY = "qwen"


class _Task:
    """The loader reads only ``task_id`` (the driver passes Task
    objects; the shape stays the plugin's business)."""

    def __init__(self, task_id):
        self.task_id = task_id


def _write_scores(tmp_path, rows, name="scores.json"):
    payload = {
        "tasks": {f"{model}:{task}": entry
                  for (model, task), entry in rows.items()},
        f"{MODEL_KEY}_avg@5": 0.5,
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path), hashlib.sha256(path.read_bytes()).hexdigest()


def _campaign_rows(rewards_by_task, model=MODEL_KEY):
    return {(model, task): {"n": len(rewards), "rewards": rewards,
                            "success_rate": sum(rewards) / len(rewards)}
            for task, rewards in rewards_by_task.items()}


def test_loads_the_aligned_slice_as_metrics(tmp_path):
    path, sha = _write_scores(tmp_path, _campaign_rows({
        "t1": [1.0, 0.0, 1.0, 0.0, 1.0],
        "t2": [0.0, 0.0, 0.0, 1.0, 1.0],
        "t3": [0.0, 0.0, 0.0, 0.0, 0.0],
    }))
    metrics, rewards = external_control.load_external_control(
        path=path, sha256=sha, model_key=MODEL_KEY, benchmark=BENCHMARK,
        tasks=[_Task(t) for t in TASKS], n_attempts=3)
    # Keys are the STORED prefixed ids -- the same keys
    # compute_task_metrics produces, so the arms' task sets compare.
    assert sorted(metrics) == sorted(make_task_id(BENCHMARK, t)
                                     for t in TASKS)
    assert metrics[make_task_id(BENCHMARK, "t1")] == {
        "attempts": 3, "valid": 3, "success": 2, "infra": 0}
    assert metrics[make_task_id(BENCHMARK, "t2")]["success"] == 0
    assert rewards[make_task_id(BENCHMARK, "t1")] == [1.0, 0.0, 1.0]


def test_missing_file_refused(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        external_control.load_external_control(
            path=str(tmp_path / "nope.json"), sha256="0" * 64,
            model_key=MODEL_KEY, benchmark=BENCHMARK,
            tasks=[_Task("t1")], n_attempts=3)


def test_sha_mismatch_refused(tmp_path):
    path, _ = _write_scores(tmp_path, _campaign_rows({"t1": [1.0] * 5}))
    with pytest.raises(ConfigError, match="hashes to sha256"):
        external_control.load_external_control(
            path=path, sha256="b" * 64, model_key=MODEL_KEY,
            benchmark=BENCHMARK, tasks=[_Task("t1")], n_attempts=3)


def test_verify_file_is_pure_bytes(tmp_path):
    path, sha = _write_scores(tmp_path, _campaign_rows({"t1": [1.0] * 5}))
    # The freeze step's check works on a file with non-JSON bytes too:
    # it pins identity, not shape.
    raw = tmp_path / "raw.bin"
    raw.write_bytes(b"\x00\x01not json")
    raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
    assert external_control.verify_external_control_file(
        str(raw), raw_sha) == raw
    with pytest.raises(ConfigError):
        external_control.verify_external_control_file(path, raw_sha)


def test_uncovered_task_refused_with_the_list(tmp_path):
    path, sha = _write_scores(tmp_path, _campaign_rows({
        "t1": [1.0] * 5, "t2": [1.0] * 5}))
    with pytest.raises(ConfigError, match="'t3'"):
        external_control.load_external_control(
            path=path, sha256=sha, model_key=MODEL_KEY,
            benchmark=BENCHMARK,
            tasks=[_Task(t) for t in TASKS], n_attempts=3)


def test_wrong_model_key_is_a_coverage_failure(tmp_path):
    path, sha = _write_scores(tmp_path, _campaign_rows(
        {"t1": [1.0] * 5}, model="dpsk"))
    with pytest.raises(ConfigError, match="model key 'qwen'"):
        external_control.load_external_control(
            path=path, sha256=sha, model_key=MODEL_KEY,
            benchmark=BENCHMARK,
            tasks=[_Task("t1")], n_attempts=3)


def test_fewer_archived_attempts_than_the_aligned_depth(tmp_path):
    path, sha = _write_scores(tmp_path, _campaign_rows(
        {"t1": [1.0, 0.0]}))
    with pytest.raises(ConfigError, match="aligned depth 3"):
        external_control.load_external_control(
            path=path, sha256=sha, model_key=MODEL_KEY,
            benchmark=BENCHMARK, tasks=[_Task("t1")], n_attempts=3)


def test_non_binary_reward_refused(tmp_path):
    rows = _campaign_rows({"t1": [1.0, 0.5, 1.0, 1.0, 1.0]})
    path, sha = _write_scores(tmp_path, rows)
    with pytest.raises(ConfigError, match="non-binary reward"):
        external_control.load_external_control(
            path=path, sha256=sha, model_key=MODEL_KEY,
            benchmark=BENCHMARK, tasks=[_Task("t1")], n_attempts=3)


def test_bad_payload_shapes_refused(tmp_path):
    # Not JSON.
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    bad_sha = hashlib.sha256(bad.read_bytes()).hexdigest()
    with pytest.raises(ConfigError, match="readable JSON"):
        external_control.load_external_control(
            path=str(bad), sha256=bad_sha, model_key=MODEL_KEY,
            benchmark=BENCHMARK, tasks=[_Task("t1")], n_attempts=1)
    # JSON without the tasks mapping.
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"qwen_avg@5": 0.5}),
                     encoding="utf-8")
    empty_sha = hashlib.sha256(empty.read_bytes()).hexdigest()
    with pytest.raises(ConfigError, match="'tasks' mapping"):
        external_control.load_external_control(
            path=str(empty), sha256=empty_sha, model_key=MODEL_KEY,
            benchmark=BENCHMARK, tasks=[_Task("t1")], n_attempts=1)


def test_the_audit_record_reproduces_the_control(tmp_path):
    path, sha = _write_scores(tmp_path, _campaign_rows({
        "t1": [1.0, 0.0, 1.0, 0.0, 0.0]}))
    _, rewards = external_control.load_external_control(
        path=path, sha256=sha, model_key=MODEL_KEY, benchmark=BENCHMARK,
        tasks=[_Task("t1")], n_attempts=3)
    record = external_control.external_control_record(
        spec={"path": path, "sha256": sha, "model_key": MODEL_KEY},
        n_attempts=3, rewards=rewards)
    assert record["n_attempts"] == 3
    assert record["rewards"] == {make_task_id(BENCHMARK, "t1"):
                                 [1.0, 0.0, 1.0]}
    # The pinned identity rides along: the summary alone names the file
    # and the exact bytes it hashes to.
    assert record["sha256"] == sha
    assert record["model_key"] == MODEL_KEY
