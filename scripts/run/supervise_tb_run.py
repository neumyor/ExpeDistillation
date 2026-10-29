#!/usr/bin/env python3
"""Detached-run supervisor for a TB experiment's final-test phase (the
ops discipline of the outer AGENTS.md: adopt the driver named in the
run's pidfile, poll product files -- never pgrep; on a driver exit
decide completeness from the phase summary and relaunch the SAME CLI
command, which retries only the failed units).

Usage: supervise_tb_run.py <run_dir> <config.yaml> [phase_label]

``phase_label`` defaults to ``p07:test`` (the folder experiment's
final-test phase). Source .env first: the relaunch command and the
ssh probe read EXPEDISTILL_TB_* from the environment.

Patrol line (hourly): driver alive, stored count, UNRESOLVED failure
count, link state. The unresolved count is the failure-ledger rows whose
unit is still not stored: the ledger is append-only and never drops a
row, so a raw line count would keep reporting retried-and-succeeded
units as failed forever. Both sets are compared in the raw unit-id
space -- the store's filenames are percent-encoded and carry a .json
suffix, the ledger's unit_ids are raw -- the 2026-10-18 patrol bug was
exactly that mismatch (the count never fell).
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

MAX_RESTARTS = 30


def repo_root() -> Path:
    """The repository root (this script lives in <root>/scripts/run/) --
    the relaunch command's relative paths resolve against it."""
    return Path(__file__).resolve().parents[2]


def cli_command(config: str, run_dir: Path) -> list:
    """The driver's launch command, ABSOLUTE first token: a relative
    executable would break under any cwd (the 2026-10-19 crash -- the
    supervisor chdir'd one level short of the root, the relaunch's
    .venv/bin/expedistill did not exist there, and the unhandled
    FileNotFoundError killed the supervisor itself, silently)."""
    return [str(repo_root() / ".venv" / "bin" / "expedistill"),
            "run", "--config", config, "--run-dir", str(run_dir),
            "--workers", "64", "--k1-stagger", "20",
            "--llm-timeout", "1500",
            "--llm-infra-retries", "5", "--llm-infra-backoff", "10"]


def relaunch_driver(run_dir: Path, cmd: list, log) -> "int | None":
    """Start one driver, new session, pidfile written. A FAILED relaunch
    is LOGGED, never a silent death: the exception goes to the
    supervisor log and stdout before the caller decides to give up."""
    try:
        console = open(run_dir / "console.log", "ab")
        proc = subprocess.Popen(cmd, stdout=console, stderr=console,
                                start_new_session=True)
    except OSError as exc:
        log(f"relaunch FAILED: {type(exc).__name__}: {exc}")
        return None
    with open(run_dir / "driver.pids", "w") as handle:
        handle.write(f"{proc.pid}\n")
    return proc.pid


def _traj_dir(run_dir: Path, phase_label: str) -> Path:
    directory = phase_label.replace(":", "_")
    return (run_dir / "phases" / directory / "with_bank" / "store"
            / "stage1_baseline" / "trajectories")


def _ledger_path(run_dir: Path, phase_label: str) -> Path:
    directory = phase_label.replace(":", "_")
    return (run_dir / "phases" / directory / "with_bank" / "store"
            / "failed_units.jsonl")


def stored_unit_ids(run_dir: Path, phase_label: str) -> set:
    """The stored units as RAW unit ids: the store's percent-encoded
    ``.json`` filenames decoded and stripped back to the ledger's
    spelling."""
    try:
        names = [path.name for path in _traj_dir(run_dir, phase_label).iterdir()]
    except OSError:
        return set()
    ids = set()
    for name in names:
        raw = urllib.parse.unquote(name)
        if raw.endswith(".json"):
            raw = raw[:-len(".json")]
        ids.add(raw)
    return ids


def unresolved_failures(run_dir: Path, phase_label: str) -> int:
    """Ledger rows whose unit is still not stored (the honest "failed"
    number: a retried unit that succeeded leaves the count)."""
    try:
        rows = [json.loads(line) for line in
                _ledger_path(run_dir, phase_label).read_text(
                    encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return 0
    ledger_ids = {row.get("unit_id", "") for row in rows}
    return len(ledger_ids - stored_unit_ids(run_dir, phase_label))


def main() -> int:
    run_dir = Path(sys.argv[1]).resolve()
    config = sys.argv[2]
    phase_label = sys.argv[3] if len(sys.argv) > 3 else "p07:test"
    cmd = cli_command(config, run_dir)
    summary_path = (run_dir / "phases" / phase_label.replace(":", "_")
                    / "phase_summary.json")
    ssh_host = os.environ["EXPEDISTILL_TB_HOST"]
    ssh_key = os.environ["EXPEDISTILL_TB_KEY"]

    def log(message):
        stamp = time.strftime("%F %T")
        with open(run_dir / "supervisor.log", "a") as handle:
            handle.write(f"{stamp} {message}\n")
        print(f"{stamp} {message}", flush=True)

    def driver_pid():
        try:
            return int((run_dir / "driver.pids").read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None

    def alive(pid):
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def run_complete():
        try:
            return json.loads(summary_path.read_text()).get("complete") is True
        except (OSError, ValueError):
            return False

    def link_up():
        probe = subprocess.run(
            ["ssh", "-S", "none", "-o", "BatchMode=yes",
             "-o", "ConnectTimeout=12", "-i", ssh_key, ssh_host, "echo ok"],
            capture_output=True, text=True)
        return probe.returncode == 0

    def wait_for_link():
        for _ in range(240):  # up to 20 hours
            if link_up():
                return True
            log("benchmark host unreachable; waiting 5 min")
            time.sleep(300)
        return False

    os.chdir(repo_root())  # the launch files and configs are repo-relative
    log(f"supervisor start: adopting driver pid {driver_pid()}")
    restarts = 0
    last_hour_mark = -1
    while True:
        pid = driver_pid()
        if pid is not None and alive(pid):
            hour = int(time.time() // 3600)
            if hour != last_hour_mark:
                last_hour_mark = hour
                log(f"patrol: driver {pid} alive, "
                    f"{len(stored_unit_ids(run_dir, phase_label))} stored / "
                    f"{unresolved_failures(run_dir, phase_label)} failed "
                    f"unresolved, "
                    f"link={'up' if link_up() else 'DOWN'}")
            time.sleep(60)
            continue
        if run_complete():
            log("run COMPLETE; supervisor exits")
            return 0
        if restarts >= MAX_RESTARTS:
            log(f"giving up after {restarts} restarts")
            return 1
        if not wait_for_link():
            log("link never came back; supervisor gives up")
            return 1
        restarts += 1
        log(f"driver gone; relaunching (restart {restarts}/{MAX_RESTARTS})")
        pid = relaunch_driver(run_dir, cmd, log)
        if pid is None:
            log("supervisor exits: the relaunch command itself failed")
            return 1
        log(f"relaunched driver pid {pid}")
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
