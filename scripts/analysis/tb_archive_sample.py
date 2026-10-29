#!/usr/bin/env python3
"""Sample TerminalBench campaign trials from the benchmark-host archive into tmp/.

Read-only: streams one ``ssh ... tar czf -`` per trial directory (whole trial
for archive trials; for out-of-archive trials only result.json + the verifier
log, which is all the pseudo-zero cross-check needs) and extracts under a
local target dir.

Usage:
    python3 scripts/analysis/tb_archive_sample.py <manifest-root> <target-dir>
Input:  <manifest-root>/trials.txt -- one remote trial dir per line, either
        absolute or relative to EXPEDISTILL_TB_ARCHIVE_ROOT.
        A trailing ``files=TAB``-joined list restricts a trial to given files.
Output: extracted trial trees under <target-dir>.

Example:
    python3 scripts/analysis/tb_archive_sample.py tmp/tbval tmp/tbval/sample
"""

import os
import shlex
import subprocess
import sys
from pathlib import Path

HOST = os.environ.get("EXPEDISTILL_TB_HOST", "")
KEY = os.environ.get("EXPEDISTILL_TB_KEY", "")
if KEY.startswith("~"):
    KEY = os.path.expanduser(KEY)
ARCHIVE_BASE = os.environ.get("EXPEDISTILL_TB_ARCHIVE_ROOT", "")
SSH = ["ssh", "-S", "none", "-o", "BatchMode=yes", "-i", KEY, HOST]


def fetch(remote_trial: str, files, target: Path) -> None:
    remote_trial = remote_trial.rstrip("/")
    if not remote_trial.startswith("/"):
        remote_trial = f"{ARCHIVE_BASE}/{remote_trial}"
    names = [f"{remote_trial}/{f}" for f in files]
    # Only files that exist: KEEP_MODEL trials never ran the verifier.
    listing = "; ".join(f"[ -e {shlex.quote(n)} ] && echo {shlex.quote(n)}"
                        for n in names)
    cmd = SSH + [f"{{ {listing}; true; }} | tar czf - -C / -T -"]
    target.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    untar = subprocess.run(["tar", "xzf", "-", "-C", str(target)],
                           stdin=proc.stdout, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE)
    proc.stdout.close()
    _, err = proc.communicate()
    if proc.returncode != 0 or untar.returncode != 0:
        raise SystemExit(f"fetch failed for {remote_trial}: {err.decode()!r} "
                         f"/ {untar.stderr.decode()!r}")
    print(f"ok {remote_trial} ({len(files)} files)")


def main() -> None:
    root = Path(sys.argv[1]).expanduser()
    target = Path(sys.argv[2]).expanduser()
    for line in (root / "trials.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            trial, filelist = line.split("\t", 1)
            files = filelist.split("\t")
        else:
            trial, files = line, ["agent/trajectory.json", "verifier/test-stdout.txt",
                                  "verifier/reward.txt", "result.json"]
        fetch(trial, files, target)


if __name__ == "__main__":
    main()
