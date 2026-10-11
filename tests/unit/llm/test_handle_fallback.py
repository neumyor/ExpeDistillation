"""Regression: ModelHandle carries usage/run_dir so E1-threaded handles keep
accounting without every layer growing parameters (design decision 2026-10-10)."""
from expedistill.infra import UsageRecorder
from expedistill.llm import call_model
from expedistill.llm.fake import json_script, make_fake_handle


def test_call_model_falls_back_to_handle_usage_and_run_dir(tmp_path):
    usage = UsageRecorder(tmp_path / "usage.jsonl")
    handle = make_fake_handle("executor_weak", script=json_script({"ok": 1}))
    handle.usage = usage
    handle.run_dir = tmp_path
    result = call_model(handle, "JUDGE", {
        "question_text": "q", "reference_answer": "a", "weak_answer": "w"},
        output_json=True)
    assert result == {"ok": 1}
    totals = usage.totals()
    assert totals["overall"]["request_count"] == 1
    dumps = list((tmp_path / "llm_requests").glob("*.json"))
    assert dumps and "api_key" not in dumps[0].read_text()


def test_explicit_arguments_win_over_handle_attachments(tmp_path):
    usage_a = UsageRecorder(tmp_path / "a.jsonl")
    usage_b = UsageRecorder(tmp_path / "b.jsonl")
    handle = make_fake_handle("judge", script=json_script({"x": 1}))
    handle.usage = usage_a
    call_model(handle, "JUDGE", {
        "question_text": "q", "reference_answer": "a", "weak_answer": "w"},
        usage=usage_b)
    assert usage_b.totals()["overall"]["request_count"] == 1
    assert usage_a.totals()["overall"]["request_count"] == 0
