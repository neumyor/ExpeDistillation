"""Detach-launch a long expedistill CLI command with a pidfile (AGENTS.md).

macOS has no ``setsid(1)``, and ``nohup ... & disown`` only leaves the
shell's job table -- it does NOT leave the launcher's process group, so a
launcher that gets killed takes the driver with it (outer AGENTS.md, the
2026-09-20 incident). This script does what the incident report prescribes:
``subprocess.Popen(..., start_new_session=True)`` (``os.setsid`` for the
child), a pidfile with the child PID for liveness checks (``kill -0``, never
``pgrep -f``), and the child's console output appended to a log file.

Usage:
    .venv/bin/python scripts/smoke/launch_detached.py \\
        --pidfile <run>/driver.pid --log <run>/driver.console.log -- \\
        <command...>

The caller's environment (``set -a; . ./.env; set +a``) is inherited by the
child. Exit code 0 once the child is running detached; the child's own exit
status is written to ``<pidfile>.exit`` when it finishes.
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pidfile", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="the command to run, after --")
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no command given after --")

    for path in (args.pidfile, args.log):
        path.parent.mkdir(parents=True, exist_ok=True)
    if args.pidfile.exists():
        old = args.pidfile.read_text().strip()
        try:
            os.kill(int(old), 0)
            parser.error(f"refusing to start: pidfile {args.pidfile} names a "
                         f"live process {old}")
        except (ValueError, ProcessLookupError, PermissionError):
            print(f"warning: stale pidfile {args.pidfile} (pid {old}); "
                  "overwriting", file=sys.stderr)

    # start_new_session=True = os.setsid() in the child: its own session and
    # process group, immune to the launcher's death (outer AGENTS.md rule 7).
    with open(args.log, "ab") as log_fh:
        # Console log must be live: the child's stdout is block-buffered
        # otherwise, and during the 2026-10-13 stage-6 stall the buffer hid
        # the current restart's progress entirely (the log showed only old
        # restarts' lines), which sent the diagnosis down wrong paths.
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        proc = subprocess.Popen(
            command,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            env=env,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    args.pidfile.write_text(f"{proc.pid}\n")
    print(f"launched pid {proc.pid} (session leader); pidfile {args.pidfile}")
    print(f"console log: {args.log}")

    # Reap from a new shell later via the exit file; here we just confirm the
    # child survived its first second (import errors die fast).
    time.sleep(1.0)
    try:
        os.kill(proc.pid, 0)
    except ProcessLookupError:
        rc = proc.wait()
        print(f"child died within 1s, exit code {rc}; see {args.log}",
              file=sys.stderr)
        args.pidfile.with_suffix(".pid.exit").write_text(f"{rc}\n")
        return 1

    # Park until the child exits, then record its status. We are the parent,
    # so the child is never a zombie while we live. If THIS supervisor is
    # killed, the child (a session leader) survives, is reparented to init
    # and keeps running; its status is then only in the console log and the
    # run's own ledger, never in the .exit file.
    def _on_term(_signum, _frame):
        # Deliberately does NOT signal the child: the whole point of the
        # detach is that killing the launcher leaves the driver alone.
        sys.exit(0)

    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    rc = proc.wait()
    args.pidfile.with_suffix(".pid.exit").write_text(f"{rc}\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
