"""Thin ssh/scp transport for the TerminalBench remote link (node29).

One helper per operation, no business logic: the plugin composes these into
submit/poll/fetch; tests monkeypatch or fake this class, never ssh for real.

Configuration comes from the environment (see ``.env.example``):

- ``EXPEDISTILL_TB_HOST``: ``user@host`` for ssh (BatchMode, key auth only).
- ``EXPEDISTILL_TB_KEY``: path to the ssh private key.
- ``EXPEDISTILL_TB_ROOT``: remote work root, under ``~/niuyiming/`` or
  ``/data2/niuyiming/`` (outer AGENTS.md section 9).

Key material is never logged, echoed in error messages, or written anywhere:
ssh timeouts are re-raised without the subprocess argv (which contains the
``-i`` path), and scp failures report only the remote path and the exit code.
"""

import os
import subprocess
from typing import Optional, Tuple

from expedistill.infra.reliability import ConfigError, InfrastructureError

__all__ = [
    "ENV_HOST",
    "ENV_KEY",
    "ENV_ROOT",
    "RemoteLink",
]

ENV_HOST = "EXPEDISTILL_TB_HOST"
ENV_KEY = "EXPEDISTILL_TB_KEY"
ENV_ROOT = "EXPEDISTILL_TB_ROOT"

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
        missing = [
            name for name, value in (
                (ENV_HOST, self.host),
                (ENV_KEY, self.key_path),
                (ENV_ROOT, self.root),
            ) if not str(value).strip()
        ]
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
        argv = ["ssh", *_SSH_OPTIONS, "-i", self.key_path, self.host, cmd]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            # The exception's str() embeds the argv, which contains the key
            # path: re-raise without it.
            raise InfrastructureError(
                f"ssh to {self.host} timed out after {timeout:g}s") from None
        return proc.stdout, proc.stderr, proc.returncode

    def put(self, local: str, remote: str,
            timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        """Copy a local file to the remote host (scp)."""
        argv = ["scp", *_SSH_OPTIONS, "-i", self.key_path, str(local),
                f"{self.host}:{remote}"]
        self._scp(argv, f"put {local} -> {remote}", timeout)

    def get(self, remote: str, local: str,
            timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        """Copy a remote file to the local host (scp)."""
        argv = ["scp", *_SSH_OPTIONS, "-i", self.key_path,
                f"{self.host}:{remote}", str(local)]
        self._scp(argv, f"get {remote} -> {local}", timeout)

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
