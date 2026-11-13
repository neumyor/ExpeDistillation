"""Regenerate the golden artifact matrix.

Usage:
    .venv/bin/python tests/golden/generate.py [--out tests/fixtures/golden]

For every combo (tests/golden/harness.py's COMBOS) this writes
``<out>/<label>.json`` (the normalised artifact snapshot of one straight
pipeline run) plus ``provenance.json`` (base commit, per-combo model call
counts, crash points). Each combo additionally runs once with
``--workers 8`` and three crash+resume variants (``harness.CRASH_POINTS``:
between two diagnoses, before a rewrite, between two new cards), whose
final artifacts must all equal the straight snapshot -- any difference
ABORTS generation loudly; the matrix never lands on a resume or
parallelism bug or an unnormalised volatile field.

Regenerate whenever a change DELIBERATELY alters run artifacts and land
the diff in the same commit: that diff is what the change's reviewers
read. A change that alters nothing must not touch the matrix.
"""

import json
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.golden.harness import (  # noqa: E402
    COMBOS,
    CRASH_POINTS,
    GoldenPlugin,
    assert_resume_equivalent,
    combo_label,
    crash_points as find_crash_points,
    installed_plugin,
    run_crash_and_resume,
    run_straight,
    snapshot,
    write_config,
)


def _base_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        check=True).stdout.strip()


def main() -> int:
    out = Path(sys.argv[sys.argv.index("--out") + 1]) \
        if "--out" in sys.argv else Path(__file__).resolve().parents[1] \
        / "fixtures" / "golden"
    out.mkdir(parents=True, exist_ok=True)
    provenance = {
        "base_commit": _base_commit(),
        "generated_on": date.today().isoformat(),
        "crash_kinds": [name for name, _, _ in CRASH_POINTS],
        "combos": {},
    }
    for combo in COMBOS:
        label = combo_label(combo)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            config = write_config(tmp / "config.yaml", combo)
            with installed_plugin(GoldenPlugin()):
                run_dir = tmp / "run"
                prompts = run_straight(config, run_dir)
                calls = len(prompts)
                golden = snapshot(run_dir)
                run_straight(config, tmp / "run_workers8", workers=8)
                if snapshot(tmp / "run_workers8") != golden:
                    raise SystemExit(
                        f"{label}: --workers 8 differs from --workers 1")
                crash_points = find_crash_points(prompts)
                for point in crash_points:
                    run_crash_and_resume(config, tmp / f"run_crash_{point}",
                                         point)
                    try:
                        assert_resume_equivalent(
                            golden, snapshot(tmp / f"run_crash_{point}"))
                    except AssertionError as exc:
                        raise SystemExit(
                            f"resume inequivalence in {label} at crash "
                            f"point {point}: {exc}") from None
        (out / f"{label}.json").write_text(
            json.dumps(golden, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
        provenance["combos"][label] = {"model_calls": calls,
                                       "crash_points": crash_points}
        print(f"{label}: {calls} model calls, "
              f"--workers 8 and {len(crash_points)} crash+resume "
              f"variants equal")
    (out / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8")
    print(f"matrix written to {out} (base {provenance['base_commit']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
