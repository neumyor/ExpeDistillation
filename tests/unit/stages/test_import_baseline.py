"""stages/import_baseline.py: an earlier run's baseline reused in a new run.

The source run is written by hand the way an older run left it: a manifest
(its frozen config and split) and schema-2 baseline trajectories, whose ids
carry no phase. Each identity check gets one counter-example that must
write nothing; the happy path checks the rewritten ids and the untouched
fields; the end-to-end test runs the new run's baseline phase over the
golden stub benchmark and checks that it executes nothing.
"""

import copy
import json

import pytest
import yaml

from expedistill import cli
from expedistill.core.ids import make_trajectory_id
from expedistill.core.models import Trajectory
from expedistill.infra.reliability import ConfigError
from expedistill.infra.store import KIND_DIRS, Store, object_filename, read_json
from expedistill.stages.import_baseline import (
    BASELINE_ORIGIN_NAME,
    import_baseline,
)
from tests.golden.harness import (
    FAKE_BENCH,
    STRONG_MODEL,
    WEAK_MODEL,
    GoldenPlugin,
    config_dict,
    installed_plugin,
)

#: The new config's train list; the source ran one more task.
TRAIN = ["t1", "t2", "t4"]
SOURCE_TRAIN = TRAIN + ["t5"]
LABEL = "p01:baseline"


def _config(**overrides):
    config = config_dict({"delivery": "whole_bank"})
    config.update(overrides)
    return config


def _write_config(tmp_path, name="new.yaml", **overrides):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(_config(**overrides)), encoding="utf-8")
    return path


def _source_record(task, role, attempt, **fields):
    task_id = f"{FAKE_BENCH}:{task}"
    record = {
        "trajectory_id": f"traj/{task_id}/{role}/{attempt:02d}",
        "task_id": task_id, "benchmark": FAKE_BENCH, "model_role": role,
        "model_name": STRONG_MODEL if role == "strong" else WEAK_MODEL,
        "attempt_index": attempt, "skills_injected": None,
        "steps": [{"action": "inspect", "observation": f"{task} state"},
                  {"action": "run", "observation": "done"}],
        "correctness": role == "strong" or task == "t1",
        "meta": {"termination": "terminated", "workdir": "/elsewhere"},
        "schema_version": 2,
    }
    record.update(fields)
    return record


def _write_source(tmp_path, *, depths=(1, 1), records=None, **config):
    """A finished source run: the manifest and the baseline records."""
    run_dir = tmp_path / "source"
    directory = run_dir / KIND_DIRS["trajectories"]
    directory.mkdir(parents=True)
    frozen = {"benchmark": FAKE_BENCH,
              "models": {"executor_strong": STRONG_MODEL,
                         "executor_weak": WEAK_MODEL,
                         "examiner": STRONG_MODEL},
              "n_attempts_strong": depths[0], "n_attempts_weak": depths[1],
              "schema_version": 7}
    frozen.update(config)
    (run_dir / "manifest.json").write_text(json.dumps({
        "benchmark": frozen["benchmark"], "config": frozen,
        "split": {"train": list(SOURCE_TRAIN), "test": ["t3"]}}),
        encoding="utf-8")
    if records is None:
        records = [_source_record(task, role, attempt)
                   for task in SOURCE_TRAIN
                   for role, depth in zip(("strong", "weak"), depths)
                   for attempt in range(depth)]
    for record in records:
        (directory / object_filename(record["trajectory_id"])).write_text(
            json.dumps(record), encoding="utf-8")
    return run_dir


def _import(tmp_path, source, config=None):
    return import_baseline(source_run=source,
                           config_path=config or _write_config(tmp_path),
                           run_dir=tmp_path / "new")


def _wrote_nothing(tmp_path):
    new = tmp_path / "new"
    trajectories = new / KIND_DIRS["trajectories"]
    return not (new / BASELINE_ORIGIN_NAME).exists() and not (
        trajectories.is_dir() and any(trajectories.iterdir()))


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------

def test_the_train_trajectories_land_under_the_baseline_label(tmp_path):
    source = _write_source(tmp_path)

    summary = _import(tmp_path, source)

    store = Store(tmp_path / "new")
    expected = sorted(make_trajectory_id(LABEL, f"{FAKE_BENCH}:{task}", role,
                                         0)
                      for task in TRAIN for role in ("strong", "weak"))
    assert store.ids("trajectories") == expected
    assert summary["trajectories"] == 6 and summary["phase_label"] == LABEL
    assert summary["by_role"] == {"strong": {"count": 3, "successes": 3},
                                  "weak": {"count": 3, "successes": 1}}
    for trajectory_id in expected:
        stored = read_json(store.path_for("trajectories", trajectory_id))
        task_id = stored["task_id"]
        role, attempt = stored["model_role"], stored["attempt_index"]
        original = _source_record(task_id.split(":")[1], role, attempt)
        # Only the id and the schema version change.
        assert stored == {**original, "trajectory_id": trajectory_id,
                          "schema_version": 3}
        Trajectory.from_dict(stored)


def test_the_origin_record_names_the_source_counts_and_digest(tmp_path):
    source = _write_source(tmp_path)

    summary = _import(tmp_path, source)

    text = (tmp_path / "new" / BASELINE_ORIGIN_NAME).read_text("utf-8")
    assert str(source.resolve()) in text
    assert "| strong | 3 | 3 |" in text and "| weak | 3 | 1 |" in text
    assert summary["source_files_sha256"] in text
    # The digest covers the imported files only, so it does not move when
    # the source holds other tasks.
    other = tmp_path / "other"
    other.mkdir()
    assert _import(other, _write_source(other, records=[
        _source_record(task, role, 0) for task in TRAIN
        for role in ("strong", "weak")]))["source_files_sha256"] \
        == summary["source_files_sha256"]


def test_the_cli_prints_one_json_line_and_refuses_a_second_import(
        tmp_path, capsys):
    source = _write_source(tmp_path)
    argv = ["import-baseline", "--source-run", str(source), "--config",
            str(_write_config(tmp_path)), "--run-dir", str(tmp_path / "new")]

    assert cli.main(argv) == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith("import-baseline: ")
    assert json.loads(line[len("import-baseline: "):])["trajectories"] == 6
    assert cli.main(argv) == 1
    assert "already holds" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# One counter-example per identity check; none of them writes anything
# ---------------------------------------------------------------------------

def test_another_benchmark_is_refused(tmp_path):
    source = _write_source(tmp_path, benchmark="otherbench")
    with pytest.raises(ConfigError, match="'otherbench' run"):
        _import(tmp_path, source)
    assert _wrote_nothing(tmp_path)


@pytest.mark.parametrize("role", ["executor_strong", "executor_weak"])
def test_another_executor_model_is_refused(tmp_path, role):
    models = {"executor_strong": STRONG_MODEL, "executor_weak": WEAK_MODEL}
    models[role] = "another-model"
    source = _write_source(tmp_path, models=models)
    with pytest.raises(ConfigError, match="executor models differ"):
        _import(tmp_path, source)
    assert _wrote_nothing(tmp_path)


@pytest.mark.parametrize("depths", [(2, 1), (1, 2)])
def test_another_sampling_depth_is_refused(tmp_path, depths):
    source = _write_source(tmp_path, depths=depths)
    with pytest.raises(ConfigError, match="sampling depths differ"):
        _import(tmp_path, source)
    assert _wrote_nothing(tmp_path)


def test_a_train_task_the_source_did_not_run_is_refused(tmp_path):
    source = _write_source(tmp_path)
    split = copy.deepcopy(_config()["split"])
    split["train"] = TRAIN + ["t9"]
    with pytest.raises(ConfigError, match="not in the source run's train"):
        _import(tmp_path, source, _write_config(tmp_path, split=split))
    assert _wrote_nothing(tmp_path)


def test_a_missing_trajectory_is_refused(tmp_path):
    records = [_source_record(task, role, 0) for task in TRAIN
               for role in ("strong", "weak")][1:]
    source = _write_source(tmp_path, records=records)
    with pytest.raises(ConfigError, match="1 missing"):
        _import(tmp_path, source)
    assert _wrote_nothing(tmp_path)


def test_a_sample_beyond_the_depth_is_refused(tmp_path):
    records = [_source_record(task, role, 0) for task in TRAIN
               for role in ("strong", "weak")]
    records.append(_source_record("t1", "weak", 1))
    source = _write_source(tmp_path, records=records)
    with pytest.raises(ConfigError, match="1 beyond the sampling depth"):
        _import(tmp_path, source)
    assert _wrote_nothing(tmp_path)


def test_a_repeated_sample_is_refused(tmp_path):
    records = [_source_record(task, role, 0) for task in TRAIN
               for role in ("strong", "weak")]
    # A second file whose fields claim the same (task, role, sample).
    twin = _source_record("t1", "weak", 0)
    twin["trajectory_id"] = "traj/fakebench:t1/weak/00"
    source = _write_source(tmp_path, records=records)
    (source / KIND_DIRS["trajectories"] / "twin.json").write_text(
        json.dumps(twin), encoding="utf-8")
    with pytest.raises(ConfigError, match="1 repeated"):
        _import(tmp_path, source)
    assert _wrote_nothing(tmp_path)


def test_a_record_whose_fields_disagree_with_its_id_is_refused(tmp_path):
    records = [_source_record(task, role, 0) for task in TRAIN
               for role in ("strong", "weak")]
    records[0]["attempt_index"] = 1
    source = _write_source(tmp_path, records=records)
    with pytest.raises(ConfigError, match="disagree with its id"):
        _import(tmp_path, source)
    assert _wrote_nothing(tmp_path)


def test_a_source_trajectory_of_another_schema_is_refused(tmp_path):
    records = [_source_record(task, role, 0) for task in TRAIN
               for role in ("strong", "weak")]
    records[0]["schema_version"] = 3
    source = _write_source(tmp_path, records=records)
    with pytest.raises(ConfigError, match="schema-3 trajectory"):
        _import(tmp_path, source)
    assert _wrote_nothing(tmp_path)


def test_a_config_without_a_baseline_is_refused(tmp_path):
    config = _config()
    config["sequence"] = ["test"]
    config["phases"] = {"test": config["phases"]["test"]}
    path = tmp_path / "test_only.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ConfigError, match="no baseline to import"):
        _import(tmp_path, _write_source(tmp_path), path)
    assert _wrote_nothing(tmp_path)


@pytest.mark.parametrize("taken", ["manifest", "trajectory"])
def test_a_run_directory_already_in_use_is_refused(tmp_path, taken):
    new = tmp_path / "new"
    if taken == "manifest":
        new.mkdir()
        (new / "manifest.json").write_text("{}", encoding="utf-8")
    else:
        directory = new / KIND_DIRS["trajectories"]
        directory.mkdir(parents=True)
        (directory / "x.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigError, match="already holds"):
        _import(tmp_path, _write_source(tmp_path))
    assert not (new / BASELINE_ORIGIN_NAME).exists()


# ---------------------------------------------------------------------------
# The new run's baseline phase over the imported trajectories
# ---------------------------------------------------------------------------

class CountingPlugin(GoldenPlugin):
    """The golden stub benchmark, counting the units it is asked to run."""

    def __init__(self):
        self.runs = []

    def run(self, task, handle, skills_block, workdir):
        self.runs.append((handle.role, task.task_id))
        return super().run(task, handle, skills_block, workdir)


class CountingClient:
    """A model client that must never be asked anything."""

    calls = []

    def complete(self, *args, **kwargs):
        CountingClient.calls.append(args)
        raise AssertionError("the imported baseline made a model call")


def test_the_baseline_phase_runs_nothing_and_still_reports(tmp_path):
    source = _write_source(tmp_path)
    config = _write_config(tmp_path)
    _import(tmp_path, source, config)
    run_dir = tmp_path / "new"
    CountingClient.calls = []

    with installed_plugin(CountingPlugin()) as plugin:
        summary = cli.run_experiment(
            config_path=config, run_dir=run_dir, until_phase=0,
            make_client=lambda model_name: CountingClient())

    assert plugin.runs == [] and CountingClient.calls == []
    assert summary["role"] == "baseline" and summary["complete"] is True
    phase_dir = run_dir / "phases" / "p01_baseline"
    sheets = sorted((phase_dir / "answer_sheets").glob("*.json"))
    assert len(sheets) == 6
    verdicts = sorted(read_json(path)["grading"]["verdict"]
                      for path in sheets)
    assert verdicts == ["failure"] * 2 + ["success"] * 4
    assert read_json(phase_dir / "phase_summary.json") == summary
