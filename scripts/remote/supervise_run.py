#!/usr/bin/env python3
"""Supervise one run on the benchmark host: resume it after exit code 3.

Usage (on the benchmark host, after the run was started with
scripts/remote/start_run_bench_host.sh, which writes <run dir>/launch.sh):

    .venv/bin/python scripts/remote/supervise_run.py <run dir> \\
        [--backoff 300] [--max-stalled 5] [--poll 30]

Returns at once: the supervisor detaches itself into a new session
(``start_new_session=True``), records its PID in ``<run dir>/supervisor.pid``
and logs to ``<run dir>/supervisor.log``. It refuses to start while
``supervisor.pid`` names a live process.

What it does, in a loop:

1. wait for the run's driver: ``kill -0`` on the PID in ``driver.pid`` (never
   ``pgrep -f``); once it is gone, read ``driver.pid.exit``, written by the
   detached waiter of scripts/smoke/launch_detached.py;
2. decide from the exit code (``decide``): 0 done, 1 needs a human, 2 usage
   error, any other code (a signal, a crash) unknown -- all stop the
   supervisor with the reason in its log; 3 means a resume retries the failed
   units, so after ``--backoff`` seconds it runs ``bash <run dir>/launch.sh``;
3. a driver that is gone without an exit file (its waiter was killed) also
   stops the supervisor: the exit code is unknown.

Progress guard: before each attempt it takes a ``Snapshot`` of the run --
the number of phases completed in ``pipeline_log.jsonl``, the number of
stored objects (trajectories in the run-level store and in every test arm's
store, segments, skills) and the number of rows in the learn phases'
``findings.jsonl`` / ``outcomes.jsonl`` (all append-only or add-only, so a
count can only grow when a unit finished). An attempt that ends with exit 3
and no count grew is stalled; after ``--max-stalled`` consecutive stalled
attempts the supervisor stops instead of resuming again.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

#: Exit codes of ``expedistill run`` (design spec section 7).
EXIT_DONE = 0
EXIT_HUMAN = 1
EXIT_USAGE = 2
EXIT_RESUMABLE = 3

DEFAULT_BACKOFF_SECONDS = 300.0
DEFAULT_MAX_STALLED = 5
DEFAULT_POLL_SECONDS = 30.0
#: How long a gone driver may lack its exit file before the code counts as
#: unknown (the waiter writes it right after the driver ends).
EXIT_FILE_GRACE_SECONDS = 120.0
LAUNCH_TIMEOUT_SECONDS = 300.0

SUPERVISOR_PID = "supervisor.pid"
SUPERVISOR_LOG = "supervisor.log"
DRIVER_PID = "driver.pid"
DRIVER_EXIT = "driver.pid.exit"
LAUNCH_SCRIPT = "launch.sh"

#: Store directories whose ``*.json`` files are stored units (relative to
#: the run directory, and for trajectories also to each test arm's store).
_STORE_DIRS = ("stage1_baseline/trajectories", "stage2_segments/subtasks",
               "stage3_generation/skills")
_LEARN_LEDGERS = ("findings.jsonl", "outcomes.jsonl")


# ---------------------------------------------------------------------------
# Pure decision logic
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Snapshot:
    """Counts that only grow when the run finished more work."""

    phases_done: int
    stored_objects: int
    learn_rows: int


@dataclass(frozen=True)
class Decision:
    action: str  # "resume" or "stop"
    reason: str


def made_progress(before: Snapshot, after: Snapshot) -> bool:
    """True when any count grew between two snapshots."""
    return (after.phases_done > before.phases_done
            or after.stored_objects > before.stored_objects
            or after.learn_rows > before.learn_rows)


def next_stalled(stalled: int, progressed: bool) -> int:
    """Consecutive stalled exit-3 attempts after one more such attempt."""
    return 0 if progressed else stalled + 1


def decide(exit_code: Optional[int], stalled: int,
           max_stalled: int) -> Decision:
    """What to do after the driver ended with ``exit_code`` (None: unknown);
    ``stalled`` already counts the attempt that just ended."""
    if exit_code is None:
        return Decision("stop", "the driver is gone and left no exit code "
                                "(its waiter was killed?); read "
                                "driver.console.log")
    if exit_code == EXIT_DONE:
        return Decision("stop", "exit 0: the run finished")
    if exit_code == EXIT_HUMAN:
        return Decision("stop", "exit 1: needs a human (config error, "
                                "manifest mismatch, run lock, model output "
                                "error or a code bug); read "
                                "driver.console.log")
    if exit_code == EXIT_USAGE:
        return Decision("stop", "exit 2: command-line usage error")
    if exit_code != EXIT_RESUMABLE:
        return Decision("stop", f"exit {exit_code}: not a code the CLI "
                                "returns (killed by a signal or crashed)")
    if stalled >= max_stalled:
        return Decision("stop", f"exit 3 after {stalled} consecutive "
                                "attempts without progress")
    return Decision("resume", f"exit 3 (stalled attempts: {stalled}/"
                              f"{max_stalled})")


# ---------------------------------------------------------------------------
# Reading the run directory
# ---------------------------------------------------------------------------

def _count_json(directory: Path) -> int:
    try:
        return sum(1 for entry in os.scandir(directory)
                   if entry.name.endswith(".json") and entry.is_file())
    except OSError:
        return 0


def _count_lines(path: Path) -> int:
    try:
        with open(path, "rb") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def snapshot(run_dir: Path) -> Snapshot:
    """The progress counts of ``run_dir`` (missing files count as 0)."""
    done = set()
    try:
        with open(run_dir / "pipeline_log.jsonl", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("stage"), str) \
                        and isinstance(row.get("summary"), dict):
                    done.add(row["stage"])
    except OSError:
        pass
    stored = sum(_count_json(run_dir / sub) for sub in _STORE_DIRS)
    phases = run_dir / "phases"
    stored += sum(_count_json(arm_store / _STORE_DIRS[0])
                  for arm_store in phases.glob("*/*/store"))
    learn_rows = sum(_count_lines(path) for name in _LEARN_LEDGERS
                     for path in phases.glob(f"*/{name}"))
    return Snapshot(len(done), stored, learn_rows)


def read_int(path: Path) -> Optional[int]:
    try:
        return int(path.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def pid_alive(pid: Optional[int]) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

class Supervisor:
    def __init__(self, run_dir: Path, backoff: float, max_stalled: int,
                 poll: float, sleep: Callable[[float], None] = time.sleep):
        self.run_dir = run_dir
        self.backoff = backoff
        self.max_stalled = max_stalled
        self.poll = poll
        self.sleep = sleep

    def log(self, message: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"{stamp} {message}", flush=True)

    def wait_for_driver(self) -> Optional[int]:
        """Block until the driver is gone; return its exit code or None."""
        pidfile = self.run_dir / DRIVER_PID
        exit_file = self.run_dir / DRIVER_EXIT
        while pid_alive(read_int(pidfile)):
            self.sleep(self.poll)
        waited = 0.0
        while True:
            code = read_int(exit_file)
            if code is not None or waited >= EXIT_FILE_GRACE_SECONDS:
                return code
            self.sleep(min(self.poll, 5.0))
            waited += min(self.poll, 5.0)

    def relaunch(self) -> bool:
        """Run launch.sh; True when a driver is running afterwards or left
        an exit code to act on."""
        launch = self.run_dir / LAUNCH_SCRIPT
        if not launch.is_file():
            self.log(f"stop: {launch} does not exist; start the run with "
                     "scripts/remote/start_run_bench_host.sh first")
            return False
        # The exit code just acted on is in this log; a launch that fails
        # before starting a driver must not leave it to be read again.
        try:
            (self.run_dir / DRIVER_EXIT).unlink()
        except FileNotFoundError:
            pass
        try:
            done = subprocess.run(["bash", str(launch)],
                                  stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True,
                                  timeout=LAUNCH_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.log(f"stop: launch.sh failed: {type(exc).__name__}: {exc}")
            return False
        for line in (done.stdout + done.stderr).splitlines():
            self.log(f"  launch.sh: {line}")
        pid = read_int(self.run_dir / DRIVER_PID)
        if pid_alive(pid) or read_int(self.run_dir / DRIVER_EXIT) is not None:
            self.log(f"launch.sh exited {done.returncode}; driver pid {pid}")
            return True
        self.log(f"stop: launch.sh exited {done.returncode} and no driver "
                 "is running")
        return False

    def run(self) -> int:
        self.log(f"supervisor {os.getpid()} watching {self.run_dir} "
                 f"(backoff {self.backoff:.0f}s, max stalled "
                 f"{self.max_stalled}, poll {self.poll:.0f}s)")
        pid = read_int(self.run_dir / DRIVER_PID)
        if not pid_alive(pid) and read_int(self.run_dir / DRIVER_EXIT) is None:
            self.log("stop: no live driver and no exit code to act on")
            return 1
        stalled = 0
        before = snapshot(self.run_dir)
        while True:
            self.log(f"waiting for driver pid {read_int(self.run_dir / DRIVER_PID)}")
            code = self.wait_for_driver()
            after = snapshot(self.run_dir)
            if code == EXIT_RESUMABLE:
                stalled = next_stalled(stalled, made_progress(before, after))
            decision = decide(code, stalled, self.max_stalled)
            self.log(f"driver ended with exit {code}; progress "
                     f"{asdict(before)} -> {asdict(after)}; "
                     f"{decision.action}: {decision.reason}")
            if decision.action == "stop":
                self.log(f"stop: {decision.reason}")
                return 0 if code == EXIT_DONE else 1
            self.log(f"resuming in {self.backoff:.0f}s")
            self.sleep(self.backoff)
            before = after
            if not self.relaunch():
                return 1


def _detach(args: argparse.Namespace, run_dir: Path) -> int:
    """Start this script again in a new session and return."""
    command = [sys.executable, str(Path(__file__).resolve()), str(run_dir),
               "--backoff", str(args.backoff),
               "--max-stalled", str(args.max_stalled),
               "--poll", str(args.poll), "--foreground"]
    with open(run_dir / SUPERVISOR_LOG, "ab") as log:
        proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL,
                                env=dict(os.environ, PYTHONUNBUFFERED="1"),
                                start_new_session=True)
    time.sleep(1.0)
    if proc.poll() is not None:
        print(f"supervisor exited at once (code {proc.returncode}); see "
              f"{run_dir / SUPERVISOR_LOG}", file=sys.stderr)
        return 1
    print(f"supervisor pid {proc.pid} (own session)")
    print(f"pidfile: {run_dir / SUPERVISOR_PID}")
    print(f"log:     {run_dir / SUPERVISOR_LOG}")
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--backoff", type=float, default=DEFAULT_BACKOFF_SECONDS,
                        help="seconds to wait before a resume (default 300)")
    parser.add_argument("--max-stalled", type=int, default=DEFAULT_MAX_STALLED,
                        help="consecutive exit-3 attempts without progress "
                             "before giving up (default 5)")
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_SECONDS,
                        help="seconds between driver liveness checks")
    parser.add_argument("--foreground", action="store_true",
                        help="run in this process (the detached child uses it)")
    args = parser.parse_args(argv)
    if args.backoff < 0 or args.max_stalled < 1 or args.poll <= 0:
        parser.error("--backoff must be >= 0, --max-stalled >= 1, --poll > 0")
    run_dir = args.run_dir.resolve()
    if not run_dir.is_dir():
        parser.error(f"not a directory: {run_dir}")

    pidfile = run_dir / SUPERVISOR_PID
    holder = read_int(pidfile)
    if holder != os.getpid() and pid_alive(holder):
        print(f"refusing to start: {pidfile} names a live supervisor {holder}",
              file=sys.stderr)
        return 1
    if not args.foreground:
        return _detach(args, run_dir)
    pidfile.write_text(f"{os.getpid()}\n")
    return Supervisor(run_dir, args.backoff, args.max_stalled, args.poll).run()


if __name__ == "__main__":
    sys.exit(main())
