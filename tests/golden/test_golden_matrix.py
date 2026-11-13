"""The golden-matrix gate.

Every combo's fresh run must stay identical to the checked-in golden
matrix (after the harness's volatile-field normalisation), whatever the
worker count, and a crash+resume of every combo must land on the same
golden. One combo also crashes in the "revision persisted, next practice
not yet run" window. The scenario itself is checked from the golden's own
ledgers: every practice leaves weak failures, every learn phase sees card
evidence, and the run takes the gap, trigger, content and delete routes.

A failure means the change altered run artifacts: either accidentally
(fix the change) or deliberately (regenerate the golden in the SAME
commit; the diff is what the review reads).
"""

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from expedistill.benchmarks.base import Task
from expedistill.composites import evidence
from expedistill.core.ids import make_skill_id
from expedistill.infra.store import KIND_DIRS
from expedistill.methods.revise import OUTCOME_OF_SIDE

from tests.golden.harness import (
    COMBOS,
    CRASH_POINTS,
    SEQUENCE,
    GoldenPlugin,
    assert_resume_equivalent,
    combo_label,
    installed_plugin,
    run_crash_and_resume,
    run_straight,
    snapshot,
    write_config,
)
from tests.golden.revision_window import crash_in_revision_window

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "golden"

LABELS = tuple(combo_label(combo) for combo in COMBOS)


def _golden(label: str) -> dict:
    return json.loads((GOLDEN_DIR / f"{label}.json").read_text(encoding="utf-8"))


def _provenance() -> dict:
    return json.loads((GOLDEN_DIR / "provenance.json").read_text(encoding="utf-8"))


def _combo(label: str) -> dict:
    return next(c for c in COMBOS if combo_label(c) == label)


def _phase_dirs(golden: dict, role: str) -> list:
    """The golden's phase directories of ``role``, in sequence order."""
    return sorted({key.split("/")[1] for key in golden
                   if key.startswith("phases/")
                   and key.split("/")[1].endswith(f"_{role}")})


def test_fixture_set_is_exactly_the_matrix():
    on_disk = {path.stem for path in GOLDEN_DIR.glob("*.json")} - {"provenance"}
    assert on_disk == set(LABELS)
    assert set(_provenance()["combos"]) == set(LABELS)
    assert _provenance()["crash_kinds"] == [name for name, _, _
                                            in CRASH_POINTS]


@pytest.mark.parametrize("label", LABELS)
def test_fresh_run_matches_golden(label, tmp_path):
    config = write_config(tmp_path / "config.yaml", _combo(label))
    with installed_plugin(GoldenPlugin()):
        run_straight(config, tmp_path / "run")
        fresh = snapshot(tmp_path / "run")
    assert fresh == _golden(label)


@pytest.mark.parametrize("label", LABELS)
def test_eight_workers_write_the_same_snapshot_as_one(label, tmp_path):
    config = write_config(tmp_path / "config.yaml", _combo(label))
    with installed_plugin(GoldenPlugin()):
        run_straight(config, tmp_path / "one", workers=1)
        run_straight(config, tmp_path / "eight", workers=8)
        one = snapshot(tmp_path / "one")
        eight = snapshot(tmp_path / "eight")
    assert eight == one
    assert one == _golden(label)


@pytest.mark.parametrize("label", LABELS)
@pytest.mark.parametrize("slot", range(len(CRASH_POINTS)),
                         ids=[name for name, _, _ in CRASH_POINTS])
def test_crash_resume_matches_golden(label, slot, tmp_path):
    # The crash points recorded at generation time, so every suite run
    # crashes before the same calls.
    crash_at = _provenance()["combos"][label]["crash_points"][slot]
    config = write_config(tmp_path / "config.yaml", _combo(label))
    with installed_plugin(GoldenPlugin()):
        run_crash_and_resume(config, tmp_path / "run", crash_at)
        resumed = snapshot(tmp_path / "run")
    assert_resume_equivalent(_golden(label), resumed)


@pytest.mark.parametrize("label", LABELS)
def test_every_practice_leaves_weak_failures(label):
    golden = _golden(label)
    practices = _phase_dirs(golden, "practice")
    assert len(practices) == SEQUENCE.count("practice")
    for phase in practices:
        sheets = [value for key, value in golden.items()
                  if key.startswith(f"phases/{phase}/answer_sheets/")
                  and key.endswith(".json")]
        assert any(sheet["execution"]["role"] == "weak"
                   and sheet["grading"]["verdict"] == "failure"
                   for sheet in sheets), phase


@pytest.mark.parametrize("label", LABELS)
def test_every_learn_phase_routes_evidence_to_its_card_outcome(label):
    """Each learn phase has card evidence, and each piece of it reached
    the outcome its route prescribes in the same phase: a gap type's new
    card is written, a trigger-evidence card trigger-revised, a
    content-evidence card content-revised or (the delete rule) discarded.
    Across the run every route is taken."""
    golden = _golden(label)
    learns = _phase_dirs(golden, "learn")
    assert len(learns) == SEQUENCE.count("learn")
    categories, outcomes = set(), set()
    for phase in learns:
        rows = golden[f"phases/{phase}/evidence.jsonl"]
        receipts = {}
        for row in golden.get(f"phases/{phase}/outcomes.jsonl", []):
            receipts.setdefault(row["skill_id"], set()).add(row["outcome"])
        routed = [row for row in rows if row["category"] != evidence.NONE]
        assert routed, f"{phase} saw no card evidence"
        for row in routed:
            category = row["category"]
            if category == evidence.GAP:
                assert row["skill_id"] is None
                assert "written" in receipts[make_skill_id(row["type_id"])]
            elif category == evidence.TRIGGER:
                assert OUTCOME_OF_SIDE["trigger"] in receipts[row["skill_id"]]
            else:
                assert receipts[row["skill_id"]] & {
                    OUTCOME_OF_SIDE["content"], "discarded"}
        categories |= {row["category"] for row in rows}
        outcomes |= {outcome for found in receipts.values()
                     for outcome in found}
    assert categories == set(evidence.CATEGORIES)
    assert {"written", "discarded", *OUTCOME_OF_SIDE.values()} <= outcomes


def test_the_stub_runs_an_empty_bank_folder(tmp_path):
    """An empty bank's folder unit still carries a manifest, with no
    files: the stub opens nothing and has no skills in reach."""
    task = Task(task_id="t3", instruction="the test task",
                meta={"skill_folder": {"catalog_text": "", "files": []},
                      "skill_folder_dir": str(tmp_path)})
    outcome = GoldenPlugin().run(task, SimpleNamespace(role="executor_weak"),
                                 "", tmp_path)
    assert [step["action"] for step in outcome.steps] == ["inspect", "run"]
    assert outcome.correctness is False


def test_revision_window_crash_lands_before_the_next_practice(tmp_path):
    """At the crash point the revision is in the skills store and the next
    practice has stored no trajectory and written no answer sheet;
    resuming must then land on the golden snapshot."""
    label = "whole_bank"
    run_dir = tmp_path / "run"
    config = write_config(tmp_path / "config.yaml", _combo(label))
    assert crash_in_revision_window(config, run_dir)
    golden = _golden(label)
    revising = next(phase for phase in _phase_dirs(golden, "learn")
                    if f"phases/{phase}/revisions.jsonl" in golden)
    following = next(phase for phase in _phase_dirs(golden, "practice")
                     if phase > revising)
    assert (run_dir / "phases" / revising / "learn_summary.json").exists()
    # A trajectory id carries its phase's tag, the phase directory's name.
    trajectories = run_dir / KIND_DIRS["trajectories"]
    assert not [path for path in trajectories.glob("*.json")
                if following in unquote(path.name)]
    sheets = run_dir / "phases" / following / "answer_sheets"
    assert list(sheets.glob("*.json")) == []
    with installed_plugin(GoldenPlugin()):
        run_straight(config, run_dir)
    assert_resume_equivalent(golden, snapshot(run_dir))
