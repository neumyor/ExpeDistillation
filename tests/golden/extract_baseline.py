"""Extract the semantic baseline bundle from a git commit (M4.1).

Usage:
    .venv/bin/python tests/golden/extract_baseline.py \
        [--commit 69904f7] [--out tests/fixtures/golden_semantic_baseline]

The baseline is the golden matrix as of commit 69904f7 -- the last
v1-object, stage-layout snapshot, i.e. the "pre-change truth" M4.3's v4
artifacts must reproduce under the E1-E6 criteria (implementation plan
section 1.3 and section 8). For every combo this reads
``tests/fixtures/golden/<label>.json`` AT THAT COMMIT (a plain ``git
show`` -- no checkout), projects it onto the pre-registered semantic
views (tests/golden/semantics.py) and writes the bundle:

    <out>/<label>.json   the combo's E1-E6 views
    <out>/provenance.json  source commit, per-combo model call counts and
                           crash points, the E5 variant machinery's inputs

Output is byte-deterministic (sorted keys, fixed indent, no timestamps):
re-running the extraction must reproduce the committed bundle exactly --
that is the standing identity test, so the instrument can never drift
away from its own evidence.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.golden.harness import COMBOS, combo_label  # noqa: E402
from tests.golden.semantics import (  # noqa: E402
    CRASH_FRACTIONS,
    semantic_views,
)

#: The pre-change truth (implementation plan section 1.3): the last
#: commit whose golden fixtures still carry v1 objects and the stage
#: directory layout.
DEFAULT_COMMIT = "69904f7"

_SOURCE_TREE = "tests/fixtures/golden"


def _git_snapshot(commit: str, label: str) -> dict:
    blob = f"{_SOURCE_TREE}/{label}.json"
    result = subprocess.run(["git", "show", f"{commit}:{blob}"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"cannot read {blob} at {commit}: "
                         f"{result.stderr.strip()}")
    return json.loads(result.stdout)


def _full_commit(commit: str) -> str:
    result = subprocess.run(["git", "rev-parse", commit],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--commit", default=DEFAULT_COMMIT,
                        help="source commit (default: %(default)s)")
    parser.add_argument("--out", type=Path, default=None,
                        help="output directory (default: the committed "
                             "bundle location)")
    args = parser.parse_args()
    out = args.out or (Path(__file__).resolve().parents[1] / "fixtures"
                       / "golden_semantic_baseline")
    out.mkdir(parents=True, exist_ok=True)

    source_provenance = _git_snapshot(args.commit, "provenance")
    provenance = {
        "source_commit": _full_commit(args.commit),
        "source_tree": _SOURCE_TREE,
        "extraction": "tests/golden/extract_baseline.py",
        "crash_fractions": list(CRASH_FRACTIONS),
        "combos": {},
    }
    for combo in COMBOS:
        label = combo_label(combo)
        views = semantic_views(_git_snapshot(args.commit, label), combo)
        path = out / f"{label}.json"
        path.write_text(
            json.dumps(views, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
        combo_source = source_provenance["combos"][label]
        provenance["combos"][label] = {
            "model_calls": combo_source["model_calls"],
            "crash_points": combo_source["crash_points"],
        }
        print(f"{label}: extracted "
              f"({combo_source['model_calls']} model calls at source)")
    (out / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8")
    print(f"bundle written to {out} (source {provenance['source_commit']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
