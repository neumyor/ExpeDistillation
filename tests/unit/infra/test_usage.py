"""B7 UsageRecorder: null-not-zero tokens, cross-process accumulation."""

import json

from expedistill.infra.usage import UsageRecorder

PATH_KWARGS = dict(role="weak", prompt="answer", model="model-b",
                   request_id="req-1")


def test_absent_file_totals_zero(tmp_path):
    totals = UsageRecorder(tmp_path / "usage.jsonl").totals()
    assert totals == {"overall": {"in": 0, "out": 0, "cached_tokens": 0,
                                   "request_count": 0},
                      "by_role": {}}


def test_record_writes_one_json_line(tmp_path):
    path = tmp_path / "usage.jsonl"
    recorder = UsageRecorder(path)
    recorder.record(in_tokens=100, out_tokens=20, status="ok", meta=None,
                    **PATH_KWARGS)
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["role"] == "weak"
    assert row["in_tokens"] == 100
    assert row["out_tokens"] == 20
    assert row["status"] == "ok"
    assert row["request_id"] == "req-1"


def test_unknown_tokens_are_null_never_zero(tmp_path):
    path = tmp_path / "usage.jsonl"
    UsageRecorder(path).record(in_tokens=None, out_tokens=None,
                               status="error", **PATH_KWARGS)
    row = json.loads(path.read_text().splitlines()[0])
    assert row["in_tokens"] is None
    assert row["out_tokens"] is None
    totals = UsageRecorder(path).totals()
    assert totals["overall"] == {"in": 0, "out": 0, "cached_tokens": 0,
                                 "request_count": 1}


def test_totals_break_down_by_role(tmp_path):
    path = tmp_path / "usage.jsonl"
    recorder = UsageRecorder(path)
    recorder.record(role="strong", prompt="segment", model="model-a",
                    request_id="r1", in_tokens=10, out_tokens=5)
    recorder.record(role="weak", prompt="answer", model="model-b",
                    request_id="r2", in_tokens=7, out_tokens=3)
    recorder.record(role="weak", prompt="answer", model="model-b",
                    request_id="r3", in_tokens=2, out_tokens=1)
    totals = recorder.totals()
    assert totals["overall"] == {"in": 19, "out": 9, "cached_tokens": 0,
                                 "request_count": 3}
    assert totals["by_role"]["strong"] == {"in": 10, "out": 5,
                                            "cached_tokens": 0,
                                            "request_count": 1}
    assert totals["by_role"]["weak"] == {"in": 9, "out": 4,
                                          "cached_tokens": 0,
                                          "request_count": 2}


def test_totals_fold_cached_tokens(tmp_path):
    # cached_tokens rides in meta (only when the provider reported it). The
    # provider already counts it inside prompt_tokens, so it is folded into
    # its own total, never into in/out.
    path = tmp_path / "usage.jsonl"
    recorder = UsageRecorder(path)
    recorder.record(role="strong", prompt="skill", model="m", request_id="r1",
                    in_tokens=3927, out_tokens=4,
                    meta={"finish_reason": "stop", "cached_tokens": 3712})
    recorder.record(role="strong", prompt="skill", model="m", request_id="r2",
                    in_tokens=100, out_tokens=4,
                    meta={"finish_reason": "stop"})
    recorder.record(role="weak", prompt="answer", model="m", request_id="r3",
                    in_tokens=10, out_tokens=2, meta=None)
    recorder.record(role="weak", prompt="answer", model="m", request_id="r4",
                    in_tokens=5, out_tokens=1, meta={"cached_tokens": 3})
    totals = recorder.totals()
    assert totals["overall"]["cached_tokens"] == 3715
    assert totals["by_role"]["strong"]["cached_tokens"] == 3712
    assert totals["by_role"]["weak"]["cached_tokens"] == 3
    # in/out are untouched by the folding.
    assert totals["overall"]["in"] == 4042
    assert totals["overall"]["out"] == 11


def test_cached_tokens_null_contributes_nothing(tmp_path):
    # A row whose meta carries cached_tokens=null (never 0-as-unknown) adds
    # nothing to the cached total.
    path = tmp_path / "usage.jsonl"
    recorder = UsageRecorder(path)
    recorder.record(role="weak", prompt="answer", model="m", request_id="r1",
                    in_tokens=1, out_tokens=1, meta={"cached_tokens": None})
    totals = recorder.totals()
    assert totals["overall"]["cached_tokens"] == 0
    assert totals["by_role"]["weak"]["cached_tokens"] == 0


def test_totals_accumulate_across_two_instances(tmp_path):
    """A resumed run is a second recorder instance; it must see the first
    process's lines and add to them."""
    path = tmp_path / "usage.jsonl"
    first = UsageRecorder(path)
    first.record(role="weak", prompt="answer", model="model-b",
                 request_id="r1", in_tokens=100, out_tokens=50)

    second = UsageRecorder(path)  # simulates the resume
    second.record(role="weak", prompt="answer", model="model-b",
                  request_id="r2", in_tokens=30, out_tokens=10)

    totals = second.totals()
    assert totals["overall"] == {"in": 130, "out": 60, "cached_tokens": 0,
                                 "request_count": 2}
    # The first instance agrees: totals fold from the file, not memory.
    assert first.totals() == totals


def test_recorder_creates_parent_directory(tmp_path):
    path = tmp_path / "llm_requests" / "usage.jsonl"
    UsageRecorder(path).record(in_tokens=1, out_tokens=1, **PATH_KWARGS)
    assert path.exists()
