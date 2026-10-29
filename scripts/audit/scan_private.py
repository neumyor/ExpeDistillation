"""Scan tracked files for private deployment details before publishing.

Usage:  python scripts/audit/scan_private.py [--patterns FILE]
Input : the files git tracks (and the staged ones), plus a pattern list.
Output: one line per hit; exit code 1 when anything matches, 0 when clean.

The default patterns catch the shapes that must never reach a public remote:
private IPv4 addresses, login accounts in ``user@host`` form, home and work
directories under a personal account, and service endpoints on internal
domains. Names that belong to one deployment (a host nickname, an account,
an internal domain) differ per site, so they are read from a local file that
is itself gitignored: ``tmp/private_patterns.txt`` (one regex per line, ``#``
comments allowed). Pass ``--patterns`` to use another file.

``log.md``, ``runs/`` and ``tmp/`` are not tracked, so they are not scanned.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_PATTERNS_FILE = REPO / "tmp" / "private_patterns.txt"

#: Shapes that are private everywhere. Documentation addresses (RFC 5737 /
#: 3849) and loopback are allowed.
BUILTIN = {
    "private IPv4": r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}"
                    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b",
    "personal absolute path": r"/Users/[A-Za-z][\w.-]*/",
    "login account": r"\b[a-z][\w.-]{2,}@(?:\d{1,3}\.){3}\d{1,3}\b",
}
#: Data files and reports quoting benchmark trajectories: they contain example
#: addresses and sandbox home directories that are not deployment details.
SKIP_PREFIXES = ("configs/seed/", "tests/fixtures/", "docs/reports/")


def tracked_files():
    out = subprocess.check_output(["git", "ls-files"], cwd=REPO, text=True)
    return [name for name in out.splitlines() if name]


def load_patterns(path):
    patterns = dict(BUILTIN)
    if path.exists():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if line and not line.startswith("#"):
                patterns[f"local:{number}"] = line
    return {name: re.compile(rx) for name, rx in patterns.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--patterns", type=Path, default=DEFAULT_PATTERNS_FILE)
    args = parser.parse_args()
    patterns = load_patterns(args.patterns)
    hits = 0
    for name in tracked_files():
        if name.startswith(SKIP_PREFIXES):
            continue
        path = REPO / name
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for label, rx in patterns.items():
                if rx.search(line):
                    print(f"{name}:{number}: [{label}] {line.strip()[:110]}")
                    hits += 1
                    break
    print(f"{hits} hit(s)" if hits else "clean", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
