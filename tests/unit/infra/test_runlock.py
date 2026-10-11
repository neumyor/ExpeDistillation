"""B7 RunLock: O_EXCL lock file, holder identity, stale takeover, no pgrep."""

import json
import os
import socket
import subprocess

import pytest

from expedistill.infra.reliability import ConfigError
from expedistill.infra.runlock import RunLock, RunLockedError


def _write_lock(run_dir, pid, hostname=None, acquired_at="2026-10-10T00:00:00"):
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "run.lock"
    path.write_text(json.dumps({
        "pid": pid,
        "hostname": hostname or socket.gethostname(),
        "acquired_at": acquired_at,
    }))


def test_acquire_creates_and_release_removes(tmp_path):
    lock = RunLock(tmp_path / "run")
    assert lock.acquire() is None
    assert (tmp_path / "run" / "run.lock").exists()
    lock.release()
    assert not (tmp_path / "run" / "run.lock").exists()
    lock.release()  # idempotent


def test_context_manager(tmp_path):
    with RunLock(tmp_path / "run"):
        assert (tmp_path / "run" / "run.lock").exists()
    assert not (tmp_path / "run" / "run.lock").exists()


def test_second_lock_refuses_a_live_holder(tmp_path):
    # A live pid that is definitely not a lock holder process: our own.
    _write_lock(tmp_path / "run", pid=os.getpid())
    with pytest.raises(RunLockedError) as excinfo:
        RunLock(tmp_path / "run").acquire()
    assert str(os.getpid()) in str(excinfo.value)
    assert socket.gethostname() in str(excinfo.value)


def test_second_lock_refuses_a_real_live_child(tmp_path):
    child = subprocess.Popen(["sleep", "60"])
    try:
        _write_lock(tmp_path / "run", pid=child.pid)
        with pytest.raises(RunLockedError):
            RunLock(tmp_path / "run").acquire()
    finally:
        child.terminate()
        child.wait()


def test_stale_lock_is_taken_over_with_warning(tmp_path):
    # A child that exited and was reaped: its pid is genuinely dead.
    child = subprocess.Popen(["true"])
    child.wait()
    _write_lock(tmp_path / "run", pid=child.pid)
    lock = RunLock(tmp_path / "run")
    warning = lock.acquire()
    assert warning is not None
    assert str(child.pid) in warning
    holder = json.loads((tmp_path / "run" / "run.lock").read_text())
    assert holder["pid"] == os.getpid()


def test_foreign_hostname_lock_is_treated_as_held(tmp_path):
    # The holder's pid cannot be probed on another machine; refuse rather
    # than risk two writers on a shared filesystem.
    _write_lock(tmp_path / "run", pid=1, hostname="some-other-host")
    with pytest.raises(RunLockedError):
        RunLock(tmp_path / "run").acquire()


def test_unreadable_lock_is_replaced_with_warning(tmp_path):
    (tmp_path / "run").mkdir(parents=True)
    (tmp_path / "run" / "run.lock").write_text("not json at all")
    warning = RunLock(tmp_path / "run").acquire()
    assert warning is not None
    assert "unreadable" in warning


def test_double_acquire_in_same_process_raises(tmp_path):
    lock = RunLock(tmp_path / "run")
    lock.acquire()
    with pytest.raises(RunLockedError):
        lock.acquire()


def test_run_locked_error_is_config_error():
    assert issubclass(RunLockedError, ConfigError)
    assert issubclass(RunLockedError, RuntimeError)


def test_lock_roundtrip_survives_rapid_reacquire(tmp_path):
    for _ in range(3):
        lock = RunLock(tmp_path / "run")
        lock.acquire()
        lock.release()
