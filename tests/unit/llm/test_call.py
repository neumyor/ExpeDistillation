"""Tests for call_model: prompt lookup, JSON repair retry, usage accounting,
and request dumps. Uses FakeClient only; no network."""

import json
import re
import uuid

import pytest

from expedistill.infra.reliability import ModelOutputError
from expedistill.llm import (
    DEFAULT_SYSTEM,
    REPAIR_INSTRUCTION,
    call_model,
    json_script,
    make_fake_handle,
    make_handle,
)
from expedistill.llm.fake import FakeClient

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


class RecordingUsage:
    """Test double for the B7 UsageRecorder (same record signature)."""

    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)

    def totals(self):
        return {}


def test_text_call_roundtrip():
    handle = make_fake_handle(script="the answer")
    out = call_model(handle, "ANSWER", ANSWER_VARS)
    assert out == "the answer"
    call = handle.client.calls[0]
    assert call["model"] == "fake-model"
    assert call["system"] == DEFAULT_SYSTEM
    assert "What should you do when the build fails?" in call["user"]
    assert call["temperature"] == 0.0
    # Default changed from None (uncapped) to 5120: reasoning models need
    # several thousand output tokens, so the cap is uniform but explicit.
    assert call["max_tokens"] == 5120


def test_system_override_replaces_default():
    handle = make_fake_handle(script="x")
    call_model(handle, "ANSWER", ANSWER_VARS, system_override="You are a grader.")
    assert handle.client.calls[0]["system"] == "You are a grader."


def test_handle_temperature_and_max_tokens_forwarded():
    handle = make_fake_handle(script="x", temperature=0.7, max_tokens=64)
    call_model(handle, "ANSWER", ANSWER_VARS)
    assert handle.client.calls[0]["temperature"] == 0.7
    assert handle.client.calls[0]["max_tokens"] == 64


def test_max_tokens_default_is_5120():
    handle = make_fake_handle(script="x")
    assert handle.max_tokens == 5120


def test_extra_body_max_tokens_wins_over_handle_field():
    # Per-model override end to end: the handle field goes out as
    # max_tokens=5120 and the client merges extra_body after it, so a
    # model_options entry {"max_tokens": 128} overrides the default
    # (the client-side merge order is pinned in test_client.py).
    handle = make_fake_handle(script="x", extra_body={"max_tokens": 128})
    call_model(handle, "ANSWER", ANSWER_VARS)
    assert handle.client.calls[0]["max_tokens"] == 5120
    assert handle.client.calls[0]["extra"] == {"max_tokens": 128}


def _result_script(text, **result_kw):
    """A callable FakeClient script returning a CompletionResult."""
    def script(system, user):
        from expedistill.llm.client import CompletionResult

        return CompletionResult(text=text, **result_kw)

    return script


def test_usage_meta_and_dump_record_finish_reason_and_cached_tokens(tmp_path):
    handle = make_fake_handle(
        role="judge",
        script=_result_script("plain text", in_tokens=3927, out_tokens=5,
                              finish_reason="stop", cached_tokens=3712))
    usage = RecordingUsage()
    call_model(handle, "ANSWER", ANSWER_VARS, usage=usage, run_dir=tmp_path)
    record = usage.records[0]
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


def test_error_dump_carries_finish_reason_none(tmp_path):
    # The exception path has no response; the dump still carries the field.
    handle = make_fake_handle(script=[ValueError("boom")])
    with pytest.raises(ValueError):
        call_model(handle, "ANSWER", ANSWER_VARS, run_dir=tmp_path)
    dump = json.loads(
        (tmp_path / "llm_requests" / "0001_ANSWER.json").read_text())
    assert dump["status"] == "error"
    assert dump["finish_reason"] is None


def test_client_output_error_finish_reason_reaches_usage_and_dump(tmp_path):
    # Pilot finding: a client-raised ModelOutputError (empty content,
    # finish_reason=length) was recorded and dumped with finish_reason=None;
    # when the exception carries the finish_reason as an attribute, both the
    # usage meta and the request dump must show it instead.
    error = ModelOutputError(
        "model fake-model returned empty or whitespace-only content "
        "(finish_reason='length')")
    error.finish_reason = "length"
    handle = make_fake_handle(script=[error])
    usage = RecordingUsage()
    with pytest.raises(ModelOutputError):
        call_model(handle, "ANSWER", ANSWER_VARS, usage=usage, run_dir=tmp_path)
    record = usage.records[0]
    assert record["status"] == "error"
    assert record["meta"]["error"] == "ModelOutputError"
    assert record["meta"]["finish_reason"] == "length"
    dump = json.loads(
        (tmp_path / "llm_requests" / "0001_ANSWER.json").read_text())
    assert dump["status"] == "error"
    assert dump["finish_reason"] == "length"


def test_json_fenced_extraction():
    handle = make_fake_handle(
        script=json_script({"verdict": "correct", "error_description": ""})
    )
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True)
    assert out == {"verdict": "correct", "error_description": ""}


def test_json_unfenced_extraction_with_prose():
    script = (
        "Sure, here is my grading.\n"
        '{"verdict": "incorrect", "error_description": "missed the retry"}'
        "\nHope that helps."
    )
    handle = make_fake_handle(script=script)
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True)
    assert out["verdict"] == "incorrect"


def test_segment_array_extraction():
    script = json_script(
        [
            {"start": 0, "end": 1, "label": "look", "description": "d", "success": True},
            {"start": 1, "end": 2, "label": "act", "description": "d", "success": False},
        ]
    )
    handle = make_fake_handle(script=script)
    out = call_model(handle, "SEGMENT", SEGMENT_VARS, output_json=True)
    assert isinstance(out, list) and len(out) == 2
    assert out[0]["start"] == 0 and out[1]["end"] == 2


def test_format_retry_then_success():
    handle = make_fake_handle(
        script=["I cannot answer in JSON.", json_script({"verdict": "correct", "error_description": ""})]
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


def test_empty_content_json_reask_then_success(tmp_path):
    # Incident 2026-10-12: a G7 call with a 74.5k-token prompt died because
    # reasoning burned the whole output budget (empty content, finish_reason
    # length) and the client's ModelOutputError propagated with ZERO re-asks
    # -- the same failure class as malformed JSON. On output_json calls it
    # must consume the same format budget and re-ask with the repair suffix.
    handle = make_fake_handle(
        script=[_empty_content_error(),
                json_script({"verdict": "correct", "error_description": ""})])
    usage = RecordingUsage()
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True,
                    usage=usage, run_dir=tmp_path)
    assert out == {"verdict": "correct", "error_description": ""}
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


def test_empty_content_reask_disabled_makes_one_call():
    handle = make_fake_handle(script=[_empty_content_error()])
    with pytest.raises(ModelOutputError, match="empty"):
        call_model(handle, "JUDGE", JUDGE_VARS, output_json=True,
                   max_format_retries=0)
    assert len(handle.client.calls) == 1


def test_empty_content_plain_text_propagates_immediately():
    # Plain-text calls (SKILL_DISTILL, MERGE) keep the current contract:
    # empty content fails the unit at once, exactly one request.
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
                json_script({"verdict": "correct", "error_description": ""})])
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True)
    assert out["verdict"] == "correct"
    assert len(handle.client.calls) == 2


def test_mixed_empty_and_malformed_share_one_budget():
    # Empty and malformed replies are re-asks of the SAME budget: empty,
    # malformed, valid takes three attempts and format_retries=2.
    handle = make_fake_handle(
        script=[_empty_content_error(), "not json",
                json_script({"verdict": "correct", "error_description": ""})])
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
    from expedistill.infra.reliability import InfrastructureError

    handle = make_fake_handle(
        script=[InfrastructureError("endpoint down"), _empty_content_error(),
                json_script({"verdict": "correct", "error_description": ""})],
        infra_retries=1, infra_backoff_seconds=0.0)
    usage = RecordingUsage()
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, usage=usage)
    assert out["verdict"] == "correct"
    assert len(handle.client.calls) == 3
    assert [record["status"] for record in usage.records] == [
        "error", "error", "ok"]
    assert usage.records[2]["meta"]["infra_retries"] == 1
    assert usage.records[2]["meta"]["format_retries"] == 1


def test_format_retry_disabled_makes_one_call():
    handle = make_fake_handle(script=["not json", "also not json"])
    with pytest.raises(ModelOutputError):
        call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, max_format_retries=0)
    assert len(handle.client.calls) == 1


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


def test_real_usage_recorder_writes_null_tokens(tmp_path):
    """End-to-end against the landed B7 recorder: unknown usage is stored as
    null on disk, never as 0 (AGENTS.md section 6 rule 5)."""
    from expedistill.infra.usage import UsageRecorder

    usage = UsageRecorder(tmp_path / "usage.jsonl")
    handle = make_fake_handle(script="plain text")
    call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    line = json.loads((tmp_path / "usage.jsonl").read_text().splitlines()[0])
    assert line["in_tokens"] is None
    assert line["out_tokens"] is None
    assert line["status"] == "ok"
    assert line["role"] == "test"


def test_usage_recorded_with_real_tokens():
    payload = json_script({"verdict": "correct", "error_description": ""})

    def script(system, user):
        from expedistill.llm.client import CompletionResult

        return CompletionResult(text=payload, in_tokens=3, out_tokens=5)

    handle = make_fake_handle(role="judge", script=script)
    usage = RecordingUsage()
    call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, usage=usage)
    assert usage.records[0]["in_tokens"] == 3
    assert usage.records[0]["out_tokens"] == 5
    assert usage.records[0]["status"] == "ok"


def test_usage_records_format_errors_then_success():
    handle = make_fake_handle(
        script=["broken", json_script({"verdict": "correct", "error_description": ""})]
    )
    usage = RecordingUsage()
    call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, usage=usage)
    statuses = [record["status"] for record in usage.records]
    assert statuses == ["format_error", "ok"]


def test_usage_records_client_error_and_reraises():
    handle = make_fake_handle(script=[ValueError("endpoint exploded")])
    usage = RecordingUsage()
    with pytest.raises(ValueError):
        call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    assert usage.records[0]["status"] == "error"
    assert usage.records[0]["meta"] == {"error": "ValueError"}
    assert usage.records[0]["in_tokens"] is None


def test_usage_records_tokens_from_client_model_output_error(tmp_path):
    # Real-data finding: the client raises ModelOutputError on empty content
    # AFTER reading response.usage, so a ~40k-token spend was recorded as
    # null in/out. When the exception carries usage, the ledger row and the
    # request dump must carry the real tokens (status stays "error", and
    # client-side ModelOutputError is still not retried: exactly one call).
    exc = ModelOutputError(
        "model fake-model returned empty or whitespace-only content "
        "(finish_reason='length')",
        usage={"prompt_tokens": 40000, "completion_tokens": 5120,
               "total_tokens": 45120},
        finish_reason="length",
    )
    handle = make_fake_handle(script=[exc])
    usage = RecordingUsage()
    with pytest.raises(ModelOutputError):
        call_model(handle, "ANSWER", ANSWER_VARS, usage=usage, run_dir=tmp_path)
    assert len(handle.client.calls) == 1
    record = usage.records[0]
    assert record["status"] == "error"
    assert record["in_tokens"] == 40000
    assert record["out_tokens"] == 5120
    dump = json.loads(
        (tmp_path / "llm_requests" / "0001_ANSWER.json").read_text())
    assert dump["status"] == "error"
    assert dump["in_tokens"] == 40000
    assert dump["out_tokens"] == 5120
    assert dump["finish_reason"] == "length"


def test_model_output_error_without_usage_still_records_null(tmp_path):
    # Regression: raise sites that carry no usage (old clients, extract_json)
    # must keep recording null tokens, never 0.
    handle = make_fake_handle(script=[ModelOutputError("bad output")])
    usage = RecordingUsage()
    with pytest.raises(ModelOutputError):
        call_model(handle, "ANSWER", ANSWER_VARS, usage=usage, run_dir=tmp_path)
    assert usage.records[0]["status"] == "error"
    assert usage.records[0]["in_tokens"] is None
    assert usage.records[0]["out_tokens"] is None
    dump = json.loads(
        (tmp_path / "llm_requests" / "0001_ANSWER.json").read_text())
    assert dump["status"] == "error"
    assert dump["in_tokens"] is None
    assert dump["out_tokens"] is None


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
        script=["broken", json_script({"verdict": "correct", "error_description": ""})]
    )
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, run_dir=tmp_path)
    assert out["verdict"] == "correct"
    dump_dir = tmp_path / "llm_requests"
    first = json.loads((dump_dir / "0001_JUDGE.json").read_text())
    second = json.loads((dump_dir / "0002_JUDGE.json").read_text())
    assert first["status"] == "format_error"
    assert first["response_text"] == "broken"
    assert second["status"] == "ok"
    assert second["parsed"] == {"verdict": "correct", "error_description": ""}
    assert REPAIR_INSTRUCTION in second["request_text"]


def test_missing_variable_propagates_key_error():
    handle = make_fake_handle(script="x")
    with pytest.raises(KeyError):
        call_model(handle, "ANSWER", {})


def test_unknown_prompt_propagates():
    handle = make_fake_handle(script="x")
    with pytest.raises(KeyError, match="NO_SUCH_PROMPT"):
        call_model(handle, "NO_SUCH_PROMPT", {})


def test_make_handle_wires_fields():
    client = FakeClient("x")
    handle = make_handle("executor_weak", "qwen-7b", client, temperature=0.2)
    assert handle.name == "qwen-7b"
    assert handle.role == "executor_weak"
    assert handle.client is client
    assert handle.temperature == 0.2


def test_extra_body_forwarded_to_client():
    client = FakeClient("x")
    handle = make_handle("executor_strong", "deepseek", client,
                         extra_body={"reasoning_effort": "max"})
    call_model(handle, "ANSWER", ANSWER_VARS)
    assert handle.client.calls[0]["extra"] == {"reasoning_effort": "max"}


def test_extra_body_omitted_when_empty():
    # Client doubles without an ``extra`` parameter must keep working: the
    # kwarg is only sent when the handle actually carries options.
    handle = make_fake_handle(script="x")
    assert handle.extra_body == {}
    call_model(handle, "ANSWER", ANSWER_VARS)
    assert handle.client.calls[0]["extra"] is None


def test_extra_body_survives_dataclasses_replace():
    # K1 rebuilds its working handle with replace(); the options must carry
    # over without being re-specified.
    import dataclasses

    client = FakeClient("x")
    handle = make_handle("executor_strong", "deepseek", client,
                         extra_body={"reasoning_effort": "max"})
    working = dataclasses.replace(handle, usage=None, run_dir=None)
    call_model(working, "ANSWER", ANSWER_VARS)
    assert working.client.calls[0]["extra"] == {"reasoning_effort": "max"}
