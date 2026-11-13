"""fork_run tests: a synthetic finished source run, forked at a phase.

No real run directory is read: the fixture writes a small finished source
(manifest, phase records, the run-level object stores and ledgers) in
tmp_path. The source sequence is ``baseline, learn, practice, learn,
test``. Each learn phase cuts segments, adds a subtask type and writes
cards; p04 also rewrites p02's card, so the final skill store is the state
after p04 -- which is exactly what a fork through p03 must NOT inherit.
"""

import copy
import json

import pytest
import yaml

from expedistill.cli import main
from expedistill.composites.locate import (
    SEGMENT_TYPES_PATH,
    SUBTASK_TYPES_PATH,
)
from expedistill.composites.phase_bank import (
    BANK_NAME,
    SUMMARY_NAME as BANK_SUMMARY_NAME,
    phase_bank_dir,
)
from expedistill.composites.provenance import PROVENANCE_NAME
from expedistill.composites.segment_checked import AUDIT_PATH
from expedistill.composites.shortfall import stage1_shortfall_path
from expedistill.core import ids
from expedistill.core.config import ExperimentConfig, load_config
from expedistill.core.models import Skill, Subtask, SubtaskType, Trajectory
from expedistill.core.trace import Provenance, TraceRef
from expedistill.infra.reliability import ConfigError
from expedistill.infra.runlock import LOCK_NAME
from expedistill.infra.store import (
    Store,
    append_jsonl,
    read_jsonl,
    write_json,
    write_jsonl,
)
from expedistill.inject import bank_reference
from expedistill.stages import fork_run as fork_module
from expedistill.stages import phase_learn
from expedistill.stages.fork_run import fork_run
from expedistill.stages.phase_execute import phase_disk
from tests.helpers.configs import reference_method
from tests.helpers.skills import make_skill

_SOURCE_SEQUENCE = ["baseline", "learn", "practice", "learn", "test"]
_EXTENDED = ["baseline", "learn", "practice", "learn", "practice", "learn",
             "test"]
_BASE = {
    "benchmark": "forkbench",
    "split": {"train": ["t1"], "val": ["t4"], "test": ["t2"],
              "reserved": ["t3"]},
    "models": {"executor_strong": "big-model", "executor_weak": "weak-7b"},
    "n_attempts_strong": 1,
    "n_attempts_weak": 1,
    "schema_version": 9,
    "token_budgets": {"default_max_output_tokens": 512},
}
_TASK = "forkbench:t1"


def _config_dict(sequence, **overrides):
    config = copy.deepcopy(_BASE)
    phases = reference_method()["phases"]
    config["sequence"] = list(sequence)
    config["phases"] = {name: phases[name]
                        for name in dict.fromkeys(sequence)}
    for key, value in overrides.items():
        config[key] = value
    return config


def _target(tmp_path, sequence, name="target.yaml", **overrides):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(_config_dict(sequence, **overrides),
                                   sort_keys=True), encoding="utf-8")
    return path


def _labels(sequence):
    return [f"p{index + 1:02d}:{name}" for index, name in enumerate(sequence)]


def _write_phase(run_dir, label, role, **files):
    directory = phase_bank_dir(run_dir, label)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "phase.json",
               {"label": label, "name": label.split(":")[1], "role": role})
    for filename, payload in files.items():
        write_json(directory / filename, payload)
    return directory


def _write_bank(run_dir, label, skills):
    rows = [skill.to_dict() for skill in skills]
    directory = _write_phase(
        run_dir, label, "learn",
        **{phase_learn.SUMMARY_NAME: {"bank": {"size": len(rows)}}})
    write_jsonl(directory / BANK_NAME, rows)
    write_json(directory / BANK_SUMMARY_NAME,
               {"members": [row["skill_id"] for row in rows],
                "size": len(rows), "bank_reference": bank_reference(rows)})


def _provenance(run_dir, label, skill, cause):
    append_jsonl(phase_bank_dir(run_dir, label) / PROVENANCE_NAME,
                 Provenance(skill_id=skill.skill_id, version=skill.version,
                            phase_label=label, cause=cause,
                            sources=[TraceRef("p01:baseline", "sheet", 0)]
                            ).to_dict())


def _trajectory(store, label, role):
    trajectory = Trajectory(
        trajectory_id=ids.make_trajectory_id(label, _TASK, role, 0),
        task_id=_TASK, benchmark="forkbench", model_role=role,
        model_name="big-model" if role == "strong" else "weak-7b",
        attempt_index=0, steps=[], correctness=role == "strong", meta={})
    store.save(trajectory, "trajectories")
    return trajectory.trajectory_id


def _subtask(store, trajectory_id, cut_by):
    subtask = Subtask(
        subtask_id=ids.make_subtask_id(trajectory_id, 0),
        phase_label=cut_by, task_id=_TASK, trajectory_id=trajectory_id,
        source=ids.parse_trajectory_id(trajectory_id).role,
        trajectory_success=True, segment_index=0, step_range=(0, 1),
        label="seg", description="a segment", success=True)
    store.save(subtask, "subtasks")
    return subtask.subtask_id


class Source:
    """The built source run and the ids the assertions name."""

    def __init__(self, run_dir, trajectories, subtasks, p02_bank):
        self.run_dir = run_dir
        self.trajectories = trajectories
        self.subtasks = subtasks
        self.p02_bank = p02_bank


def build_source(tmp_path, sequence=_SOURCE_SEQUENCE, finished=None
                 ) -> Source:
    run_dir = tmp_path / "source"
    config = ExperimentConfig.from_dict(_config_dict(sequence))
    run_dir.mkdir()
    write_json(run_dir / "manifest.json",
               {"benchmark": "forkbench", "config": config.to_dict()})
    store = Store(run_dir)
    labels = _labels(sequence)
    done = len(sequence) if finished is None else finished
    trajectories, subtasks = {}, {}
    bank = {}
    p02_bank = []
    learn_seen = 0
    pending_failures = []
    for index, (label, name) in enumerate(zip(labels, sequence)):
        if index >= done:
            break
        if name in ("baseline", "practice"):
            roles = ("strong", "weak") if name == "baseline" else ("weak",)
            for role in roles:
                trajectories[(label, role)] = _trajectory(store, label, role)
            pending_failures = [trajectories[(label, "weak")]]
            if name == "baseline":
                pending_failures.append(trajectories[(label, "strong")])
            _write_phase(run_dir, label, name,
                         **{phase_disk.SUMMARY_NAME: {"complete": True}})
        elif name == "learn":
            learn_seen += 1
            type_id = ids.make_type_id(learn_seen)
            for trajectory_id in pending_failures:
                subtasks[(label, trajectory_id)] = _subtask(
                    store, trajectory_id, label)
                append_jsonl(run_dir / SEGMENT_TYPES_PATH, {
                    "subtask_id": subtasks[(label, trajectory_id)],
                    "type_id": type_id, "phase_label": label})
            append_jsonl(run_dir / SUBTASK_TYPES_PATH, SubtaskType(
                type_id=type_id, name=f"type {learn_seen}",
                entry_state="holding the object", phase_label=label
            ).to_dict())
            append_jsonl(run_dir / AUDIT_PATH, {
                "trajectory_id": pending_failures[0], "status": "skipped",
                "reason": "empty trajectory"})
            append_jsonl(run_dir / "failed_units.jsonl", {
                "unit_id": f"diag/{label}", "stage": label,
                "error_class": "ModelOutputError", "message": "x"})
            if learn_seen > 1:
                # A later learn phase rewrites the earlier card's content.
                first = bank[ids.make_skill_id(ids.make_type_id(1))]
                first.version += 1
                first.not_applicable_when = "rewritten"
                _provenance(run_dir, label, first, "revise_content")
            card = make_skill(type_id, f"card of {type_id}")
            bank[card.skill_id] = card
            _provenance(run_dir, label, card, "add")
            for skill in bank.values():
                store.save(skill, "skills")
            _write_bank(run_dir, label, list(bank.values()))
            if learn_seen == 1:
                p02_bank = [skill.to_dict() for skill in bank.values()]
            pending_failures = []
        else:
            _write_phase(run_dir, label, "test",
                         **{phase_disk.SUMMARY_NAME: {"complete": True}})
    write_json(stage1_shortfall_path(run_dir), {"unit_ids": []})
    # Source-only records a fork must leave behind.
    (run_dir / "pipeline_log.jsonl").write_text("{}\n", encoding="utf-8")
    (run_dir / "llm_requests").mkdir(exist_ok=True)
    (run_dir / "llm_requests" / "usage.jsonl").write_text(
        "{}\n", encoding="utf-8")
    (run_dir / "work" / "p01_baseline").mkdir(parents=True)
    (run_dir / "phases" / "p02_learn" / "outcomes.jsonl.lock").write_text(
        "", encoding="utf-8")
    return Source(run_dir, trajectories, subtasks, p02_bank)


def _fork(tmp_path, source, target, through):
    return fork_run(source_run=source.run_dir, config_path=target,
                    run_dir=tmp_path / "fork", through=through)


# ---------------------------------------------------------------------------
# What a fork copies
# ---------------------------------------------------------------------------

def test_a_fork_keeps_only_the_objects_its_prefix_created(tmp_path):
    source = build_source(tmp_path)
    target = _target(tmp_path, _EXTENDED)
    run_dir = tmp_path / "fork"

    summary = _fork(tmp_path, source, target, "p03")

    assert summary["phases"] == ["p01:baseline", "p02:learn", "p03:practice"]
    store = Store(run_dir)
    assert store.ids("trajectories") == sorted(source.trajectories.values())
    # p03's failure was cut by p04, outside the prefix.
    assert store.ids("subtasks") == sorted(
        subtask_id for (label, _), subtask_id in source.subtasks.items()
        if label == "p02:learn")
    for path in (SUBTASK_TYPES_PATH, SEGMENT_TYPES_PATH):
        rows = read_jsonl(run_dir / path)
        assert rows and {row["phase_label"] for row in rows} == {"p02:learn"}
    audit = read_jsonl(run_dir / AUDIT_PATH)
    assert [row["trajectory_id"] for row in audit] == [
        source.trajectories[("p01:baseline", "weak")]]
    assert [row["stage"] for row in read_jsonl(
        run_dir / "failed_units.jsonl")] == ["p02:learn"]
    assert stage1_shortfall_path(run_dir).is_file()
    assert summary["stores"] == {
        "trajectories": 3, "subtasks": 2, "subtask_types": 1,
        "segment_types": 2, "segmentation_audit": 1, "failed_units": 1}
    assert sorted(path.name for path in (run_dir / "phases").iterdir()) == [
        "p01_baseline", "p02_learn", "p03_practice"]


def test_the_skill_store_is_the_last_prefix_learn_artifact(tmp_path):
    source = build_source(tmp_path)
    run_dir = tmp_path / "fork"

    summary = _fork(tmp_path, source, _target(tmp_path, _EXTENDED), "p03")

    assert summary["skills"]["from_phase"] == "p02:learn"
    # p04's new card is dropped; p02's card is back at its p02 version.
    assert summary["skills"]["dropped_later_products"] == [
        ids.make_skill_id(ids.make_type_id(2))]
    store = Store(run_dir)
    assert [store.load("skills", row["skill_id"], Skill).to_dict()
            for row in source.p02_bank] == source.p02_bank
    assert store.ids("skills") == [row["skill_id"]
                                   for row in source.p02_bank]


def test_a_fork_through_the_last_learn_phase_keeps_the_final_store(
        tmp_path):
    source = build_source(tmp_path)
    target = _target(tmp_path, _SOURCE_SEQUENCE,
                     split={"train": ["t1"], "val": ["t4"], "test": ["t3"],
                            "reserved": ["t2"]})
    run_dir = tmp_path / "fork"

    summary = _fork(tmp_path, source, target, "p04")

    assert summary["skills"]["from_phase"] == "p04:learn"
    assert summary["skills"]["dropped_later_products"] == []
    assert Store(run_dir).ids("skills") == Store(source.run_dir).ids("skills")
    for skill_id in Store(run_dir).ids("skills"):
        assert (Store(run_dir).load("skills", skill_id, Skill)
                == Store(source.run_dir).load("skills", skill_id, Skill))
    assert summary["stores"]["subtasks"] == 3


def test_a_fork_through_the_baseline_keeps_no_card(tmp_path):
    source = build_source(tmp_path)

    summary = _fork(tmp_path, source, _target(tmp_path, _EXTENDED), "p01")

    assert summary["skills"]["from_phase"] is None
    assert summary["skills"]["count"] == 0
    assert len(summary["skills"]["dropped_later_products"]) == 2
    assert Store(tmp_path / "fork").ids("skills") == []
    assert summary["stores"]["subtasks"] == 0
    assert summary["stores"]["trajectories"] == 2


def test_a_fork_leaves_the_source_records_behind(tmp_path):
    source = build_source(tmp_path)
    run_dir = tmp_path / "fork"

    _fork(tmp_path, source, _target(tmp_path, _EXTENDED), "p03")

    for name in ("manifest.json", "pipeline_log.jsonl", "work",
                 LOCK_NAME):
        assert not (run_dir / name).exists(), name
    assert not list((run_dir / "llm_requests").glob("*.jsonl"))
    assert not list(run_dir.rglob("*.lock"))
    text = (run_dir / "FORKED_FROM.md").read_text(encoding="utf-8")
    assert "p01:baseline, p02:learn, p03:practice" in text
    assert (source.run_dir / "manifest.json").is_file()
    assert (source.run_dir / "phases" / "p04_learn").is_dir()


# ---------------------------------------------------------------------------
# What a fork refuses
# ---------------------------------------------------------------------------

def test_the_target_must_start_with_the_same_phases(tmp_path):
    source = build_source(tmp_path)
    target = _target(tmp_path, ["baseline", "learn", "test"])
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
                    split={"train": ["t1"], "val": ["t4"], "test": ["t3"],
                           "reserved": ["t2"]})
    assert _fork(tmp_path, source, moved, "p03")["phases"]
    other = _target(tmp_path, _EXTENDED, name="other.yaml",
                    split={"train": ["t2"], "val": ["t4"], "test": ["t1"],
                           "reserved": ["t3"]})
    with pytest.raises(ConfigError, match="split"):
        fork_run(source_run=source.run_dir, config_path=other,
                 run_dir=tmp_path / "fork2", through="p03")


def test_the_val_list_matters_only_to_a_prefix_that_practises_on_it(
        tmp_path):
    # The source practises on train: its val list acts on nothing copied.
    source = build_source(tmp_path)
    moved = _target(tmp_path, _EXTENDED, name="moved.yaml",
                    split={"train": ["t1"], "val": ["t3"], "test": ["t2"],
                           "reserved": ["t4"]})
    assert _fork(tmp_path, source, moved, "p03")["phases"]


def test_a_changed_val_list_is_refused_under_a_val_practice(tmp_path):
    config = _config_dict(_SOURCE_SEQUENCE)
    config["phases"]["practice"]["tasks"] = "val"
    run_dir = tmp_path / "source"
    run_dir.mkdir()
    write_json(run_dir / "manifest.json", {
        "benchmark": "forkbench",
        "config": ExperimentConfig.from_dict(config).to_dict()})
    for label, role in (("p01:baseline", "baseline"),
                        ("p03:practice", "practice")):
        _write_phase(run_dir, label, role,
                     **{phase_disk.SUMMARY_NAME: {"complete": True}})
    _write_bank(run_dir, "p02:learn", [])
    moved = copy.deepcopy(config)
    moved["sequence"] = list(_EXTENDED)
    moved["split"] = {"train": ["t1"], "val": ["t3"], "test": ["t2"],
                      "reserved": ["t4"]}
    path = tmp_path / "moved.yaml"
    path.write_text(yaml.safe_dump(moved), encoding="utf-8")
    with pytest.raises(ConfigError, match="split"):
        fork_run(source_run=run_dir, config_path=path,
                 run_dir=tmp_path / "fork", through="p03")


def test_an_unfinished_prefix_phase_is_refused(tmp_path):
    source = build_source(tmp_path, finished=2)
    with pytest.raises(ConfigError, match="not finished"):
        _fork(tmp_path, source, _target(tmp_path, _EXTENDED), "p03")


def test_a_running_source_is_refused(tmp_path):
    source = build_source(tmp_path)
    (source.run_dir / LOCK_NAME).write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigError, match="idle"):
        _fork(tmp_path, source, _target(tmp_path, _EXTENDED), "p03")


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
    with pytest.raises(ConfigError, match="through"):
        _fork(tmp_path, source, _target(tmp_path, _EXTENDED), through)


def test_an_altered_bank_artifact_is_refused(tmp_path):
    source = build_source(tmp_path)
    path = phase_bank_dir(source.run_dir, "p02:learn") / BANK_NAME
    write_jsonl(path, [])
    with pytest.raises(ConfigError, match="digest"):
        _fork(tmp_path, source, _target(tmp_path, _EXTENDED), "p03")


def test_a_stored_skill_not_written_after_the_prefix_is_refused(tmp_path):
    source = build_source(tmp_path)
    Store(source.run_dir).save(make_skill(ids.make_type_id(9)), "skills")
    with pytest.raises(ConfigError, match="not written after it"):
        _fork(tmp_path, source, _target(tmp_path, _EXTENDED), "p03")


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
    assert _fork(tmp_path, source, target, "p03")["skills"]["from_phase"] \
        == "p02:learn"


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
    assert json.loads(line[len("fork-run: "):])["phases"][-1] == \
        "p03:practice"

    assert main(argv) == 1  # the directory is no longer empty
    assert "not empty" in capsys.readouterr().err
