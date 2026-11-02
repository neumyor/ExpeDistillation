"""blocks/adherence.py: both judges' output validation, the card-step
parser, and what each prompt is given."""

import pytest

from expedistill.blocks.adherence import (
    ADHERENCE_STATUSES,
    card_steps,
    judge_adherence,
    judge_applicability,
    render_card_listing,
)
from expedistill.infra.reliability import ModelOutputError
from expedistill.llm.fake import json_script, make_fake_handle

CARDS = [
    {"skill_id": "s/alpha", "description": "Alpha teaches alpha. Use when A."},
    {"skill_id": "s/beta", "description": "Beta\nteaches beta."},
]
CARD = "\n".join([
    "[When to use] When it matters.",
    "[Steps]",
    "1. first",
    "2. second line one",
    "second line two",
    "3. third",
    "[Do not use when] Never.",
])


def _entry(skill_id, applicable=True, first_step=1, reason="because"):
    return {"skill_id": skill_id, "applicable": applicable,
            "first_step": first_step, "reason": reason}


def _applicability(entries, cards=CARDS, n_steps=5):
    handle = make_fake_handle("judge", json_script({"skills": entries}))
    return judge_applicability(
        "do the task", cards, "step 0\naction: a\nobservation: b",
        n_steps, handle), handle


def _adherence(steps, card=CARD):
    handle = make_fake_handle("judge", json_script({"steps": steps}))
    return judge_adherence(card, "step 2\naction: a\nobservation: b",
                           handle), handle


def _step(number, status="followed", evidence="e"):
    return {"number": number, "status": status, "evidence": evidence}


# ---------------------------------------------------------------------------
# card_steps / render_card_listing
# ---------------------------------------------------------------------------

def test_card_steps_numbers_items_and_keeps_continuation_lines():
    assert card_steps(CARD) == [
        "first", "second line one\nsecond line two", "third"]


def test_card_steps_is_not_split_by_a_stray_number_in_a_continuation():
    card = "[Steps]\n1. run\n3. not the next item\n2. real second"
    assert card_steps(card) == ["run\n3. not the next item", "real second"]


@pytest.mark.parametrize("card", [
    "no markers at all", "[When to use] x\n[Do not use when] y",
    "[Steps]\nfree text only"])
def test_a_card_without_numbered_steps_has_none(card):
    assert card_steps(card) == []


def test_listing_puts_id_and_one_line_description_per_card():
    assert render_card_listing(CARDS) == (
        "- skill_id: s/alpha\n  description: Alpha teaches alpha. Use when A."
        "\n- skill_id: s/beta\n  description: Beta teaches beta.")


@pytest.mark.parametrize("cards", [
    [], [{"skill_id": "", "description": "d"}],
    [{"skill_id": "a", "description": 3}],
    [{"skill_id": "a", "description": "d"},
     {"skill_id": "a", "description": "e"}]])
def test_a_malformed_card_list_is_a_caller_bug(cards):
    with pytest.raises(ValueError):
        render_card_listing(cards)


# ---------------------------------------------------------------------------
# judge_applicability
# ---------------------------------------------------------------------------

def test_applicability_returns_every_card_in_list_order():
    result, handle = _applicability([
        _entry("s/beta", applicable=False, first_step=None),
        _entry("s/alpha", first_step=3, reason=" a reason ")])

    assert list(result) == ["s/alpha", "s/beta"]
    assert result["s/alpha"] == {
        "applicable": True, "first_step": 3, "reason": "a reason"}
    assert result["s/beta"]["applicable"] is False
    prompt = handle.client.calls[0]["user"]
    assert "do the task" in prompt
    assert render_card_listing(CARDS) in prompt
    assert "action: a" in prompt


def test_a_card_that_does_not_apply_never_reports_a_first_step():
    result, _ = _applicability([
        _entry("s/alpha", applicable=False, first_step=2),
        _entry("s/beta", first_step=None)])

    assert result["s/alpha"]["first_step"] is None
    assert result["s/beta"] == {
        "applicable": True, "first_step": None, "reason": "because"}


@pytest.mark.parametrize("entries, fragment", [
    ([_entry("s/alpha"), _entry("s/other")], "unknown skill_id"),
    ([_entry("s/alpha")], "leaves out listed card"),
    ([_entry("s/alpha"), _entry("s/alpha"), _entry("s/beta")],
     "appears twice"),
    ([_entry("s/alpha", applicable="yes"), _entry("s/beta")],
     "'applicable'"),
    ([_entry("s/alpha", first_step=9), _entry("s/beta")], "'first_step'"),
    ([_entry("s/alpha", first_step=-1), _entry("s/beta")], "'first_step'"),
    ([_entry("s/alpha", first_step="2"), _entry("s/beta")], "'first_step'"),
    ([_entry("s/alpha", first_step=True), _entry("s/beta")],
     "'first_step'"),
    ([_entry("s/alpha", reason=None), _entry("s/beta")], "'reason'"),
    (["s/alpha", "s/beta"], "must be an object"),
])
def test_applicability_rejects_a_reply_that_breaks_the_contract(
        entries, fragment):
    with pytest.raises(ModelOutputError, match=fragment):
        _applicability(entries)


@pytest.mark.parametrize("payload", [{"cards": []}, {"skills": "none"}, [1]])
def test_applicability_rejects_a_reply_of_the_wrong_shape(payload):
    handle = make_fake_handle("judge", json_script(payload))
    with pytest.raises(ModelOutputError):
        judge_applicability("t", CARDS, "view", 3, handle)


# ---------------------------------------------------------------------------
# judge_adherence
# ---------------------------------------------------------------------------

def test_adherence_returns_one_item_per_numbered_step_in_order():
    items, handle = _adherence([
        _step(3, "not_reached", ""), _step(1, "followed", "ran  it"),
        _step(2, "partial", "q")])

    assert items == [
        {"number": 1, "status": "followed", "evidence": "ran it"},
        {"number": 2, "status": "partial", "evidence": "q"},
        {"number": 3, "status": "not_reached", "evidence": ""}]
    prompt = handle.client.calls[0]["user"]
    assert CARD in prompt
    assert "has 3 numbered item(s)" in prompt
    assert "action: a" in prompt


@pytest.mark.parametrize("steps, fragment", [
    ([_step(1), _step(2)], "leaves out step"),
    ([_step(1), _step(1), _step(2), _step(3)], "appears twice"),
    ([_step(1), _step(2), _step(3), _step(4)], "'number'"),
    ([_step(0), _step(2), _step(3)], "'number'"),
    ([_step(True), _step(2), _step(3)], "'number'"),
    ([_step(1, "done"), _step(2), _step(3)], "'status'"),
    ([_step(1, "Followed"), _step(2), _step(3)], "'status'"),
    ([_step(1, evidence=None), _step(2), _step(3)], "'evidence'"),
    (["x", _step(2), _step(3)], "must be an object"),
])
def test_adherence_rejects_a_reply_that_breaks_the_contract(steps, fragment):
    with pytest.raises(ModelOutputError, match=fragment):
        _adherence(steps)


def test_adherence_statuses_are_the_four_words():
    assert ADHERENCE_STATUSES == (
        "followed", "partial", "skipped", "not_reached")


def test_a_card_without_numbered_steps_is_not_sent_to_the_model():
    handle = make_fake_handle("judge", json_script({"steps": []}))
    with pytest.raises(ValueError):
        judge_adherence("[Steps]\nfree text", "view", handle)
    assert handle.client.calls == []
