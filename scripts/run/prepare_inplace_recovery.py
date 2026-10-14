#!/usr/bin/env python3
"""Prepare the interrupted tb21_split_v2 run for an in-place recovery.

On 2026-10-13 the run ``runs/tb21_split_v2_20261013_2125`` stopped in stage 5
(SIGTERM) with 72/315 stage-3 units in the model_output_failed terminal state
-- all 72 were OUR bugs, not the model's (docs/decisions/
g2_multiline_and_json_extraction_fix_20261014.md): 67 legal heredoc-shaped
steps rejected by the over-strict newline validation, 5 raw JSON objects
hijacked to top-level arrays by the fence-priority extraction bug. The fix is
in place; this script re-arms the run directory so a resume regenerates the
72 units and restarts stage 5 cleanly.

What it does (only with --apply; the default is a dry run that prints the
operations and touches nothing):

1. archives ``stage3_generation/generation_failures.jsonl`` to
   ``stage3_generation/generation_failures.archived_20261014.jsonl``
   (the terminal-failure record is what keeps a resume from re-offering
   the 72 units; archiving it re-arms them -- the mv keeps the evidence);
2. archives ``stage5_curation/curation_log.json`` to
   ``stage5_curation/interrupted_20261014/curation_log.json``
   (the v3 journal of the interrupted round; without it a resumed stage 5
   would refuse the 8 orphaned in-flight merges);
3. discards the 8 interrupted in-flight merges (``bank/merge/0000`` ..
   ``bank/merge/0007``, all still "pending") through the Store, setting
   ``status="discarded"`` and ``discard_reason="stage5_interrupted_restart"``
   and leaving every other field untouched (their sources are all still
   accepted, so discarding loses nothing -- the merges will be re-proposed
   by the fresh curation round);
4. writes ``stage5_curation/interrupted_20261014/rollback.json``
   (reason, the 72 unit ids, the per-file operations, timestamp) and prints
   an audit summary plus the next command.

Safety model: the script refuses to run unless the run directory is exactly
the expected interrupted state (see check_preconditions), is idempotent (a
re-run on the already-recovered directory is a no-op exit 0), and never
touches manifest.json, the ledgers, or any stored skill other than the 8
in-flight merges. AGENTS.md section 6 rule 8 (repair in a NEW run directory)
is explicitly waived for this run by the user; the waiver's argument is in
the decision doc above.

Inputs : the run directory (default: the path recorded in
         /tmp/tb_split_v2_rundir) -- manifest.json, failed_units.jsonl,
         stage3_generation/generation_failures.jsonl, the store's skills/,
         stage5_curation/curation_log.json, run.lock. Read-only except for
         the four operations above.
Output : the re-armed run directory + interrupted_20261014/rollback.json.
         Exit 0 on success (or on the already-applied no-op), 1 on any
         failed precondition -- including a partially applied state, which
         needs a human.

Example (dry run, then apply, then resume the driver):
    .venv/bin/python scripts/run/prepare_inplace_recovery.py --run-dir \
        runs/tb21_split_v2_20261013_2125
    .venv/bin/python scripts/run/prepare_inplace_recovery.py --run-dir \
        runs/tb21_split_v2_20261013_2125 --apply
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from expedistill.core.models import Skill  # noqa: E402
from expedistill.infra.runlock import RunLock  # noqa: E402
from expedistill.infra.store import Store, read_jsonl, write_json  # noqa: E402

#: The run this script was written for. Another run needs its own review,
#: not just new flags (the pending-merge set below is this run's).
RUN_DIR_POINTER = Path("/tmp/tb_split_v2_rundir")

STAGE3_DIR = "stage3_generation"
FAILURES_LIVE = Path(STAGE3_DIR) / "generation_failures.jsonl"
FAILURES_ARCHIVED = Path(STAGE3_DIR) / "generation_failures.archived_20261014.jsonl"
STAGE5_DIR = "stage5_curation"
INTERRUPTED_DIR = Path(STAGE5_DIR) / "interrupted_20261014"
LOG_LIVE = Path(STAGE5_DIR) / "curation_log.json"
LOG_ARCHIVED = INTERRUPTED_DIR / "curation_log.json"
ROLLBACK = INTERRUPTED_DIR / "rollback.json"

#: The eight in-flight merges the interrupted stage-5 round left pending
#: (this run's curation_log.json dispatch map names serials 0..7).
EXPECTED_PENDING_MERGES = tuple(
    f"bank/merge/{index:04d}" for index in range(8))

DISCARD_REASON = "stage5_interrupted_restart"

RECOVERY_REASON = (
    "72/315 stage-3 units were terminal model_output_failed on 2026-10-13, "
    "all from the two G2-output handling bugs fixed on 2026-10-14 "
    "(67 over-strict newline rejections of legal heredoc-shaped steps, "
    "5 fence-priority extraction hijacks to top-level arrays); the user "
    "ordered an in-place recovery (docs/decisions/"
    "g2_multiline_and_json_extraction_fix_20261014.md)")


def _load_failures_rows(run_dir: Path, problems: list) -> list:
    """The terminal-failure rows; anything but the live file is a problem.

    Called only when the run is NOT in the fully-recovered state (main
    checks that first), so an existing archive here means the recovery was
    interrupted halfway: the apply step would then move files that no
    longer exist, so a human must look at it instead.
    """
    live = run_dir / FAILURES_LIVE
    archived = run_dir / FAILURES_ARCHIVED
    if live.exists() and archived.exists():
        problems.append(
            f"partially applied: both {FAILURES_LIVE} and its archive "
            f"{FAILURES_ARCHIVED} exist; a human must look at this run")
        return []
    if live.exists():
        return read_jsonl(live)
    if archived.exists():
        problems.append(
            f"partially applied: {FAILURES_LIVE} is already archived as "
            f"{FAILURES_ARCHIVED} but the run is not in the fully "
            "recovered state; a human must look at this run")
        return []
    problems.append(
        f"neither {FAILURES_LIVE} nor its archive {FAILURES_ARCHIVED} "
        "exists; the run directory is not the expected interrupted state")
    return []


def check_preconditions(run_dir: Path) -> dict:
    """Assert the run directory is exactly the expected interrupted state.

    Returns the data the apply step needs (the 72 unit ids, the store, the
    pending merges). Every violated precondition is printed; a non-empty
    problem list means the caller must exit non-zero without touching
    anything.
    """
    problems: list = []
    data: dict = {}

    # P1: a run directory with a frozen manifest.
    manifest = run_dir / "manifest.json"
    if not run_dir.is_dir():
        problems.append(f"run directory {run_dir} does not exist")
    elif not manifest.is_file():
        problems.append(f"{manifest} is missing: not a run directory")

    # P2: stage 5 never wrote the bank (otherwise this is not an
    # interrupted curation, and stage 6 already consumed the old bank).
    bank = run_dir / "skill_bank.jsonl"
    if bank.exists():
        problems.append(
            f"{bank} already exists: stage 5 finished for this run; an "
            "in-place recovery would silently strand the bank's consumers")

    store = Store(run_dir) if run_dir.is_dir() else None
    pending_merges = []
    merged_skills = []
    if store is not None:
        for skill_id in store.ids("skills"):
            skill = store.load("skills", skill_id, Skill)
            if skill_id in EXPECTED_PENDING_MERGES and skill.status == "pending":
                pending_merges.append(skill_id)
            if skill.status == "merged":
                merged_skills.append(skill_id)

    # P3: the interrupted stage-5 journal is still in place (it is what
    # the apply step archives).
    if not (run_dir / LOG_LIVE).is_file():
        problems.append(
            f"{LOG_LIVE} is missing: stage 5 either never started or was "
            "already recovered")

    # P4: the store holds exactly the eight interrupted in-flight merges,
    # still pending (the curation dispatch map names serials 0..7).
    expected = set(EXPECTED_PENDING_MERGES)
    if set(pending_merges) != expected:
        problems.append(
            "pending bank/merge/* skills are "
            f"{sorted(set(pending_merges))}, expected exactly "
            f"{sorted(expected)}")

    # P5: no source was consumed yet (nothing has status "merged"), so
    # discarding the in-flight merges loses no committed decision.
    if merged_skills:
        problems.append(
            f"skills with status='merged' exist ({sorted(merged_skills)}); "
            "a commit was already applied, this is not the interrupted "
            "state this script handles")

    # P6: no live holder on the run directory (the driver was SIGTERMed).
    # RunLock's own probe (os.kill(pid, 0), never pgrep -- the 2026-09-20
    # incident) decides liveness; a stale lock is fine and the next
    # pipeline start takes it over.
    lock = RunLock(run_dir)
    if lock.path.exists():
        holder = lock._read_holder()
        if holder is not None and lock._holder_alive(holder):
            problems.append(
                f"{lock.path} is held by a live process (pid "
                f"{holder.get('pid')} on {holder.get('hostname')}); stop it "
                "before recovering in place")
        else:
            data["stale_lock"] = True

    # P7: the terminal-failure file and the unit-failure ledger name the
    # same 72 stage-3 ModelOutputError units (derived from the files, not
    # hardcoded: the archive must cover exactly what the ledger recorded).
    failures_rows = _load_failures_rows(run_dir, problems)
    failures_ids = {row["subtask_id"] for row in failures_rows}
    ledger_ids = set()
    ledger_path = run_dir / "failed_units.jsonl"
    if run_dir.is_dir() and not ledger_path.is_file():
        problems.append(f"{ledger_path} is missing: cannot cross-check the "
                        "terminal failures against the ledger")
    elif ledger_path.is_file():
        for row in read_jsonl(ledger_path):
            if (row.get("stage") == "stage3_generation"
                    and row.get("error_class") == "ModelOutputError"):
                ledger_ids.add(row["unit_id"])
    if failures_rows and failures_ids != ledger_ids:
        only_file = sorted(failures_ids - ledger_ids)
        only_ledger = sorted(ledger_ids - failures_ids)
        problems.append(
            "generation_failures subtask_ids do not match the "
            "stage3_generation+ModelOutputError rows of failed_units.jsonl "
            f"(only in the failures file: {only_file}; only in the ledger: "
            f"{only_ledger})")

    data["problems"] = problems
    data["store"] = store
    data["pending_merges"] = sorted(pending_merges)
    data["failure_ids"] = sorted(failures_ids)
    return data


def plan_operations(run_dir: Path, data: dict) -> list:
    """The (description, action) pairs -- printed by the dry run."""
    operations = [
        (f"mv {FAILURES_LIVE} -> {FAILURES_ARCHIVED}"
         "  (re-arms the 72 terminal stage-3 units for regeneration)",
         lambda: _archive_failures(run_dir)),
        (f"mv {LOG_LIVE} -> {LOG_ARCHIVED}"
         "  (clears the interrupted stage-5 journal)",
         lambda: _archive_curation_log(run_dir)),
        (f"discard {len(data['pending_merges'])} pending merges "
         f"{data['pending_merges'][0]}..{data['pending_merges'][-1]} "
         f'(status="discarded", discard_reason={DISCARD_REASON!r}, other '
         "fields untouched, via Store load/save)",
         lambda: _discard_pending_merges(run_dir, data)),
        (f"write {ROLLBACK}  (reason, the "
         f"{len(data['failure_ids'])} unit ids, per-file operations, "
         "timestamp)",
         lambda: _write_rollback(run_dir, data)),
    ]
    return operations


def _archive_failures(run_dir: Path) -> None:
    (run_dir / FAILURES_ARCHIVED).parent.mkdir(parents=True, exist_ok=True)
    os.replace(run_dir / FAILURES_LIVE, run_dir / FAILURES_ARCHIVED)


def _archive_curation_log(run_dir: Path) -> None:
    (run_dir / LOG_ARCHIVED).parent.mkdir(parents=True, exist_ok=True)
    os.replace(run_dir / LOG_LIVE, run_dir / LOG_ARCHIVED)


def _discard_pending_merges(run_dir: Path, data: dict) -> None:
    store: Store = data["store"]
    for skill_id in data["pending_merges"]:
        skill = store.load("skills", skill_id, Skill)
        before = skill.to_dict()
        skill.status = "discarded"
        skill.discard_reason = DISCARD_REASON
        store.save(skill, "skills")
        after = store.load("skills", skill_id, Skill).to_dict()
        # Only the two intended fields may differ; everything else must be
        # preserved byte-for-byte (values, not file bytes: the Store's
        # canonical serialization stays the store's own).
        changed = {key for key in set(before) | set(after)
                   if before.get(key) != after.get(key)}
        if changed != {"status", "discard_reason"}:
            raise RuntimeError(
                f"discard of {skill_id} changed fields beyond status/"
                f"discard_reason: {sorted(changed)}")


def _write_rollback(run_dir: Path, data: dict) -> None:
    payload = {
        "schema": "inplace-recovery-v1",
        "reason": RECOVERY_REASON,
        "time": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir),
        "regenerate_unit_ids": data["failure_ids"],
        "operations": [
            {"op": "mv", "from": str(FAILURES_LIVE),
             "to": str(FAILURES_ARCHIVED)},
            {"op": "mv", "from": str(LOG_LIVE), "to": str(LOG_ARCHIVED)},
            {"op": "discard", "skill_ids": data["pending_merges"],
             "discard_reason": DISCARD_REASON},
            {"op": "write", "path": str(ROLLBACK)},
        ],
    }
    path = run_dir / ROLLBACK
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # Idempotent: keep the FIRST rollback record (the applied one);
        # a differing second one would mean the state moved unexpectedly.
        recorded = json.loads(path.read_text(encoding="utf-8"))
        if (recorded.get("regenerate_unit_ids")
                != payload["regenerate_unit_ids"]):
            raise RuntimeError(
                f"{path} already exists with a different unit-id list; a "
                "human must look at this run")
        return
    write_json(path, payload, indent=2)


def _is_already_applied(run_dir: Path) -> bool:
    """The four operations all done: a re-run must be a no-op."""
    return ((not (run_dir / FAILURES_LIVE).exists())
            and (run_dir / FAILURES_ARCHIVED).exists()
            and (not (run_dir / LOG_LIVE).exists())
            and (run_dir / LOG_ARCHIVED).exists()
            and (run_dir / ROLLBACK).exists())


def _print_audit(run_dir: Path, data: dict) -> None:
    print()
    print("audit summary")
    print(f"  run directory             : {run_dir}")
    print(f"  terminal units re-armed   : {len(data['failure_ids'])}")
    print(f"  in-flight merges discarded: "
          f"{len(data['pending_merges'])} "
          f"({data['pending_merges'][0]} .. {data['pending_merges'][-1]})")
    if data.get("stale_lock"):
        print("  run.lock                  : stale (holder dead); the next "
              "pipeline start takes it over")
    print(f"  rollback record           : {ROLLBACK}")
    print()
    print("next: resume the driver on this run directory, e.g.")
    print(f"    .venv/bin/python -m expedistill.cli run "
          f"--config configs/experiments/tb21_split_v2.yaml "
          f"--run-dir {run_dir}")
    print("stage 3 regenerates the 72 units, stage 4 accepts the new pairs,")
    print("stage 5 re-curates from a fresh journal, stage 6 runs the bank.")
    print("gate: if stage 3 reports model_output_failed > 15/315 again,")
    print("STOP and diagnose -- do not continue a sick run "
          "(decision doc, residual gate).")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare the interrupted tb21_split_v2 run for an "
                    "in-place recovery (dry run by default).")
    parser.add_argument(
        "--run-dir", default=None,
        help="the run directory (default: the path recorded in "
             f"{RUN_DIR_POINTER})")
    parser.add_argument(
        "--apply", action="store_true",
        help="perform the operations (without it, only print them)")
    args = parser.parse_args()

    if args.run_dir is None:
        if not RUN_DIR_POINTER.is_file():
            print(f"error: no --run-dir given and {RUN_DIR_POINTER} is "
                  "missing", file=sys.stderr)
            return 1
        args.run_dir = RUN_DIR_POINTER.read_text(encoding="utf-8").strip()
    run_dir = Path(args.run_dir).resolve()

    if not run_dir.is_dir():
        print(f"error: run directory {run_dir} does not exist",
              file=sys.stderr)
        return 1

    if _is_already_applied(run_dir):
        print(f"{run_dir} is already recovered; nothing to do "
              f"(rollback record: {ROLLBACK})")
        return 0

    data = check_preconditions(run_dir)
    if data["problems"]:
        print("preconditions FAILED -- nothing was changed:", file=sys.stderr)
        for problem in data["problems"]:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    operations = plan_operations(run_dir, data)
    mode = ("APPLY" if args.apply
            else "dry run -- nothing is changed; pass --apply to perform")
    print(f"preconditions OK; planned operations ({mode}):")
    for description, _ in operations:
        print(f"  - {description}")
    if not args.apply:
        _print_audit(run_dir, data)
        return 0

    for _, action in operations:
        action()
    print("applied.")
    _print_audit(run_dir, data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
