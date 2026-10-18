"""The supervisor's counting contract: the patrol line's "failed
unresolved" number must fall when a retried unit succeeds -- the
2026-10-18 bug quoted the ledger's unit ids but compared them against
the store's encoded .json filenames, so the sets never overlapped and
the count never moved (39 stored / 74 failed with 84 units total).
"""
import importlib.util
import json
import urllib.parse
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "run" / \
    "supervise_tb_run.py"
_spec = importlib.util.spec_from_file_location("supervise_tb_run", _SCRIPT)
supervise = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(supervise)


def _skeleton(tmp_path, stored, ledger_rows):
    run_dir = tmp_path / "run"
    traj = run_dir / "phases" / "p07_C" / "with_bank" / "store" \
        / "stage1_baseline" / "trajectories"
    traj.mkdir(parents=True)
    for unit_id in stored:
        name = urllib.parse.quote(unit_id, safe="") + ".json"
        (traj / name).write_text("{}", encoding="utf-8")
    ledger = run_dir / "phases" / "p07_C" / "with_bank" / "store" \
        / "failed_units.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger, "w", encoding="utf-8") as handle:
        for unit_id in ledger_rows:
            handle.write(json.dumps(
                {"unit_id": unit_id, "error_class": "InfrastructureError",
                 "message": "x"}) + "\n")
    return run_dir


def test_retried_and_succeeded_units_leave_the_failed_count(tmp_path):
    run_dir = _skeleton(
        tmp_path,
        stored=["traj/tb:t1/weak/00", "traj/tb:t1/weak/01"],
        ledger_rows=["traj/tb:t1/weak/00", "traj/tb:t1/weak/01",
                     "traj/tb:t2/weak/00"])
    assert supervise.unresolved_failures(run_dir, "p07:C") == 1
    assert supervise.stored_unit_ids(run_dir, "p07:C") == {
        "traj/tb:t1/weak/00", "traj/tb:t1/weak/01"}


def test_all_failed_still_pending_counts_every_row(tmp_path):
    run_dir = _skeleton(tmp_path, stored=[],
                        ledger_rows=[f"traj/tb:t{i}/weak/02"
                                     for i in range(74)])
    assert supervise.unresolved_failures(run_dir, "p07:C") == 74


def test_a_missing_ledger_or_store_reads_zero(tmp_path):
    run_dir = _skeleton(tmp_path, stored=[], ledger_rows=[])
    assert supervise.unresolved_failures(run_dir, "p07:C") == 0
    assert supervise.stored_unit_ids(run_dir, "p07:C") == set()
    assert supervise.unresolved_failures(tmp_path / "nowhere", "p07:C") == 0
