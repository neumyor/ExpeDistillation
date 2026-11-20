"""The learn phase's ablation switches on a hand-built run.

Same stage as test_phase_learn.py (the reference sequence, the stub
benchmark, the fake provider), with one switch of ``review`` or ``author``
turned on in the ``learn`` definition: ``cards: false``,
``revise_scope``, ``evolve_profile: false`` and ``trigger_view: prefix``.
"""

import json

import pytest

from expedistill.composites import evolving_text
from expedistill.composites import learn_ledger as ledger
from expedistill.composites.phase_bank import load_phase_bank
from expedistill.composites.provenance import PROVENANCE_NAME
from expedistill.core.models import Skill
from expedistill.infra.store import read_json, read_jsonl
from expedistill.stages import phase_learn
from tests.helpers.golden_harness import PinnedClient
from tests.helpers.learn_runs import (
    WORK,
    LearnRun,
    read_skill,
    run_through,
    trial,
)

pytestmark = pytest.mark.usefixtures("golden_plugin")

CARD = "type/002/skill"
PROFILE_PROMPT = "You maintain the accumulated profile of a weaker"
PLAYBOOK_PROMPT = "You maintain the GLOBAL PLAYBOOK"
CARD_PROMPT = "You are writing ONE skill card"
SEGMENT_PROMPT = "You are given the view"
NO_PROFILE = "No weak-model profile is provided"


def practice(index):
    """t1 opens the answer card before failing; t2 never opens it."""
    return [trial("t1", "weak", (read_skill(CARD),) + WORK, correct=False),
            trial("t2", "weak", WORK, correct=False)]


def unread_practice(index):
    """Neither trial can open a card: there is none."""
    return [trial("t1", "weak", WORK, correct=False),
            trial("t2", "weak", WORK, correct=False)]


def _rows(run, index, name):
    return read_jsonl(run.phase_dir(index) / name)


def _prompts(client, anchor):
    return [call["user"] for call in client.calls
            if call["user"].startswith(anchor)]


def _outcomes(run, index):
    return [(row["action"], row["outcome"], row["version"])
            for row in _rows(run, index, ledger.OUTCOMES_NAME)]


# -- cards: false ---------------------------------------------------------------------

def test_cards_off_publishes_the_bank_untouched_and_keeps_the_texts_evolving(
        tmp_path):
    client = PinnedClient()
    run = LearnRun(tmp_path / "run", client=client,
                   review={"cards": False})
    run_through(run, 3, unread_practice)

    first = read_json(run.phase_dir(1) / phase_learn.SUMMARY_NAME)
    second = read_json(run.phase_dir(3) / phase_learn.SUMMARY_NAME)
    # Evidence is still routed and recorded: every failure is a gap, since
    # no card ever exists.
    assert first["evidence"] == {"content": 0, "gap": 2, "none": 0,
                                 "trigger": 0}
    assert second["evidence"]["gap"] == 2
    # Each gap type is receipted as skipped, never written.
    for summary in (first, second):
        assert (summary["card_units"], summary["outcomes"]) == (
            0, {"skipped_cards_off": 1})
        assert summary["bank"]["members"] == []
    # Nothing is written, rewritten or discarded.
    assert run.store.ids("skills") == []
    assert not _prompts(client, CARD_PROMPT)
    for index in (1, 3):
        assert load_phase_bank(run.run_dir, run.label(index)) == []
        receipt, = _rows(run, index, ledger.OUTCOMES_NAME)
        assert (receipt["target_id"], receipt["action"],
                receipt["outcome"], receipt["skill_id"]) == (
            "type/002", "add", "skipped_cards_off", None)
        assert not (run.phase_dir(index) / PROVENANCE_NAME).exists()
    # The profile and the playbook evolve as usual.
    assert (first["profile_version"], first["playbook_version"]) == (1, 1)
    assert (second["profile_version"], second["playbook_version"]) == (2, 2)
    assert len(_prompts(client, PROFILE_PROMPT)) == 2
    assert len(_prompts(client, PLAYBOOK_PROMPT)) == 2


def test_cards_off_cuts_only_the_weak_failures(tmp_path):
    full = LearnRun(tmp_path / "full")
    full.execute(0, [trial(task, role, WORK, correct=role == "strong")
                     for task in ("t1", "t2")
                     for role in ("strong", "weak")])
    off = LearnRun(tmp_path / "off", review={"cards": False})
    off.execute(0, [trial(task, role, WORK, correct=role == "strong")
                    for task in ("t1", "t2") for role in ("strong", "weak")])

    # The strong baseline cuts only feed card material.
    assert full.learn(1)["segments_cut"] == 12
    assert off.learn(1)["segments_cut"] == 6


def test_the_next_phase_after_a_cards_off_phase_receives_the_empty_bank(
        tmp_path):
    run = LearnRun(tmp_path / "run", review={"cards": False})
    run_through(run, 1)
    manifest = run.manifest(1)
    assert manifest["files"] == [] and manifest["catalog_text"] == ""


# -- revise_scope ---------------------------------------------------------------------

@pytest.mark.parametrize("scope", ["trigger_only", "content_only"])
def test_a_gap_writes_its_card_under_every_scope(tmp_path, scope):
    run = LearnRun(tmp_path / "run", review={"revise_scope": scope})
    run_through(run, 1)
    summary = read_json(run.phase_dir(1) / phase_learn.SUMMARY_NAME)
    assert summary["outcomes"] == {"written": 1}
    assert summary["bank"]["members"] == [CARD]


def test_content_only_records_the_trigger_evidence_without_rewriting(
        tmp_path):
    run = LearnRun(tmp_path / "run", review={"revise_scope": "content_only"})
    run_through(run, 3, practice)

    summary = read_json(run.phase_dir(3) / phase_learn.SUMMARY_NAME)
    assert (summary["evidence"]["content"], summary["evidence"]["trigger"]) \
        == (1, 1)
    assert _outcomes(run, 3) == [
        ("revise_content", "content_revised", 2),
        ("revise_trigger", "skipped_out_of_scope", None)]
    card = run.store.load("skills", CARD, Skill)
    assert (card.version, [entry["side"] for entry in card.rewrite_history]) \
        == (2, ["content"])
    assert [row["cause"] for row in _rows(run, 3, PROVENANCE_NAME)] \
        == ["revise_content"]
    assert [row["category"] for row in _rows(
        run, 3, phase_learn.EVIDENCE_NAME)].count("trigger") == 1
    # The trigger side is byte for byte what the first phase wrote.
    first = load_phase_bank(run.run_dir, run.label(1))[0]
    assert (card.summary, card.when_to_use) == (first["summary"],
                                                first["when_to_use"])


def test_trigger_only_records_the_content_evidence_without_rewriting(
        tmp_path):
    run = LearnRun(tmp_path / "run", review={"revise_scope": "trigger_only"})
    run_through(run, 3, practice)

    assert _outcomes(run, 3) == [
        ("revise_content", "skipped_out_of_scope", None),
        ("revise_trigger", "trigger_revised", 2)]
    card = run.store.load("skills", CARD, Skill)
    assert (card.version, [entry["side"] for entry in card.rewrite_history]) \
        == (2, ["trigger"])
    first = load_phase_bank(run.run_dir, run.label(1))[0]
    assert card.steps == first["steps"]
    assert [row["category"] for row in _rows(
        run, 3, phase_learn.EVIDENCE_NAME)].count("content") == 1


def test_trigger_only_never_deletes_a_card_for_content_evidence(tmp_path):
    run = LearnRun(tmp_path / "run", review={"revise_scope": "trigger_only"})
    run_through(run, 5, practice)

    card = run.store.load("skills", CARD, Skill)
    assert (card.status, card.version) == ("active", 3)
    assert _outcomes(run, 5) == [
        ("revise_content", "skipped_out_of_scope", None),
        ("revise_trigger", "trigger_revised", 3)]


def test_content_only_still_deletes_a_card_after_two_content_phases(
        tmp_path):
    run = LearnRun(tmp_path / "run", review={"revise_scope": "content_only"})
    run_through(run, 5, practice)

    card = run.store.load("skills", CARD, Skill)
    assert (card.status, card.discard_reason) == (
        "discarded", "content_failed_twice")
    assert [(row["action"], row["outcome"])
            for row in _rows(run, 5, ledger.OUTCOMES_NAME)] == [
        ("delete", "discarded"), ("revise_trigger", "skipped_out_of_scope")]


def test_the_scope_receipts_are_not_written_twice_on_a_resume(tmp_path):
    run = LearnRun(tmp_path / "run", review={"revise_scope": "content_only"})
    run_through(run, 3, practice)
    before = _rows(run, 3, ledger.OUTCOMES_NAME)
    (run.phase_dir(3) / phase_learn.SUMMARY_NAME).unlink()

    run.learn(3)

    assert _rows(run, 3, ledger.OUTCOMES_NAME) == before


# -- evolve_profile: false --------------------------------------------------------------

def test_without_a_profile_every_writer_reads_the_neutral_section(tmp_path):
    client = PinnedClient()
    run = LearnRun(tmp_path / "run", client=client,
                   review={"evolve_profile": False})
    run_through(run, 3, practice)

    assert not _prompts(client, PROFILE_PROMPT)
    for index in (1, 3):
        profile = evolving_text.read_text(run.run_dir, run.label(index),
                                          "profile")
        assert (profile.text, profile.version, profile.source_sheet_ids) \
            == ("", 1, [])
        assert read_json(run.phase_dir(index) / phase_learn.SUMMARY_NAME)[
            "profile_version"] == 1
    # The card writer, both rewrite prompts and the playbook writer.
    for anchor in (CARD_PROMPT, PLAYBOOK_PROMPT,
                   "A skill card written for a weaker"):
        prompts = _prompts(client, anchor)
        assert prompts and all(NO_PROFILE in prompt for prompt in prompts)
    # The playbook still evolves.
    assert read_json(run.phase_dir(3) / phase_learn.SUMMARY_NAME)[
        "playbook_version"] == 2


def test_with_the_default_the_profile_evolves_and_reaches_the_writers(
        tmp_path):
    client = PinnedClient()
    run = LearnRun(tmp_path / "run", client=client)
    run_through(run, 3, practice)

    assert len(_prompts(client, PROFILE_PROMPT)) == 2
    profile = evolving_text.read_text(run.run_dir, run.label(3), "profile")
    assert profile.text and profile.version == 2
    assert not any(NO_PROFILE in prompt
                   for prompt in _prompts(client, CARD_PROMPT))


# -- trigger_view: prefix -----------------------------------------------------------------

class _TaggedCards(PinnedClient):
    """A card whose every field names the call that wrote it: ``full``
    when the segments show their own last step, ``prefix`` when they show
    only the steps before the segment."""

    def _card_write(self, user):
        kind = "full" if "action: finish" in user else "prefix"
        reply = json.loads(super()._card_write(user).split("\n", 1)[1]
                           .rsplit("\n", 1)[0])
        return "```json\n" + json.dumps({
            "summary": f"{kind} summary.", "when_to_use": f"{kind} when.",
            "steps": [f"{kind} {step}" for step in reply["steps"]],
            "not_applicable_when": f"{kind} not applicable."}) + "\n```"


def test_prefix_authoring_composes_the_card_from_two_calls(tmp_path):
    client = _TaggedCards()
    run = LearnRun(tmp_path / "run", client=client,
                   author={"trigger_view": "prefix"})
    run_through(run, 1)

    card = run.store.load("skills", CARD, Skill)
    assert (card.summary, card.when_to_use) == ("prefix summary.",
                                                "prefix when.")
    assert all(step.startswith("full ") for step in card.steps)
    assert card.not_applicable_when == "full not applicable."
    first, second = _prompts(client, CARD_PROMPT)
    assert "action: finish" in first
    # The prefix view stops before the segment: the last step is cut off,
    # the steps before it are there.
    assert "action: finish" not in second and "action: act" in second
    receipt, = _rows(run, 1, ledger.OUTCOMES_NAME)
    assert (receipt["outcome"], receipt["reason"]) == ("written", "gap")


def test_the_default_card_is_one_call_with_the_segments_own_steps(tmp_path):
    client = _TaggedCards()
    run = LearnRun(tmp_path / "run", client=client)
    run_through(run, 1)

    card = run.store.load("skills", CARD, Skill)
    assert card.summary == "full summary."
    assert len(_prompts(client, CARD_PROMPT)) == 1


def test_a_segment_at_step_zero_has_no_prefix_and_the_receipt_says_so(
        tmp_path):
    # K = 1: the one segment of every trajectory starts at step 0.
    client = _TaggedCards()
    run = LearnRun(tmp_path / "run", client=client, review={"K": 1},
                   author={"trigger_view": "prefix"})
    run_through(run, 1)

    assert len(_prompts(client, CARD_PROMPT)) == 1
    receipt, = _rows(run, 1, ledger.OUTCOMES_NAME)
    assert receipt["outcome"] == "written"
    assert receipt["reason"] == (
        f"gap; {phase_learn.PREFIX_UNAVAILABLE}")
    card = run.store.load("skills", receipt["skill_id"], Skill)
    assert (card.summary, card.when_to_use) == ("full summary.",
                                                "full when.")
