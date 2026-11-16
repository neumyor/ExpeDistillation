"""CLI (layer 8) tests: run/summary wiring and the error contract.

``run`` is tested against a monkeypatched run_experiment (the sequence
loop) and ``summary`` against a fabricated manifest + ledger. The
loop's own behavior (fake drivers over a fake benchmark) is covered by
tests/unit/test_cli_run_loop.py.
"""

import json
from pathlib import Path

import pytest

from expedistill import cli
from expedistill.infra.manifest import FrozenProtocolChanged
from expedistill.infra.reliability import (
    CodeBugError,
    ConfigError,
    InfrastructureError,
    ProviderRefusalError,
)
from expedistill.infra.runlock import RunLockedError

#: The fabricated runs' frozen sequence and the completeness checklist
#: derived from it (cli._expected_labels reads the manifest, never a
#: code constant).
SEQUENCE = ["baseline", "learn", "test"]
EXPECTED_LABELS = ["p00:freeze", "p01:baseline", "p02:learn", "p03:test"]


def _fabricate_ledger(run_dir: Path, labels) -> None:
    """Write a manifest with the fixture sequence and one pipeline_log
    line for every given label."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(json.dumps(
        {"benchmark": "fakebench",
         "config": {"sequence": SEQUENCE, "schema_version": 9}}),
        encoding="utf-8")
    with (run_dir / "pipeline_log.jsonl").open("w", encoding="utf-8") as fh:
        for name in labels:
            fh.write(json.dumps({
                "stage": name,
                "summary": {"stage": name, "done": True},
            }) + "\n")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def test_run_calls_the_loop_and_prints_phase_lines(
        monkeypatch, capsys, tmp_path):
    calls = {}

    def fake_run_experiment(**kwargs):
        calls.update(kwargs)
        for name in EXPECTED_LABELS:
            kwargs["on_stage"](name, {"stage": name, "units": 1})

    monkeypatch.setattr(cli, "run_experiment", fake_run_experiment)
    code = cli.main([
        "run", "--config", str(tmp_path / "cfg.yaml"),
        "--run-dir", str(tmp_path / "run"), "--workers", "4",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert calls["config_path"] == tmp_path / "cfg.yaml"
    assert calls["run_dir"] == tmp_path / "run"
    assert calls["workers"] == 4
    # One line per phase, printed as phases complete (in order).
    assert out.splitlines() == [
        f"{name}: " + json.dumps({"stage": name, "units": 1}, sort_keys=True)
        for name in EXPECTED_LABELS
    ]


@pytest.mark.parametrize("error", [
    pytest.param(FrozenProtocolChanged("manifest differs"), id="frozen"),
    pytest.param(RunLockedError("locked by pid 1"), id="locked"),
    pytest.param(ConfigError("bad config"), id="config"),
])
def test_run_reports_expected_errors_nonzero(monkeypatch, capsys, tmp_path, error):
    def fake_run_experiment(**kwargs):
        raise error

    monkeypatch.setattr(cli, "run_experiment", fake_run_experiment)
    code = cli.main([
        "run", "--config", str(tmp_path / "cfg.yaml"),
        "--run-dir", str(tmp_path / "run"),
    ])
    captured = capsys.readouterr()
    assert code == 1
    assert str(error) in captured.err
    assert captured.out == ""


def _run_raising(monkeypatch, tmp_path, error, *extra):
    def fake_run_experiment(**kwargs):
        raise error

    monkeypatch.setattr(cli, "run_experiment", fake_run_experiment)
    return cli.main([
        "run", "--config", str(tmp_path / "cfg.yaml"),
        "--run-dir", str(tmp_path / "run"), *extra,
    ])


def test_run_infra_exhaustion_says_resume_and_exits_nonzero(
        monkeypatch, capsys, tmp_path):
    # exam phase that finished every other unit and then raised
    # InfrastructureError is resumable: no traceback, a clear message
    # telling the user to re-run the same command, and the exact command.
    error = InfrastructureError(
        "3 units failed on infrastructure; resume to retry")
    code = _run_raising(monkeypatch, tmp_path, error, "--workers", "4")
    captured = capsys.readouterr()
    assert code == cli.EXIT_INFRA_RESUMABLE
    assert code not in (0, 1)
    assert str(error) in captured.err
    assert "re-run the same command" in captured.err
    assert (f"--config {tmp_path / 'cfg.yaml'} --run-dir {tmp_path / 'run'} "
            "--workers 4") in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_run_provider_refusal_stops_with_clear_message(
        monkeypatch, capsys, tmp_path):
    error = ProviderRefusalError("model weak refused the request "
                                 "(finish_reason='content_filter')")
    code = _run_raising(monkeypatch, tmp_path, error)
    captured = capsys.readouterr()
    assert code == 1
    assert str(error) in captured.err
    assert "refused" in captured.err
    # A refusal is not resumable by repetition: no "re-run" advice.
    assert "re-run the same command" not in captured.err
    assert captured.out == ""


@pytest.mark.parametrize("error", [
    pytest.param(CodeBugError("invariant broken"), id="code_bug"),
    pytest.param(KeyError("unexpected"), id="unknown"),
])
def test_run_code_bugs_keep_their_traceback(monkeypatch, tmp_path, error):
    with pytest.raises(type(error)):
        _run_raising(monkeypatch, tmp_path, error)


@pytest.mark.parametrize("argv", [
    pytest.param(["run", "--run-dir", "r"], id="missing_config"),
    pytest.param(["run", "--config", "c.yaml", "--run-dir", "r",
                  "--until-phase", "x"], id="until_phase_not_an_int"),
    pytest.param([], id="no_subcommand"),
])
def test_bad_arguments_are_a_usage_error(monkeypatch, argv):
    monkeypatch.setattr(
        cli, "run_experiment",
        lambda **kwargs: pytest.fail("run_experiment must not be called"))
    with pytest.raises(SystemExit) as excinfo:
        cli.main(argv)
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def test_summary_complete_run_prints_every_phase(capsys, tmp_path):
    run_dir = tmp_path / "run"
    _fabricate_ledger(run_dir, EXPECTED_LABELS)
    code = cli.main(["summary", "--run-dir", str(run_dir)])
    captured = capsys.readouterr()
    assert code == 0
    for name in EXPECTED_LABELS:
        assert name in captured.out


def test_summary_prints_cached_tokens_from_usage_meta(capsys, tmp_path):
    # Pilot finding: cached_tokens lived only in usage.jsonl's meta; summary
    # must fold and show it without a manual re-read.
    run_dir = tmp_path / "run"
    _fabricate_ledger(run_dir, EXPECTED_LABELS)
    usage_path = run_dir / "llm_requests" / "usage.jsonl"
    usage_path.parent.mkdir(parents=True)
    rows = [
        {"role": "strong", "prompt": "SKILL", "model": "m",
         "in_tokens": 3927, "out_tokens": 4, "request_id": "r1",
         "status": "ok",
         "meta": {"finish_reason": "stop", "cached_tokens": 3712}},
        {"role": "strong", "prompt": "SKILL", "model": "m",
         "in_tokens": 100, "out_tokens": 4, "request_id": "r2",
         "status": "ok", "meta": {"finish_reason": "stop"}},
        {"role": "weak", "prompt": "ANSWER", "model": "m",
         "in_tokens": 5, "out_tokens": 1, "request_id": "r3",
         "status": "ok", "meta": {"cached_tokens": 3}},
    ]
    with usage_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    code = cli.main(["summary", "--run-dir", str(run_dir)])
    assert code == 0
    usage_line = next(line for line in capsys.readouterr().out.splitlines()
                      if line.startswith("usage: "))
    totals = json.loads(usage_line[len("usage: "):])
    assert totals["overall"]["cached_tokens"] == 3715
    assert totals["by_role"]["strong"]["cached_tokens"] == 3712
    assert totals["by_role"]["weak"]["cached_tokens"] == 3
    # in/out are not inflated by the folding.
    assert totals["overall"]["in"] == 4032
    assert totals["overall"]["out"] == 9


def test_summary_prints_role_prompt_usage_section(capsys, tmp_path):
    # The per-(role, prompt) section: one line per group after the existing
    # usage totals line. Mixed rows prove the tolerance rules (new meta
    # keys, an error row, a TB-style row without meta).
    run_dir = tmp_path / "run"
    _fabricate_ledger(run_dir, EXPECTED_LABELS)
    usage_path = run_dir / "llm_requests" / "usage.jsonl"
    usage_path.parent.mkdir(parents=True)
    rows = [
        {"role": "judge", "prompt": "JUDGE", "model": "m",
         "in_tokens": 120, "out_tokens": 10, "request_id": "r1",
         "status": "ok",
         "meta": {"finish_reason": "length", "cap_violation": True,
                  "estimated_in": 100, "cached_tokens": 90}},
        {"role": "judge", "prompt": "JUDGE", "model": "m",
         "in_tokens": None, "out_tokens": None, "request_id": "r2",
         "status": "error", "meta": {"error": "InfrastructureError"}},
        {"role": "executor_weak", "prompt": "TB_TERMINUS_TEMPLATE", "model": "m",
         "in_tokens": 2000, "out_tokens": None, "request_id": "r3",
         "status": "ok", "meta": None},
    ]
    with usage_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    code = cli.main(["summary", "--run-dir", str(run_dir)])
    out = capsys.readouterr().out
    assert code == 0
    assert "usage by (role, prompt):" in out
    judge_line = next(line for line in out.splitlines() if "JUDGE" in line)
    assert "judge | JUDGE |" in judge_line
    assert "calls=2 ok=1 err=1" in judge_line
    assert 'errors={"InfrastructureError": 1}' in judge_line
    assert "in_med=120 in_max=120 in_tot=120" in judge_line
    assert "cap_viol=1" in judge_line
    assert "len_trunc=1" in judge_line
    assert "est_in_med=100" in judge_line
    assert "in_over_est=1.2" in judge_line
    tb_line = next(line for line in out.splitlines()
                   if "TB_TERMINUS_TEMPLATE" in line)
    assert "executor_weak | TB_TERMINUS_TEMPLATE |" in tb_line
    # Never-reported output usage prints as unknown, never as a fake 0 size.
    assert "out_med=-" in tb_line
    assert "unknown=" in tb_line
    # The existing totals line is unchanged and still present.
    totals_line = next(line for line in out.splitlines()
                       if line.startswith("usage: "))
    assert json.loads(totals_line[len("usage: "):])["overall"] == {
        "in": 2120, "out": 10, "cached_tokens": 90, "request_count": 3}


def test_fmt_stat_renders_unknown_and_bounded_floats():
    # Unknown is '-', never a fake 0; integral medians (statistics.median
    # of an even count) print as integers; ratios stay bounded.
    assert cli._fmt_stat(None) == "-"
    assert cli._fmt_stat(120) == "120"
    assert cli._fmt_stat(75000.0) == "75000"
    assert cli._fmt_stat(150.5) == "150.5"
    assert cli._fmt_stat(1.7646666) == "1.765"


def test_summary_incomplete_run_exits_one(capsys, tmp_path):
    run_dir = tmp_path / "run"
    _fabricate_ledger(run_dir, EXPECTED_LABELS[:-1])  # C never completed
    code = cli.main(["summary", "--run-dir", str(run_dir)])
    captured = capsys.readouterr()
    assert code == 1
    assert "p03:test" in captured.err


def test_summary_takes_last_line_per_phase(capsys, tmp_path):
    # A resumed run appends fresh lines; the last one per phase wins.
    run_dir = tmp_path / "run"
    _fabricate_ledger(run_dir, EXPECTED_LABELS)
    with (run_dir / "pipeline_log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "stage": "p03:test",
            "summary": {"stage": "p03:test", "rerun": True},
        }) + "\n")
    code = cli.main(["summary", "--run-dir", str(run_dir)])
    out = capsys.readouterr().out
    assert code == 0
    assert '"rerun": true' in out
    assert '"done": true' not in out.splitlines()[-2]


def test_summary_prints_card_use_and_evidence_counts(capsys, tmp_path):
    run_dir = tmp_path / "run"
    _fabricate_ledger(run_dir, EXPECTED_LABELS)
    practice = {
        "role": "practice", "tasks": "train", "complete": True,
        "skill_files_read": {"traj/p03_practice/t/weak/00": ["x"]},
        "skill_use": {"trials": 6, "valid": 6, "success": 4,
                      "success_rate": 4 / 6, "opened_any": 2,
                      "opened_any_success": 1,
                      "cards": {"type/001/skill": {"opened": 2,
                                                   "opened_success": 1},
                                "type/002/skill": {"opened": 0,
                                                   "opened_success": 0}}}}
    learn = {"role": "learn", "input_phase": "p01:baseline",
             "failed_sheets": 5, "diagnosed": 5, "located": 4,
             "evidence": {"content": 1, "gap": 3}, "outcomes": {"add": 3},
             "scopes": {"general": 1, "subtask": 4},
             "bank": {"size": 3, "members": ["a", "b", "c"]}}
    with (run_dir / "pipeline_log.jsonl").open("a", encoding="utf-8") as fh:
        for name, summary in (("p02:learn", learn), ("p03:test", practice)):
            fh.write(json.dumps({"stage": name, "summary": summary}) + "\n")

    assert cli.main(["summary", "--run-dir", str(run_dir)]) == 0
    lines = {line.split(": ", 1)[0]: line.split(": ", 1)[1]
             for line in capsys.readouterr().out.splitlines()
             if line.startswith("p0")}
    shown = json.loads(lines["p03:test"])
    assert shown["success"] == 4 and shown["opened_any"] == 2
    assert (shown["cards_delivered"], shown["cards_opened"]) == (2, 1)
    assert "skill_files_read" not in shown
    shown = json.loads(lines["p02:learn"])
    assert shown["evidence"] == {"content": 1, "gap": 3}
    assert shown["bank_size"] == 3 and "scopes" not in shown
    assert json.loads(lines["p01:baseline"]) == {"stage": "p01:baseline",
                                                 "done": True}


def test_summary_without_ledger_exits_one(capsys, tmp_path):
    run_dir = tmp_path / "empty_run"
    run_dir.mkdir()
    code = cli.main(["summary", "--run-dir", str(run_dir)])
    captured = capsys.readouterr()
    assert code == 1
    assert "no readable manifest" in captured.err


def test_summary_of_a_pre_v4_run_exits_one(capsys, tmp_path):
    # The checklist comes from the manifest's frozen sequence; a run
    # without one (e.g. frozen under an older schema) is refused rather
    # than checked against a code constant.
    run_dir = tmp_path / "old_run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps(
        {"benchmark": "fakebench",
         "config": {"schema_version": 3, "methods": {}}}),
        encoding="utf-8")
    code = cli.main(["summary", "--run-dir", str(run_dir)])
    captured = capsys.readouterr()
    assert code == 1
    assert "frozen sequence" in captured.err


def test_bad_run_setting_exits_one_without_traceback(capsys, tmp_path):
    # The REAL run_experiment: validation must happen before the config is
    # even read (the config path does not exist here). The full list of bad
    # values is pinned in test_cli_run_loop.
    code = cli.main(["run", "--config", str(tmp_path / "missing.yaml"),
                     "--run-dir", str(tmp_path / "run"), "--workers", "0"])
    captured = capsys.readouterr()
    assert code == 1
    assert "workers" in captured.err
    assert not (tmp_path / "run").exists()


def test_missing_config_file_exits_one_without_traceback(capsys, tmp_path):
    config = tmp_path / "missing.yaml"
    code = cli.main(["run", "--config", str(config),
                     "--run-dir", str(tmp_path / "run")])
    captured = capsys.readouterr()
    assert code == 1
    assert str(config) in captured.err


def test_run_model_output_error_exits_one_with_resume_hint(
        monkeypatch, capsys, tmp_path):
    from expedistill.infra.reliability import ModelOutputError

    error = ModelOutputError("no JSON object found in the model output")
    code = _run_raising(monkeypatch, tmp_path, error)
    captured = capsys.readouterr()
    # Exit 1, not EXIT_INFRA_RESUMABLE: a supervisor must not auto-resume.
    assert code == 1
    assert str(error) in captured.err
    assert "repair budget" in captured.err
    assert "re-run the same command" in captured.err
    assert "expedistill run --config" in captured.err
    assert captured.out == ""


# ---------------------------------------------------------------------------
# run settings reach run_experiment; the resume command keeps them
# ---------------------------------------------------------------------------

_DEFAULT_SETTINGS = {
    "llm_infra_retries": 3, "llm_infra_backoff_seconds": 5.0,
    "llm_timeout_sec": 300, "allow_infra_shortfall": False,
    "k1_stagger": 0.0, "until_phase": None, "rollout_workers": None,
}
_OVERRIDE_FLAGS = [
    "--llm-infra-retries", "0", "--llm-infra-backoff", "1.5",
    "--llm-timeout", "120.5", "--allow-infra-shortfall",
    "--k1-stagger", "1.5", "--until-phase", "3", "--rollout-workers", "64",
]
_OVERRIDE_SETTINGS = {
    "llm_infra_retries": 0, "llm_infra_backoff_seconds": 1.5,
    "llm_timeout_sec": 120.5, "allow_infra_shortfall": True,
    "k1_stagger": 1.5, "until_phase": 3, "rollout_workers": 64,
}


@pytest.mark.parametrize("flags, expected", [
    pytest.param([], _DEFAULT_SETTINGS, id="defaults"),
    pytest.param(_OVERRIDE_FLAGS, _OVERRIDE_SETTINGS, id="overrides"),
])
def test_run_passes_its_settings_to_the_loop(
        monkeypatch, tmp_path, flags, expected):
    calls = {}
    monkeypatch.setattr(cli, "run_experiment",
                        lambda **kwargs: calls.update(kwargs))
    assert cli.main(["run", "--config", str(tmp_path / "cfg.yaml"),
                     "--run-dir", str(tmp_path / "run"), *flags]) == 0
    assert {key: calls[key] for key in expected} == expected


@pytest.mark.parametrize("flags, present, absent", [
    pytest.param([], {}, ["--until-phase", "--rollout-workers",
                          "--allow-infra-shortfall"], id="defaults"),
    pytest.param(
        _OVERRIDE_FLAGS,
        {"--llm-infra-retries": "0", "--llm-infra-backoff": "1.5",
         "--llm-timeout": "120.5", "--k1-stagger": "1.5",
         "--until-phase": "3", "--rollout-workers": "64"},
        [], id="overrides"),
])
def test_resume_command_keeps_the_run_settings(
        monkeypatch, capsys, tmp_path, flags, present, absent):
    import shlex

    # Paths with spaces prove the command is shell-quoted.
    config = tmp_path / "cfg dir" / "x.yaml"
    run_dir = tmp_path / "my run"

    def fake_run_experiment(**kwargs):
        raise InfrastructureError("1 unit failed on infrastructure")

    monkeypatch.setattr(cli, "run_experiment", fake_run_experiment)
    code = cli.main(["run", "--config", str(config),
                     "--run-dir", str(run_dir), "--workers", "128", *flags])
    assert code == cli.EXIT_INFRA_RESUMABLE
    argv = shlex.split(capsys.readouterr().err.splitlines()[-1])
    assert argv[:2] == ["expedistill", "run"]
    assert argv[argv.index("--config") + 1] == str(config)
    assert argv[argv.index("--run-dir") + 1] == str(run_dir)
    assert argv[argv.index("--workers") + 1] == "128"
    for flag, value in present.items():
        assert argv[argv.index(flag) + 1] == value, flag
    for flag in absent:
        assert flag not in argv
    assert ("--allow-infra-shortfall" in argv) is ("--allow-infra-shortfall"
                                                  in flags)
