"""W3-B (R10): audit_live_run's schema dispatch and the v4 read paths.

scripts/audit/audit_live_run.py routes by the frozen manifest's schema
version: a v4 run is audited through its phase directories and the
frozen sequence decides the acceptance mode (the alignment review's
finding 1), a pre-v4 run keeps the historical stage paths (R10's hard
requirement: history stays auditable). The script is loaded from its
path (scripts/ is not a package); the fake v4 run is the script's own
self-test builder (a hand-built minimal run, per the tests/fixtures
discipline).
"""

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

from expedistill.core import ids
from expedistill.core.models import Skill
from expedistill.infra.store import Store, read_json, write_json, write_jsonl

_SCRIPT = (Path(__file__).resolve().parents[3]
           / "scripts" / "audit" / "audit_live_run.py")


def _load_audit_module():
    spec = importlib.util.spec_from_file_location("audit_live_run", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit_live_run = _load_audit_module()


def _clean_v4_run(tmp_path: Path) -> Path:
    """The self-test's clean v4 fake run, rebuilt fresh per test (the
    tampers of one test must not leak into the next)."""
    return audit_live_run._build_fake_v4_run(tmp_path)


def _errors(run_dir: Path):
    return [finding["message"] for finding in
            audit_live_run.audit_run(run_dir).errors()]


# ---------------------------------------------------------------------------
# The schema dispatch
# ---------------------------------------------------------------------------

def test_a_v4_manifest_goes_to_the_phase_paths(tmp_path):
    run_dir = _clean_v4_run(tmp_path)
    audit = audit_live_run.audit_run(run_dir)
    # No crash, and the phase-directory checks ran: the phase dirs are
    # all named in the findings universe only if something is off -- the
    # clean run reports only the expected INFOs.
    assert not audit.errors()
    checks = {finding["check"] for finding in audit.findings}
    assert "phase-dirs" not in checks  # clean: silent
    # The manifest is the v4 shape (the dispatch really keyed off it).
    config = read_json(run_dir / "manifest.json")["config"]
    assert config["schema_version"] == 4


def test_a_pre_v4_manifest_keeps_the_legacy_paths(tmp_path):
    run_dir = audit_live_run._build_fake_run(tmp_path)
    manifest = read_json(run_dir / "manifest.json")
    assert "sequence" not in manifest["config"]  # the legacy fake run
    audit = audit_live_run.audit_run(run_dir)
    assert not audit.errors()
    # The legacy five checks ran over the stage paths (the verdicts
    # ledger of the live subtree was read, not any phase directory).
    assert (run_dir / "stage4_acceptance" / "live_run").exists()


def test_the_mode_comes_from_the_frozen_sequence_not_a_slot(tmp_path):
    """The alignment review's finding 1: a sequence carrying an exam
    phase is exam mode; a live phase without one is live_run mode. No
    config.methods.acceptance is consulted anywhere on the v4 path."""
    run_dir = _clean_v4_run(tmp_path)
    manifest = read_json(run_dir / "manifest.json")
    # Flip the live phases to exam phases: the live subtree then contradicts
    # the frozen identity exactly as the legacy exam-mode check demands.
    for name in ("live_1", "live_2"):
        manifest["config"]["phases"][name]["role"] = "exam"
    write_json(run_dir / "manifest.json", manifest, indent=2)
    errors = _errors(run_dir)
    assert any("froze the exam mode but a live_run subtree exists"
               in message for message in errors)


def test_a_live_sequence_without_the_live_subtree_is_a_broken_run(tmp_path):
    run_dir = _clean_v4_run(tmp_path)
    import shutil
    shutil.rmtree(run_dir / "stage4_acceptance" / "live_run")
    errors = _errors(run_dir)
    assert any("carries live phases but there is no "
               "stage4_acceptance/live_run/ subtree" in message
               for message in errors)


def test_n_attempts_is_read_per_live_phase_and_overridable(tmp_path):
    """Each live phase carries its own depth (execution.n_attempts); the
    --n-attempts override replaces every round's depth."""
    run_dir = _clean_v4_run(tmp_path)
    # Round 1's frozen depth (3) mismatches a stored round built for 2:
    # shrinking the second live phase's depth must flag the stored
    # round-1 units as beyond the frozen depth.
    manifest = read_json(run_dir / "manifest.json")
    manifest["config"]["phases"]["live_2"]["execution"]["n_attempts"] = 2
    write_json(run_dir / "manifest.json", manifest, indent=2)
    errors = _errors(run_dir)
    assert any("unexpected stored unit" in message
               or "neither stored nor skipped" in message
               or "!= expected 2" in message
               for message in errors)
    # The override restores the audit: every round is judged at 3.
    assert not _errors_with_override(run_dir, 3)


def _errors_with_override(run_dir: Path, n_attempts: int):
    audit = audit_live_run.audit_run(run_dir, n_attempts)
    return [finding["message"] for finding in audit.errors()]


# ---------------------------------------------------------------------------
# The v4 phase-directory checks
# ---------------------------------------------------------------------------

def test_a_phase_record_drifted_from_the_sequence_is_an_error(tmp_path):
    run_dir = _clean_v4_run(tmp_path)
    record_path = (run_dir / "phases" / "p03_live_1"
                   / audit_live_run.PHASE_RECORD_NAME)
    record = read_json(record_path)
    record["index"] = 5
    write_json(record_path, record, indent=2)
    assert any("phase.json says index=5" in message
               for message in _errors(run_dir))


def test_an_unplanned_report_row_is_an_error(tmp_path):
    run_dir = _clean_v4_run(tmp_path)
    report_path = (run_dir / "phases" / "p03_live_1"
                   / audit_live_run.VERDICTS_NAME)
    rows = [json.loads(line) for line in
            report_path.read_text(encoding="utf-8").splitlines() if line]
    rows.append(audit_live_run._verdict_row(
        "p03:live_1:tb:tb:t1:weak:deadbeef"))
    write_jsonl(report_path, rows)
    assert any("never resolved" in message
               for message in _errors(run_dir))


def test_a_final_test_report_is_an_error(tmp_path):
    run_dir = _clean_v4_run(tmp_path)
    write_jsonl(run_dir / "phases" / "p07_test"
               / audit_live_run.VERDICTS_NAME, [])
    assert any("final-test phase wrote per-unit verdicts" in message
               for message in _errors(run_dir))


def test_a_bank_artifact_without_the_summary_commit_is_a_warn(tmp_path):
    run_dir = _clean_v4_run(tmp_path)
    # Remove the last learn phase's commit signal but keep its artifact:
    # the artifact is then an interrupted commit the resume must finish
    # (and every assembled run sits in this state until its last learn
    # phase runs) -- a human look, not a dead run (Wave 3 review O3's
    # ruling: ERROR downgraded to WARN, matching the module's own
    # grading docstring and the legacy terminals check).
    (run_dir / "phases" / "p06_learn_final"
     / audit_live_run.LEARN_SUMMARY_NAME).unlink()
    audit = audit_live_run.audit_run(run_dir)
    assert not audit.errors()
    warns = [finding["message"] for finding in audit.findings
             if finding["severity"] == "WARN"]
    assert any("never committed it" in message for message in warns)


def test_an_already_accepted_echo_of_an_earlier_receipt_is_the_resume_idiom(
        tmp_path):
    """Wave 3 review O2's ruling: a phase-internal resume re-derives the
    same keep row and lands it on the now-accepted member, so
    "accepted" followed by "already_accepted" for one (target, skill)
    is the legal resume idiom, not a replayed landing. Only a second
    receipt that CARRIES AN ACTION is a duplicate."""
    run_dir = _clean_v4_run(tmp_path)
    # A task no live round ever measured, so no group verdict claims it.
    keeper = audit_live_run._fake_group_skill(ids.make_task_id("tb", "t9"),
                                               0)
    keeper.status = "accepted"
    Store(run_dir).save(keeper, "skills")
    outcomes = run_dir / "phases" / "p02_learn_seed" / audit_live_run.OUTCOMES_NAME
    for outcome in ("accepted", "already_accepted"):
        audit_live_run._append_jsonl_raw(outcomes, {
            "schema": audit_live_run.LEARN_OUTCOME_SCHEMA,
            "phase_label": "p02:learn_seed", "target_kind": "skill",
            "target_id": keeper.skill_id, "action": "keep",
            "skill_id": keeper.skill_id, "outcome": outcome, "version": 1})
    assert not _errors(run_dir)


def test_a_second_action_receipt_for_one_target_skill_is_an_error(tmp_path):
    run_dir = _clean_v4_run(tmp_path)
    outcomes = run_dir / "phases" / "p04_learn_mid" / audit_live_run.OUTCOMES_NAME
    duplicate = json.loads(outcomes.read_text(encoding="utf-8"
                                              ).splitlines()[0])
    audit_live_run._append_jsonl_raw(outcomes, duplicate)
    assert any("duplicate receipt" in message
               for message in _errors(run_dir))


def test_a_revision_ledger_the_store_contradicts_is_an_error(tmp_path):
    run_dir = _clean_v4_run(tmp_path)
    member = ids.make_skill_id(ids.make_subtask_id(
        ids.make_trajectory_id(ids.make_task_id("tb", "t1"),
                               "strong", 0), 0))
    task_id = ids.make_task_id("tb", "t1")
    # A revision plan naming a from_version the store never held (the
    # skill is at 2, the plan claims it was at 5 when the revision
    # started): the store cannot be behind its own revision ledger.
    # The row goes to the final learn phase (its revisions ledger is
    # empty, so this plan row is the first one read for the skill).
    audit_live_run._append_jsonl_raw(
        run_dir / "phases" / "p06_learn_final" / audit_live_run.REVISIONS_NAME, {
            "schema": "learn-revision-v1", "phase_label": "p06:learn_final",
            "target_kind": "group", "target_id": task_id,
            "skill_id": member, "from_version": 5})
    assert any("drifted from the ledger" in message
               for message in _errors(run_dir))


def test_a_dropped_final_test_unit_on_a_complete_summary_is_an_error(tmp_path):
    run_dir = _clean_v4_run(tmp_path)
    test_id = ids.make_task_id("tb", "tt")
    victim = Store(run_dir / "phases" / "p07_test" / "with_bank"
                   / "store").path_for(
        "trajectories", ids.make_trajectory_id(test_id, "weak", 0))
    victim.unlink()
    assert any("with_bank arm is missing" in message
               for message in _errors(run_dir))


# ---------------------------------------------------------------------------
# The external-campaign control: no control units anywhere; the check is
# the phase directory's byte copy of the pinned score file.
# ---------------------------------------------------------------------------

def _make_external_c_run(tmp_path: Path) -> Path:
    """The clean v4 fake run with the test phase flipped to the external
    campaign control: no control store, the score file's bytes copied
    into the phase directory, the manifest pinning its sha256."""
    run_dir = _clean_v4_run(tmp_path)
    payload = json.dumps({"tasks": {"qwen:tt": {"n": 5,
                                               "rewards": [0.0] * 5,
                                               "success_rate": 0.0}}}
                         ).encode("utf-8")
    sha = hashlib.sha256(payload).hexdigest()
    manifest = read_json(run_dir / "manifest.json")
    manifest["config"]["phases"]["test"]["compare"] = {
        "against": "external_campaign",
        "external": {"path": "/tmp/scores.json", "sha256": sha,
                     "model_key": "qwen"}}
    write_json(run_dir / "manifest.json", manifest, indent=2)
    shutil.rmtree(run_dir / "phases" / "p07_test" / "control")
    (run_dir / "phases" / "p07_test" / "external_control.json"
     ).write_bytes(payload)
    return run_dir


def test_an_external_campaign_control_skips_units_and_pins_bytes(tmp_path):
    run_dir = _make_external_c_run(tmp_path)
    assert not _errors(run_dir)
    # The absence of a control store is not read as a reused-baseline
    # hole: nothing in the findings mentions the control arm at all.
    assert not any("control" in finding["message"]
                   for finding in audit_live_run.audit_run(run_dir).findings
                   if finding["check"] == "final-test")


def test_a_drifted_external_control_copy_is_an_error(tmp_path):
    run_dir = _make_external_c_run(tmp_path)
    (run_dir / "phases" / "p07_test" / "external_control.json"
     ).write_bytes(b"drifted bytes")
    assert any("score-file copy hashes to" in message
               for message in _errors(run_dir))


def test_a_missing_external_control_copy_is_an_error(tmp_path):
    run_dir = _make_external_c_run(tmp_path)
    (run_dir / "phases" / "p07_test" / "external_control.json").unlink()
    assert any("score-file copy is missing" in message
               for message in _errors(run_dir))


def _terminal_group_store(tmp_path: Path) -> "audit_live_run.Store":
    """One task group whose every member is terminal with a discard
    reason no verdict row of THIS run explains (the assembled shape)."""
    store = audit_live_run.Store(tmp_path / "s")
    skill = Skill(
        skill_id="traj/tb:tt/strong/00/seg00/skill",
        content="x", version=1, status="discarded",
        subtask_id="traj/tb:tt/strong/00/seg00",
        source_subtasks=["traj/tb:tt/strong/00/seg00"],
        discard_reason="leaked")
    store.save(skill, "skills")
    return store


def test_an_assembled_terminal_group_without_verdicts_is_provenance(
        tmp_path):
    store = _terminal_group_store(tmp_path)
    groups = {"tb:tt": [store.load(
        "skills", "traj/tb:tt/strong/00/seg00/skill", Skill)]}
    audit = audit_live_run.Audit(tmp_path)
    audit_live_run.check_group_terminals_v4(
        audit, store, groups, verdict_rows={}, delete_receipts={},
        assembled=True)
    severities = [(f["severity"], f["check"]) for f in audit.findings]
    assert severities == [("INFO", "terminals")]
    assert "assembled" in audit.findings[0]["message"]


def test_a_scratch_terminal_group_without_verdicts_still_warns(tmp_path):
    store = _terminal_group_store(tmp_path)
    groups = {"tb:tt": [store.load(
        "skills", "traj/tb:tt/strong/00/seg00/skill", Skill)]}
    audit = audit_live_run.Audit(tmp_path)
    audit_live_run.check_group_terminals_v4(
        audit, store, groups, verdict_rows={}, delete_receipts={},
        assembled=False)
    severities = [(f["severity"], f["check"]) for f in audit.findings]
    assert severities == [("WARN", "terminals")]
