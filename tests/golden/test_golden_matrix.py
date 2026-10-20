"""The P-1 prime acceptance gate (decision doc 2026-10-15 section 7).

Every combo's fresh run must stay identical to the checked-in golden
matrix (after the harness's volatile-field normalisation), and a
crash+resume of every combo must land on the same golden -- ordinary
snapshot tests, members of the standing suite. The live combos also crash
in the "revision persisted, re-answer not yet run" window (C-1).

A failure after a refactor step means the step changed run artifacts:
either accidentally (fix the step) or deliberately (regenerate the
golden in the SAME commit and show the diff in its message -- that diff
is what the step's review reads).
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
from tests.golden.revision_window import _crash_in_revision_window

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "golden"

#: Every combo re-runs the crash+resume variant on every suite run
#: (generation covers the same four; see generate.py).
CRASH_RESUME_COMBOS = tuple(combo_label(combo) for combo in COMBOS)


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


def test_revision_window_crash_lands_mid_reanswer(tmp_path):
    """The window crash's meaning, checked at the crash point itself:
    the revision IS in the skills store and NOTHING of the re-answer
    round has persisted (zero round-1 trajectories -- the K1 dispatch
    stops at the raise). Resuming must then land on the straight run's
    snapshot."""
    combo = next(c for c in COMBOS
                 if combo_label(c) == "live_run-whole_bank-split")
    config = write_config(tmp_path / "config.yaml", combo)
    with installed_plugin(plugin_for(combo)):
        run_straight(config, tmp_path / "straight")
        golden = snapshot(tmp_path / "straight")
    _crash_in_revision_window(config, combo, tmp_path / "run")
    skills = (tmp_path / "run" / "stage3_generation" / "skills")
    revised = [path.name for path in skills.glob("*.json")
               if "REVISED-BY-G11" in path.read_text(encoding="utf-8")]
    assert revised
    round01 = (tmp_path / "run" / "stage4_acceptance" / "live_run"
               / "round01" / "stage1_baseline" / "trajectories")
    assert list(round01.glob("*.json")) == []
    with installed_plugin(plugin_for(combo)):
        run_straight(config, tmp_path / "run")
    assert_resume_equivalent(golden, snapshot(tmp_path / "run"))


def test_ignore_keys_comparison_covers_only_the_named_keys():
    # The pre-registered P1b criterion, on a real golden: a tampered
    # manifest.json passes under ignore=("manifest.json",) and fails
    # without it -- the helper can never hide drift elsewhere.
    import pytest

    from tests.golden.harness import assert_equal_ignoring

    golden = _golden("exam-whole_bank-full")
    tampered = json.loads(json.dumps(golden))
    tampered["manifest.json"]["tasks"] = 999
    assert_equal_ignoring(golden, tampered, ignore=("manifest.json",))
    with pytest.raises(AssertionError):
        assert_equal_ignoring(golden, tampered, ignore=())
