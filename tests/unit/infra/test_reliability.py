"""B6 reliability: taxonomy, with_retries, UnitFailureRecorder."""

import pytest

from expedistill.infra.reliability import (
    CodeBugError, ConfigError, InfrastructureError, ModelOutputError,
    ProviderRefusalError, ReliabilityError, UnitFailureRecorder,
    with_retries,
)


def test_taxonomy():
    for cls in (InfrastructureError, ModelOutputError, ProviderRefusalError,
                ConfigError, CodeBugError):
        assert issubclass(cls, ReliabilityError)
    assert InfrastructureError.retryable
    assert ModelOutputError.retryable
    assert not ProviderRefusalError.retryable
    assert not ConfigError.retryable
    assert not CodeBugError.retryable


def test_with_retries_succeeds_after_transient_failures():
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise InfrastructureError("sandbox down")
        return "ok"

    assert with_retries(flaky, attempts=5,
                        retry_on=InfrastructureError, sleep=lambda s: None) \
        == "ok"
    assert len(attempts) == 3


def test_with_retries_exhausts_and_reports_via_on_give_up():
    seen = []

    def always_fails():
        raise ModelOutputError("not json")

    with pytest.raises(ModelOutputError):
        with_retries(always_fails, attempts=2,
                     retry_on=(ModelOutputError, InfrastructureError),
                     on_give_up=seen.append, sleep=lambda s: None)
    assert len(seen) == 1
    assert isinstance(seen[0], ModelOutputError)


def test_with_retries_propagates_non_matching_immediately():
    calls = []

    def wrong_kind():
        calls.append(1)
        raise CodeBugError("not retryable here")

    with pytest.raises(CodeBugError):
        with_retries(wrong_kind, attempts=5,
                     retry_on=InfrastructureError, sleep=lambda s: None)
    assert calls == [1]


def test_with_retries_backoff_receives_failure_count():
    delays = []

    def always_fails():
        raise InfrastructureError("down")

    with pytest.raises(InfrastructureError):
        with_retries(always_fails, attempts=4,
                     retry_on=InfrastructureError,
                     backoff=delays.append, sleep=lambda s: None)
    # attempts=4 means 3 sleeps, with 0-based failure counts.
    assert delays == [0, 1, 2]


def test_with_retries_constant_backoff_sleeps():
    slept = []

    def fail_twice():
        if len(slept) < 2:
            raise InfrastructureError("down")
        raise InfrastructureError("still down")

    with pytest.raises(InfrastructureError):
        with_retries(fail_twice, attempts=4,
                     retry_on=InfrastructureError, backoff=0.5,
                     sleep=slept.append)
    assert slept == [0.5, 0.5, 0.5]


def test_with_retries_rejects_bad_attempts():
    with pytest.raises(ValueError):
        with_retries(lambda: 1, attempts=0,
                     retry_on=InfrastructureError)


def test_failure_recorder_roundtrip(tmp_path):
    recorder = UnitFailureRecorder(tmp_path)
    recorder.record("traj/tb:t1/weak/00", "stage1",
                    InfrastructureError, "sandbox timeout")
    recorder.record("traj/tb:t1/weak/01", "stage1",
                    ModelOutputError("bad json"), "unparseable")
    recorder.record("traj/tb:t1/weak/02", "stage1", "ConfigError", "bad cfg")

    rows = recorder.list_failures()
    assert len(rows) == 3
    assert rows[0]["unit_id"] == "traj/tb:t1/weak/00"
    assert rows[0]["error_class"] == "InfrastructureError"
    assert rows[0]["stage"] == "stage1"
    assert rows[1]["error_class"] == "ModelOutputError"
    assert rows[2]["error_class"] == "ConfigError"
    assert all(row["schema"] == "unit-failure-v1" for row in rows)


def test_failure_recorder_truncates_long_messages(tmp_path):
    recorder = UnitFailureRecorder(tmp_path)
    recorder.record("u", "stage2", CodeBugError, "x" * 5000)
    (row,) = recorder.list_failures()
    assert len(row["message"]) == UnitFailureRecorder.MESSAGE_LIMIT
    assert row["message"].endswith("...")


def test_failure_recorder_accumulates_across_instances(tmp_path):
    # A resume reads the same ledger; records from the first process remain.
    UnitFailureRecorder(tmp_path).record("u1", "stage1", "ConfigError", "a")
    UnitFailureRecorder(tmp_path).record("u2", "stage1", "ConfigError", "b")
    rows = UnitFailureRecorder(tmp_path).list_failures()
    assert [row["unit_id"] for row in rows] == ["u1", "u2"]
