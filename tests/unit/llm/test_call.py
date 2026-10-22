"""Tests for call_model: prompt lookup, JSON repair retry, usage accounting,
and request dumps. Uses FakeClient only; no network."""

import json
import re
import uuid

import pytest

from expedistill.infra.reliability import ConfigError, ModelOutputError
from expedistill.inject import estimate_tokens
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
    assert call["temperature"] == 1.0
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


def _result(text, **result_kw):
    """A CompletionResult instance, for LIST scripts: a list item is
    returned as-is (only a whole-callable script gets invoked)."""
    from expedistill.llm.client import CompletionResult

    return CompletionResult(text=text, **result_kw)


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


def test_raw_object_with_embedded_code_fence_parses_first_try():
    # The 2026-10-13 incident (dump 0363 shape, shortened): a raw UNFENCED
    # JSON object whose string values embed ``` code blocks with `[]` /
    # `[0]` fragments. The old fence-first candidate order parsed the
    # fragment as a top-level array, the block refused it ("expected a JSON
    # object, got list") and the unit burned its whole format budget on a
    # reply that was valid all along. The object must parse on the first
    # attempt with zero format retries.
    reply = json.dumps({
        "question_text": "How do you probe an unknown binary file?",
        "reference_answer": "Run this first:\n"
                           "```\n"
                           "python3 - <<'PY'\n"
                           "seen = []\n"
                           "print(seen[0])\n"
                           "PY\n"
                           "```",
    }, ensure_ascii=False)
    handle = make_fake_handle(role="examiner", script=reply)

    out = call_model(handle, "EXAM_GEN", {
        "label": "probe the file",
        "description": "identify the file format",
        "steps_view": "step 0\naction: run file",
    }, output_json=True)

    assert isinstance(out, dict)
    assert out["question_text"] == "How do you probe an unknown binary file?"
    # Zero format retries: one request, one ok usage row.
    assert len(handle.client.calls) == 1


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
    # Plain-text calls (method-level since protocol v2 that is MERGE alone --
    # SKILL_DISTILL, like REWRITE, calls with output_json=True) keep the
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
    assert usage.records[0]["meta"]["error"] == "ValueError"
    # The budget measurement keys land on error rows too: the request was
    # sent with the handle's cap, and the prompt was measured before it.
    assert usage.records[0]["meta"]["requested_max_tokens"] == 5120
    assert usage.records[0]["meta"]["estimated_in"] == estimate_tokens(
        DEFAULT_SYSTEM + handle.client.calls[0]["user"])
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


# ---------------------------------------------------------------------------
# Token budget measurement: requested_max_tokens, estimated_in,
# cap_violation, input_violation -- measured on every row, never enforced.
# ---------------------------------------------------------------------------

def test_requested_max_tokens_in_meta_for_ok_row():
    handle = make_fake_handle(role="judge", script="x", max_tokens=64)
    usage = RecordingUsage()
    call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    assert usage.records[0]["meta"]["requested_max_tokens"] == 64


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
                json_script({"verdict": "correct", "error_description": ""})],
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


def test_estimated_in_grows_on_repair_reask():
    handle = make_fake_handle(
        script=["not json", json_script({"verdict": "correct",
                                         "error_description": ""})])
    usage = RecordingUsage()
    call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, usage=usage)
    assert [record["status"] for record in usage.records] == [
        "format_error", "ok"]
    first = estimate_tokens(DEFAULT_SYSTEM + handle.client.calls[0]["user"])
    second = estimate_tokens(DEFAULT_SYSTEM + handle.client.calls[1]["user"])
    assert usage.records[0]["meta"]["estimated_in"] == first
    assert usage.records[1]["meta"]["estimated_in"] == second
    assert second > first


def test_estimated_in_grows_on_empty_reask():
    handle = make_fake_handle(
        script=[_empty_content_error(),
                json_script({"verdict": "correct", "error_description": ""})])
    usage = RecordingUsage()
    call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, usage=usage)
    assert (usage.records[1]["meta"]["estimated_in"]
            > usage.records[0]["meta"]["estimated_in"])


def test_cap_violation_flagged_when_output_exceeds_requested():
    # Real-data evidence (qwen ignored the cap 99/727 times): a response
    # with out_tokens over the requested cap is flagged in the row's meta.
    handle = make_fake_handle(
        script=_result_script("long reply", in_tokens=10, out_tokens=6000),
        max_tokens=5120)
    usage = RecordingUsage()
    call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    assert usage.records[0]["meta"]["cap_violation"] is True


def test_cap_violation_absent_at_or_below_the_cap():
    handle = make_fake_handle(
        script=_result_script("x", out_tokens=5120), max_tokens=5120)
    usage = RecordingUsage()
    call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    assert "cap_violation" not in usage.records[0]["meta"]


def test_cap_violation_absent_when_out_tokens_or_cap_unknown():
    # Plain-string results have no out_tokens; an uncapped handle has no
    # requested cap: nothing to compare, so no flag.
    handle = make_fake_handle(script="plain text")
    usage = RecordingUsage()
    call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    assert "cap_violation" not in usage.records[0]["meta"]

    uncapped = make_fake_handle(
        script=_result_script("x", out_tokens=5), max_tokens=None)
    usage2 = RecordingUsage()
    call_model(uncapped, "ANSWER", ANSWER_VARS, usage=usage2)
    assert "cap_violation" not in usage2.records[0]["meta"]


def test_input_violation_flagged_and_call_proceeds():
    # v1 decision: measure only. A prompt estimated over the handle's
    # max_input_tokens NEVER aborts the call; the flag lands in the row.
    handle = make_fake_handle(script="the answer", max_input_tokens=8)
    usage = RecordingUsage()
    out = call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    assert out == "the answer"
    assert len(handle.client.calls) == 1
    assert usage.records[0]["meta"]["input_violation"] is True


def test_input_violation_absent_without_limit_or_under_it():
    handle = make_fake_handle(script="the answer")
    usage = RecordingUsage()
    call_model(handle, "ANSWER", ANSWER_VARS, usage=usage)
    assert "input_violation" not in usage.records[0]["meta"]

    capped = make_fake_handle(script="the answer", max_input_tokens=10**9)
    usage2 = RecordingUsage()
    call_model(capped, "ANSWER", ANSWER_VARS, usage=usage2)
    assert "input_violation" not in usage2.records[0]["meta"]


def test_handle_max_input_tokens_validation():
    # Like the other handle numerics: positive int or None.
    client = FakeClient("x")
    assert make_handle("judge", "m", client).max_input_tokens is None
    make_handle("judge", "m", client, max_input_tokens=131072)
    for bad in (0, -5, True, "512", 1.5):
        with pytest.raises(ConfigError):
            make_handle("judge", "m", client, max_input_tokens=bad)


def test_max_input_tokens_survives_dataclasses_replace():
    import dataclasses

    handle = make_handle("judge", "m", FakeClient("x"), max_input_tokens=999)
    working = dataclasses.replace(handle, usage=None, run_dir=None)
    assert working.max_input_tokens == 999


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
            json_script({"verdict": "correct", "error_description": ""}),
            in_tokens=10, out_tokens=6000, finish_reason="length"),
        max_tokens=5120)
    usage = RecordingUsage()
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, usage=usage)
    assert out == {"verdict": "correct", "error_description": ""}
    assert len(handle.client.calls) == 1
    assert usage.records[0]["meta"]["finish_reason"] == "length"
    assert usage.records[0]["meta"]["cap_violation"] is True


def test_json_truncated_text_keeps_extract_json_retry():
    # The JSON path rejects truncation via extract_json (a truncated value
    # fails to parse -> the existing format retry), NOT via the new
    # plain-text truncation re-ask.
    handle = make_fake_handle(
        script=[_result('{"verdict": "corr', finish_reason="length"),
                json_script({"verdict": "correct", "error_description": ""})])
    usage = RecordingUsage()
    out = call_model(handle, "JUDGE", JUDGE_VARS, output_json=True, usage=usage)
    assert out["verdict"] == "correct"
    assert len(handle.client.calls) == 2
    second_user = handle.client.calls[1]["user"]
    assert "not valid JSON" in second_user
    assert "cut off at the output limit" not in second_user
    assert usage.records[0]["status"] == "format_error"
    assert usage.records[0]["meta"]["finish_reason"] == "length"
