"""The ablation switches through a whole run (the golden stub benchmark,
the fake provider, the seven-phase reference sequence).

Each switch runs the sequence end to end; ``cards: false`` additionally
proves that the practice and test phases run on the empty skill folder, and
the resume check crashes between the two writing calls of prefix
authoring.
"""

import importlib.util
from pathlib import Path

import pytest
import yaml

from expedistill.core.models import Skill
from expedistill.infra.store import Store, read_json, read_jsonl
from tests.helpers.golden_harness import (
    CARD_ANCHOR,
    COMBOS,
    GoldenPlugin,
    assert_resume_equivalent,
    config_dict,
    installed_plugin,
    run_crash_and_resume,
    run_straight,
    snapshot,
)

_AUDIT = Path(__file__).resolve().parents[2] / "scripts" / "audit" \
    / "audit_run.py"
_SPEC = importlib.util.spec_from_file_location("audit_run", _AUDIT)
audit_run = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit_run)

FOLDER = next(combo for combo in COMBOS if combo["delivery"] == "folder")


def _config(tmp_path, *, review=None, author=None) -> Path:
    config = config_dict(FOLDER)
    config["phases"]["learn"]["review"].update(review or {})
    config["phases"]["learn"]["author"].update(author or {})
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def _phase(run_dir, role):
    return sorted((run_dir / "phases").glob(f"p*_{role}"))


def _run(tmp_path, name="run", **switches):
    config = _config(tmp_path, **switches)
    with installed_plugin(GoldenPlugin()):
        prompts = run_straight(config, tmp_path / name)
    return tmp_path / name, prompts


def test_cards_off_runs_every_phase_on_an_empty_skill_folder(tmp_path):
    run_dir, prompts = _run(tmp_path, review={"cards": False})
    normal, _ = _run(tmp_path, "normal")

    assert not [prompt for prompt in prompts
                if prompt.startswith(CARD_ANCHOR)]
    assert Store(run_dir).ids("skills") == []
    for learn in _phase(run_dir, "learn"):
        summary = read_json(learn / "learn_summary.json")
        assert summary["bank"]["members"] == []
        assert set(summary["outcomes"]) == {"skipped_cards_off"}
        assert (learn / "skill_bank.jsonl").read_text() == ""
        assert not (learn / "provenance.jsonl").exists()
    # The practices and the test deliver a folder without any file, in the
    # same format as a folder with cards, with the evolved playbook.
    for phase in _phase(run_dir, "practice") + _phase(run_dir, "test"):
        assert read_json(phase / "phase_summary.json")["complete"] is True
        rows = read_jsonl(phase / "units.jsonl")
        assert rows
        for row in rows:
            manifest = row["aux"]["skill_folder"]
            assert manifest["files"] == [] and manifest["catalog_text"] == ""
            # The same manifest format as a folder with cards.
            reference = read_jsonl(normal / phase.relative_to(run_dir)
                                   / "units.jsonl")[0]["aux"]["skill_folder"]
            assert set(manifest) == set(reference)
        folder = phase / "skill_folder"
        assert folder.is_dir() and not any(folder.iterdir())
    playbook = read_json(_phase(run_dir, "learn")[-1] / "playbook.json")
    assert playbook["version"] == 3 and playbook["text"]
    assert audit_run.main([str(run_dir)]) == 0


@pytest.mark.parametrize("switches", [
    {"review": {"revise_scope": "content_only"}},
    {"review": {"revise_scope": "trigger_only"}},
    {"review": {"evolve_profile": False}},
    {"author": {"trigger_view": "prefix"}},
], ids=["content_only", "trigger_only", "no_profile", "prefix"])
def test_every_switch_finishes_the_sequence_with_the_same_snapshot_under_eight_workers(
        tmp_path, switches):
    one, _ = _run(tmp_path, "one", **switches)
    config = _config(tmp_path, **switches)
    with installed_plugin(GoldenPlugin()):
        run_straight(config, tmp_path / "eight", workers=8)
        eight = snapshot(tmp_path / "eight")
        straight = snapshot(one)
    assert eight == straight
    for phase in _phase(one, "test"):
        assert read_json(phase / "phase_summary.json")["complete"] is True
    assert audit_run.main([str(one)]) == 0


def test_the_scoped_runs_keep_the_other_sides_unrewritten(tmp_path):
    for scope, kept in (("content_only", "content"),
                        ("trigger_only", "trigger")):
        run_dir, _ = _run(tmp_path, scope, review={"revise_scope": scope})
        store = Store(run_dir)
        skills = [store.load("skills", skill_id, Skill)
                  for skill_id in store.ids("skills")]
        sides = {entry["side"] for skill in skills
                 for entry in skill.rewrite_history}
        assert sides <= {kept}, (scope, sides)
        outcomes = {row["outcome"]
                    for learn in _phase(run_dir, "learn")
                    if (learn / "outcomes.jsonl").exists()
                    for row in read_jsonl(learn / "outcomes.jsonl")}
        assert "skipped_out_of_scope" in outcomes


def test_a_crash_between_the_two_writing_calls_resumes_to_the_same_run(
        tmp_path):
    switches = {"author": {"trigger_view": "prefix"}}
    config = _config(tmp_path, **switches)
    with installed_plugin(GoldenPlugin()):
        prompts = run_straight(config, tmp_path / "straight")
        straight = snapshot(tmp_path / "straight")
        hits = [index for index, prompt in enumerate(prompts, start=1)
                if prompt.startswith(CARD_ANCHOR)]
        # The first learn phase writes two cards. The search card's one
        # segment starts at step 0 (no prefix, one call); the answer
        # card's writing is two calls. Crash before the second of them.
        assert len(hits) == 3
        run_crash_and_resume(config, tmp_path / "resumed", hits[2])
        resumed = snapshot(tmp_path / "resumed")
    assert_resume_equivalent(straight, resumed)
