"""Tests for the FakeClient test double itself: scripting modes, call
recording, error paths, and the json_script helper."""

import pytest

from expedistill.llm import (
    CompletionResult,
    FakeClient,
    ModelHandle,
    json_script,
    make_fake_handle,
)


def test_string_script_returned_for_every_call():
    client = FakeClient("constant")
    assert client.complete("m", "s", "u") == "constant"
    assert client.complete("m", "s", "u") == "constant"


def test_list_script_consumed_in_order():
    client = FakeClient(["first", "second"])
    assert client.complete("m", "s", "u") == "first"
    assert client.complete("m", "s", "u") == "second"


def test_exhausted_script_raises():
    client = FakeClient(["only"])
    client.complete("m", "s", "u")
    with pytest.raises(AssertionError, match="exhausted"):
        client.complete("m", "s", "u")


def test_scripted_exception_raised():
    client = FakeClient([ValueError("boom")])
    with pytest.raises(ValueError):
        client.complete("m", "s", "u")


def test_callable_script_receives_system_and_user():
    client = FakeClient(lambda system, user: f"{system}|{user}")
    assert client.complete("m", "sys", "usr") == "sys|usr"


def test_callable_script_can_return_completion_result():
    client = FakeClient(
        lambda system, user: CompletionResult(text="t", in_tokens=1, out_tokens=2)
    )
    result = client.complete("m", "s", "u")
    assert result.text == "t" and result.in_tokens == 1 and result.out_tokens == 2


def test_calls_recorded_for_assertions():
    client = FakeClient("x")
    client.complete("model", "system", "user", temperature=0.5, max_tokens=32)
    assert client.calls == [
        {
            "model": "model",
            "system": "system",
            "user": "user",
            "temperature": 0.5,
            "max_tokens": 32,
            "extra": None,
        }
    ]


def test_make_fake_handle_wires_fake_client():
    handle = make_fake_handle(role="judge", script="x")
    assert isinstance(handle, ModelHandle)
    assert handle.role == "judge"
    assert handle.name == "fake-model"
    assert isinstance(handle.client, FakeClient)
    assert handle.client.script == "x"


def test_make_fake_handle_passes_kwargs_through():
    handle = make_fake_handle(script="x", temperature=0.9, max_tokens=16, name="m2")
    assert handle.temperature == 0.9
    assert handle.max_tokens == 16
    assert handle.name == "m2"


def test_json_script_fences_and_round_trips():
    fenced = json_script({"verdict": "correct"})
    assert fenced.startswith("```json\n")
    assert fenced.endswith("\n```")
    import json

    assert json.loads(fenced[len("```json\n") : -len("\n```")]) == {"verdict": "correct"}
