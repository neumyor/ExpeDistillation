"""B7 UsageRecorder: null-not-zero tokens, cross-process accumulation.

Also UsageStats.by_role_prompt: the pre-registered per-(role, prompt)
observability metrics over the same ledger.
"""

import json

import pytest

from expedistill.infra.usage import UsageRecorder, UsageStats

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


# ---------------------------------------------------------------------------
# UsageStats.by_role_prompt: pre-registered per-(role, prompt) metrics
# ---------------------------------------------------------------------------


def _write_rows(path, rows):
    """Write raw ledger rows (old formats the recorder never produces)."""
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _row(role, prompt, *, status="ok", in_tokens=None, out_tokens=None,
         meta=None, request_id="r", model="m"):
    """One usage-row dict; meta=None is a written null, like TB plugin rows."""
    return {"role": role, "prompt": prompt, "model": model,
            "in_tokens": in_tokens, "out_tokens": out_tokens,
            "request_id": request_id, "status": status, "meta": meta}


def test_by_role_prompt_counts_errors_and_totals(tmp_path):
    path = tmp_path / "usage.jsonl"
    _write_rows(path, [
        _row("judge", "JUDGE", in_tokens=100, out_tokens=10,
             meta={"finish_reason": "stop", "cap_violation": True,
                   "estimated_in": 80, "cached_tokens": 90}),
        # A failed attempt with partial usage: real cost, but not the size
        # of a successful call.
        _row("judge", "JUDGE", status="error", in_tokens=50,
             meta={"error": "InfrastructureError"}),
        # finish_reason=length on a FAILED attempt: an error, not a
        # length_truncation (that metric watches successful calls).
        _row("judge", "JUDGE", status="format_error", in_tokens=20,
             out_tokens=5,
             meta={"error": "ModelOutputError", "finish_reason": "length"}),
    ])
    group = UsageStats(path).by_role_prompt()[("judge", "JUDGE")]
    assert group["n_calls"] == 3
    assert group["n_ok"] == 1
    assert group["n_error"] == 2
    assert group["errors_by_type"] == {"InfrastructureError": 1,
                                       "ModelOutputError": 1}
    # Medians and maxima over OK rows only.
    assert group["in_median"] == 100
    assert group["in_max"] == 100
    assert group["out_median"] == 10
    assert group["out_max"] == 10
    # Totals over ALL rows: burned failed attempts are real cost.
    assert group["total_in"] == 170
    assert group["total_out"] == 15
    assert group["cap_violations"] == 1
    assert group["length_truncations"] == 0
    assert group["estimated_in_median"] == 80
    assert group["in_over_estimated_median"] == pytest.approx(100 / 80)
    assert group["unknown"] == {}


def test_by_role_prompt_median_excludes_unknown_tokens(tmp_path):
    path = tmp_path / "usage.jsonl"
    _write_rows(path, [
        _row("weak", "ANSWER", in_tokens=100, out_tokens=10),
        _row("weak", "ANSWER", in_tokens=None, out_tokens=None),
        _row("weak", "ANSWER", in_tokens=200, out_tokens=40),
        _row("weak", "ANSWER", status="error", in_tokens=999,
             meta={"error": "TimeoutError"}),
    ])
    group = UsageStats(path).by_role_prompt()[("weak", "ANSWER")]
    # The null row is absent from the median (never a 0) and tallied as
    # unknown instead; the error row is absent from both, present in totals.
    assert group["in_median"] == 150
    assert group["in_max"] == 200
    assert group["out_median"] == 25
    assert group["out_max"] == 40
    assert group["total_in"] == 1299
    assert group["total_out"] == 50
    assert group["unknown"] == {"in_tokens": 1, "out_tokens": 1,
                                "estimated_in": 3, "finish_reason": 3,
                                "cap_violation": 3}


def test_by_role_prompt_calibration_ratio_needs_both_values(tmp_path):
    path = tmp_path / "usage.jsonl"
    _write_rows(path, [
        _row("strong", "SKILL", in_tokens=120, meta={"estimated_in": 100}),
        _row("strong", "SKILL", in_tokens=180, meta={"estimated_in": 200}),
        _row("strong", "SKILL", in_tokens=300, meta={"estimated_in": 100}),
        # in known, estimate not reported: no ratio contribution.
        _row("strong", "SKILL", in_tokens=400, meta={"estimated_in": None}),
        # estimate known, in not reported: no ratio contribution either.
        _row("strong", "SKILL", in_tokens=None, meta={"estimated_in": 500}),
    ])
    group = UsageStats(path).by_role_prompt()[("strong", "SKILL")]
    # Per-row ratios 1.2, 0.9, 3.0 -> median 1.2; only ok rows with BOTH a
    # measured in and an estimate count.
    assert group["in_over_estimated_median"] == pytest.approx(1.2)
    # estimated_in_median ignores in_tokens: all four reported estimates.
    assert group["estimated_in_median"] == 150
    assert group["in_median"] == 240
    assert group["unknown"] == {"in_tokens": 1, "out_tokens": 5,
                                "estimated_in": 1, "finish_reason": 5,
                                "cap_violation": 5}


def test_by_role_prompt_tolerates_tb_rows_without_meta(tmp_path):
    # TB remote rows (role executor_weak, prompt TB_TASK_PROMPT) never
    # carry the new meta keys; they must aggregate without crashing and
    # surface as unknown, never as clean zeros.
    path = tmp_path / "usage.jsonl"
    _write_rows(path, [
        _row("executor_weak", "TB_TASK_PROMPT", in_tokens=1000),
        _row("executor_weak", "TB_TASK_PROMPT", in_tokens=2000),
        _row("executor_weak", "TB_TASK_PROMPT", status="error"),
    ])
    group = UsageStats(path).by_role_prompt()[("executor_weak",
                                              "TB_TASK_PROMPT")]
    assert group["n_calls"] == 3
    assert group["n_ok"] == 2
    assert group["n_error"] == 1
    assert group["errors_by_type"] == {"(unknown)": 1}
    assert group["in_median"] == 1500
    assert group["in_max"] == 2000
    assert group["total_in"] == 3000
    # Output usage was never reported: unknown (absent from the median,
    # never 0-as-measurement); the total stays a sum of nothing measured.
    assert group["out_median"] is None
    assert group["out_max"] is None
    assert group["total_out"] == 0
    assert group["cap_violations"] == 0
    assert group["length_truncations"] == 0
    assert group["estimated_in_median"] is None
    assert group["in_over_estimated_median"] is None
    assert group["unknown"] == {"out_tokens": 2, "estimated_in": 2,
                                "finish_reason": 2, "cap_violation": 2,
                                "error": 1}


def test_by_role_prompt_length_truncation_vs_cap_violation(tmp_path):
    path = tmp_path / "usage.jsonl"
    _write_rows(path, [
        # cap honored but too small: finish_reason=length.
        _row("judge", "SEGMENT", in_tokens=10, out_tokens=10,
             meta={"finish_reason": "length", "cap_violation": False}),
        # cap ignored by the endpoint.
        _row("judge", "SEGMENT", in_tokens=10, out_tokens=10,
             meta={"finish_reason": "stop", "cap_violation": True}),
    ])
    group = UsageStats(path).by_role_prompt()[("judge", "SEGMENT")]
    assert group["length_truncations"] == 1
    assert group["cap_violations"] == 1
    # cap_violation=False is a real report (not unknown): reported, and
    # not violated.
    assert group["unknown"] == {"estimated_in": 2}


def test_by_role_prompt_old_ledger_without_meta_key(tmp_path):
    # Old-format rows (written before meta existed at all): no crash, the
    # meta-derived metrics read as unknown instead of clean zeros.
    path = tmp_path / "usage.jsonl"
    path.write_text(json.dumps({
        "role": "weak", "prompt": "answer", "model": "model-b",
        "in_tokens": 7, "out_tokens": 3, "request_id": "r1", "status": "ok",
    }) + "\n", encoding="utf-8")
    group = UsageStats(path).by_role_prompt()[("weak", "answer")]
    assert group["n_calls"] == 1
    assert group["n_ok"] == 1
    assert group["n_error"] == 0
    assert group["in_median"] == 7
    assert group["in_max"] == 7
    assert group["estimated_in_median"] is None
    assert group["in_over_estimated_median"] is None
    assert group["unknown"] == {"estimated_in": 1, "finish_reason": 1,
                                "cap_violation": 1}


def test_by_role_prompt_groups_by_role_and_prompt(tmp_path):
    # Recorder-written rows flow through UsageStats too; the group key is
    # the (role, prompt) pair, not role alone.
    path = tmp_path / "usage.jsonl"
    recorder = UsageRecorder(path)
    recorder.record(role="judge", prompt="JUDGE", model="m", request_id="r1",
                   in_tokens=10, out_tokens=1)
    recorder.record(role="judge", prompt="SEGMENT", model="m", request_id="r2",
                   in_tokens=20, out_tokens=2)
    recorder.record(role="executor_weak", prompt="JUDGE", model="m",
                    request_id="r3", in_tokens=30, out_tokens=3)
    stats = UsageStats(path).by_role_prompt()
    assert set(stats) == {("judge", "JUDGE"), ("judge", "SEGMENT"),
                          ("executor_weak", "JUDGE")}
    assert stats[("executor_weak", "JUDGE")]["total_in"] == 30
    assert stats[("judge", "SEGMENT")]["n_calls"] == 1


def test_by_role_prompt_absent_file_is_empty(tmp_path):
    assert UsageStats(tmp_path / "usage.jsonl").by_role_prompt() == {}
