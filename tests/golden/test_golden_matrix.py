"""The P-1 prime acceptance gate (decision doc 2026-10-15 section 7).

Every refactor step (P0, P1a, P1b) must keep a fresh run of every combo
byte-identical to the checked-in golden matrix (after the harness's
volatile-field normalisation), and a crash+resume of two representative
combos must land on the same golden -- the standing, executable form of
"行为不变" for the multi-method refactor.

The matrix itself was generated from pre-refactor v2 code (see
tests/fixtures/golden/provenance.json); if this file fails after a
refactor step, the step changed behaviour -- fix it before proceeding,
never regenerate the golden from refactored code.
"""

import json
from pathlib import Path

import pytest

from tests.golden.harness import (
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

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "golden"

#: One exam-path and one live-path combo re-run the crash+resume variant
#: on every suite run (generation covers all eight; see generate.py).
CRASH_RESUME_COMBOS = ("exam-whole_bank-full", "live_run-retrieval-split")


def _golden(label: str) -> dict:
    return json.loads((GOLDEN_DIR / f"{label}.json").read_text(encoding="utf-8"))


def _provenance() -> dict:
    return json.loads((GOLDEN_DIR / "provenance.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("combo", COMBOS, ids=[combo_label(c) for c in COMBOS])
def test_fresh_run_matches_golden(combo, tmp_path):
    config = write_config(tmp_path / "config.yaml", combo)
    with installed_plugin(plugin_for(combo)):
        run_straight(config, tmp_path / "run")
        fresh = snapshot(tmp_path / "run")
    assert fresh == _golden(combo_label(combo))


@pytest.mark.parametrize("label", CRASH_RESUME_COMBOS)
def test_crash_resume_matches_golden(label, tmp_path):
    combo = next(c for c in COMBOS if combo_label(c) == label)
    # The mid-run crash point recorded at generation time (the 0.6
    # fraction slot), so the standing test crashes the same call every run.
    crash_at = _provenance()["combos"][label]["crash_points"][1]
    config = write_config(tmp_path / "config.yaml", combo)
    with installed_plugin(plugin_for(combo)):
        run_crash_and_resume(config, tmp_path / "run", crash_at)
        resumed = snapshot(tmp_path / "run")
    assert_resume_equivalent(_golden(label), resumed)
