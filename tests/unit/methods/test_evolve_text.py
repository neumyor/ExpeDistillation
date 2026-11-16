"""Learn step 2: the shared shell that evolves the profile and the playbook."""

import pytest

from expedistill.benchmarks.base import PlaybookRequirement
from expedistill.infra.reliability import ModelOutputError
from tests.helpers.fake_llm import json_script, make_fake_handle
from expedistill.methods.evolve_text import (
    MAX_PLAYBOOK_CHARS,
    MIN_PLAYBOOK_CHARS,
    REPAIRS,
    evolve_text,
    extract_playbook,
)

BODY = "## recovery\n" + "Read the error before retrying. " * 20
CHECK = PlaybookRequirement("verification", "say how to check the result",
                            lambda text: "## verification" in text)


def _tagged(text):
    return f"draft\n<playbook>\n{text}\n</playbook>"


@pytest.mark.parametrize("kind", ["profile", "playbook"])
def test_no_notes_and_no_facts_copy_the_old_text_without_a_call(kind):
    handle = make_fake_handle(script=[])
    assert evolve_text(kind, "old text", ["", "  "], "", handle) \
        == "old text"
    assert handle.client.calls == []


def test_the_profile_merges_the_notes():
    handle = make_fake_handle(script=[json_script(
        {"profile_text": "- skips checks"})])
    text = evolve_text("profile", "", ["skips checks"], "", handle)
    assert text == "- skips checks"
    prompt = handle.client.calls[0]["user"]
    assert "CURRENT PROFILE:\n(empty)" in prompt
    assert "skips checks" in prompt


def test_an_empty_profile_is_sent_back():
    handle = make_fake_handle(script=[
        json_script({"profile_text": ""}),
        json_script({"profile_text": "- skips checks"})])
    text = evolve_text("profile", "", ["skips checks"], "", handle)
    assert text == "- skips checks"
    assert "rejected: profile_text" in handle.client.calls[1]["user"]


def test_facts_alone_are_enough_to_write_the_playbook():
    handle = make_fake_handle(script=[_tagged(BODY)])
    text = evolve_text("playbook", "", [], "FACTS", handle)
    assert text == BODY.strip()
    assert "FACTS" in handle.client.calls[0]["user"]


def test_a_missing_part_is_named_and_repaired():
    handle = make_fake_handle(script=[
        _tagged(BODY), _tagged(BODY + "\n## verification\nRe-run it.")])
    text = evolve_text("playbook", "old", ["note"], "", handle,
                          checks=[CHECK])
    assert "## verification" in text
    second = handle.client.calls[1]["user"]
    assert "the playbook is missing: verification" in second
    assert "say how to check the result" in second


def test_a_playbook_that_stays_incomplete_is_a_model_output_error():
    handle = make_fake_handle(script=[_tagged(BODY)] * (REPAIRS + 1))
    with pytest.raises(ModelOutputError, match="verification"):
        evolve_text("playbook", "old", ["note"], "", handle, checks=[CHECK])
    assert len(handle.client.calls) == REPAIRS + 1


def test_the_playbook_reader_profile_reaches_the_prompt():
    handle = make_fake_handle(script=[_tagged(BODY)])
    evolve_text("playbook", "", ["note"], "", handle,
                profile="the reader forgets old steps")
    assert "the reader forgets old steps" in handle.client.calls[0]["user"]


@pytest.mark.parametrize("reply, problem", [
    ("no tags at all", "closing"),
    ("<playbook>short</playbook>", f"at least {MIN_PLAYBOOK_CHARS}"),
    ("<playbook>" + "x" * (MAX_PLAYBOOK_CHARS + 1) + "</playbook>",
     "the limit"),
    (_tagged(BODY) + " trailing", "closing"),
])
def test_extract_playbook_names_what_is_wrong(reply, problem):
    with pytest.raises(ValueError, match=problem):
        extract_playbook(reply)


def test_an_empty_playbook_reply_is_asked_again():
    # The plain-text playbook call has no format re-ask in call_model: an
    # empty reply raises there, and the writer asks again within its own
    # budget instead of stopping the learn phase.
    handle = make_fake_handle(script=[
        ModelOutputError("empty content"), _tagged(BODY)])
    assert evolve_text("playbook", "old", ["note"], "", handle) \
        == BODY.strip()
    assert len(handle.client.calls) == 2


def test_replies_that_stay_empty_are_a_model_output_error():
    handle = make_fake_handle(
        script=[ModelOutputError("empty content")] * (REPAIRS + 1))
    with pytest.raises(ModelOutputError, match="empty content"):
        evolve_text("playbook", "old", ["note"], "", handle)
    assert len(handle.client.calls) == REPAIRS + 1
