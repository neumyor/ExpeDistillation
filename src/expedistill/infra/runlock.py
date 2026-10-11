"""B7 part 1: exclusive run-directory lock (``<run_dir>/run.lock``).

The 2026-09-20 incident (outer AGENTS.md section 1) had two trainers
writing into one run directory at once; the fix there was an flock, whose
weakness is that the holder cannot be named after the fact. This lock is an
O_CREAT|O_EXCL file that records pid, hostname and timestamp, so a second
start fails *with the holder's identity in the error*. Liveness is probed
with ``os.kill(pid, 0)`` -- never ``pgrep -f``, which matches the launcher
shell (same incident: the supervisor adopted its own launcher as the driver
and started a second one).

A stale lock (holder pid dead and on this host) is taken over with a
warning rather than blocking a resume forever; a lock from another hostname
cannot be probed, so it is treated as held and must be removed by hand.
"""

import json
import os
import socket
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from expedistill.infra.reliability import ConfigError

LOCK_NAME = "run.lock"


class RunLockedError(ConfigError, RuntimeError):
    """Another live process holds the run directory's lock. RuntimeError too,
    so generic "cannot proceed" handlers catch it."""


class RunLock:
    """Exclusive writer lock for a run directory, held while the object is
    alive or inside its ``with`` block; ``release`` is idempotent."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / LOCK_NAME
        self._held = False

    def acquire(self) -> Optional[str]:
        """Take the lock; returns a warning string on stale takeover, else None.

        Raises RunLockedError naming the holder when a live process owns it.
        """
        if self._held:
            raise RunLockedError(
                f"{self.run_dir} is already locked by this process")
        payload = json.dumps(self._holder_info())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path),
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            holder = self._read_holder()
            if holder is not None and self._holder_alive(holder):
                raise RunLockedError(
                    f"{self.run_dir} is locked by pid {holder.get('pid')} "
                    f"on {holder.get('hostname')} since "
                    f"{holder.get('acquired_at')}; if this is wrong, remove "
                    f"{self.path} by hand and restart") from None
            self._replace_lock(payload)
            self._held = True
            if holder is None:
                return (f"replaced unreadable lock file {self.path}; the "
                        "previous holder could not be identified")
            return (f"took over stale {self.path}: pid {holder.get('pid')} "
                    f"on {holder.get('hostname')} is no longer running")
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        self._held = True
        return None

    def release(self) -> None:
        if self._held:
            self._held = False
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _holder_info() -> dict:
        return {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }

    def _read_holder(self) -> Optional[dict]:
        try:
            holder = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return holder if isinstance(holder, dict) else None

    @staticmethod
    def _holder_alive(holder: dict) -> bool:
        """Probe the holder's pid with os.kill(pid, 0) -- never pgrep.

        Anything that cannot be verified (foreign hostname, malformed pid)
        counts as alive: a wrongly-refused start costs one manual look, a
        wrongly-allowed start corrupts the run.
        """
        pid = holder.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            return True
        if holder.get("hostname") != socket.gethostname():
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _replace_lock(self, payload: str) -> None:
        """Take over a stale lock with an atomic replace, not unlink+create:
        the unlink route would let two simultaneous takeovers both win."""
        fd, temp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=LOCK_NAME + ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, self.path)

    # -- context manager ----------------------------------------------------

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
