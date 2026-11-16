"""The run audit passes a consistent synthetic run and fails each kind of
inconsistency it is meant to catch, including the TB-only checks."""

import importlib.util
import json
import urllib.parse
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "audit_run.py"
spec = importlib.util.spec_from_file_location("audit_run", SCRIPT)
audit_run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_run)

SKILL = "type/001/skill"


def _json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows),
                    encoding="utf-8")


def _sheet(sheet_id, label, role, verdict):
    return {"sheet_id": sheet_id, "phase_label": label,
            "coverage": {"status": "present"},
            "execution": {"model_role": role},
            "grading": {"verdict": verdict}}


def _trajectory(directory, trajectory_id, role, meta=None):
    name = urllib.parse.quote(trajectory_id, safe="") + ".json"
    _json(directory / name, {"trajectory_id": trajectory_id,
                             "model_role": role, "meta": meta or {}})


def build_run(tmp_path, benchmark="alfworld"):
    run = tmp_path / "run"
    phases = run / "phases"
    _json(run / "manifest.json", {
        "benchmark": benchmark,
        "split": {"train": ["t1"], "val": [], "test": ["t3"], "reserved": []},
        "config": {"sequence": ["baseline", "learn", "test"], "phases": {
            "baseline": {"kind": "execute", "role": "baseline"},
            "learn": {"kind": "learn", "role": "learn"},
            "test": {"kind": "execute", "role": "test"}}}})
    _jsonl(run / "pipeline_log.jsonl", [
        {"stage": label, "summary": {}}
        for label in ("p00:freeze", "p01:baseline", "p02:learn", "p03:test")])

    base = phases / "p01_baseline"
    _json(base / "phase_summary.json", {
        "role": "baseline", "infra_missing": [], "infra_accepted": [],
        "complete": True})
    for sheet in (_sheet("s1", "p01:baseline", "weak", "failure"),
                  _sheet("s2", "p01:baseline", "weak", "failure"),
                  _sheet("s3", "p01:baseline", "strong", "success")):
        _json(base / "answer_sheets" / f"{sheet['sheet_id']}.json", sheet)
    _jsonl(base / "coverage.jsonl", [{"status": "present"}] * 3)

    learn = phases / "p02_learn"
    _json(learn / "learn_summary.json", {
        "role": "learn", "input_phase": "p01:baseline",
        "bank": {"members": [SKILL], "size": 1}})
    _jsonl(learn / "findings.jsonl", [{"sheet_id": "s1", "scope": "subtask"}])
    _jsonl(learn / "evidence.jsonl", [{
        "sheet_id": "s1", "type_id": "type/001", "skill_id": None,
        "category": "gap"}])
    _jsonl(learn / "outcomes.jsonl", [{
        "phase_label": "p02:learn", "target_kind": "type",
        "target_id": "type/001", "skill_id": SKILL, "outcome": "written"}])
    _jsonl(learn / "skill_bank.jsonl", [{"skill_id": SKILL}])
    _json(learn / "bank_summary.json", {"members": [SKILL], "size": 1})
    _jsonl(run / "failed_units.jsonl", [{
        "unit_id": "s2", "stage": "p02:learn",
        "error_class": "ModelOutputError", "message": "x"}])

    test = phases / "p03_test"
    _json(test / "phase_summary.json", {
        "role": "test", "with_bank": {"tasks": 1}, "control": {"tasks": 1},
        "infra_missing": {"with_bank": [], "control": []},
        "infra_accepted": {"with_bank": [], "control": []},
        "complete": True})
    _json(test / "answer_sheets" / "w.json",
          _sheet("w", "p03:test", "weak", "success"))
    _json(test / "answer_sheets" / "c.json",
          _sheet("c", "p03:test", "weak", "invalid"))
    _jsonl(test / "coverage.jsonl", [{"status": "present"}] * 2)

    store = run / "stage1_baseline" / "trajectories"
    _trajectory(store, "traj/p01_baseline/t1/weak/00", "weak")
    _trajectory(store, "traj/p01_baseline/t1/strong/00", "strong")
    _trajectory(test / "with_bank" / "store" / "stage1_baseline" / "trajectories",
                "traj/p03_test/t3/weak/00", "weak",
                {"eval_mode": "skills", "read_suffix_uploaded": True,
                 "remote_root": str(tmp_path / "bench"), "run_id": "r1"})
    _trajectory(test / "control" / "store" / "stage1_baseline" / "trajectories",
                "traj/p03_test/t3/weak/00", "weak", {"eval_mode": "bare"})
    _jsonl(run / "llm_requests" / "usage.jsonl", [
        {"role": "executor_weak", "meta": {"finish_reason": "stop"}}])
    return run


def _statuses(report):
    return {item["check"]: item["status"] for item in report.items}


def test_a_consistent_run_passes(tmp_path, capsys):
    run = build_run(tmp_path)
    before = sorted(run.rglob("*"))
    assert audit_run.main([str(run)]) == 0
    out = capsys.readouterr().out
    assert "AUDIT PASSED" in out
    assert "FAIL" not in out.replace("0 FAIL", "")
    assert sorted(run.rglob("*")) == before  # read-only


def test_a_missing_diagnosis_fails_checks_3_and_4(tmp_path):
    run = build_run(tmp_path)
    _jsonl(run / "phases" / "p02_learn" / "findings.jsonl", [])
    statuses = _statuses(audit_run.audit(run))
    assert statuses["3 p02:learn diagnoses"] == "FAIL"
    assert statuses["4 p02:learn evidence"] == "FAIL"
    assert audit_run.main([str(run)]) == 1


def test_evidence_without_receipt_fails(tmp_path):
    run = build_run(tmp_path)
    _jsonl(run / "phases" / "p02_learn" / "outcomes.jsonl", [])
    assert _statuses(audit_run.audit(run))["4 p02:learn receipts"] == "FAIL"


def test_bank_summary_disagreeing_with_the_bank_fails(tmp_path):
    run = build_run(tmp_path)
    _json(run / "phases" / "p02_learn" / "bank_summary.json", {"members": []})
    assert _statuses(audit_run.audit(run))["5 p02:learn bank"] == "FAIL"


def test_holes_fail_and_accepted_holes_warn(tmp_path):
    run = build_run(tmp_path)
    summary = run / "phases" / "p01_baseline" / "phase_summary.json"
    _json(summary, {"role": "baseline", "infra_missing": ["u1"],
                    "infra_accepted": [], "complete": False})
    assert _statuses(audit_run.audit(run))["2 p01:baseline summary"] == "FAIL"
    _json(summary, {"role": "baseline", "infra_missing": [],
                    "infra_accepted": ["u1"], "complete": True})
    statuses = _statuses(audit_run.audit(run))
    assert statuses["2 p01:baseline summary"] == "PASS"
    assert statuses["2 p01:baseline accepted holes"] == "WARN"


def test_ungraded_sheet_and_missing_coverage_fail(tmp_path):
    run = build_run(tmp_path)
    base = run / "phases" / "p01_baseline"
    _json(base / "answer_sheets" / "s9.json",
          _sheet("s9", "p01:baseline", "strong", None))
    _jsonl(base / "coverage.jsonl", [{"status": "missing"}])
    statuses = _statuses(audit_run.audit(run))
    assert statuses["2 p01:baseline sheets"] == "FAIL"
    assert statuses["2 p01:baseline coverage"] == "FAIL"


def test_test_arm_not_covering_the_test_list_fails(tmp_path):
    run = build_run(tmp_path)
    summary = run / "phases" / "p03_test" / "phase_summary.json"
    data = json.loads(summary.read_text())
    data["control"]["tasks"] = 0
    _json(summary, data)
    assert _statuses(audit_run.audit(run))["6 p03:test arms"] == "FAIL"


def test_unfinished_ledger_and_malformed_files_fail_without_crashing(tmp_path):
    run = build_run(tmp_path)
    _jsonl(run / "pipeline_log.jsonl", [{"stage": "p00:freeze", "summary": {}}])
    (run / "phases" / "p02_learn" / "evidence.jsonl").write_text("{broken\n")
    statuses = _statuses(audit_run.audit(run))
    assert statuses["1 ledger"] == "FAIL"
    assert statuses["3-5 p02:learn"] == "FAIL"
    assert statuses["6 p03:test arms"] == "PASS"


def test_missing_manifest_fails(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    assert audit_run.main([str(run)]) == 1


def test_tb_checks(tmp_path):
    run = build_run(tmp_path, benchmark="tb")
    statuses = _statuses(audit_run.audit(run))
    assert statuses["tb read suffix"] == "PASS"
    assert statuses["tb p03:test invalid"] == "INFO"
    assert statuses["tb weak reasoning_effort"] == "INFO"  # no config readable

    configs = tmp_path / "bench" / "configs"
    configs.mkdir(parents=True)
    (configs / "r1.yaml").write_text("agent:\n  model: weak-model\n")
    assert _statuses(audit_run.audit(run))["tb weak reasoning_effort"] == "PASS"
    (configs / "r1.yaml").write_text(
        "agent:\n  kwargs:\n    reasoning_effort: max\n")
    assert _statuses(audit_run.audit(run))["tb weak reasoning_effort"] == "FAIL"

    store = (run / "phases" / "p03_test" / "with_bank" / "store"
             / "stage1_baseline" / "trajectories")
    _trajectory(store, "traj/p03_test/t3/weak/00", "weak",
                {"eval_mode": "skills"})
    assert _statuses(audit_run.audit(run))["tb read suffix"] == "FAIL"


def test_tb_checks_are_skipped_for_other_benchmarks(tmp_path):
    run = build_run(tmp_path)
    assert not any(check.startswith("tb ")
                   for check in _statuses(audit_run.audit(run)))


def test_report_file_is_written_outside_the_run_only(tmp_path):
    run = build_run(tmp_path)
    with pytest.raises(SystemExit) as raised:
        audit_run.main([str(run), "--out", str(run / "audit.json")])
    assert raised.value.code == 2
    out = tmp_path / "reports" / "audit.json"
    assert audit_run.main([str(run), "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["passed"] is True
    assert {item["status"] for item in report["items"]} <= {"PASS", "INFO", "WARN"}
