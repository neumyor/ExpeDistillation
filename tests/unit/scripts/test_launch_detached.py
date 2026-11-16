"""The detached launcher returns at once, keeps driver.pid naming the
driver, clears a stale exit file and records the exit code from its
detached waiter."""

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "smoke" / "launch_detached.py"
spec = importlib.util.spec_from_file_location("launch_detached", SCRIPT)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def _wait_for(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _launch(tmp_path, code):
    pidfile = tmp_path / "driver.pid"
    log = tmp_path / "driver.console.log"
    started = time.monotonic()
    rc = launcher.main(["--pidfile", str(pidfile), "--log", str(log), "--",
                        sys.executable, "-c", code])
    return rc, time.monotonic() - started, pidfile, log


def test_returns_while_the_driver_runs_then_records_its_exit(tmp_path):
    (tmp_path / "driver.pid").write_text("999999999\n")
    (tmp_path / "driver.pid.exit").write_text("3\n")
    rc, elapsed, pidfile, log = _launch(
        tmp_path, "import time; print('driver up', flush=True); "
                  "time.sleep(4); raise SystemExit(3)")
    assert rc == 0
    assert elapsed < 4
    pid = launcher.read_pid(pidfile)
    assert launcher.pid_alive(pid)
    assert pid != os.getpid()
    assert not launcher.exit_path(pidfile).exists()  # the stale one is gone
    assert _wait_for(lambda: launcher.read_exit_code(
        launcher.exit_path(pidfile)) == 3)
    text = log.read_text()
    assert "driver up" in text
    assert f"driver pid {pid} exited with code 3" in text


def test_a_driver_dying_at_startup_fails_the_launch(tmp_path):
    rc, _, pidfile, _ = _launch(tmp_path, "raise SystemExit(1)")
    assert rc == 1
    assert launcher.read_exit_code(launcher.exit_path(pidfile)) == 1


def test_refuses_while_the_pidfile_names_a_live_process(tmp_path):
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (tmp_path / "driver.pid").write_text(f"{holder.pid}\n")
        (tmp_path / "driver.pid.exit").write_text("3\n")
        rc, _, pidfile, _ = _launch(tmp_path, "pass")
        assert rc == 1
        assert launcher.read_pid(pidfile) == holder.pid
        assert launcher.exit_path(pidfile).exists()
    finally:
        holder.kill()
        holder.wait()
