"""The supervisor's counting contract: the patrol line's "failed
unresolved" number must fall when a retried unit succeeds -- the
2026-10-18 bug quoted the ledger's unit ids but compared them against
the store's encoded .json filenames, so the sets never overlapped and
the count never moved (39 stored / 74 failed with 84 units total).
"""
import importlib.util
import json
import urllib.parse
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "run" / \
    "supervise_tb_run.py"
_spec = importlib.util.spec_from_file_location("supervise_tb_run", _SCRIPT)
supervise = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(supervise)


def _skeleton(tmp_path, stored, ledger_rows):
    run_dir = tmp_path / "run"
    traj = run_dir / "phases" / "p07_C" / "with_bank" / "store" \
        / "stage1_baseline" / "trajectories"
    traj.mkdir(parents=True)
    for unit_id in stored:
        name = urllib.parse.quote(unit_id, safe="") + ".json"
        (traj / name).write_text("{}", encoding="utf-8")
    ledger = run_dir / "phases" / "p07_C" / "with_bank" / "store" \
        / "failed_units.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger, "w", encoding="utf-8") as handle:
        for unit_id in ledger_rows:
            handle.write(json.dumps(
                {"unit_id": unit_id, "error_class": "InfrastructureError",
                 "message": "x"}) + "\n")
    return run_dir


def test_retried_and_succeeded_units_leave_the_failed_count(tmp_path):
    run_dir = _skeleton(
        tmp_path,
        stored=["traj/tb:t1/weak/00", "traj/tb:t1/weak/01"],
        ledger_rows=["traj/tb:t1/weak/00", "traj/tb:t1/weak/01",
                     "traj/tb:t2/weak/00"])
    assert supervise.unresolved_failures(run_dir, "p07:C") == 1
    assert supervise.stored_unit_ids(run_dir, "p07:C") == {
        "traj/tb:t1/weak/00", "traj/tb:t1/weak/01"}


def test_all_failed_still_pending_counts_every_row(tmp_path):
    run_dir = _skeleton(tmp_path, stored=[],
                        ledger_rows=[f"traj/tb:t{i}/weak/02"
                                     for i in range(74)])
    assert supervise.unresolved_failures(run_dir, "p07:C") == 74


def test_a_missing_ledger_or_store_reads_zero(tmp_path):
    run_dir = _skeleton(tmp_path, stored=[], ledger_rows=[])
    assert supervise.unresolved_failures(run_dir, "p07:C") == 0
    assert supervise.stored_unit_ids(run_dir, "p07:C") == set()
    assert supervise.unresolved_failures(tmp_path / "nowhere", "p07:C") == 0


def test_the_relaunch_command_is_absolute_and_exists(tmp_path):
    cmd = supervise.cli_command("configs/experiments/x.yaml", tmp_path / "r")
    assert Path(cmd[0]).is_absolute()
    # The supervisor may run under any cwd: the executable must resolve
    # from the script's own location (the 2026-10-19 crash was a
    # relative path under a one-level-short chdir).
    assert Path(cmd[0]).exists()
    assert cmd[1:] == ["run", "--config", "configs/experiments/x.yaml",
                       "--run-dir", str(tmp_path / "r"),
                       "--workers", "64", "--k1-stagger", "20",
                       "--llm-timeout", "1500",
                       "--llm-infra-retries", "5",
                       "--llm-infra-backoff", "10"]


def test_a_failed_relaunch_is_logged_never_silent(tmp_path, monkeypatch):
    lines = []

    def log(message):
        lines.append(message)

    def broken_popen(*args, **kwargs):
        raise FileNotFoundError("No such file or directory: 'x'")

    monkeypatch.setattr(supervise.subprocess, "Popen", broken_popen)
    result = supervise.relaunch_driver(tmp_path, ["x"], log)
    assert result is None
    assert any("relaunch FAILED" in line for line in lines)


def test_a_successful_relaunch_writes_the_pidfile(tmp_path, monkeypatch):
    lines = []

    def log(message):
        lines.append(message)

    class FakeProc:
        pid = 4321

    monkeypatch.setattr(supervise.subprocess, "Popen",
                        lambda *a, **k: FakeProc())
    result = supervise.relaunch_driver(tmp_path, ["x"], log)
    assert result == 4321
    assert (tmp_path / "driver.pids").read_text() == "4321\n"
