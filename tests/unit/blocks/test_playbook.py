"""G13: the playbook is read from the end of the reply; a reply that breaks
the contract is asked again with the reason, never guessed at."""

import pytest

from expedistill.blocks.playbook import (
    MAX_PLAYBOOK_CHARS, MIN_PLAYBOOK_CHARS, extract_playbook, write_playbook)
from expedistill.infra.reliability import ModelOutputError
from expedistill.llm import make_fake_handle
from expedistill.prompts.profile import PROFILE_ROLES

PROMPT = "ALFWORLD_PLAYBOOK_WRITE"
BODY = ("Search every place once. " * 20).strip()      # above the minimum


@pytest.fixture(autouse=True)
def _alfworld_prompt_registered():
    from tests.helpers.plugins import ensure_plugin
    ensure_plugin("alfworld")


def _block(text):
    return f"<playbook>\n{text}\n</playbook>"


# -- extract_playbook ---------------------------------------------------------

def test_the_playbook_is_the_span_ending_the_reply():
    assert extract_playbook("thinking...\n" + _block(BODY) + "\n") == BODY


def test_drafts_and_talk_about_the_tags_before_the_answer_are_ignored():
    # Shape of the 2026-11-05 K=3 reply: tags quoted from the prompt, two
    # full drafts, then the final playbook behind "Let's final." on the same
    # line. The answer is the span from the LAST opening tag.
    reply = (
        'The prompt says a line that says <playbook> and </playbook>.\n'
        + _block("DRAFT ONE " + BODY) + "\nstill thinking, our content has "
        "<playbook> tags; okay.\n" + _block("DRAFT TWO " + BODY)
        + "\nreasoning...\nLet's final." + _block(BODY))
    assert extract_playbook(reply) == BODY


@pytest.mark.parametrize("reply, reason", [
    ("no tags at all", "does not end with the closing"),
    (_block(BODY) + "\nthanks!", "does not end with the closing"),
    (BODY + "\n</playbook>", "no opening"),
    (_block("short"), "at least"),
    (_block("x" * (MAX_PLAYBOOK_CHARS + 1)), "limit is"),
    ("<playbook>\n" + BODY + "\n</playbook>\n" + BODY + "\n</playbook>",
     "appears between"),
])
def test_a_reply_that_breaks_the_contract_names_the_problem(reply, reason):
    with pytest.raises(ValueError, match=reason):
        extract_playbook(reply)


def test_a_long_final_text_is_never_cut_to_fit():
    long_final = _block("x" * (MAX_PLAYBOOK_CHARS + 1))
    with pytest.raises(ValueError):
        extract_playbook(_block(BODY) + "\nmore thinking\n" + long_final)


def test_the_minimum_is_a_real_threshold():
    assert MIN_PLAYBOOK_CHARS > len("...")


# -- write_playbook -----------------------------------------------------------

def test_facts_profile_and_an_empty_note_reach_the_first_prompt():
    handle = make_fake_handle("author", [_block(BODY)])
    text = write_playbook(handle, "THE-FACTS", prompt_name=PROMPT,
                          weak_profile="reader keeps two steps")
    assert text == BODY
    user = handle.client.calls[0]["user"]
    assert "THE-FACTS" in user
    assert PROFILE_ROLES["playbook_reader"] in user
    assert "reader keeps two steps" in user
    assert "Your previous reply was rejected" not in user


def test_the_write_keeps_the_callers_temperature():
    # The write is sampled, deliberately: pinning temperature 0 made the
    # author (reasoning effort max) burn its whole output budget on
    # reasoning and return no content. The block must not change the
    # sampling the role table gives it.
    handle = make_fake_handle("author", [_block(BODY)])
    handle.temperature = 1.0
    write_playbook(handle, "F", prompt_name=PROMPT)
    assert handle.client.calls[0]["temperature"] == 1.0


def test_no_profile_gives_the_neutral_section():
    handle = make_fake_handle("author", [_block(BODY)])
    write_playbook(handle, "F", prompt_name=PROMPT)
    assert "No weak-model profile is provided" in \
        handle.client.calls[0]["user"]


def test_a_broken_reply_is_asked_again_with_the_reason():
    handle = make_fake_handle(
        "author", [_block("x" * (MAX_PLAYBOOK_CHARS + 1)), _block(BODY)])
    assert write_playbook(handle, "F", prompt_name=PROMPT) == BODY
    first, second = (call["user"] for call in handle.client.calls)
    assert "Your previous reply was rejected" not in first
    assert "Your previous reply was rejected" in second
    assert f"the limit is {MAX_PLAYBOOK_CHARS}" in second


def test_two_broken_replies_end_in_a_model_output_error():
    handle = make_fake_handle("author", ["no tags", "still no tags"])
    with pytest.raises(ModelOutputError, match="does not end with"):
        write_playbook(handle, "F", prompt_name=PROMPT)
    assert len(handle.client.calls) == 2


def _requirement(name, needs):
    from expedistill.benchmarks.base import PlaybookRequirement
    return PlaybookRequirement(name, f"include {needs}", lambda t: needs in t)


def test_a_missing_requirement_sends_the_writer_back_with_its_name():
    req = _requirement("search order", "## search order")
    handle = make_fake_handle("author", [_block(BODY), _block(
        "## search order\n" + BODY)])
    record = []
    text = write_playbook(handle, "F", prompt_name=PROMPT,
                          requirements=[req], record=record)
    assert text.startswith("## search order")
    first, second = (call["user"] for call in handle.client.calls)
    assert "search order" not in first.split("Write the playbook")[0][-80:]
    assert "the playbook is missing: search order" in second
    assert record[0]["missing"] == ["search order"]
    assert record[1]["missing"] == []


def test_a_reply_that_never_meets_a_requirement_fails_loudly():
    req = _requirement("recipes", "## recipes")
    handle = make_fake_handle("author", [_block(BODY), _block(BODY)])
    with pytest.raises(ModelOutputError, match="missing: recipes"):
        write_playbook(handle, "F", prompt_name=PROMPT, requirements=[req])
    assert len(handle.client.calls) == 2
