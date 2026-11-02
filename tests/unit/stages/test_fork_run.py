"""fork_run tests: a synthetic finished source run, forked at a phase.

No real run directory is read: the fixture writes a small finished source
(manifest, phase records, the five object stores, the source's own ledgers)
in tmp_path. The source sequence is ``baseline, learn, exam, learn, test``:
its first learn phase (p02) leaves an as-is bank artifact, its second (p04,
the last) curates, and the final store is the state after p04 -- statuses
rewritten and a merge product added -- which is exactly what a fork through
p03 must NOT inherit.
"""

import copy
import json

import pytest
import yaml

from expedistill.cli import main
from expedistill.composites.phase_bank import (
    BANK_NAME,
    SUMMARY_NAME as BANK_SUMMARY_NAME,
    phase_bank_dir,
)
from expedistill.core.config import ExperimentConfig, load_config
from expedistill.core.models import Skill, Subtask, Trajectory
from expedistill.infra.reliability import ConfigError
from expedistill.infra.runlock import LOCK_NAME
from expedistill.infra.store import Store, write_json, write_jsonl
from expedistill.inject import bank_reference
from expedistill.stages import fork_run as fork_module
from expedistill.stages import phase_learn
from expedistill.stages.fork_run import fork_run
from expedistill.stages.phase_execute import phase_disk
from tests.helpers.skills import make_skill

_LEARN = {
    "kind": "learn",
    "review": {"name": "review", "decision": "task_level",
               "unread_in_won_group": "delete", "before_steps": 10,
               "after_steps": 10, "K": 4, "retry_limit": 2,
               "write_lessons": True},
    "author": {"name": "distill_diagnose"},
    "curate": {"name": "agentbrew", "merge_check": "none",
               "token_budget": 4096, "rounds": 3},
}
_PHASES = {
    "baseline": {"kind": "execute", "role": "baseline", "tasks": "train",
                 "delivery": {"name": "none"},
                 "execution": {"name": "real",
                               "arms": ["executor_strong",
                                        "executor_weak"]}},
    "learn": _LEARN,
    "exam": {"kind": "execute", "role": "exam",
             "delivery": {"name": "exam_folder",
                          "description_mode": "summary_then_when_to_use"},
             "execution": {"name": "exam", "bare_control": True,
                           "leak_audit": True, "max_skill_reads": 3}},
    "test": {"kind": "execute", "role": "test", "tasks": "test",
             "delivery": {"name": "whole_bank"},
             "execution": {"name": "real"},
             "compare": {"against": "baseline"}},
}
_SOURCE_SEQUENCE = ["baseline", "learn", "exam", "learn", "test"]
_BASE = {
    "benchmark": "forkbench",
    "mode": "split",
    "split": {"train": ["t1"], "test": ["t2"], "reserved": ["t3"]},
    "models": {"executor_strong": "big-model", "executor_weak": "weak-7b"},
    "weak_profile": "a weak model",
    "n_attempts_strong": 1,
    "n_attempts_weak": 1,
    "schema_version": 7,
    "token_budgets": {"default_max_output_tokens": 512},
}


def _config_dict(sequence, **overrides):
    config = copy.deepcopy(_BASE)
    config["sequence"] = list(sequence)
    config["phases"] = copy.deepcopy(
        {name: _PHASES[name] for name in dict.fromkeys(sequence)})
    for key, value in overrides.items():
        config[key] = value
    return config


def _target(tmp_path, sequence, name="target.yaml", **overrides):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(_config_dict(sequence, **overrides),
                                   sort_keys=True), encoding="utf-8")
    return path


def _skill(task, status, **fields):
    return make_skill(f"traj/forkbench:{task}/strong/00/seg00",
                      f"content {task}", status=status, **fields)


def _write_phase(run_dir, label, name, role, **files):
    directory = phase_bank_dir(run_dir, label)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "phase.json",
               {"label": label, "name": name, "role": role})
    for filename, payload in files.items():
        write_json(directory / filename, payload)
    return directory


def _write_bank(run_dir, label, name, rows):
    directory = _write_phase(
        run_dir, label, name, "learn",
        **{phase_learn.SUMMARY_NAME: {"bank": {"size": len(rows)}}})
    write_jsonl(directory / BANK_NAME, rows)
    write_json(directory / BANK_SUMMARY_NAME,
               {"members": [row["skill_id"] for row in rows],
                "size": len(rows), "bank_reference": bank_reference(rows)})


class Source:
    """The built source run and the objects the assertions name."""

    def __init__(self, run_dir, ids, p02_rows, p04_rows):
        self.run_dir = run_dir
        self.ids = ids
        self.p02_rows = p02_rows
        self.p04_rows = p04_rows


def build_source(tmp_path, sequence=_SOURCE_SEQUENCE, finished=None) -> Source:
    run_dir = tmp_path / "source"
    config = ExperimentConfig.from_dict(_config_dict(sequence))
    run_dir.mkdir()
    write_json(run_dir / "manifest.json",
               {"benchmark": "forkbench", "config": config.to_dict()})
    store = Store(run_dir)

    # Stage 1-2: trajectories and subtasks (append-only after p02).
    trajectory = Trajectory(
        trajectory_id="traj/forkbench:t1/strong/00", task_id="forkbench:t1",
        benchmark="forkbench", model_role="strong", model_name="big-model",
        attempt_index=0, steps=[], correctness=True, meta={})
    store.save(trajectory, "trajectories")
    store.save(Subtask(
        subtask_id="traj/forkbench:t1/strong/00/seg00",
        task_id="forkbench:t1", trajectory_id=trajectory.trajectory_id,
        source="strong", trajectory_success=True, segment_index=0,
        step_range=(0, 1), label="seg", description="a segment",
        success=True), "subtasks")

    # The state after p02 (as-is artifact): one accepted, one pending.
    s1 = _skill("t1", "accepted")
    s2 = make_skill("traj/forkbench:t1/strong/00/seg01", "content t1b",
                    status="pending")
    p02_rows = [s1.to_dict(), s2.to_dict()]

    # What p04 did to the shared store: both originals merged away, one
    # curation product added -- and its curated artifact holds only that.
    merged = Skill(
        skill_id="bank/merge/0000", content="merged content",
        subtask_id=s1.subtask_id, source_subtasks=[s1.subtask_id],
        status="accepted", merged_from=[s1.skill_id, s2.skill_id])
    for skill in (s1, s2):
        skill.status = "merged"
        store.save(skill, "skills")
    store.save(merged, "skills")
    p04_rows = [merged.to_dict()]

    labels = [f"p{index + 1:02d}:{name}"
              for index, name in enumerate(sequence)]
    done = len(sequence) if finished is None else finished
    for index, (label, name) in enumerate(zip(labels, sequence)):
        if index >= done:
            break
        if name == "learn":
            rows = p02_rows if index == 1 else p04_rows
            _write_bank(run_dir, label, name, rows)
        else:
            role = "baseline" if name == "baseline" else (
                "exam" if name == "exam" else "test")
            _write_phase(run_dir, label, name, role,
                         **{phase_disk.SUMMARY_NAME: {"complete": True}})
    # Source-only records a fork must leave behind.
    (run_dir / "pipeline_log.jsonl").write_text("{}\n", encoding="utf-8")
    (run_dir / "llm_requests").mkdir(exist_ok=True)
    (run_dir / "llm_requests" / "usage.jsonl").write_text(
        "{}\n", encoding="utf-8")
    (run_dir / "work" / "p01_baseline").mkdir(parents=True)
    (run_dir / "phases" / "p02_learn" / "outcomes.jsonl.lock").write_text(
        "", encoding="utf-8")
    return Source(run_dir, {"s1": s1.skill_id, "s2": s2.skill_id,
                            "merge": merged.skill_id}, p02_rows, p04_rows)


def _skill_ids(run_dir):
    return Store(run_dir).ids("skills")


# ---------------------------------------------------------------------------
# The two skill-store modes
# ---------------------------------------------------------------------------

def test_a_fork_before_a_later_learn_phase_rebuilds_the_skill_store(tmp_path):
    source = build_source(tmp_path)
    target = _target(tmp_path, ["baseline", "learn", "exam", "learn", "exam",
                                "learn", "test"])
    run_dir = tmp_path / "fork"

    summary = fork_run(source_run=source.run_dir, config_path=target,
                       run_dir=run_dir, through="p03")

    assert summary["phases"] == ["p01:baseline", "p02:learn", "p03:exam"]
    assert summary["skills"]["mode"] == "rebuilt"
    assert summary["skills"]["from_phase"] == "p02:learn"
    assert summary["skills"]["dropped_later_products"] == [
        source.ids["merge"]]
    # The store is the state after p02: the artifact's rows, not the final
    # store's merged originals and curation product.
    store = Store(run_dir)
    assert _skill_ids(run_dir) == sorted(
        [source.ids["s1"], source.ids["s2"]])
    assert [store.load("skills", row["skill_id"], Skill).to_dict()
            for row in source.p02_rows] == source.p02_rows
    # The phases of the prefix are there, the later ones are not.
    assert sorted(path.name for path in (run_dir / "phases").iterdir()) == [
        "p01_baseline", "p02_learn", "p03_exam"]
    assert summary["stores"] == {"trajectories": 1, "subtasks": 1,
                                 "exams": 0, "attempts": 0}


def test_a_fork_through_the_last_learn_phase_copies_the_store_verbatim(
        tmp_path):
    source = build_source(tmp_path)
    # Same four phases, a different final test: nothing after the prefix
    # but the test phase, which keeps its own stores.
    target = _target(tmp_path, ["baseline", "learn", "exam", "learn", "test"],
                     split={"train": ["t1"], "test": ["t3"],
                            "reserved": ["t2"]})
    run_dir = tmp_path / "fork"

    summary = fork_run(source_run=source.run_dir, config_path=target,
                       run_dir=run_dir, through="p04")

    assert summary["skills"]["mode"] == "verbatim"
    assert summary["skills"]["dropped_later_products"] == []
    assert _skill_ids(run_dir) == _skill_ids(source.run_dir)
    for skill_id in _skill_ids(run_dir):
        assert (Store(run_dir).path_for("skills", skill_id).read_bytes()
                == Store(source.run_dir).path_for(
                    "skills", skill_id).read_bytes())


def test_a_fork_leaves_the_source_records_behind(tmp_path):
    source = build_source(tmp_path)
    target = _target(tmp_path, ["baseline", "learn", "exam", "learn", "exam",
                                "learn", "test"])
    run_dir = tmp_path / "fork"

    fork_run(source_run=source.run_dir, config_path=target, run_dir=run_dir,
             through="p03")

    for name in ("manifest.json", "pipeline_log.jsonl", "work",
                 LOCK_NAME):
        assert not (run_dir / name).exists(), name
    assert not list((run_dir / "llm_requests").glob("*.jsonl"))
    assert not list(run_dir.rglob("*.lock"))
    text = (run_dir / "FORKED_FROM.md").read_text(encoding="utf-8")
    assert "p01:baseline, p02:learn, p03:exam" in text
    # The source run is untouched.
    assert (source.run_dir / "manifest.json").is_file()
    assert (source.run_dir / "phases" / "p04_learn").is_dir()


# ---------------------------------------------------------------------------
# What a fork refuses
# ---------------------------------------------------------------------------

def _fork(tmp_path, source, target, through):
    return fork_run(source_run=source.run_dir, config_path=target,
                    run_dir=tmp_path / "fork", through=through)


_EXTENDED = ["baseline", "learn", "exam", "learn", "exam", "learn", "test"]


def test_the_target_must_start_with_the_same_phases(tmp_path):
    source = build_source(tmp_path)
    target = _target(tmp_path, ["baseline", "learn", "learn", "exam", "test"])
    with pytest.raises(ConfigError, match="must start with"):
        _fork(tmp_path, source, target, "p03")


def test_a_prefix_phase_defined_differently_is_refused(tmp_path):
    source = build_source(tmp_path)
    config = _config_dict(_EXTENDED)
    config["phases"]["learn"]["review"]["K"] = 9
    path = tmp_path / "changed.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ConfigError, match="defined differently"):
        _fork(tmp_path, source, path, "p03")


def test_a_changed_global_key_is_refused(tmp_path):
    source = build_source(tmp_path)
    target = _target(
        tmp_path, _EXTENDED,
        models={"executor_strong": "other-big", "executor_weak": "weak-7b"})
    with pytest.raises(ConfigError, match="models"):
        _fork(tmp_path, source, target, "p03")


def test_a_changed_train_split_is_refused_but_the_test_split_may_move(
        tmp_path):
    source = build_source(tmp_path)
    moved = _target(tmp_path, _EXTENDED, name="moved.yaml",
                    split={"train": ["t1"], "test": ["t3"],
                           "reserved": ["t2"]})
    assert _fork(tmp_path, source, moved, "p03")["phases"]
    other = _target(tmp_path, _EXTENDED, name="other.yaml",
                    split={"train": ["t2"], "test": ["t1"],
                           "reserved": ["t3"]})
    with pytest.raises(ConfigError, match="split"):
        fork_run(source_run=source.run_dir, config_path=other,
                 run_dir=tmp_path / "fork2", through="p03")


def test_the_last_learn_phase_cannot_stop_being_the_last(tmp_path):
    # p02 is the source's only learn phase, so it curates; in the target
    # another learn phase follows it. Both sequences still have a check
    # after p02, so only the curation rule can object.
    source = build_source(tmp_path, sequence=["baseline", "learn", "exam",
                                              "test"])
    target = _target(tmp_path, ["baseline", "learn", "exam", "learn", "exam",
                                "test"])
    with pytest.raises(ConfigError, match="last learn phase"):
        _fork(tmp_path, source, target, "p03")


def test_a_last_check_cannot_become_an_earlier_one(tmp_path):
    source = build_source(tmp_path)
    target = _target(tmp_path, _EXTENDED)
    # p04 had no check after it in the source; the target adds one.
    with pytest.raises(ConfigError, match="later check"):
        _fork(tmp_path, source, target, "p04")


def test_a_learn_phase_cannot_lose_its_later_check(tmp_path):
    source = build_source(tmp_path)
    target = _target(tmp_path, ["baseline", "learn", "test"])
    # p02 learned with an exam still to come in the source; the target
    # has none after it.
    with pytest.raises(ConfigError, match="later check"):
        _fork(tmp_path, source, target, "p02")


def test_a_later_execute_phase_that_writes_the_shared_stores_is_refused(
        tmp_path):
    source = build_source(tmp_path, sequence=_EXTENDED)
    target = _target(tmp_path, _EXTENDED)
    with pytest.raises(ConfigError, match="cannot be separated"):
        _fork(tmp_path, source, target, "p03")


def test_an_unfinished_prefix_phase_is_refused(tmp_path):
    source = build_source(tmp_path, finished=2)
    target = _target(tmp_path, _EXTENDED)
    with pytest.raises(ConfigError, match="not finished"):
        _fork(tmp_path, source, target, "p03")


def test_a_running_source_is_refused(tmp_path):
    source = build_source(tmp_path)
    (source.run_dir / LOCK_NAME).write_text("{}", encoding="utf-8")
    target = _target(tmp_path, _EXTENDED)
    with pytest.raises(ConfigError, match="idle"):
        _fork(tmp_path, source, target, "p03")


def test_a_non_empty_target_directory_is_refused(tmp_path):
    source = build_source(tmp_path)
    target = _target(tmp_path, _EXTENDED)
    (tmp_path / "fork").mkdir()
    (tmp_path / "fork" / "stray.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ConfigError, match="not empty"):
        _fork(tmp_path, source, target, "p03")


@pytest.mark.parametrize("through", ["5", "p5x", "p00", "p06", "P03"])
def test_a_malformed_or_out_of_range_through_is_refused(tmp_path, through):
    source = build_source(tmp_path)
    target = _target(tmp_path, _EXTENDED)
    with pytest.raises(ConfigError, match="through"):
        _fork(tmp_path, source, target, through)


def test_an_altered_bank_artifact_is_refused(tmp_path):
    source = build_source(tmp_path)
    path = phase_bank_dir(source.run_dir, "p02:learn") / BANK_NAME
    rows = source.p02_rows[:1]
    write_jsonl(path, rows)
    target = _target(tmp_path, _EXTENDED)
    with pytest.raises(ConfigError, match="digest"):
        _fork(tmp_path, source, target, "p03")


def test_a_stored_skill_that_is_not_provably_later_is_refused(tmp_path):
    source = build_source(tmp_path)
    Store(source.run_dir).save(_skill("t9", "discarded"), "skills")
    target = _target(tmp_path, _EXTENDED)
    with pytest.raises(ConfigError, match="neither in the bank artifact"):
        _fork(tmp_path, source, target, "p03")


def test_a_requirement_subtask_written_after_the_prefix_is_refused(tmp_path):
    source = build_source(tmp_path)
    Store(source.run_dir).save(Subtask(
        subtask_id="req/abc/seg00", task_id="forkbench:t1",
        trajectory_id="traj/forkbench:t1/strong/00", source="weak",
        trajectory_success=False, segment_index=0, step_range=(0, 1),
        label="req", description="a requirement", success=False),
        "subtasks")
    target = _target(tmp_path, _EXTENDED)
    with pytest.raises(ConfigError, match="written after the prefix"):
        _fork(tmp_path, source, target, "p03")


# ---------------------------------------------------------------------------
# The seams
# ---------------------------------------------------------------------------

def test_the_completion_record_names_match_the_phase_drivers():
    assert fork_module._EXECUTE_SUMMARY_NAME == phase_disk.SUMMARY_NAME
    assert fork_module._LEARN_SUMMARY_NAME == phase_learn.SUMMARY_NAME


def test_the_forked_target_config_still_loads(tmp_path):
    source = build_source(tmp_path)
    target = _target(tmp_path, _EXTENDED)
    assert isinstance(load_config(str(target)), ExperimentConfig)
    assert _fork(tmp_path, source, target, "p03")["skills"]["mode"] == "rebuilt"


def test_the_cli_prints_one_json_line_and_exits_one_on_a_refusal(
        capsys, tmp_path):
    source = build_source(tmp_path)
    target = _target(tmp_path, _EXTENDED)
    argv = ["fork-run", "--source-run", str(source.run_dir), "--config",
            str(target), "--run-dir", str(tmp_path / "fork"), "--through",
            "p03"]

    assert main(argv) == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith("fork-run: ")
    assert json.loads(line[len("fork-run: "):])["phases"][-1] == "p03:exam"

    assert main(argv) == 1  # the directory is no longer empty
    assert "not empty" in capsys.readouterr().err
