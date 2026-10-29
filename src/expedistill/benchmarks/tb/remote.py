"""Thin ssh/scp transport for the TerminalBench remote link (the benchmark host).

One helper per operation, no business logic: the plugin composes these into
submit/poll/fetch; tests monkeypatch or fake this class, never ssh for real.

Configuration comes from the environment (see ``.env.example``):

- ``EXPEDISTILL_TB_HOST``: ``user@host`` for ssh (BatchMode, key auth only),
  or ``local`` when the driver itself runs on the benchmark host: commands
  then run in a local ``bash`` and ``put``/``get`` copy files, with no ssh.
- ``EXPEDISTILL_TB_KEY``: path to the ssh private key (not used by ``local``).
- ``EXPEDISTILL_TB_ROOT``: remote work root, under the prefixes named by
  ``EXPEDISTILL_TB_ALLOWED_ROOTS`` when that is set.

Key material is never logged, echoed in error messages, or written anywhere:
ssh timeouts are re-raised without the subprocess argv (which contains the
``-i`` path), and scp failures report only the remote path and the exit code.
"""

import os
import shutil
import subprocess
import sys
from typing import Dict, Optional, Tuple

from expedistill.infra.reliability import ConfigError, InfrastructureError

__all__ = [
    "ENV_HOST",
    "ENV_KEY",
    "ENV_ROOT",
    "LOCAL_HOST",
    "RemoteLink",
]

ENV_HOST = "EXPEDISTILL_TB_HOST"
ENV_KEY = "EXPEDISTILL_TB_KEY"
ENV_ROOT = "EXPEDISTILL_TB_ROOT"

#: The ``EXPEDISTILL_TB_HOST`` value that means "this machine". It is spelled
#: out because an empty host must stay a configuration error: a laptop with a
#: forgotten variable must not silently run remote commands locally.
LOCAL_HOST = "local"

#: What a local command inherits. An ssh session starts from a clean login
#: environment; the driver's own variables (its API key, its venv) must not
#: leak into the benchmark runner.
_LOCAL_ENV_KEYS = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "SHELL",
                   "TMPDIR")

#: BatchMode=yes refuses password prompts instead of hanging forever;
#: accept-new trusts a host on first contact but rejects silent key changes.
_SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new")

#: Default round-trip bound for one ssh/scp call (seconds).
DEFAULT_TIMEOUT_SECONDS = 120.0


class RemoteLink:
    """One (host, key, root) triple with run/put/get helpers.

    ``run`` returns ``(stdout, stderr, rc)`` so callers can distinguish a
    failing remote command (its own exit code) from a dead transport (an
    ``InfrastructureError``). ``put``/``get`` raise ``InfrastructureError``
    on a non-zero scp exit; their messages carry the remote path, never the
    key path.
    """

    def __init__(self,
                 host: Optional[str] = None,
                 key_path: Optional[str] = None,
                 root: Optional[str] = None) -> None:
        self.host = host if host is not None else os.environ.get(ENV_HOST, "")
        self.key_path = key_path if key_path is not None else os.environ.get(ENV_KEY, "")
        self.root = root if root is not None else os.environ.get(ENV_ROOT, "")
        self.local = self.host.strip() == LOCAL_HOST
        required = [(ENV_HOST, self.host), (ENV_ROOT, self.root)]
        if not self.local:
            required.insert(1, (ENV_KEY, self.key_path))
        missing = [name for name, value in required if not str(value).strip()]
        if missing:
            raise ConfigError(
                "TerminalBench remote link is not configured: "
                + ", ".join(missing)
                + " unset or empty. Fill them in .env (see .env.example).")
        if not str(self.root).startswith(("/", "~")):
            raise ConfigError(
                f"{ENV_ROOT} must be an absolute remote path (got {self.root!r}); "
                "see .env.example.")

    def run(self, cmd: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Tuple[str, str, int]:
        """Run ``cmd`` on the remote host; return (stdout, stderr, rc)."""
        if self.local:
            argv = ["bash", "-c", cmd]
            options = {"env": _local_env(),
                       "cwd": os.path.expanduser("~")}
        else:
            argv = ["ssh", *_SSH_OPTIONS, "-i", self.key_path, self.host, cmd]
            options = {}
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=timeout, check=False, **options)
        except subprocess.TimeoutExpired:
            # The exception's str() embeds the argv, which contains the key
            # path: re-raise without it.
            where = "local command" if self.local else f"ssh to {self.host}"
            raise InfrastructureError(
                f"{where} timed out after {timeout:g}s") from None
        return proc.stdout, proc.stderr, proc.returncode

    def put(self, local: str, remote: str,
            timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        """Copy a local file to the remote host (scp)."""
        if self.local:
            self._copy(local, remote, f"put {local} -> {remote}")
            return
        argv = ["scp", *_SSH_OPTIONS, "-i", self.key_path, str(local),
                f"{self.host}:{remote}"]
        self._scp(argv, f"put {local} -> {remote}", timeout)

    def get(self, remote: str, local: str,
            timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        """Copy a remote file to the local host (scp)."""
        if self.local:
            self._copy(remote, local, f"get {remote} -> {local}")
            return
        argv = ["scp", *_SSH_OPTIONS, "-i", self.key_path,
                f"{self.host}:{remote}", str(local)]
        self._scp(argv, f"get {remote} -> {local}", timeout)

    @staticmethod
    def _copy(source: str, target: str, what: str) -> None:
        try:
            shutil.copyfile(os.path.expanduser(str(source)),
                            os.path.expanduser(str(target)))
        except OSError as exc:
            raise InfrastructureError(
                f"local copy failed {what}: {exc.strerror or exc}") from None

    def _scp(self, argv, what: str, timeout: float) -> None:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise InfrastructureError(
                f"scp to {self.host} timed out after {timeout:g}s ({what})") from None
        if proc.returncode != 0:
            detail = " ".join(proc.stderr.split())[-300:]
            raise InfrastructureError(
                f"scp failed (rc={proc.returncode}) {what}"
                + (f": {detail}" if detail else ""))


def _local_env() -> Dict[str, str]:
    """The clean environment of a local command (see ``_LOCAL_ENV_KEYS``).

    ``PATH`` is the driver's own minus its interpreter's ``bin``, so a bare
    ``python3`` in a runner script resolves as it would over ssh.
    """
    env = {key: os.environ[key] for key in _LOCAL_ENV_KEYS
           if key in os.environ}
    own_bin = os.path.join(sys.prefix, "bin")
    entries = [entry for entry in os.environ.get("PATH", "").split(os.pathsep)
               if entry and entry != own_bin]
    env["PATH"] = os.pathsep.join(entries) or "/usr/bin:/bin"
    return env
