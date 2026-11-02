"""CLI wiring of the four analysis subcommands: import-bank, judge-skills,
exam-replay and paper-tables. The stages are tested on their own; what is
pinned here is the argument mapping, the handles built from the run's frozen
config, and the error contract."""

import json

import pytest

from types import SimpleNamespace

from expedistill import cli
from expedistill.infra.reliability import ConfigError, InfrastructureError
from expedistill.prompts.learning_context import LearningContext
from tests.helpers.skill_use_runs import write_exam_source_run

VOCABULARY = LearningContext(name="fake", slots={"agent_kind": "a fake agent"})


@pytest.fixture
def source_run(monkeypatch, tmp_path):
    """A finished run whose benchmark has no plugin: the CLI only asks the
    plugin for its learning vocabulary, so that lookup is stubbed."""
    monkeypatch.setattr(
        cli, "resolve_plugin",
        lambda name: SimpleNamespace(learning_context=VOCABULARY))
    run_dir = tmp_path / "source"
    write_exam_source_run(run_dir)
    return run_dir


def _last_json(capsys, prefix):
    line = capsys.readouterr().out.splitlines()[-1]
    assert line.startswith(prefix)
    return json.loads(line[len(prefix):])


# ---------------------------------------------------------------------------
# import-bank
# ---------------------------------------------------------------------------

def test_import_bank_maps_its_arguments_and_hides_the_id_list(
        monkeypatch, capsys, tmp_path):
    calls = {}

    def fake_import(source, source_format, run_dir, *, origin):
        calls.update(source=source, source_format=source_format,
                     run_dir=run_dir, origin=origin)
        return {"n_skills": 2, "skill_ids": ["a", "b"], "origin": origin}

    monkeypatch.setattr(cli, "import_external_bank", fake_import)
    code = cli.main(["import-bank", "--source", str(tmp_path / "s"),
                     "--source-format", "notes_jsonl",
                     "--run-dir", str(tmp_path / "r"), "--origin", "expel"])
    assert code == 0
    assert calls == {"source": tmp_path / "s", "source_format": "notes_jsonl",
                     "run_dir": tmp_path / "r", "origin": "expel"}
    printed = _last_json(capsys, "import-bank: ")
    assert printed == {"n_skills": 2, "origin": "expel"}


def test_import_bank_refuses_an_unknown_format(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["import-bank", "--source", "s", "--source-format", "pdf",
                  "--run-dir", str(tmp_path), "--origin", "x"])
    assert excinfo.value.code == 2


def test_import_bank_errors_exit_one(monkeypatch, capsys, tmp_path):
    def fake_import(*args, **kwargs):
        raise ConfigError("the run directory already holds a manifest")

    monkeypatch.setattr(cli, "import_external_bank", fake_import)
    code = cli.main(["import-bank", "--source", "s",
                     "--source-format", "skill_folder",
                     "--run-dir", str(tmp_path), "--origin", "x"])
    assert code == 1
    assert "already holds a manifest" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# judge-skills and exam-replay build their handles from the frozen config
# ---------------------------------------------------------------------------

class _Recorder:
    def __init__(self):
        self.models = []

    def __call__(self, model_name, timeout_sec=300.0):
        self.models.append((model_name, timeout_sec))
        return object()


def test_judge_skills_uses_the_runs_frozen_judge_and_budgets(
        monkeypatch, capsys, tmp_path, source_run):
    clients, seen = _Recorder(), {}
    monkeypatch.setattr(cli, "_default_client", clients)

    def fake_judge(run_dir, phase, out_dir, handle, **kwargs):
        seen.update(run_dir=run_dir, phase=phase, out_dir=out_dir,
                    handle=handle, **kwargs)
        return {"judged_trials": 3}

    monkeypatch.setattr(cli, "judge_phase", fake_judge)
    out = tmp_path / "out"
    code = cli.main(["judge-skills", "--run-dir", str(source_run),
                     "--out-dir", str(out), "--phase", "p06:test",
                     "--workers", "4", "--bank-phase", "p04:learn",
                     "--llm-timeout", "77"])
    assert code == 0
    handle = seen["handle"]
    # No --judge-model: the run's frozen judge role (defaults to the strong
    # model) answers, under the run's own output budget.
    assert handle.role == "judge" and handle.name == "big-model"
    assert handle.max_tokens == 512
    assert clients.models == [("big-model", 77.0)]
    assert handle.run_dir == out
    # The benchmark's vocabulary rides every analysis handle, like the
    # handles of a real run.
    assert handle.prompt_defaults == {"agent_kind": "a fake agent"}
    assert (seen["phase"], seen["workers"], seen["arm"],
            seen["bank_phase_label"]) == ("p06:test", 4, "with_bank",
                                          "p04:learn")
    assert _last_json(capsys, "judge-skills: ") == {"judged_trials": 3}


def test_judge_skills_accepts_another_judge_model(
        monkeypatch, tmp_path, source_run):
    clients, seen = _Recorder(), {}
    monkeypatch.setattr(cli, "_default_client", clients)
    monkeypatch.setattr(
        cli, "judge_phase",
        lambda run_dir, phase, out_dir, handle, **kw:
        seen.update(handle=handle) or {})
    cli.main(["judge-skills", "--run-dir", str(source_run),
              "--out-dir", str(tmp_path / "out"), "--phase", "p06:test",
              "--judge-model", "other-judge"])
    assert seen["handle"].name == "other-judge"


def test_exam_replay_builds_two_handles_and_reads_the_exam_cap(
        monkeypatch, capsys, tmp_path, source_run):
    clients, seen = _Recorder(), {}
    monkeypatch.setattr(cli, "_default_client", clients)

    def fake_replay(run_dir, out_dir, answerer, judge, **kwargs):
        seen.update(answerer=answerer, judge=judge, **kwargs)
        return {"answered": 4}

    monkeypatch.setattr(cli, "replay_exams", fake_replay)
    code = cli.main(["exam-replay", "--run-dir", str(source_run),
                     "--out-dir", str(tmp_path / "out"),
                     "--answerer-model", "student-model"])
    assert code == 0
    assert (seen["answerer"].role, seen["answerer"].name) \
        == ("answerer", "student-model")
    assert (seen["judge"].role, seen["judge"].name) == ("judge", "big-model")
    # The cap comes from the source run's exam phase (the helper's is 3).
    assert seen["max_skill_reads"] == 3
    assert seen["bank_phase_label"] is None and seen["workers"] == 1
    assert _last_json(capsys, "exam-replay: ") == {"answered": 4}


def test_exam_replay_cap_can_be_overridden(monkeypatch, tmp_path, source_run):
    monkeypatch.setattr(cli, "_default_client", _Recorder())
    seen = {}
    monkeypatch.setattr(
        cli, "replay_exams",
        lambda run_dir, out_dir, answerer, judge, **kw: seen.update(kw) or {})
    cli.main(["exam-replay", "--run-dir", str(source_run),
              "--out-dir", str(tmp_path / "out"),
              "--answerer-model", "m", "--max-skill-reads", "1"])
    assert seen["max_skill_reads"] == 1


def test_a_run_without_a_manifest_is_refused(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_default_client", _Recorder())
    code = cli.main(["judge-skills", "--run-dir", str(tmp_path / "nowhere"),
                     "--out-dir", str(tmp_path / "out"), "--phase", "p01:x"])
    assert code == 1
    assert "no manifest.json" in capsys.readouterr().err


def test_analysis_infra_exhaustion_exits_three(
        monkeypatch, capsys, tmp_path, source_run):
    monkeypatch.setattr(cli, "_default_client", _Recorder())

    def fake_replay(*args, **kwargs):
        raise InfrastructureError("3 exam(s) are pending on infrastructure")

    monkeypatch.setattr(cli, "replay_exams", fake_replay)
    code = cli.main(["exam-replay", "--run-dir", str(source_run),
                     "--out-dir", str(tmp_path / "out"),
                     "--answerer-model", "m"])
    assert code == cli.EXIT_INFRA_RESUMABLE
    assert "pending on infrastructure" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# paper-tables
# ---------------------------------------------------------------------------

def test_paper_tables_maps_its_arguments_and_prints_the_markdown(
        monkeypatch, capsys, tmp_path):
    seen = {}

    def fake_run(spec, **kwargs):
        seen.update(spec=spec, **kwargs)
        return "| table |"

    monkeypatch.setattr(cli, "run_paper_tables", fake_run)
    code = cli.main(["paper-tables", "--spec", str(tmp_path / "spec.json"),
                     "--out-md", str(tmp_path / "t.md"),
                     "--n-boot", "500", "--seed", "3",
                     "--metric", "pass_at_n"])
    assert code == 0
    assert seen == {"spec": tmp_path / "spec.json",
                    "out_md": tmp_path / "t.md", "out_json": None,
                    "n_boot": 500, "seed": 3, "metric": "pass_at_n"}
    assert capsys.readouterr().out.strip() == "| table |"


def test_paper_tables_value_errors_exit_two(monkeypatch, capsys, tmp_path):
    def fake_run(spec, **kwargs):
        raise ValueError("arm 'x' has no scored task in common with 'y'")

    monkeypatch.setattr(cli, "run_paper_tables", fake_run)
    code = cli.main(["paper-tables", "--spec", str(tmp_path / "spec.json")])
    assert code == 2
    assert "no scored task in common" in capsys.readouterr().err


def test_paper_tables_refuses_an_unknown_metric(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["paper-tables", "--spec", str(tmp_path / "s.json"),
                  "--metric", "best_of_all"])
    assert excinfo.value.code == 2
