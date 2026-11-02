"""call_model fills a learning prompt's vocabulary slots from GENERIC, then
the handle's prompt_defaults, then the caller's own variables."""

import pytest

from expedistill.llm import make_fake_handle
from expedistill.llm.call import call_model
from expedistill.prompts import PROMPTS, PromptSpec
from expedistill.prompts.learning_context import GENERIC


@pytest.fixture(autouse=True)
def toy_prompt(monkeypatch):
    monkeypatch.setitem(PROMPTS, "TOY_SLOT_PROMPT", PromptSpec(
        name="TOY_SLOT_PROMPT",
        template="agent={agent_name} body={body}",
        description="Test-only prompt with one vocabulary slot."))
    monkeypatch.setitem(GENERIC.slots, "agent_name", "generic agent")


def _user(handle):
    return handle.client.calls[0]["user"]


def test_generic_fills_a_slot_the_caller_does_not_pass():
    handle = make_fake_handle(script="ok")
    call_model(handle, "TOY_SLOT_PROMPT", {"body": "b"})
    assert _user(handle) == "agent=generic agent body=b"


def test_the_handle_defaults_override_generic():
    handle = make_fake_handle(script="ok",
                              prompt_defaults={"agent_name": "terminal agent"})
    call_model(handle, "TOY_SLOT_PROMPT", {"body": "b"})
    assert _user(handle) == "agent=terminal agent body=b"


def test_the_caller_overrides_the_handle_defaults():
    handle = make_fake_handle(script="ok",
                              prompt_defaults={"agent_name": "terminal agent"})
    call_model(handle, "TOY_SLOT_PROMPT",
               {"body": "b", "agent_name": "explicit"})
    assert _user(handle) == "agent=explicit body=b"


def test_a_slot_no_layer_provides_is_still_a_caller_bug():
    handle = make_fake_handle(script="ok")
    with pytest.raises(KeyError):
        call_model(handle, "TOY_SLOT_PROMPT", {"agent_name": "x"})


def test_slots_a_template_does_not_use_are_ignored():
    handle = make_fake_handle(script="ok",
                              prompt_defaults={"unused_slot": "x"})
    call_model(handle, "TOY_SLOT_PROMPT", {"body": "b"})
    assert _user(handle) == "agent=generic agent body=b"
