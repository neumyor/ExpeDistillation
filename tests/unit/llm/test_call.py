"""Tests for call_model: prompt lookup and aliasing, vocabulary slots, JSON
and plain-text re-asks, infrastructure retries, usage accounting, request
dumps and token-budget measurement. Uses FakeClient only; no network."""

import dataclasses
import json
import logging
import re
import threading
import uuid

import pytest

from expedistill.infra.reliability import (
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    ProviderRefusalError,
)
from expedistill.infra.usage import UsageRecorder
from expedistill.inject.skills import estimate_tokens
from expedistill.llm import call as call_module
from expedistill.llm.call import (
    DEFAULT_SYSTEM,
    REPAIR_INSTRUCTION,
    call_model,
    make_handle,
)
from expedistill.llm.client import CompletionResult
from expedistill.prompts import PROMPTS, PromptSpec
from expedistill.prompts.learning_context import GENERIC
from tests.helpers.fake_llm import (
    FakeClient,
    RecordingUsage,
    json_script,
    make_fake_handle,
)

ANSWER_VARS = {
    "question_text": "What should you do when the build fails?",
    "skills_block": "",
}
JUDGE_VARS = {
    "question_text": "q",
    "reference_answer": "r",
    "weak_answer": "w",
}
SEGMENT_VARS = {"trajectory_view": "step 0: look\nstep 1: act", "K": 2}
VERDICT = {"verdict": "correct", "error_description": ""}


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(call_module, "_sleep", calls.append)
    return calls


def _result_script(text, **result_kw):
    """A callable FakeClient script returning a CompletionResult."""
    def script(system, user):
        return CompletionResult(text=text, **result_kw)

    return script


def _result(text, **result_kw):
    """A CompletionResult instance, for LIST scripts: a list item is
    returned as-is (only a whole-callable script gets invoked)."""
    return CompletionResult(text=text, **result_kw)


def _infra(n=1):
    return [InfrastructureError(f"HTTP 502 #{i}") for i in range(n)]


def _empty_content_error(finish_reason="length", usage=None):
    """A client-raised ModelOutputError shaped like OpenAICompatClient's
    empty-content raise: carries the burned usage and finish_reason."""
    if usage is None:
        usage = {"prompt_tokens": 40000, "completion_tokens": 5120,
                 "total_tokens": 45120}
    return ModelOutputError(
        "model fake-model returned empty or whitespace-only content "
        f"(finish_reason={finish_reason!r})",
        usage=usage,
        finish_reason=finish_reason,
    )



# ---------------------------------------------------------------------------
# One call: request, handle defaults, accounting attachments
# ---------------------------------------------------------------------------


def test_text_call_roundtrip():
    handle = make_fake_handle(script="the answer")
    out = call_model(handle, "ANSWER", ANSWER_VARS)
    assert out == "the answer"
    call = handle.client.calls[0]
    assert call["model"] == "fake-model"
    assert call["system"] == DEFAULT_SYSTEM
    assert "What should you do when the build fails?" in call["user"]
    assert call["temperature"] == 1.0
    # Reasoning models need several thousand output tokens, so the default
    # cap is uniform but explicit.
    assert call["max_tokens"] == 5120

def test_an_exhausted_script_fails_loudly():
    # The test double must never silently repeat or invent a reply.
    handle = make_fake_handle(script=["only"])
    call_model(handle, "ANSWER", ANSWER_VARS)
    with pytest.raises(AssertionError, match="exhausted"):
        call_model(handle, "ANSWER", ANSWER_VARS)


def test_system_override_replaces_default():
    handle = make_fake_handle(script="x")
    call_model(handle, "ANSWER", ANSWER_VARS, system_override="You are a grader.")
    assert handle.client.calls[0]["system"] == "You are a grader."

def test_handle_temperature_and_max_tokens_forwarded():
    handle = make_fake_handle(script="x", temperature=0.7, max_tokens=64)
    call_model(handle, "ANSWER", ANSWER_VARS)
    assert handle.client.calls[0]["temperature"] == 0.7
    assert handle.client.calls[0]["max_tokens"] == 64

def test_extra_body_forwarded_to_client():
    client = FakeClient("x")
    handle = make_handle("executor_strong", "deepseek", client,
                         extra_body={"reasoning_effort": "max"})
    call_model(handle, "ANSWER", ANSWER_VARS)
    assert handle.client.calls[0]["extra"] == {"reasoning_effort": "max"}

def test_missing_variable_propagates_key_error():
    handle = make_fake_handle(script="x")
    with pytest.raises(KeyError):
        call_model(handle, "ANSWER", {})

def test_unknown_prompt_propagates():
    handle = make_fake_handle(script="x")
    with pytest.raises(KeyError, match="NO_SUCH_PROMPT"):
        call_model(handle, "NO_SUCH_PROMPT", {})

def test_call_model_falls_back_to_handle_usage_and_run_dir(tmp_path):
    # ModelHandle carries usage/run_dir so E1-threaded handles keep
    # accounting without every layer growing parameters.
    usage = UsageRecorder(tmp_path / "usage.jsonl")
    handle = make_fake_handle("executor_weak", script=json_script({"ok": 1}))
    handle.usage = usage
    handle.run_dir = tmp_path
    result = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True)
    assert result == {"ok": 1}
    assert usage.totals()["overall"]["request_count"] == 1
    assert list((tmp_path / "llm_requests").glob("*.json"))


def test_explicit_arguments_win_over_handle_attachments(tmp_path):
    usage_a = UsageRecorder(tmp_path / "a.jsonl")
    usage_b = UsageRecorder(tmp_path / "b.jsonl")
    handle = make_fake_handle("judge", script=json_script({"x": 1}))
    handle.usage = usage_a
    call_model(handle, "JUDGE", JUDGE_VARS, usage=usage_b)
    assert usage_b.totals()["overall"]["request_count"] == 1
    assert usage_a.totals()["overall"]["request_count"] == 0


# ---------------------------------------------------------------------------
# Prompt aliases and vocabulary slots
# ---------------------------------------------------------------------------

@pytest.fixture
def toy_prompts(monkeypatch):
    for name, template in (("TOY_METHOD", "method {body}"),
                           ("TOY_REPLACEMENT", "replacement {body}"),
                           ("TOY_SLOT_PROMPT", "agent={agent_name} body={body}")):
        monkeypatch.setitem(PROMPTS, name, PromptSpec(
            name=name, template=template, description="Test-only prompt."))
    monkeypatch.setitem(GENERIC.slots, "agent_name", "generic agent")


def _user(handle):
    return handle.client.calls[0]["user"]


@pytest.mark.usefixtures("toy_prompts")
class TestPromptAliases:
    # call_model renders the template a handle's prompt_aliases name in
    # place of the method prompt, with the same variables; the request is
    # still recorded under the method prompt's name.
    def test_an_aliased_prompt_renders_the_replacement(self, tmp_path):
        handle = make_fake_handle(
            script="ok", prompt_aliases={"TOY_METHOD": "TOY_REPLACEMENT"})
        call_model(handle, "TOY_METHOD", {"body": "b"}, run_dir=tmp_path)
        assert _user(handle) == "replacement b"
        dumps = sorted(p.name
                       for p in (tmp_path / "llm_requests").rglob("*.json"))
        assert dumps and all(n.endswith("_TOY_METHOD.json") for n in dumps)

    def test_a_prompt_without_an_alias_renders_itself(self):
        handle = make_fake_handle(
            script="ok", prompt_aliases={"OTHER_METHOD": "TOY_REPLACEMENT"})
        call_model(handle, "TOY_METHOD", {"body": "b"})
        assert _user(handle) == "method b"


@pytest.mark.usefixtures("toy_prompts")
class TestPromptSlots:
    # call_model fills a learning prompt's vocabulary slots from GENERIC,
    # then the handle's prompt_defaults, then the caller's own variables.
    def test_generic_fills_a_slot_the_caller_does_not_pass(self):
        handle = make_fake_handle(script="ok")
        call_model(handle, "TOY_SLOT_PROMPT", {"body": "b"})
        assert _user(handle) == "agent=generic agent body=b"

    def test_the_handle_defaults_override_generic(self):
        handle = make_fake_handle(
            script="ok", prompt_defaults={"agent_name": "terminal agent"})
        call_model(handle, "TOY_SLOT_PROMPT", {"body": "b"})
        assert _user(handle) == "agent=terminal agent body=b"

    def test_the_caller_overrides_the_handle_defaults(self):
        handle = make_fake_handle(
            script="ok", prompt_defaults={"agent_name": "terminal agent"})
        call_model(handle, "TOY_SLOT_PROMPT",
                   {"body": "b", "agent_name": "explicit"})
        assert _user(handle) == "agent=explicit body=b"

    def test_a_slot_no_layer_provides_is_still_a_caller_bug(self):
        handle = make_fake_handle(script="ok")
        with pytest.raises(KeyError):
            call_model(handle, "TOY_SLOT_PROMPT", {"agent_name": "x"})

    def test_slots_a_template_does_not_use_are_ignored(self):
        handle = make_fake_handle(
            script="ok", prompt_defaults={"unused_slot": "x"})
        call_model(handle, "TOY_SLOT_PROMPT", {"body": "b"})
        assert _user(handle) == "agent=generic agent body=b"


# ---------------------------------------------------------------------------
# JSON calls: format re-asks, empty-content re-asks
# ---------------------------------------------------------------------------


def test_format_retry_then_success():
    handle = make_fake_handle(
        script=["I cannot answer in JSON.", json_script(VERDICT)]
    )
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True)
    assert out["verdict"] == "correct"
    assert len(handle.client.calls) == 2
    first_user = handle.client.calls[0]["user"]
    second_user = handle.client.calls[1]["user"]
    assert REPAIR_INSTRUCTION not in first_user
    assert REPAIR_INSTRUCTION in second_user
    # The repair message quotes the previous reply so the model can fix it.
    assert "I cannot answer in JSON." in second_user
    # The retry keeps the original prompt content.
    assert first_user in second_user

def test_format_retry_exhaustion_raises_model_output_error():
    handle = make_fake_handle(script=["not json"] * 4)
    with pytest.raises(ModelOutputError, match="JUDGE"):
        call_model(handle, "JUDGE", JUDGE_VARS, output_json=True)
    # One initial call plus three retries.
    assert len(handle.client.calls) == 4

@pytest.mark.parametrize("script,match", [
    (["not json", "also not json"], "JUDGE"),
    ([_empty_content_error()], "empty"),
], ids=["malformed", "empty-content"])
def test_format_retry_disabled_makes_one_call(script, match):
    handle = make_fake_handle(script=script)
    with pytest.raises(ModelOutputError, match=match):
        call_model(handle, "JUDGE", JUDGE_VARS, output_json=True,
                   max_format_retries=0)
    assert len(handle.client.calls) == 1


def test_empty_content_json_reask_then_success(tmp_path):
    # Incident 2026-10-12: a G7 call with a 74.5k-token prompt died because
    # reasoning burned the whole output budget (empty content, finish_reason
    # length) and the client's ModelOutputError propagated with ZERO re-asks
    # -- the same failure class as malformed JSON. On output_json calls it
    # must consume the same format budget and re-ask with the repair suffix.
    handle = make_fake_handle(
        script=[_empty_content_error(),
                json_script(VERDICT)])
    usage = RecordingUsage()
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True,
                    usage=usage, run_dir=tmp_path)
    assert out == VERDICT
    assert len(handle.client.calls) == 2
    # The re-ask appends the previous empty outcome and the repair
    # instruction to the original prompt.
    first_user = handle.client.calls[0]["user"]
    second_user = handle.client.calls[1]["user"]
    assert first_user in second_user
    assert "empty or whitespace-only content" in second_user
    assert REPAIR_INSTRUCTION in second_user
    # Both attempts are accounted: the empty one as an error row carrying the
    # burned tokens, the successful re-ask as ok with format_retries=1.
    assert [record["status"] for record in usage.records] == ["error", "ok"]
    assert usage.records[0]["in_tokens"] == 40000
    assert usage.records[0]["out_tokens"] == 5120
    assert usage.records[0]["meta"]["error"] == "ModelOutputError"
    assert usage.records[0]["meta"]["finish_reason"] == "length"
    assert usage.records[1]["meta"]["format_retries"] == 1
    dump_dir = tmp_path / "llm_requests"
    first = json.loads((dump_dir / "0001_JUDGE.json").read_text())
    second = json.loads((dump_dir / "0002_JUDGE.json").read_text())
    assert first["status"] == "error"
    assert first["in_tokens"] == 40000
    assert first["finish_reason"] == "length"
    assert second["status"] == "ok"

def test_empty_content_json_exhaustion_raises_model_output_error():
    def always_empty(system, user):
        raise _empty_content_error()

    handle = make_fake_handle(script=always_empty)
    usage = RecordingUsage()
    with pytest.raises(ModelOutputError, match="empty") as exc_info:
        call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, usage=usage)
    # One ask plus every format re-ask, then the error.
    assert len(handle.client.calls) == 1 + 3
    assert len(usage.records) == 4
    assert all(record["status"] == "error" for record in usage.records)
    # The exhaustion error still carries the last response's diagnostics.
    assert exc_info.value.finish_reason == "length"
    assert exc_info.value.usage["prompt_tokens"] == 40000

def test_empty_content_plain_text_propagates_immediately():
    # Plain-text calls (at method level the playbook writer) keep the
    # current contract: empty content fails the unit at once, exactly one
    # request.
    handle = make_fake_handle(script=[_empty_content_error()])
    usage = RecordingUsage()
    with pytest.raises(ModelOutputError):
        call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    assert len(handle.client.calls) == 1
    assert len(usage.records) == 1
    assert usage.records[0]["status"] == "error"

def test_bare_client_model_output_error_on_json_call_propagates():
    # Only the client's empty-content raise (the one carrying the rejected
    # response's usage/finish_reason, per reliability.py's contract) is a
    # format failure. A bare ModelOutputError from another raise site keeps
    # today's disposition: propagate at once, exactly one request.
    handle = make_fake_handle(script=[ModelOutputError("JUDGE: bad verdict")])
    usage = RecordingUsage()
    with pytest.raises(ModelOutputError, match="bad verdict"):
        call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, usage=usage)
    assert len(handle.client.calls) == 1
    assert len(usage.records) == 1

def test_empty_content_finish_reason_stop_still_reasked():
    # "Model chose silence" (finish_reason=stop) is the same failure class as
    # budget burned on reasoning (finish_reason=length): re-ask either way.
    handle = make_fake_handle(
        script=[_empty_content_error("stop"),
                json_script(VERDICT)])
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True)
    assert out["verdict"] == "correct"
    assert len(handle.client.calls) == 2

def test_mixed_empty_and_malformed_share_one_budget():
    # Empty and malformed replies are re-asks of the SAME budget: empty,
    # malformed, valid takes three attempts and format_retries=2.
    handle = make_fake_handle(
        script=[_empty_content_error(), "not json",
                json_script(VERDICT)])
    usage = RecordingUsage()
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, usage=usage)
    assert out["verdict"] == "correct"
    assert len(handle.client.calls) == 3
    assert [record["status"] for record in usage.records] == [
        "error", "format_error", "ok"]
    assert usage.records[2]["meta"]["format_retries"] == 2

def test_shared_budget_exhausts_across_failure_kinds():
    # With two re-asks allowed, empty -> malformed -> empty exhausts the
    # shared budget on the third attempt.
    handle = make_fake_handle(
        script=[_empty_content_error(), "not json", _empty_content_error()])
    with pytest.raises(ModelOutputError, match="empty"):
        call_model(handle, "JUDGE", JUDGE_VARS, output_json=True,
                   max_format_retries=2)
    assert len(handle.client.calls) == 3

def test_empty_reask_gets_full_infra_budget():
    # Infra retries compose as before: the first ask burns its infra retry,
    # then the empty re-ask starts with a fresh infra budget.
    handle = make_fake_handle(
        script=[InfrastructureError("endpoint down"), _empty_content_error(),
                json_script(VERDICT)],
        infra_retries=1, infra_backoff_seconds=0.0)
    usage = RecordingUsage()
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, usage=usage)
    assert out["verdict"] == "correct"
    assert len(handle.client.calls) == 3
    assert [record["status"] for record in usage.records] == [
        "error", "error", "ok"]
    assert usage.records[2]["meta"]["infra_retries"] == 1
    assert usage.records[2]["meta"]["format_retries"] == 1

# ---------------------------------------------------------------------------
# Plain-text length truncation -> bounded re-ask (the truncated-merge-in-bank
# incident): a reply cut off at the output limit is never silently accepted.
# ---------------------------------------------------------------------------


def test_plain_text_truncation_reask_then_success(tmp_path):
    handle = make_fake_handle(
        script=[_result("half a merge that just stops mid-",
                        finish_reason="length"),
                "complete merge"])
    usage = RecordingUsage()
    out = call_model(handle, "ANSWER", ANSWER_VARS, usage=usage,
                     run_dir=tmp_path)
    assert out == "complete merge"
    assert len(handle.client.calls) == 2
    # The re-ask rebuilds from the original prompt with a suffix naming the
    # truncation and asking for a complete, more concise reply. It must NOT
    # append the JSON repair instruction: this is a plain-text caller.
    first_user = handle.client.calls[0]["user"]
    second_user = handle.client.calls[1]["user"]
    assert first_user in second_user
    assert "cut off at the output limit" in second_user
    assert "concisely" in second_user
    assert REPAIR_INSTRUCTION not in second_user
    # Both attempts are accounted: the truncated one is a format_error
    # carrying the length signal and the re-ask flag, the retry an ok row
    # with the shared format budget counter.
    assert [record["status"] for record in usage.records] == [
        "format_error", "ok"]
    assert usage.records[0]["meta"]["finish_reason"] == "length"
    assert "cut off at the output limit" in usage.records[0]["meta"]["error"]
    assert usage.records[0]["meta"]["format_retried"] is True
    assert usage.records[1]["meta"]["format_retries"] == 1
    # Measurement on both rows; the re-ask prompt is longer, so is its
    # estimate.
    assert usage.records[0]["meta"]["requested_max_tokens"] == 5120
    assert (usage.records[1]["meta"]["estimated_in"]
            > usage.records[0]["meta"]["estimated_in"])
    dump_dir = tmp_path / "llm_requests"
    first = json.loads((dump_dir / "0001_ANSWER.json").read_text())
    second = json.loads((dump_dir / "0002_ANSWER.json").read_text())
    assert first["status"] == "format_error"
    assert first["finish_reason"] == "length"
    assert second["status"] == "ok"


def test_plain_text_truncation_exhaustion_raises_model_output_error():
    def always_truncated(system, user):
        return _result_script("cut short", finish_reason="length")(system, user)

    handle = make_fake_handle(script=always_truncated)
    usage = RecordingUsage()
    with pytest.raises(ModelOutputError,
                       match="cut off at the output limit"):
        call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    # One ask plus every format re-ask, then the error.
    assert len(handle.client.calls) == 1 + 3
    assert [record["status"] for record in usage.records] == [
        "format_error"] * 4
    # The last, exhausted attempt is marked terminal, not re-asked.
    assert usage.records[-1]["meta"]["format_retried"] is False


def test_plain_text_stop_is_accepted_as_today():
    handle = make_fake_handle(
        script=_result_script("full reply", finish_reason="stop"))
    usage = RecordingUsage()
    out = call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    assert out == "full reply"
    assert len(handle.client.calls) == 1
    assert usage.records[0]["status"] == "ok"
    assert usage.records[0]["meta"]["finish_reason"] == "stop"
    assert "format_retries" not in usage.records[0]["meta"]


def test_json_call_valid_despite_length_is_accepted():
    # JSON calls keep the extract_json contract: a response that parses is
    # accepted even with finish_reason=length (no plain-text re-ask), but
    # the length signal still lands in meta -- and so does the cap
    # violation, since out_tokens exceeded the requested cap.
    handle = make_fake_handle(
        script=_result_script(
            json_script(VERDICT),
            in_tokens=10, out_tokens=6000, finish_reason="length"),
        max_tokens=5120)
    usage = RecordingUsage()
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, usage=usage)
    assert out == VERDICT
    assert len(handle.client.calls) == 1
    assert usage.records[0]["meta"]["finish_reason"] == "length"
    assert usage.records[0]["meta"]["cap_violation"] is True


def test_json_truncated_text_keeps_extract_json_retry():
    # The JSON path rejects truncation via extract_json (a truncated value
    # fails to parse -> the existing format retry), NOT via the new
    # plain-text truncation re-ask.
    handle = make_fake_handle(
        script=[_result('{"verdict": "corr', finish_reason="length"),
                json_script(VERDICT)])
    usage = RecordingUsage()
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, usage=usage)
    assert out["verdict"] == "correct"
    assert len(handle.client.calls) == 2
    second_user = handle.client.calls[1]["user"]
    assert "not valid JSON" in second_user
    assert "cut off at the output limit" not in second_user
    assert usage.records[0]["status"] == "format_error"
    assert usage.records[0]["meta"]["finish_reason"] == "length"


# ---------------------------------------------------------------------------
# Infrastructure retries: bounded, opt-in per handle (``infra_retries``
# defaults to 0), InfrastructureError only, every attempt accounted and
# dumped. Sleeps are captured, never slept.
# ---------------------------------------------------------------------------


def test_default_handle_does_not_retry(sleeps):
    handle = make_fake_handle(script=_infra(1) + ["ok"])
    assert handle.infra_retries == 0
    assert handle.infra_backoff_seconds == 0.0
    with pytest.raises(InfrastructureError):
        call_model(handle, "ANSWER", ANSWER_VARS)
    assert len(handle.client.calls) == 1
    assert sleeps == []

def test_transient_failures_are_retried_then_succeed(sleeps, tmp_path):
    usage = RecordingUsage()
    handle = make_fake_handle(script=_infra(2) + ["the answer"],
                              infra_retries=3, infra_backoff_seconds=0.5)
    out = call_model(handle, "ANSWER", ANSWER_VARS, usage=usage, run_dir=tmp_path)
    assert out == "the answer"
    assert len(handle.client.calls) == 3
    # The same request is re-sent unchanged.
    assert len({c["user"] for c in handle.client.calls}) == 1
    # Exponential backoff: base * 2**retry.
    assert sleeps == [0.5, 1.0]
    # Every attempt is accounted ...
    assert [r["status"] for r in usage.records] == ["error", "error", "ok"]
    assert usage.records[0]["meta"]["error"] == "InfrastructureError"
    # ... and dumped, in order, with the infra attempt index.
    dumps = sorted((tmp_path / "llm_requests").glob("*.json"))
    assert [p.name for p in dumps] == [
        "0001_ANSWER.json", "0002_ANSWER.json", "0003_ANSWER.json"]
    records = [json.loads(p.read_text()) for p in dumps]
    assert [r["status"] for r in records] == ["error", "error", "ok"]
    assert [r["infra_attempt"] for r in records] == [0, 1, 2]

def test_backoff_is_capped(sleeps):
    handle = make_fake_handle(script=_infra(4) + ["ok"],
                              infra_retries=4, infra_backoff_seconds=40.0)
    call_model(handle, "ANSWER", ANSWER_VARS)
    assert sleeps == [40.0] + [call_module.MAX_INFRA_BACKOFF_SECONDS] * 3

def test_zero_backoff_never_sleeps(sleeps):
    handle = make_fake_handle(script=_infra(2) + ["ok"], infra_retries=2)
    assert call_model(handle, "ANSWER", ANSWER_VARS) == "ok"
    assert sleeps == []

def test_exhausted_budget_reraises_the_last_infrastructure_error(sleeps):
    usage = RecordingUsage()
    handle = make_fake_handle(script=_infra(3) + ["never reached"],
                              infra_retries=2)
    with pytest.raises(InfrastructureError, match="#2"):
        call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    assert len(handle.client.calls) == 3
    assert [r["status"] for r in usage.records] == ["error"] * 3

@pytest.mark.parametrize("exc", [
    ModelOutputError("model returned no content"),
    ProviderRefusalError("refused"),
    ConfigError("bad config"),
    ValueError("code bug"),
])
def test_non_infrastructure_errors_are_not_retried(sleeps, exc):
    usage = RecordingUsage()
    handle = make_fake_handle(script=[exc, "ok"], infra_retries=5,
                              infra_backoff_seconds=1.0)
    with pytest.raises(type(exc)):
        call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    assert len(handle.client.calls) == 1
    assert sleeps == []
    assert [r["status"] for r in usage.records] == ["error"]

def test_infra_and_format_budgets_are_independent(sleeps):
    # broken JSON -> repair re-ask hits a 502 -> infra retry resends the
    # repair request -> valid JSON. One format retry, one infra retry.
    handle = make_fake_handle(
        script=["broken"] + _infra(1) + [json_script(VERDICT)],
        infra_retries=1)
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True,
                     max_format_retries=1)
    assert out == VERDICT
    calls = handle.client.calls
    assert len(calls) == 3
    assert calls[1]["user"] == calls[2]["user"]
    assert "broken" in calls[1]["user"]

def test_infra_budget_is_per_request_not_shared_across_repairs(sleeps):
    # Each request (the first ask and every repair re-ask) gets its own infra
    # budget: a response arriving proves the endpoint recovered.
    handle = make_fake_handle(
        script=_infra(1) + ["broken"] + _infra(1) + [json_script(VERDICT)],
        infra_retries=1)
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True,
                     max_format_retries=1)
    assert out == VERDICT
    assert len(handle.client.calls) == 4

def test_infra_retries_do_not_consume_format_budget(sleeps):
    handle = make_fake_handle(
        script=_infra(2) + [json_script(VERDICT)], infra_retries=2)
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True,
                     max_format_retries=0)
    assert out == VERDICT

def test_every_retry_is_logged(sleeps, caplog):
    handle = make_fake_handle(role="judge", script=_infra(1) + ["ok"],
                              infra_retries=1)
    with caplog.at_level(logging.WARNING, logger=call_module.__name__):
        call_model(handle, "ANSWER", ANSWER_VARS)
    messages = [r.getMessage() for r in caplog.records]
    assert any("ANSWER" in m and "judge" in m and "1/1" in m for m in messages)

def test_replace_keeps_retry_fields_and_accounting_attachments(sleeps, tmp_path):
    base = make_handle("judge", "m", FakeClient(_infra(1) + ["ok"]),
                       infra_retries=2, infra_backoff_seconds=0.25)
    usage = RecordingUsage()
    working = dataclasses.replace(base, usage=usage, run_dir=tmp_path)
    assert working.infra_retries == 2
    assert working.infra_backoff_seconds == 0.25
    assert call_model(working, "ANSWER", ANSWER_VARS) == "ok"
    assert [r["status"] for r in usage.records] == ["error", "ok"]
    assert len(list((tmp_path / "llm_requests").glob("*.json"))) == 2

@pytest.mark.parametrize("kw", [
    {"infra_retries": -1},
    {"infra_retries": 1.5},
    {"infra_backoff_seconds": -0.1},
    {"infra_backoff_seconds": float("nan")},
])
def test_invalid_retry_settings_are_config_errors(kw):
    with pytest.raises(ConfigError):
        make_fake_handle(script="x", **kw)


# ---------------------------------------------------------------------------
# Usage accounting and request dumps
# ---------------------------------------------------------------------------



def test_usage_meta_and_dump_record_finish_reason_and_cached_tokens(tmp_path):
    handle = make_fake_handle(
        role="judge",
        script=_result_script("plain text", in_tokens=3927, out_tokens=5,
                              finish_reason="stop", cached_tokens=3712))
    usage = RecordingUsage()
    call_model(handle, "ANSWER", ANSWER_VARS, usage=usage, run_dir=tmp_path)
    record = usage.records[0]
    assert record["status"] == "ok"
    assert (record["in_tokens"], record["out_tokens"]) == (3927, 5)
    assert record["meta"]["finish_reason"] == "stop"
    assert record["meta"]["cached_tokens"] == 3712
    dump = json.loads(
        (tmp_path / "llm_requests" / "0001_ANSWER.json").read_text())
    assert dump["finish_reason"] == "stop"
    assert dump["in_tokens"] == 3927
    assert dump["out_tokens"] == 5



def test_meta_finish_reason_none_for_plain_string_results():
    # Client doubles returning plain strings have no finish_reason: the
    # usage row still gets the key (unknown as unknown), cached_tokens is
    # only recorded when present.
    handle = make_fake_handle(role="judge", script="plain text")
    usage = RecordingUsage()
    call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    assert usage.records[0]["meta"]["finish_reason"] is None
    assert "cached_tokens" not in usage.records[0]["meta"]


def test_finish_reason_recorded_on_format_error(tmp_path):
    handle = make_fake_handle(
        role="judge",
        script=_result_script("broken", finish_reason="length"))
    usage = RecordingUsage()
    with pytest.raises(ModelOutputError):
        call_model(handle, "JUDGE", JUDGE_VARS, output_json=True,
                   usage=usage, run_dir=tmp_path, max_format_retries=0)
    record = usage.records[0]
    assert record["status"] == "format_error"
    assert record["meta"]["finish_reason"] == "length"
    assert "error" in record["meta"]
    dump = json.loads(
        (tmp_path / "llm_requests" / "0001_JUDGE.json").read_text())
    assert dump["status"] == "format_error"
    assert dump["finish_reason"] == "length"


def test_usage_recorded_with_none_tokens():
    handle = make_fake_handle(role="judge", script="plain text")
    usage = RecordingUsage()
    call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    record = usage.records[0]
    assert record["role"] == "judge"
    assert record["prompt"] == "ANSWER"
    assert record["model"] == "fake-model"
    assert record["in_tokens"] is None
    assert record["out_tokens"] is None
    assert record["status"] == "ok"
    assert re.fullmatch(r"[0-9a-f]{32}", record["request_id"])
    assert uuid.UUID(record["request_id"])


@pytest.mark.parametrize("exc", [
    ValueError("endpoint exploded"),
    # Raise sites that carry no usage (old clients, extract_json) record
    # null tokens, never 0.
    ModelOutputError("bad output"),
], ids=["code-error", "output-error-without-usage"])
def test_usage_records_client_error_and_reraises(exc, tmp_path):
    handle = make_fake_handle(script=[exc])
    usage = RecordingUsage()
    with pytest.raises(type(exc)):
        call_model(handle, "ANSWER", ANSWER_VARS, usage=usage, run_dir=tmp_path)
    record = usage.records[0]
    assert record["status"] == "error"
    assert record["meta"]["error"] == type(exc).__name__
    # The budget measurement keys land on error rows too: the request was
    # sent with the handle's cap, and the prompt was measured before it.
    assert record["meta"]["requested_max_tokens"] == 5120
    assert record["meta"]["estimated_in"] == estimate_tokens(
        DEFAULT_SYSTEM + handle.client.calls[0]["user"])
    assert record["in_tokens"] is None
    assert record["out_tokens"] is None
    # The exception path has no response; the dump still carries the fields.
    dump = json.loads(
        (tmp_path / "llm_requests" / "0001_ANSWER.json").read_text())
    assert dump["status"] == "error"
    assert dump["finish_reason"] is None
    assert dump["in_tokens"] is None
    assert dump["out_tokens"] is None


def test_usage_records_tokens_from_client_model_output_error(tmp_path):
    # Real-data finding: the client raises ModelOutputError on empty content
    # AFTER reading response.usage, so a ~40k-token spend was recorded as
    # null in/out. When the exception carries usage and finish_reason, the
    # ledger row and the request dump must show them (status stays "error",
    # and a client-side ModelOutputError is still not retried on a plain-text
    # call: exactly one request).
    handle = make_fake_handle(script=[_empty_content_error()])
    usage = RecordingUsage()
    with pytest.raises(ModelOutputError):
        call_model(handle, "ANSWER", ANSWER_VARS, usage=usage, run_dir=tmp_path)
    assert len(handle.client.calls) == 1
    record = usage.records[0]
    assert record["status"] == "error"
    assert record["meta"]["error"] == "ModelOutputError"
    assert record["meta"]["finish_reason"] == "length"
    assert record["in_tokens"] == 40000
    assert record["out_tokens"] == 5120
    dump = json.loads(
        (tmp_path / "llm_requests" / "0001_ANSWER.json").read_text())
    assert dump["status"] == "error"
    assert dump["in_tokens"] == 40000
    assert dump["out_tokens"] == 5120
    assert dump["finish_reason"] == "length"



def test_request_dump_written_and_holds_no_api_key(tmp_path):
    handle = make_fake_handle(script="the answer")
    call_model(handle, "ANSWER", ANSWER_VARS, run_dir=tmp_path)
    call_model(handle, "ANSWER", ANSWER_VARS, run_dir=tmp_path)
    dump_dir = tmp_path / "llm_requests"
    assert sorted(p.name for p in dump_dir.iterdir()) == [
        "0001_ANSWER.json",
        "0002_ANSWER.json",
    ]
    record = json.loads((dump_dir / "0001_ANSWER.json").read_text())
    assert record["prompt_name"] == "ANSWER"
    assert record["role"] == "test"
    assert record["model"] == "fake-model"
    assert "What should you do when the build fails?" in record["request_text"]
    assert record["response_text"] == "the answer"
    assert record["status"] == "ok"
    assert isinstance(record["duration_ms"], int)
    assert record["duration_ms"] >= 0
    # The api key must never appear in dumps, by field name or by value.
    raw = (dump_dir / "0001_ANSWER.json").read_text()
    assert "api_key" not in raw
    assert "API_KEY" not in raw


def test_request_dump_covers_each_retry_attempt(tmp_path):
    handle = make_fake_handle(
        script=["broken", json_script(VERDICT)]
    )
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, run_dir=tmp_path)
    assert out["verdict"] == "correct"
    dump_dir = tmp_path / "llm_requests"
    first = json.loads((dump_dir / "0001_JUDGE.json").read_text())
    second = json.loads((dump_dir / "0002_JUDGE.json").read_text())
    assert first["status"] == "format_error"
    assert first["response_text"] == "broken"
    assert second["status"] == "ok"
    assert second["parsed"] == VERDICT
    assert REPAIR_INSTRUCTION in second["request_text"]


def test_request_dump_survives_non_json_literal_values(tmp_path):
    # 2026-10-20 SearchQA run: a SEGMENT reply that only parsed through
    # ast.literal_eval carried Python's Ellipsis; json.dumps raised inside
    # the request dump and killed the phase before validation could retry.
    handle = make_fake_handle(
        script="[{'start': 0, 'end': ..., 'label': 'x', "
               "'description': 'd', 'success': True}]")
    out = call_model(handle, "SEGMENT", SEGMENT_VARS, output_json=True,
                     run_dir=tmp_path)
    assert isinstance(out, list) and out[0]["end"] is Ellipsis
    record = json.loads(
        (tmp_path / "llm_requests" / "0001_SEGMENT.json").read_text())
    assert record["parsed"][0]["end"] == "Ellipsis"


# ---------------------------------------------------------------------------
# Request-dump sequence numbers (K1 runs units in B5 threads: the old scan for
# the max seq then write let two threads pick the same number and one dump
# silently replaced the other)
# ---------------------------------------------------------------------------

THREADS = 8
CALLS_PER_THREAD = 15


def test_threaded_dumps_get_unique_sequence_numbers(tmp_path, monkeypatch):
    # Widen the race window between "pick a seq" and "publish the file" the
    # way a busy disk would; the dump content is serialized in between.
    real_dumps = json.dumps

    def slow_dumps(*args, **kwargs):
        threading.Event().wait(0.002)
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(call_module.json, "dumps", slow_dumps)
    barrier = threading.Barrier(THREADS)
    errors = []

    def worker(index):
        handle = make_fake_handle(role=f"t{index}", script=f"reply-{index}")
        barrier.wait()
        try:
            for _ in range(CALLS_PER_THREAD):
                call_model(handle, "ANSWER", ANSWER_VARS, run_dir=tmp_path)
        except Exception as exc:  # surfaced below; a thread must not die silently
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    monkeypatch.undo()

    assert errors == []
    dump_dir = tmp_path / "llm_requests"
    names = sorted(p.name for p in dump_dir.iterdir())
    total = THREADS * CALLS_PER_THREAD
    assert names == [f"{seq:04d}_ANSWER.json" for seq in range(1, total + 1)]
    records = [json.loads((dump_dir / name).read_text()) for name in names]
    assert [r["seq"] for r in records] == list(range(1, total + 1))
    per_role = {}
    for record in records:
        per_role[record["role"]] = per_role.get(record["role"], 0) + 1
    assert per_role == {f"t{i}": CALLS_PER_THREAD for i in range(THREADS)}


def test_existing_dump_is_never_overwritten(tmp_path):
    # Another writer (e.g. a second process) published the next seq between
    # two of our calls: ours must take the following number.
    handle = make_fake_handle(script="ours")
    call_model(handle, "ANSWER", ANSWER_VARS, run_dir=tmp_path)
    dump_dir = tmp_path / "llm_requests"
    foreign = dump_dir / "0002_ANSWER.json"
    foreign.write_text('{"foreign": true}', encoding="utf-8")
    call_model(handle, "ANSWER", ANSWER_VARS, run_dir=tmp_path)
    assert foreign.read_text(encoding="utf-8") == '{"foreign": true}'
    assert sorted(p.name for p in dump_dir.iterdir()) == [
        "0001_ANSWER.json", "0002_ANSWER.json", "0003_ANSWER.json"]
    assert json.loads((dump_dir / "0003_ANSWER.json").read_text())["seq"] == 3


def test_usage_ledger_beside_dumps_is_not_a_sequence(tmp_path):
    dump_dir = tmp_path / "llm_requests"
    dump_dir.mkdir()
    (dump_dir / "usage.jsonl").write_text("", encoding="utf-8")
    call_model(make_fake_handle(script="x"), "ANSWER", ANSWER_VARS,
               run_dir=tmp_path)
    assert sorted(p.name for p in dump_dir.iterdir()) == [
        "0001_ANSWER.json", "usage.jsonl"]



# ---------------------------------------------------------------------------
# Token budget measurement: requested_max_tokens, estimated_in,
# cap_violation, input_violation -- measured on every row, never enforced.
# ---------------------------------------------------------------------------



def test_requested_max_tokens_none_when_handle_uncapped():
    handle = make_fake_handle(script="x", max_tokens=None)
    usage = RecordingUsage()
    call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    assert usage.records[0]["meta"]["requested_max_tokens"] is None


def test_requested_max_tokens_in_meta_for_every_row_kind(tmp_path):
    # ok, format_error and (client-raised) error rows all carry the cap
    # that was actually sent with the request.
    handle = make_fake_handle(
        role="judge",
        script=["broken", _empty_content_error(),
                json_script(VERDICT)],
        max_tokens=77)
    usage = RecordingUsage()
    call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, usage=usage,
               run_dir=tmp_path)
    assert [record["status"] for record in usage.records] == [
        "format_error", "error", "ok"]
    for record in usage.records:
        assert record["meta"]["requested_max_tokens"] == 77


def test_estimated_in_present_and_exact():
    handle = make_fake_handle(script="x")
    usage = RecordingUsage()
    call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    # The estimate covers system + user of THIS request.
    assert usage.records[0]["meta"]["estimated_in"] == estimate_tokens(
        DEFAULT_SYSTEM + handle.client.calls[0]["user"])


@pytest.mark.parametrize("first_failure", [
    "not json", _empty_content_error()], ids=["repair", "empty"])
def test_estimated_in_grows_on_reask(first_failure):
    handle = make_fake_handle(script=[first_failure, json_script(VERDICT)])
    usage = RecordingUsage()
    call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, usage=usage)
    assert [record["status"] for record in usage.records][1] == "ok"
    first = estimate_tokens(DEFAULT_SYSTEM + handle.client.calls[0]["user"])
    second = estimate_tokens(DEFAULT_SYSTEM + handle.client.calls[1]["user"])
    assert usage.records[0]["meta"]["estimated_in"] == first
    assert usage.records[1]["meta"]["estimated_in"] == second
    assert second > first



def test_cap_violation_flagged_when_output_exceeds_requested():
    # Real-data evidence (qwen ignored the cap 99/727 times): a response
    # with out_tokens over the requested cap is flagged in the row's meta.
    handle = make_fake_handle(
        script=_result_script("long reply", in_tokens=10, out_tokens=6000),
        max_tokens=5120)
    usage = RecordingUsage()
    call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    assert usage.records[0]["meta"]["cap_violation"] is True


@pytest.mark.parametrize("script,max_tokens", [
    (_result_script("x", out_tokens=5120), 5120),
    # Plain-string results have no out_tokens; an uncapped handle has no
    # requested cap: nothing to compare, so no flag.
    ("plain text", 5120),
    (_result_script("x", out_tokens=5), None),
], ids=["at-the-cap", "unknown-out-tokens", "uncapped-handle"])
def test_cap_violation_absent_when_nothing_exceeds_the_cap(script, max_tokens):
    handle = make_fake_handle(script=script, max_tokens=max_tokens)
    usage = RecordingUsage()
    call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    assert "cap_violation" not in usage.records[0]["meta"]



def test_input_violation_flagged_and_call_proceeds():
    # v1 decision: measure only. A prompt estimated over the handle's
    # max_input_tokens NEVER aborts the call; the flag lands in the row.
    handle = make_fake_handle(script="the answer", max_input_tokens=8)
    usage = RecordingUsage()
    out = call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    assert out == "the answer"
    assert len(handle.client.calls) == 1
    assert usage.records[0]["meta"]["input_violation"] is True


@pytest.mark.parametrize("max_input_tokens", [None, 10**9],
                         ids=["no-limit", "under-the-limit"])
def test_input_violation_absent_without_limit_or_under_it(max_input_tokens):
    handle = make_fake_handle(script="the answer",
                              max_input_tokens=max_input_tokens)
    usage = RecordingUsage()
    call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    assert "input_violation" not in usage.records[0]["meta"]



def test_handle_max_input_tokens_validation():
    # Like the other handle numerics: positive int or None.
    client = FakeClient("x")
    assert make_handle("judge", "m", client).max_input_tokens is None
    make_handle("judge", "m", client, max_input_tokens=131072)
    for bad in (0, -5, True, "512", 1.5):
        with pytest.raises(ConfigError):
            make_handle("judge", "m", client, max_input_tokens=bad)
