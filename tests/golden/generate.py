"""Regenerate the golden artifact matrix (P-1 prime; decision doc
2026-10-15 section 7).

Usage:
    .venv/bin/python tests/golden/generate.py [--out tests/fixtures/golden]

For every combo (acceptance x delivery x mode) this writes
``<out>/<label>.json`` (the normalised artifact snapshot of one straight
pipeline run) plus ``provenance.json`` (base commit, per-combo model call
counts, crash points). Each combo additionally runs three crash+resume
variants whose final artifacts must equal the straight snapshot -- a
resume-equivalence failure ABORTS generation loudly; the matrix never
lands on a resume bug or an unnormalised volatile field.

DISCIPLINE: run this only from pre-refactor v2 code (the commit recorded
in provenance.json). Regenerating from refactored code would make the
instrument certify itself.
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
    assert_resume_equivalent,
    combo_label,
    installed_plugin,
    plugin_for,
    run_crash_and_resume,
    run_straight,
    snapshot,
    write_config,
)

#: Crash points as fractions of the straight run's model call count.
CRASH_FRACTIONS = (0.3, 0.6, 0.95)


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
        "crash_fractions": list(CRASH_FRACTIONS),
        "combos": {},
    }
    for combo in COMBOS:
        label = combo_label(combo)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            config = write_config(tmp / "config.yaml", combo)
            with installed_plugin(plugin_for(combo)):
                run_dir = tmp / "run"
                calls = run_straight(config, run_dir)
                golden = snapshot(run_dir)
                crash_points = []
                for fraction in CRASH_FRACTIONS:
                    point = max(1, min(calls - 1, round(calls * fraction)))
                    crash_points.append(point)
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
              f"3 crash+resume variants equal")
    (out / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8")
    print(f"matrix written to {out} (base {provenance['base_commit']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
