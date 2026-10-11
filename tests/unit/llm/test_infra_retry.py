"""Regression: call_model retries transient endpoint failures per call.

Before this, any InfrastructureError from the client (one gateway 502)
stopped segmentation / authoring / exam / curation outright and forfeited a
whole local ReAct episode. The retry is bounded, opt-in per handle
(``infra_retries`` defaults to 0), retries only InfrastructureError, and every
failed attempt is still accounted and dumped. FakeClient only; sleeps are
captured, never slept.
"""

import dataclasses
import json
import logging

import pytest

from expedistill.infra.reliability import (
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    ProviderRefusalError,
)
from expedistill.llm import call as call_module
from expedistill.llm import call_model, json_script, make_fake_handle, make_handle
from expedistill.llm.fake import FakeClient

ANSWER_VARS = {"question_text": "q", "skills_block": ""}
JUDGE_VARS = {"question_text": "q", "reference_answer": "r", "weak_answer": "w"}
VERDICT = {"verdict": "correct", "error_description": ""}


class RecordingUsage:
    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(call_module, "_sleep", calls.append)
    return calls


def _infra(n=1):
    return [InfrastructureError(f"HTTP 502 #{i}") for i in range(n)]


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
