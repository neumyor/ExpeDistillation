"""Detach-launch a long expedistill CLI command with a pidfile and return at once.

macOS has no ``setsid(1)``, and ``nohup ... & disown`` only leaves the
shell's job table -- it does NOT leave the launcher's process group, so a
launcher that gets killed takes the driver with it (outer AGENTS.md, the
2026-09-20 incident). Here every long-lived process gets its own session
(``start_new_session=True``):

* a detached *waiter* (this script with ``--waiter``) is started in a new
  session with SIGHUP and SIGINT ignored, so an ssh drop or Ctrl-C at the
  terminal that ran the launch cannot reach it;
* the waiter starts the *driver* (the command) in another new session,
  writes its PID to the pidfile, waits for it and writes the exit status to
  ``<pidfile>.exit``; it appends one line per event to the console log.

The foreground launcher refuses to start when the pidfile names a live
process, removes a stale pidfile and a stale ``<pidfile>.exit``, starts the
waiter, waits until the pidfile is written and the driver has survived its
first seconds, prints the paths and exits. Liveness is ``kill -0 "$(cat
<pidfile>)"``, never ``pgrep -f``.

Usage:
    .venv/bin/python scripts/smoke/launch_detached.py \\
        --pidfile <run>/driver.pid --log <run>/driver.console.log -- \\
        <command...>

The caller's environment is inherited by the waiter and the driver.
Exit code: 0 once the driver runs detached; 1 when the pidfile names a live
process, or the driver or the waiter died during startup.
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

#: How long the launcher waits for the waiter to write the pidfile.
PIDFILE_TIMEOUT_SECONDS = 30.0
#: How long the driver must survive before the launch counts as started
#: (import errors and argument errors die well inside this).
SETTLE_SECONDS = 2.0
_POLL_SECONDS = 0.1


def exit_path(pidfile: Path) -> Path:
    """The file the waiter writes the driver's exit status to."""
    return pidfile.with_name(pidfile.name + ".exit")


def read_pid(pidfile: Path) -> Optional[int]:
    """The PID in ``pidfile``; None when absent or unreadable."""
    try:
        return int(pidfile.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def read_exit_code(path: Path) -> Optional[int]:
    """The exit status in an exit file; None when absent or unreadable."""
    try:
        return int(path.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def pid_alive(pid: Optional[int]) -> bool:
    """``kill -0``: a process we may not signal still exists."""
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _log_line(log, message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log.write(f"[launch_detached {stamp}] {message}\n".encode())
    log.flush()


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def run_waiter(pidfile: Path, log_path: Path, command: List[str]) -> int:
    """Start the driver in its own session, record its PID, wait, record
    its exit status. Runs inside the detached waiter process."""
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    # The console log must be live: a block-buffered driver hid a stall's
    # progress during the 2026-10-13 test phase.
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    with open(log_path, "ab") as log:
        try:
            proc = subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, env=env, start_new_session=True)
        except OSError as exc:
            _log_line(log, f"waiter {os.getpid()} could not start the "
                           f"driver: {type(exc).__name__}: {exc}")
            _write_atomic(exit_path(pidfile), "127\n")
            return 127
        _write_atomic(pidfile, f"{proc.pid}\n")
        _log_line(log, f"waiter {os.getpid()} started driver pid {proc.pid}: "
                       + " ".join(command))
        rc = proc.wait()
        _write_atomic(exit_path(pidfile), f"{rc}\n")
        _log_line(log, f"driver pid {proc.pid} exited with code {rc}")
    return rc


def launch(pidfile: Path, log_path: Path, command: List[str]) -> int:
    """The foreground launch: refuse a live driver, start the waiter,
    confirm the driver started, return."""
    old = read_pid(pidfile)
    if pid_alive(old):
        print(f"refusing to start: {pidfile} names a live process {old}",
              file=sys.stderr)
        return 1
    if pidfile.exists():
        print(f"stale pidfile {pidfile} (pid {old}); replacing it",
              file=sys.stderr)
    _unlink(pidfile)
    _unlink(exit_path(pidfile))

    env = dict(os.environ, PYTHONUNBUFFERED="1")
    with open(log_path, "ab") as log:
        waiter = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--waiter",
             "--pidfile", str(pidfile), "--log", str(log_path), "--",
             *command],
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            env=env, start_new_session=True)

    deadline = time.monotonic() + PIDFILE_TIMEOUT_SECONDS
    while read_pid(pidfile) is None:
        if exit_path(pidfile).exists() or waiter.poll() is not None:
            print(f"the driver did not start; see {log_path}", file=sys.stderr)
            return 1
        if time.monotonic() > deadline:
            print(f"no pidfile after {PIDFILE_TIMEOUT_SECONDS:.0f}s; see "
                  f"{log_path}", file=sys.stderr)
            return 1
        time.sleep(_POLL_SECONDS)
    pid = read_pid(pidfile)

    settle = time.monotonic() + SETTLE_SECONDS
    while time.monotonic() < settle:
        code = read_exit_code(exit_path(pidfile))
        if code is not None:
            print(f"driver {pid} exited within {SETTLE_SECONDS:.0f}s with "
                  f"code {code}; see {log_path}", file=sys.stderr)
            return 1
        time.sleep(_POLL_SECONDS)

    print(f"launched driver pid {pid} (own session); waiter pid {waiter.pid}")
    print(f"pidfile: {pidfile}")
    print(f"exit file (written when the driver ends): {exit_path(pidfile)}")
    print(f"console log: {log_path}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pidfile", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--waiter", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="the command to run, after --")
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no command given after --")
    pidfile = args.pidfile.resolve()
    log_path = args.log.resolve()
    for path in (pidfile, log_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    if args.waiter:
        return run_waiter(pidfile, log_path, command)
    return launch(pidfile, log_path, command)


if __name__ == "__main__":
    sys.exit(main())
