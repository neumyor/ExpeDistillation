"""call_model renders the template a handle's prompt_aliases name in place of
the method prompt, with the same variables; the request is still recorded
under the method prompt's name."""

import pytest

from expedistill.llm import make_fake_handle
from expedistill.llm.call import call_model
from expedistill.prompts import PROMPTS, PromptSpec


@pytest.fixture(autouse=True)
def toy_prompts(monkeypatch):
    for name, template in (("TOY_METHOD", "method {body}"),
                           ("TOY_REPLACEMENT", "replacement {body}")):
        monkeypatch.setitem(PROMPTS, name, PromptSpec(
            name=name, template=template, description="Test-only prompt."))


def test_an_aliased_prompt_renders_the_replacement(tmp_path):
    handle = make_fake_handle(
        script="ok", prompt_aliases={"TOY_METHOD": "TOY_REPLACEMENT"})
    call_model(handle, "TOY_METHOD", {"body": "b"}, run_dir=tmp_path)
    assert handle.client.calls[0]["user"] == "replacement b"
    dumps = sorted(p.name for p in (tmp_path / "llm_requests").rglob("*.json"))
    assert dumps and all(name.endswith("_TOY_METHOD.json") for name in dumps)


def test_a_prompt_without_an_alias_renders_itself():
    handle = make_fake_handle(
        script="ok", prompt_aliases={"OTHER_METHOD": "TOY_REPLACEMENT"})
    call_model(handle, "TOY_METHOD", {"body": "b"})
    assert handle.client.calls[0]["user"] == "method b"


def test_no_aliases_by_default():
    handle = make_fake_handle(script="ok")
    assert handle.prompt_aliases == {}
    call_model(handle, "TOY_METHOD", {"body": "b"})
    assert handle.client.calls[0]["user"] == "method b"
