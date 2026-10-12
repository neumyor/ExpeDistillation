"""CLI (layer 7) tests: run/summary wiring and the error contract.

No stage is executed: ``run`` is tested against a monkeypatched run_pipeline,
``summary`` against a fabricated ledger. The pipeline's own behavior (real
stages, fake benchmark) is covered by tests/unit/stages/test_pipeline.py.
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
from expedistill.stages.pipeline import STAGE_NAMES


def _fabricate_ledger(run_dir: Path, stages) -> None:
    """Write a pipeline_log.jsonl line for every given stage name."""
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "pipeline_log.jsonl").open("w", encoding="utf-8") as fh:
        for name in stages:
            fh.write(json.dumps({
                "stage": name,
                "summary": {"stage": name, "done": True},
            }) + "\n")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def test_run_calls_pipeline_and_prints_stage_lines(monkeypatch, capsys, tmp_path):
    calls = {}

    def fake_run_pipeline(**kwargs):
        calls.update(kwargs)
        for name in STAGE_NAMES:
            kwargs["on_stage"](name, {"stage": name, "units": 1})

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    code = cli.main([
        "run", "--config", str(tmp_path / "cfg.yaml"),
        "--run-dir", str(tmp_path / "run"), "--workers", "4",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert calls["config_path"] == tmp_path / "cfg.yaml"
    assert calls["run_dir"] == tmp_path / "run"
    assert calls["workers"] == 4
    # One line per stage, printed as stages complete (in order).
    assert out.splitlines() == [
        f"{name}: " + json.dumps({"stage": name, "units": 1}, sort_keys=True)
        for name in STAGE_NAMES
    ]


@pytest.mark.parametrize("error", [
    pytest.param(FrozenProtocolChanged("manifest differs"), id="frozen"),
    pytest.param(RunLockedError("locked by pid 1"), id="locked"),
    pytest.param(ConfigError("bad config"), id="config"),
])
def test_run_reports_expected_errors_nonzero(monkeypatch, capsys, tmp_path, error):
    def fake_run_pipeline(**kwargs):
        raise error

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    code = cli.main([
        "run", "--config", str(tmp_path / "cfg.yaml"),
        "--run-dir", str(tmp_path / "run"),
    ])
    captured = capsys.readouterr()
    assert code == 1
    assert str(error) in captured.err
    assert captured.out == ""


def _run_raising(monkeypatch, tmp_path, error, *extra):
    def fake_run_pipeline(**kwargs):
        raise error

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    return cli.main([
        "run", "--config", str(tmp_path / "cfg.yaml"),
        "--run-dir", str(tmp_path / "run"), *extra,
    ])


def test_run_infra_exhaustion_says_resume_and_exits_nonzero(
        monkeypatch, capsys, tmp_path):
    # A stage that finished every other unit and then raised
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


def test_run_missing_args_is_usage_error(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        cli, "run_pipeline",
        lambda **kwargs: pytest.fail("run_pipeline must not be called"))
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["run", "--run-dir", str(tmp_path / "run")])
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def test_summary_complete_run_prints_stages_and_usage(capsys, tmp_path):
    run_dir = tmp_path / "run"
    _fabricate_ledger(run_dir, STAGE_NAMES)
    usage_path = run_dir / "llm_requests" / "usage.jsonl"
    usage_path.parent.mkdir(parents=True)
    usage_path.write_text(json.dumps({
        "role": "judge", "prompt": "JUDGE", "model": "m",
        "in_tokens": 3, "out_tokens": 5, "request_id": "r1",
        "status": "ok",
    }) + "\n", encoding="utf-8")

    code = cli.main(["summary", "--run-dir", str(run_dir)])
    captured = capsys.readouterr()
    assert code == 0
    for name in STAGE_NAMES:
        assert name in captured.out
    # Usage totals are folded from the ledger, not recomputed.
    usage_line = next(line for line in captured.out.splitlines()
                      if line.startswith("usage: "))
    totals = json.loads(usage_line[len("usage: "):])
    assert totals["overall"] == {"in": 3, "out": 5, "cached_tokens": 0,
                                  "request_count": 1}
    assert totals["by_role"]["judge"]["request_count"] == 1


def test_summary_prints_cached_tokens_from_usage_meta(capsys, tmp_path):
    # Pilot finding: cached_tokens lived only in usage.jsonl's meta; summary
    # must fold and show it without a manual re-read.
    run_dir = tmp_path / "run"
    _fabricate_ledger(run_dir, STAGE_NAMES)
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
    _fabricate_ledger(run_dir, STAGE_NAMES)
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
        {"role": "executor_weak", "prompt": "TB_TASK_PROMPT", "model": "m",
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
                   if "TB_TASK_PROMPT" in line)
    assert "executor_weak | TB_TASK_PROMPT |" in tb_line
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
    _fabricate_ledger(run_dir, STAGE_NAMES[:-1])  # stage 6 never completed
    code = cli.main(["summary", "--run-dir", str(run_dir)])
    captured = capsys.readouterr()
    assert code == 1
    assert "stage6_test" in captured.err


def test_summary_takes_last_line_per_stage(capsys, tmp_path):
    # A resumed run appends fresh lines; the last one per stage wins.
    run_dir = tmp_path / "run"
    _fabricate_ledger(run_dir, STAGE_NAMES)
    with (run_dir / "pipeline_log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "stage": "stage6_test",
            "summary": {"stage": "stage6_test", "rerun": True},
        }) + "\n")
    code = cli.main(["summary", "--run-dir", str(run_dir)])
    out = capsys.readouterr().out
    assert code == 0
    assert '"rerun": true' in out
    assert '"done": true' not in out.splitlines()[-2]


def test_summary_without_ledger_exits_one(capsys, tmp_path):
    run_dir = tmp_path / "empty_run"
    run_dir.mkdir()
    code = cli.main(["summary", "--run-dir", str(run_dir)])
    captured = capsys.readouterr()
    assert code == 1
    assert "stage0_freeze" in captured.err


def test_main_requires_a_subcommand(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 2


def test_run_passes_infra_retry_settings(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(cli, "run_pipeline",
                        lambda **kwargs: calls.update(kwargs))
    base = ["run", "--config", str(tmp_path / "cfg.yaml"),
            "--run-dir", str(tmp_path / "run")]
    assert cli.main(base) == 0
    assert calls["llm_infra_retries"] == 3
    assert calls["llm_infra_backoff_seconds"] == 5.0
    assert cli.main(base + ["--llm-infra-retries", "0",
                            "--llm-infra-backoff", "1.5"]) == 0
    assert calls["llm_infra_retries"] == 0
    assert calls["llm_infra_backoff_seconds"] == 1.5


def test_run_passes_llm_timeout(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(cli, "run_pipeline",
                        lambda **kwargs: calls.update(kwargs))
    base = ["run", "--config", str(tmp_path / "cfg.yaml"),
            "--run-dir", str(tmp_path / "run")]
    assert cli.main(base) == 0
    assert calls["llm_timeout_sec"] == 300
    assert cli.main(base + ["--llm-timeout", "120.5"]) == 0
    assert calls["llm_timeout_sec"] == 120.5


def test_resume_command_keeps_llm_timeout(monkeypatch, capsys, tmp_path):
    import shlex

    def fake_run_pipeline(**kwargs):
        raise InfrastructureError("1 unit failed on infrastructure")

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    code = cli.main(["run", "--config", str(tmp_path / "c.yaml"),
                     "--run-dir", str(tmp_path / "r"), "--llm-timeout", "90"])
    assert code == cli.EXIT_INFRA_RESUMABLE
    argv = shlex.split(capsys.readouterr().err.splitlines()[-1])
    assert float(argv[argv.index("--llm-timeout") + 1]) == 90.0


def test_llm_timeout_help_documents_the_tradeoff():
    parser = cli.build_parser()
    run_parser = parser._subparsers._group_actions[0].choices["run"]
    help_text = next(action.help for action in run_parser._actions
                     if "--llm-timeout" in action.option_strings)
    # Reasoning-max calls legitimately take minutes, so the help must say to
    # lower the timeout only when the endpoint is unstable (pilot finding:
    # hung requests each burned the full 300s).
    assert "reasoning" in help_text
    assert "unstable" in help_text


def test_resume_command_quotes_paths_and_keeps_infra_flags(
        monkeypatch, capsys, tmp_path):
    import shlex

    run_dir = tmp_path / "my run"
    config = tmp_path / "cfg dir" / "x.yaml"

    def fake_run_pipeline(**kwargs):
        raise InfrastructureError("1 unit failed on infrastructure")

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    code = cli.main(["run", "--config", str(config), "--run-dir", str(run_dir),
                     "--llm-infra-retries", "5", "--llm-infra-backoff", "2"])
    assert code == cli.EXIT_INFRA_RESUMABLE
    command = capsys.readouterr().err.splitlines()[-1].strip()
    argv = shlex.split(command)
    assert argv[:2] == ["expedistill", "run"]
    assert argv[argv.index("--config") + 1] == str(config)
    assert argv[argv.index("--run-dir") + 1] == str(run_dir)
    assert argv[argv.index("--llm-infra-retries") + 1] == "5"
    assert float(argv[argv.index("--llm-infra-backoff") + 1]) == 2.0


def test_run_passes_shortfall_and_breaker_settings(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(cli, "run_pipeline",
                        lambda **kwargs: calls.update(kwargs))
    base = ["run", "--config", str(tmp_path / "cfg.yaml"),
            "--run-dir", str(tmp_path / "run")]
    assert cli.main(base) == 0
    assert calls["allow_infra_shortfall"] is False
    assert calls["infra_breaker"] == 5
    assert cli.main(base + ["--allow-infra-shortfall",
                            "--infra-breaker", "0"]) == 0
    assert calls["allow_infra_shortfall"] is True
    assert calls["infra_breaker"] == 0


@pytest.mark.parametrize("extra, shortfall", [
    pytest.param([], False, id="strict"),
    pytest.param(["--allow-infra-shortfall"], True, id="accepting"),
])
def test_resume_command_keeps_shortfall_and_breaker(
        monkeypatch, capsys, tmp_path, extra, shortfall):
    import shlex

    def fake_run_pipeline(**kwargs):
        raise InfrastructureError("1 unit failed on infrastructure")

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    code = cli.main(["run", "--config", str(tmp_path / "c.yaml"),
                     "--run-dir", str(tmp_path / "r"),
                     "--infra-breaker", "9", *extra])
    assert code == cli.EXIT_INFRA_RESUMABLE
    argv = shlex.split(capsys.readouterr().err.splitlines()[-1])
    assert argv[argv.index("--infra-breaker") + 1] == "9"
    assert ("--allow-infra-shortfall" in argv) is shortfall


@pytest.mark.parametrize("flag, value, setting", [
    ("--infra-breaker", "-1", "infra_breaker"),
    ("--llm-infra-retries", "-2", "llm_infra_retries"),
    ("--llm-infra-backoff", "nan", "llm_infra_backoff_seconds"),
    ("--llm-timeout", "0", "llm_timeout_sec"),
    ("--llm-timeout", "nan", "llm_timeout_sec"),
    ("--workers", "0", "workers"),
    ("--until-stage", "-1", "until_stage"),
    ("--until-stage", "7", "until_stage"),
])
def test_bad_run_setting_exits_one_without_traceback(
        capsys, tmp_path, flag, value, setting):
    # The REAL run_pipeline: validation must happen before the config is
    # even read (the config path does not exist here).
    code = cli.main(["run", "--config", str(tmp_path / "missing.yaml"),
                     "--run-dir", str(tmp_path / "run"), flag, value])
    captured = capsys.readouterr()
    assert code == 1
    assert setting in captured.err
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


def test_infra_breaker_help_counts_distinct_tasks():
    parser = cli.build_parser()
    run_parser = parser._subparsers._group_actions[0].choices["run"]
    help_text = next(action.help for action in run_parser._actions
                     if "--infra-breaker" in action.option_strings)
    assert "distinct tasks" in help_text
    assert "consecutive infra-exhausted execution units" not in help_text


# ---------------------------------------------------------------------------
# --until-stage
# ---------------------------------------------------------------------------

def test_run_passes_until_stage(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(cli, "run_pipeline",
                        lambda **kwargs: calls.update(kwargs))
    base = ["run", "--config", str(tmp_path / "cfg.yaml"),
            "--run-dir", str(tmp_path / "run")]
    assert cli.main(base) == 0
    assert calls["until_stage"] is None
    assert cli.main(base + ["--until-stage", "3"]) == 0
    assert calls["until_stage"] == 3


def test_resume_command_keeps_until_stage(monkeypatch, capsys, tmp_path):
    import shlex

    def fake_run_pipeline(**kwargs):
        raise InfrastructureError("1 unit failed on infrastructure")

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    code = cli.main(["run", "--config", str(tmp_path / "c.yaml"),
                     "--run-dir", str(tmp_path / "r"), "--until-stage", "4"])
    assert code == cli.EXIT_INFRA_RESUMABLE
    argv = shlex.split(capsys.readouterr().err.splitlines()[-1])
    assert argv[argv.index("--until-stage") + 1] == "4"


def test_resume_command_omits_until_stage_when_default(
        monkeypatch, capsys, tmp_path):
    import shlex

    def fake_run_pipeline(**kwargs):
        raise InfrastructureError("1 unit failed on infrastructure")

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    code = cli.main(["run", "--config", str(tmp_path / "c.yaml"),
                     "--run-dir", str(tmp_path / "r")])
    assert code == cli.EXIT_INFRA_RESUMABLE
    argv = shlex.split(capsys.readouterr().err.splitlines()[-1])
    assert "--until-stage" not in argv


def test_until_stage_help_says_how_to_continue():
    parser = cli.build_parser()
    run_parser = parser._subparsers._group_actions[0].choices["run"]
    help_text = next(action.help for action in run_parser._actions
                     if "--until-stage" in action.option_strings)
    # A user who stopped early must learn from the help alone that they
    # re-run with a higher --until-stage to continue.
    assert "higher --until-stage" in help_text


def test_until_stage_non_integer_is_usage_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli, "run_pipeline",
        lambda **kwargs: pytest.fail("run_pipeline must not be called"))
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["run", "--config", str(tmp_path / "cfg.yaml"),
                  "--run-dir", str(tmp_path / "run"), "--until-stage", "x"])
    assert excinfo.value.code == 2
