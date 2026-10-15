#!/usr/bin/env python3
"""Integrity audit of a stage-4 live_run acceptance directory (AGENTS 6.7).

The live_run acceptance mode (docs/decisions/acceptance_live_run_20261014.md)
derives ALL of its state from disk -- the ledgers under
``stage4_acceptance/live_run/``, the per-round frozen injection_audit rows and
stores, the accepted-shortfall records and the skill version numbers -- so
the audit can re-derive every decision from the same files the stage reads
and cross-check them against each other. This script is that cross-check;
it is the T5 deliverable of decision section 2.8 ("live run 完整性审计模板").

What it verifies (five checks, each reported separately):

  1. terminals: for every task group, the decision its verdict rows imply
     (the finalize_group precedence) must match the member statuses; a
     delete action must have landed as a live_revision_deleted discard; an
     exhausted G10 budget must have terminalized the whole group and an
     exhausted G11 budget that one skill. Members an EARLIER action already
     terminalized (delete, repair exhaustion) keep their own reason and are
     reconciled against that action instead -- finalize_group commits
     pending members only;
  2. round-stores: for every dispatched group (a frozen audit row), the
     stored trajectories must be exactly the expected units (task x
     n_attempts) minus the skip set (stage-1 weak holes of this task UNION
     the round's own accepted holes); every verdict row needs the frozen
     audit row its evidence requires (weak_already_knows judges without
     one); a round-1 audit row presupposes a round-0 not-better verdict;
  3. ledgers: at most one verdict / attribution row per (task, round)
     (append-once, first judgment sticks) and every verdict row carries
     the frozen schema fields;
  4. shortfall vs failed_units: every unit a round's accepted_infra_
     shortfall.json names must be a weak-arm unit of this run's group
     tasks within n_attempts and must appear in that round's
     failed_units.jsonl (the hole was a K1 infra exhaustion, not an
     invention);
  5. injection-audit: member skill_ids exist in the store, the sha256 /
     token estimate of the re-rendered injection block (members
     reconstructed at their RECORDED versions via rewrite_history) matches
     the frozen line, and every stored round trajectory's skills_injected
     equals the frozen members.

Findings are graded ERROR (the run's numbers must not be used; AGENTS 6.7
"有问题的 run 一律作废"), WARN (needs a human look, e.g. an interrupted
commit the resume's finalization sweep would finish) and INFO (expected
states of an unfinished run). The script is READ-ONLY on the run directory
(analysis scripts never write into a run dir); the report goes to stdout,
plus a machine-readable JSON copy to --out, which must live OUTSIDE the run
directory. Exit code: 0 with no ERROR findings, 1 with any. On an exam-mode
directory (no live_run subtree) it says so and exits 0.

Inputs : the run directory (manifest.json for n_attempts -- overridable
         with --n-attempts). Read-only.
Output : the report on stdout and, with --out, a JSON copy outside the run.
Self-test: --self-test builds a fake run directory in a temporary folder
         (the hand-built shape the composites tests use), audits it clean,
         then tampers with it three ways (a verdict-contradicting status,
         a dropped round-0 trajectory, an invented shortfall unit) and
         asserts each tamper is caught. No network, no model.

Example:
    .venv/bin/python scripts/audit/audit_live_run.py --run-dir runs/<name>
    .venv/bin/python scripts/audit/audit_live_run.py --run-dir runs/<name> \
        --out tmp/live_audit.json
    .venv/bin/python scripts/audit/audit_live_run.py --self-test
"""

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# The script lives in scripts/audit/; make "import expedistill" work when it
# is run in place (src layout). The analysis scripts do the same.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from expedistill.core import ids  # noqa: E402
from expedistill.core.models import Skill, Trajectory  # noqa: E402
from expedistill.infra.store import Store, read_json, read_jsonl  # noqa: E402
from expedistill.inject.skills import estimate_tokens, format_skills_block  # noqa: E402

LIVE_SUBTREE = Path("stage4_acceptance") / "live_run"
VERDICTS = "verdicts.jsonl"
ATTRIBUTIONS = "attributions.jsonl"
ATTRIBUTION_FAILURES = "attribution_failures.jsonl"
REVISION_FAILURES = "revision_failures.jsonl"
INJECTION_AUDIT = "injection_audit.jsonl"
SHORTFALL = "accepted_infra_shortfall.json"
STAGE1_SHORTFALL = Path("stage1_baseline") / "accepted_infra_shortfall.json"
ZERO_EXECUTION_VERDICTS = ("weak_already_knows", "live_injection_overflow",
                           "live_undetermined")
#: Discard reasons an EARLIER action lands on a member before the group's
#: final verdict commits it; finalize_group skips already-terminal members,
#: so these are reconciled against their own action, not the verdict.
EARLIER_ACTION_REASONS = ("live_revision_deleted", "model_output_error")

ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"


class Audit:
    """One run's findings collector."""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.findings: List[Dict[str, str]] = []

    def add(self, severity: str, check: str, message: str) -> None:
        self.findings.append({"severity": severity, "check": check,
                              "message": message})

    def errors(self) -> List[Dict[str, str]]:
        return [f for f in self.findings if f["severity"] == ERROR]


# ---------------------------------------------------------------------------
# Small readers over the run directory
# ---------------------------------------------------------------------------

def _round_dir(run_dir: Path, round_: int) -> Path:
    return run_dir / LIVE_SUBTREE / f"round{round_:02d}"


def _read_shortfall(path: Path) -> List[str]:
    if not path.exists():
        return []
    return sorted(read_json(path).get("unit_ids", []))


def _first_row_per_key(rows: Sequence[Dict[str, Any]],
                       *key_fields: str) -> Dict[Tuple[Any, ...], Dict[str, Any]]:
    """Rows keyed by the first occurrence of each key tuple -- the
    append-once discipline: the first row sticks, duplicates stay visible
    to the caller (check_ledgers reports them)."""
    out: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(field) for field in key_fields)
        out.setdefault(key, row)
    return out


def _expected_units(task_id: str, n_attempts: int) -> List[str]:
    return [ids.make_trajectory_id(task_id, "weak", attempt)
            for attempt in range(n_attempts)]


def _candidate_groups(store: Store) -> Dict[str, List[Skill]]:
    """The group universe: stage-3 candidates by task (pending + terminal).

    The same filter the live stage uses: a candidate's skill_id is the
    stage-3 id of its own subtask, so bank/merge products are excluded.
    """
    groups: Dict[str, List[Skill]] = {}
    for skill_id in store.ids("skills"):
        skill = store.load("skills", skill_id, Skill)
        if skill.skill_id != ids.make_skill_id(skill.subtask_id):
            continue
        try:
            trajectory_id, _ = ids.parse_subtask_id(skill.subtask_id)
            task_id = ids.parse_trajectory_id(trajectory_id)[0]
        except ValueError:
            continue
        groups.setdefault(task_id, []).append(skill)
    return groups


def _content_at_version(skill: Skill, version: int) -> Optional[str]:
    """The skill's content at ``version`` via rewrite_history, None when the
    history cannot reconstruct it. Every version bump (G6 exam rewrites and
    G11 live revisions alike) appends the OLD content, so the content at
    version v is rewrite_history[v-1] for v < the current version."""
    if version == skill.version:
        return skill.content
    if version < 1 or version > skill.version:
        return None
    if len(skill.rewrite_history) < version:
        return None
    return skill.rewrite_history[version - 1].get("content")


# ---------------------------------------------------------------------------
# Check 1: group terminals vs the ledgers
# ---------------------------------------------------------------------------

def check_group_terminals(
        audit: Audit, store: Store, groups: Dict[str, List[Skill]],
        verdict_rows: Dict[Tuple[Any, ...], Dict[str, Any]],
        attribution_rows: Dict[Tuple[Any, ...], Dict[str, Any]],
        exhausted_g10_keys: Set[Tuple[Any, ...]],
        rev_failures: List[Dict[str, Any]]) -> None:
    for task_id, members in sorted(groups.items()):
        stored = {m.skill_id: store.load("skills", m.skill_id, Skill)
                  for m in members}
        pending = [s for s in stored.values() if s.status == "pending"]

        # The decision the verdict rows imply (finalize_group precedence).
        decided: Optional[Tuple[str, Optional[str]]] = None
        for round_ in (0, 1):
            row = verdict_rows.get((task_id, round_))
            if row is None:
                continue
            if row.get("verdict") in ZERO_EXECUTION_VERDICTS:
                decided = ("discarded", row["verdict"])
                break
            if round_ == 1:
                decided = (("accepted", None) if row.get("better")
                           else ("discarded", "live_no_improvement"))
                break
            if row.get("better"):
                decided = ("accepted", None)
                break

        # An exhausted G10 budget terminalizes the whole group (the
        # discards land BEFORE the receipt line, so exhaustion plus a
        # pending member means an interrupted exhaustion). The keys come
        # from an any-across-lines read of the failure ledger: the flag
        # rides the last line, never the first.
        exhausted_g10 = (task_id, 0) in exhausted_g10_keys

        if decided is not None:
            direction, reason = decided
            tolerated = (set(EARLIER_ACTION_REASONS) if reason is None
                        else set(EARLIER_ACTION_REASONS) | {reason})
            for skill in stored.values():
                if skill.status == "pending":
                    audit.add(
                        WARN, "terminals",
                        f"{task_id}: verdict decided {direction} ({reason}) "
                        f"but skill {skill.skill_id} is still pending (an "
                        "interrupted commit; the resume's finalization "
                        "sweep finishes it)")
                elif skill.status in ("accepted", "merged"):
                    if direction != "accepted":
                        audit.add(
                            ERROR, "terminals",
                            f"{task_id}: verdict says {direction} ({reason}) "
                            f"but skill {skill.skill_id} is {skill.status}")
                elif skill.discard_reason not in tolerated:
                    audit.add(
                        ERROR, "terminals",
                        f"{task_id}: verdict says {direction} ({reason}) but "
                        f"skill {skill.skill_id} carries discard reason "
                        f"{skill.discard_reason!r} (no delete action or "
                        "repair exhaustion explains it)")
        elif exhausted_g10:
            for skill in stored.values():
                if skill.status == "pending":
                    audit.add(
                        WARN, "terminals",
                        f"{task_id}: G10 budget exhausted but skill "
                        f"{skill.skill_id} is still pending (interrupted "
                        "exhaustion; the resume re-runs the last try)")
                elif skill.discard_reason != "model_output_error":
                    audit.add(
                        ERROR, "terminals",
                        f"{task_id}: G10 budget exhausted but skill "
                        f"{skill.skill_id} is {skill.status} "
                        f"({skill.discard_reason})")
        elif pending:
            audit.add(
                INFO, "terminals",
                f"{task_id}: {len(pending)}/{len(stored)} member(s) "
                "pending (group still in flight or awaiting a resume)")
        elif not all(skill.discard_reason == "live_no_improvement"
                     for skill in stored.values()):
            # All terminal, no deciding verdict row, not the no-op guard's
            # signature (every member live_no_improvement with no round-1
            # row): either an interrupted attribution/exhaustion commit or
            # a store edited under the ledgers.
            audit.add(
                WARN, "terminals",
                f"{task_id}: terminal group whose verdict rows decide "
                "nothing and whose discards are not all the no-op guard's "
                "live_no_improvement (interrupted commit or store drift; "
                "compare the attribution and failure ledgers by hand)")

        # Delete actions land immediately as live_revision_deleted.
        plan = attribution_rows.get((task_id, 0))
        if plan is not None:
            for entry in plan.get("skill_actions", []):
                if entry.get("action") != "delete":
                    continue
                skill = stored.get(entry.get("skill_id"))
                if skill is None:
                    audit.add(
                        ERROR, "terminals",
                        f"{task_id}: attribution plan deletes unknown skill "
                        f"{entry.get('skill_id')!r}")
                elif skill.status == "pending":
                    audit.add(
                        ERROR, "terminals",
                        f"{task_id}: plan deletes {skill.skill_id} but it is "
                        "still pending (a delete is applied immediately)")
                elif skill.discard_reason != "live_revision_deleted":
                    audit.add(
                        WARN, "terminals",
                        f"{task_id}: plan deletes {skill.skill_id} but its "
                        f"terminal is {skill.status} "
                        f"({skill.discard_reason}) -- a discard an "
                        "interrupted repair-budget run landed")

        # An exhausted G11 budget discards exactly that one skill.
        for row in rev_failures:
            skill = stored.get(row.get("skill_id"))
            if (row.get("task_id") != task_id or not row.get("exhausted")
                    or skill is None):
                continue
            if skill.status == "pending":
                audit.add(
                    WARN, "terminals",
                    f"{task_id}: revision budget for {skill.skill_id} "
                    "exhausted but it is still pending (interrupted "
                    "exhaustion; the resume re-runs the last try)")
            elif skill.discard_reason != "model_output_error":
                audit.add(
                    ERROR, "terminals",
                    f"{task_id}: revision budget for {skill.skill_id} "
                    f"exhausted but it is {skill.status} "
                    f"({skill.discard_reason})")


# ---------------------------------------------------------------------------
# Check 2: round stores = expected minus skip
# ---------------------------------------------------------------------------

def check_round_stores(
        audit: Audit, run_dir: Path, groups: Dict[str, List[Skill]],
        verdict_rows: Dict[Tuple[Any, ...], Dict[str, Any]],
        audit_rows: Dict[Tuple[Any, ...], Dict[str, Any]],
        stage1_holes: Sequence[str], n_attempts: int) -> None:
    for round_ in (0, 1):
        round_store_dir = _round_dir(run_dir, round_)
        stored_units: Set[str] = set()
        if round_store_dir.exists():
            stored_units = set(Store(round_store_dir).ids("trajectories"))
        round_holes = _read_shortfall(round_store_dir / SHORTFALL)

        for task_id in sorted(groups):
            frozen = audit_rows.get((task_id, round_))
            if frozen is None:
                verdict = verdict_rows.get((task_id, round_))
                # weak_already_knows judges with no audit row (it is the
                # entry guard's zero-execution short-circuit); every other
                # verdict needs its frozen evidence.
                if (verdict is not None
                        and verdict.get("verdict")
                        not in ZERO_EXECUTION_VERDICTS):
                    audit.add(
                        ERROR, "round-stores",
                        f"{task_id} round {round_}: verdict row without a "
                        "frozen injection_audit row")
                continue
            expected = _expected_units(task_id, n_attempts)
            skip = ({u for u in stage1_holes if u in expected}
                    | {u for u in round_holes if u in expected})
            for unit in sorted(stored_units):
                if (ids.parse_trajectory_id(unit)[0] == task_id
                        and unit not in expected):
                    audit.add(
                        ERROR, "round-stores",
                        f"{task_id} round {round_}: unexpected stored unit "
                        f"{unit}")
            verdict = verdict_rows.get((task_id, round_))
            zero_exec = (verdict is not None
                         and verdict.get("verdict") in ZERO_EXECUTION_VERDICTS)
            missing = [u for u in expected
                       if u not in stored_units and u not in skip]
            if missing and not zero_exec:
                # A zero-execution terminal (weak_already_knows,
                # live_injection_overflow, live_undetermined) judges
                # without executing: an empty round store is its expected
                # state, not drift (the three-reviewer round's fix -- the
                # audit used to flag legal overflow groups as drifted).
                audit.add(
                    INFO if verdict is None else ERROR, "round-stores",
                    f"{task_id} round {round_}: {len(missing)} unit(s) "
                    f"neither stored nor skipped ({missing}) -- "
                    + ("open hole, the resume retries it" if verdict is None
                       else "but the round has a verdict row (judge_round "
                            "refuses incomplete rounds, so the store "
                            "drifted)"))
            stored_here = [u for u in expected if u in stored_units]
            if not zero_exec and len(stored_here) + len(skip) != len(expected):
                audit.add(
                    ERROR, "round-stores",
                    f"{task_id} round {round_}: stored {len(stored_here)} + "
                    f"skipped {len(skip)} != expected {len(expected)}")

        if round_ == 1:
            for (task_id, row_round) in audit_rows:
                if row_round != 1:
                    continue
                round0 = verdict_rows.get((task_id, 0))
                if round0 is None or round0.get("better") is not False:
                    audit.add(
                        ERROR, "round-stores",
                        f"{task_id}: round-1 audit row frozen without a "
                        "round-0 not-better verdict")


# ---------------------------------------------------------------------------
# Check 3: ledger self-consistency
# ---------------------------------------------------------------------------

def check_ledgers(
        audit: Audit, live_dir: Path,
        verdict_rows: Dict[Tuple[Any, ...], Dict[str, Any]],
        attribution_rows: Dict[Tuple[Any, ...], Dict[str, Any]]) -> None:
    for name, raw in ((VERDICTS, read_jsonl(live_dir / VERDICTS)),
                      (ATTRIBUTIONS, read_jsonl(live_dir / ATTRIBUTIONS))):
        seen: Set[Tuple[Any, ...]] = set()
        for row in raw:
            key = (row.get("task_id"), row.get("round"))
            if key in seen:
                audit.add(
                    ERROR, "ledgers",
                    f"{name}: duplicate row for {key} (append-once "
                    "discipline: exactly one row per (task, round))")
            seen.add(key)
    for key, row in verdict_rows.items():
        for field in ("schema", "task_id", "round", "rule", "baseline",
                      "live", "better", "injection_tokens",
                      "judged_with_shortfall", "aux"):
            if field not in row:
                audit.add(ERROR, "ledgers",
                          f"{VERDICTS}: row {key} lacks field {field!r}")
    for key, row in attribution_rows.items():
        for field in ("schema", "task_id", "round", "diagnosis",
                      "skill_actions", "injection_block_sha256"):
            if field not in row:
                audit.add(ERROR, "ledgers",
                          f"{ATTRIBUTIONS}: row {key} lacks field {field!r}")


# ---------------------------------------------------------------------------
# Check 4: shortfall records vs failed_units
# ---------------------------------------------------------------------------

def check_shortfall(
        audit: Audit, run_dir: Path, groups: Dict[str, List[Skill]],
        n_attempts: int) -> None:
    group_tasks = set(groups)
    for round_ in (0, 1):
        round_dir = _round_dir(run_dir, round_)
        units = _read_shortfall(round_dir / SHORTFALL)
        if not units:
            continue
        failures_path = round_dir / "failed_units.jsonl"
        failed: Set[str] = set()
        if failures_path.exists():
            failed = {row.get("unit_id")
                      for row in read_jsonl(failures_path)}
        else:
            # No real path produces this: every K1 infra exhaustion is
            # recorded the moment it happens, and only exhausted units can
            # be accepted. The record was written by hand or the ledger was
            # deleted.
            audit.add(
                ERROR, "shortfall",
                f"round {round_}: accepted shortfall exists but the round's "
                "failed_units.jsonl does not -- no exhaustion record backs "
                "any hole")
        for unit in units:
            try:
                task_id, role, attempt = ids.parse_trajectory_id(unit)
            except ValueError:
                audit.add(ERROR, "shortfall",
                          f"round {round_}: malformed unit id {unit!r}")
                continue
            if role != "weak":
                audit.add(
                    ERROR, "shortfall",
                    f"round {round_}: {unit} is a {role}-arm unit; a live "
                    "run never executes the strong arm")
            if task_id not in group_tasks:
                audit.add(
                    ERROR, "shortfall",
                    f"round {round_}: {unit} belongs to task {task_id!r} "
                    "which has no stage-3 candidate group")
            if attempt >= n_attempts:
                audit.add(
                    ERROR, "shortfall",
                    f"round {round_}: {unit} is attempt {attempt} but the "
                    f"run freezes n_attempts={n_attempts}")
            if failures_path.exists() and unit not in failed:
                audit.add(
                    ERROR, "shortfall",
                    f"round {round_}: {unit} accepted as a hole but has no "
                    "exhaustion record in the round's failed_units.jsonl")
    # Stage-level failures (attribution/revision infra) live in the run
    # root's ledger under the stage label; report them for context.
    root_failures = run_dir / "failed_units.jsonl"
    if root_failures.exists():
        stage4 = [row for row in read_jsonl(root_failures)
                  if row.get("stage") == "stage4_live"]
        if stage4:
            audit.add(
                INFO, "shortfall",
                f"{len(stage4)} stage-level stage4_live infra failure(s) in "
                "the run-root failed_units.jsonl (attribution/revision "
                "units; the resume retries them)")


# ---------------------------------------------------------------------------
# Check 5: frozen audit rows vs the injection set
# ---------------------------------------------------------------------------

def _normalize_refs(value: Any) -> Any:
    """A comparable shape for a skills_injected reference list."""
    if not isinstance(value, list):
        return value
    out = []
    for entry in value:
        if isinstance(entry, dict):
            out.append((entry.get("skill_id"), entry.get("version")))
        else:
            out.append(entry)
    return out


def check_injection_audit(
        audit: Audit, store: Store,
        audit_rows: Dict[Tuple[Any, ...], Dict[str, Any]]) -> None:
    for (task_id, round_), row in sorted(
            audit_rows.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        members = row.get("members", [])
        if not members:
            audit.add(ERROR, "injection-audit",
                      f"{task_id} round {round_}: frozen audit row has no "
                      "members (an empty injection is a bare run)")
        reconstructed = []
        for member in members:
            skill_id = member.get("skill_id")
            version = member.get("version")
            if not store.exists("skills", skill_id):
                audit.add(ERROR, "injection-audit",
                          f"{task_id} round {round_}: audit row names "
                          f"unknown skill {skill_id!r}")
                continue
            skill = store.load("skills", skill_id, Skill)
            content = _content_at_version(skill, version)
            if content is None:
                audit.add(
                    ERROR, "injection-audit",
                    f"{task_id} round {round_}: cannot reconstruct skill "
                    f"{skill_id} at recorded version {version} (stored "
                    f"version {skill.version}, rewrite_history "
                    f"{len(skill.rewrite_history)} entries) -- the store "
                    "drifted from the frozen line")
                continue
            # Groups are assigned by stage 5, AFTER stage 4 froze these
            # rows, so the members were ungrouped at freeze time; rendering
            # with their CURRENT group would add "### <group>" headers and
            # break the sha on a post-curation audit.
            reconstructed.append({"skill_id": skill_id, "version": version,
                                  "content": content, "group": None})
        if len(reconstructed) != len(members):
            continue  # the member errors above name the specifics
        block = format_skills_block(reconstructed)
        sha = hashlib.sha256(block.encode("utf-8")).hexdigest()
        if sha != row.get("injection_block_sha256"):
            audit.add(
                ERROR, "injection-audit",
                f"{task_id} round {round_}: re-rendered injection block "
                f"hashes to {sha} but the frozen row says "
                f"{row.get('injection_block_sha256')}")
        tokens = estimate_tokens(block)
        if tokens != row.get("injection_tokens"):
            audit.add(
                ERROR, "injection-audit",
                f"{task_id} round {round_}: re-rendered block estimates "
                f"{tokens} tokens but the frozen row says "
                f"{row.get('injection_tokens')}")
        # Every stored trajectory of the round must carry exactly this
        # member set (the references K1 recorded on dispatch).
        round_dir = _round_dir(store.run_dir, round_)
        if not round_dir.exists():
            continue
        round_store = Store(round_dir)
        for unit in sorted(round_store.ids("trajectories")):
            if ids.parse_trajectory_id(unit)[0] != task_id:
                continue
            traj = round_store.load("trajectories", unit, Trajectory)
            injected = _normalize_refs(traj.skills_injected)
            if injected != _normalize_refs(members):
                audit.add(
                    ERROR, "injection-audit",
                    f"{task_id} round {round_}: stored trajectory {unit} was "
                    f"injected {injected} but the frozen row says "
                    f"{_normalize_refs(members)}")


# ---------------------------------------------------------------------------
# The audit driver
# ---------------------------------------------------------------------------

def audit_run(run_dir: Path, n_attempts: Optional[int] = None) -> Audit:
    """Run every check over one run directory; return the findings."""
    audit = Audit(run_dir)
    # The acceptance MODE comes from the frozen manifest, not from the
    # directory tree: an exam run has no live subtree (exit 0, expected),
    # but a LIVE run whose live subtree is missing is a broken run that
    # must fail the audit, never pass silently.
    manifest_path = run_dir / "manifest.json"
    mode = None
    try:
        mode = read_json(manifest_path)["config"]["methods"]["acceptance"][
            "name"]
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        if manifest_path.exists():
            audit.add(ERROR, "layout",
                      f"cannot read the acceptance mode from {manifest_path} "
                      "(config.methods.acceptance.name); a schema-v2 run "
                      "must be audited on its own maintenance branch")
            return audit
    live_dir = run_dir / LIVE_SUBTREE
    if not live_dir.exists():
        if mode == "live_run":
            audit.add(ERROR, "layout",
                      "the manifest froze live_run but there is no "
                      "stage4_acceptance/live_run/ subtree -- a live run "
                      "without its ledgers and round stores")
            return audit
        audit.add(INFO, "layout",
                  "exam-mode (or pre-stage-4) run: no "
                  "stage4_acceptance/live_run/ subtree -- nothing to audit "
                  "here")
        return audit
    if mode == "exam":
        audit.add(ERROR, "layout",
                  "the manifest froze the exam mode but a live_run subtree "
                  "exists -- the run directory does not match its frozen "
                  "identity")
        return audit
    if n_attempts is None:
        try:
            acceptance = read_json(manifest_path)["config"]["methods"][
                "acceptance"]
            n_attempts = int(acceptance["n_attempts"])
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            audit.add(ERROR, "layout",
                      f"cannot read n_attempts from {manifest_path} "
                      "(config.methods.acceptance.n_attempts); pass "
                      "--n-attempts")
            return audit

    store = Store(run_dir)
    groups = _candidate_groups(store)
    stage1_holes = _read_shortfall(run_dir / STAGE1_SHORTFALL)

    verdict_rows = _first_row_per_key(
        read_jsonl(live_dir / VERDICTS), "task_id", "round")
    attribution_rows = _first_row_per_key(
        read_jsonl(live_dir / ATTRIBUTIONS), "task_id", "round")
    # The exhausted flag rides the LAST failure line of a (task, round)
    # (the try-0 line lands first with exhausted=False), so exhaustion is
    # an any-across-lines question -- a first-row read is always False and
    # the whole exhausted-G10 branch below was dead code (reviewer A).
    exhausted_g10_keys = {
        (row.get("task_id"), row.get("round"))
        for row in read_jsonl(live_dir / ATTRIBUTION_FAILURES)
        if row.get("exhausted")}
    rev_failures = read_jsonl(live_dir / REVISION_FAILURES)
    audit_rows: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for round_ in (0, 1):
        for row in read_jsonl(_round_dir(run_dir, round_) / INJECTION_AUDIT):
            audit_rows.setdefault((row.get("task_id"), round_), row)

    check_group_terminals(audit, store, groups, verdict_rows,
                          attribution_rows, exhausted_g10_keys, rev_failures)
    check_round_stores(audit, run_dir, groups, verdict_rows, audit_rows,
                       stage1_holes, n_attempts)
    check_ledgers(audit, live_dir, verdict_rows, attribution_rows)
    check_shortfall(audit, run_dir, groups, n_attempts)
    check_injection_audit(audit, store, audit_rows)
    return audit


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_report(audit: Audit) -> Tuple[str, Dict[str, Any]]:
    counts = {severity: sum(1 for f in audit.findings
                            if f["severity"] == severity)
              for severity in (ERROR, WARN, INFO)}
    lines = [f"live_run acceptance audit of {audit.run_dir}",
             f"findings: {counts[ERROR]} error / {counts[WARN]} warn / "
             f"{counts[INFO]} info", ""]
    by_check: Dict[str, List[Dict[str, str]]] = {}
    for finding in audit.findings:
        by_check.setdefault(finding["check"], []).append(finding)
    for check, findings in by_check.items():
        lines.append(f"[{check}]")
        for finding in findings:
            lines.append(f"  {finding['severity']}: {finding['message']}")
        lines.append("")
    report = "\n".join(lines)
    machine = {"run_dir": str(audit.run_dir), "counts": counts,
               "findings": audit.findings}
    return report, machine


# ---------------------------------------------------------------------------
# Self-test: a hand-built fake run directory (the composites-test shape)
# ---------------------------------------------------------------------------

def _fake_trajectory(unit_id: str, status: str,
                     refs: Optional[List[Dict[str, Any]]] = None) -> Trajectory:
    task_id, role, attempt = ids.parse_trajectory_id(unit_id)
    return Trajectory(
        trajectory_id=unit_id, task_id=task_id, benchmark="tb",
        model_role=role, model_name="weak-model", attempt_index=attempt,
        steps=[{"action": "do", "observation": "obs"}], status=status,
        skills_injected=refs)


def _fake_group_skill(task_id: str, segment: int) -> Skill:
    subtask_id = ids.make_subtask_id(
        ids.make_trajectory_id(task_id, "strong", 0), segment)
    return Skill(skill_id=ids.make_skill_id(subtask_id),
                 content="[When to use] x\n[Steps]\n1. do\n"
                         "[Verify] y\n[Do not use when] z",
                 subtask_id=subtask_id, version=1)


def _append_jsonl_raw(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True)
                     + "\n")


def _frozen_row(task_id: str, round_: int,
                members: Sequence[Dict[str, Any]],
                contents: Sequence[str]) -> Dict[str, Any]:
    block = format_skills_block(
        [{"skill_id": m["skill_id"], "version": m["version"],
          "content": content, "group": None}
         for m, content in zip(members, contents)])
    return {
        "schema": "injection-audit-v1", "task_id": task_id, "round": round_,
        "members": [dict(m) for m in members],
        "injection_block_sha256": hashlib.sha256(
            block.encode("utf-8")).hexdigest(),
        "injection_tokens": estimate_tokens(block),
    }


def _build_fake_run(root: Path) -> Path:
    """A complete two-round live run: 1 task, 3 attempts; round-0 miss ->
    delete one skill, revise the other; round-1 miss -> both terminal."""
    run_dir = root / "run"
    store = Store(run_dir)
    task_id = ids.make_task_id("tb", "t1")
    n_attempts = 3

    # A minimal manifest: the audit consumes the acceptance slot's name
    # and n_attempts (schema v3 paths).
    (run_dir / "manifest.json").write_text(json.dumps({
        "config": {"methods": {"acceptance": {
            "name": "live_run",
            "n_attempts": n_attempts,
            "improvement": "strict_rate_gain",
            "max_injection_tokens": None}}},
    }, indent=2), encoding="utf-8")

    # Stage-1 weak baseline: 0/3 success.
    for attempt in range(n_attempts):
        store.save(_fake_trajectory(
            ids.make_trajectory_id(task_id, "weak", attempt), "failure"),
            "trajectories")

    # The group: two pending candidates.
    members = [_fake_group_skill(task_id, 0), _fake_group_skill(task_id, 1)]
    for skill in members:
        store.save(skill, "skills")

    # Round 0: frozen audit row + executed store (0/3 -- not better).
    row0 = _frozen_row(task_id, 0,
                       [{"skill_id": s.skill_id, "version": 1}
                        for s in members],
                       [s.content for s in members])
    _append_jsonl_raw(_round_dir(run_dir, 0) / INJECTION_AUDIT, row0)
    round0_store = Store(_round_dir(run_dir, 0))
    refs0 = [{"skill_id": s.skill_id, "version": 1} for s in members]
    for attempt in range(n_attempts):
        round0_store.save(_fake_trajectory(
            ids.make_trajectory_id(task_id, "weak", attempt), "failure",
            refs0), "trajectories")

    # Verdict rows: both rounds miss (baseline 0/3, live 0/3).
    for round_ in (0, 1):
        _append_jsonl_raw(run_dir / LIVE_SUBTREE / VERDICTS, {
            "schema": "live-verdict-v1", "task_id": task_id,
            "round": round_, "rule": "strict_rate_gain",
            "baseline": {"valid": 3, "success": 0},
            "live": {"valid": 3, "success": 0}, "better": False,
            "injection_tokens": row0["injection_tokens"],
            "judged_with_shortfall": False, "aux": None,
        })

    # The attribution plan: delete member 0, revise member 1.
    deleted, revised = members
    _append_jsonl_raw(run_dir / LIVE_SUBTREE / ATTRIBUTIONS, {
        "schema": "live-attribution-v1", "task_id": task_id, "round": 0,
        "diagnosis": "the group did not help",
        "skill_actions": [
            {"skill_id": deleted.skill_id, "version": 1,
             "action": "delete", "problem": "harmful"},
            {"skill_id": revised.skill_id, "version": 1,
             "action": "revise", "problem": "unclear trigger"},
        ],
        "injection_block_sha256": row0["injection_block_sha256"],
    })
    discarded = store.load("skills", deleted.skill_id, Skill)
    discarded.status = "discarded"
    discarded.discard_reason = "live_revision_deleted"
    store.save(discarded, "skills")
    revised_stored = store.load("skills", revised.skill_id, Skill)
    revised_stored.rewrite_history.append({
        "content": revised_stored.content, "error_category": "misread",
        "reason": "spelled out the trigger"})
    revised_stored.content = ("[When to use] x2\n[Steps]\n1. do\n"
                              "[Verify] y\n[Do not use when] z")
    revised_stored.version = 2
    store.save(revised_stored, "skills")

    # Round 1 on the survivor: frozen audit row + executed store.
    row1 = _frozen_row(task_id, 1,
                       [{"skill_id": revised.skill_id, "version": 2}],
                       [revised_stored.content])
    _append_jsonl_raw(_round_dir(run_dir, 1) / INJECTION_AUDIT, row1)
    round1_store = Store(_round_dir(run_dir, 1))
    for attempt in range(n_attempts):
        round1_store.save(_fake_trajectory(
            ids.make_trajectory_id(task_id, "weak", attempt), "failure",
            [{"skill_id": revised.skill_id, "version": 2}]), "trajectories")

    # The terminal states: deleted -> live_revision_deleted,
    # survivor -> live_no_improvement (the round-1 miss).
    survivor = store.load("skills", revised.skill_id, Skill)
    survivor.status = "discarded"
    survivor.discard_reason = "live_no_improvement"
    store.save(survivor, "skills")

    # A second task whose round-0 injection overflowed the budget: a
    # zero-execution terminal -- a verdict row carrying the verdict key,
    # a frozen audit row, an EMPTY round store, and the member discarded
    # live_injection_overflow. The audit must read the empty store as
    # the expected state of this terminal, not as drift (the scenario
    # the three-reviewer round added after the audit's false positive).
    task2 = ids.make_task_id("tb", "t2")
    for attempt in range(n_attempts):
        store.save(_fake_trajectory(
            ids.make_trajectory_id(task2, "weak", attempt), "failure"),
            "trajectories")
    member2 = _fake_group_skill(task2, 0)
    store.save(member2, "skills")
    row2 = _frozen_row(task2, 0,
                       [{"skill_id": member2.skill_id, "version": 1}],
                       [member2.content])
    _append_jsonl_raw(_round_dir(run_dir, 0) / INJECTION_AUDIT, row2)
    _append_jsonl_raw(run_dir / LIVE_SUBTREE / VERDICTS, {
        "schema": "live-verdict-v1", "task_id": task2,
        "round": 0, "rule": "strict_rate_gain",
        "baseline": {"valid": 3, "success": 0},
        "live": {"valid": None, "success": None}, "better": False,
        "verdict": "live_injection_overflow",
        "injection_tokens": row2["injection_tokens"],
        "judged_with_shortfall": False, "aux": None,
    })
    overflowed = store.load("skills", member2.skill_id, Skill)
    overflowed.status = "discarded"
    overflowed.discard_reason = "live_injection_overflow"
    store.save(overflowed, "skills")

    # A third task whose G10 budget exhausted: the round-0 miss verdict
    # landed, both repair tries failed (the exhausted flag rides the LAST
    # failure line, never the first), and the group went terminal as
    # model_output_error. The audit must take the exhausted branch (any-
    # across-lines read of the flag) and stay silent -- before the fix
    # the first-row read made the branch dead code and this legal terminal
    # drew a "compare the ledgers by hand" WARN while a tampered member
    # passed clean (reviewer A).
    task3 = ids.make_task_id("tb", "t3")
    for attempt in range(n_attempts):
        store.save(_fake_trajectory(
            ids.make_trajectory_id(task3, "weak", attempt), "failure"),
            "trajectories")
    member3 = _fake_group_skill(task3, 0)
    store.save(member3, "skills")
    row3 = _frozen_row(task3, 0,
                       [{"skill_id": member3.skill_id, "version": 1}],
                       [member3.content])
    _append_jsonl_raw(_round_dir(run_dir, 0) / INJECTION_AUDIT, row3)
    _append_jsonl_raw(run_dir / LIVE_SUBTREE / VERDICTS, {
        "schema": "live-verdict-v1", "task_id": task3,
        "round": 0, "rule": "strict_rate_gain",
        "baseline": {"valid": 3, "success": 0},
        "live": {"valid": 3, "success": 0}, "better": False,
        "injection_tokens": row3["injection_tokens"],
        "judged_with_shortfall": False, "aux": None,
    })
    for attempt in range(n_attempts):
        Store(_round_dir(run_dir, 0)).save(_fake_trajectory(
            ids.make_trajectory_id(task3, "weak", attempt), "failure",
            [{"skill_id": member3.skill_id, "version": 1}]), "trajectories")
    _append_jsonl_raw(run_dir / LIVE_SUBTREE / ATTRIBUTION_FAILURES, {
        "schema": "live-repair-failure-v1", "task_id": task3, "round": 0,
        "skill_id": None, "skill_version": None, "try_index": 0,
        "exhausted": False, "message": "first try bad json",
    })
    _append_jsonl_raw(run_dir / LIVE_SUBTREE / ATTRIBUTION_FAILURES, {
        "schema": "live-repair-failure-v1", "task_id": task3, "round": 0,
        "skill_id": None, "skill_version": None, "try_index": 1,
        "exhausted": True, "message": "second try bad json",
    })
    exhausted = store.load("skills", member3.skill_id, Skill)
    exhausted.status = "discarded"
    exhausted.discard_reason = "model_output_error"
    store.save(exhausted, "skills")
    return run_dir


def self_test() -> int:
    """Audit a clean fake run, then three tampers; each must be caught."""
    with tempfile.TemporaryDirectory(prefix="live_audit_selftest_") as tmp:
        root = Path(tmp)
        task_id = ids.make_task_id("tb", "t1")

        # Clean run: no ERROR findings.
        run_dir = _build_fake_run(root)
        audit = audit_run(run_dir)
        errors = [f["message"] for f in audit.errors()]
        if errors:
            print("SELF-TEST FAIL: clean run reported errors:")
            for message in errors:
                print(f"  {message}")
            return 1
        print(f"self-test: clean fake run -> 0 errors "
              f"({len(audit.findings)} finding(s))")

        # Tamper 1: flip the survivor to accepted -> contradicts the
        # round-1 miss verdict.
        survivor_id = ids.make_skill_id(ids.make_subtask_id(
            ids.make_trajectory_id(task_id, "strong", 0), 1))
        store = Store(run_dir)
        survivor = store.load("skills", survivor_id, Skill)
        survivor.status = "accepted"
        survivor.discard_reason = None
        store.save(survivor, "skills")
        audit = audit_run(run_dir)
        if not any(survivor_id in f["message"] and "accepted" in f["message"]
                   for f in audit.errors()):
            print("SELF-TEST FAIL: tamper 1 (verdict-contradicting "
                  "status) not caught")
            return 1
        print("self-test: verdict-contradicting status caught")

        # Tamper 2: delete one stored round-0 trajectory -> a unit neither
        # stored nor skipped on a judged round.
        survivor.status = "discarded"
        survivor.discard_reason = "live_no_improvement"
        store.save(survivor, "skills")
        victim = ids.make_trajectory_id(task_id, "weak", 2)
        victim_path = Store(_round_dir(run_dir, 0)).path_for(
            "trajectories", victim)
        if not victim_path.exists():  # pragma: no cover - name contract
            print(f"SELF-TEST FAIL: cannot locate {victim_path}")
            return 1
        victim_path.unlink()
        audit = audit_run(run_dir)
        if not any("neither stored nor skipped" in f["message"]
                   for f in audit.errors()):
            print("SELF-TEST FAIL: tamper 2 (dropped round-0 trajectory) "
                  "not caught")
            return 1
        print("self-test: dropped round-0 trajectory caught")

        # Tamper 3: invent an accepted shortfall unit -> no exhaustion
        # record backs it.
        shortfall_path = _round_dir(run_dir, 1) / SHORTFALL
        shortfall_path.write_text(json.dumps(
            {"unit_ids": [ids.make_trajectory_id(task_id, "weak", 2)]}),
            encoding="utf-8")
        audit = audit_run(run_dir)
        if not any("no exhaustion record" in f["message"]
                   for f in audit.errors()):
            print("SELF-TEST FAIL: tamper 3 (invented shortfall unit) "
                  "not caught")
            return 1
        print("self-test: invented shortfall unit caught")

        # Tamper 4: flip the exhausted-G10 group's member to accepted ->
        # the exhausted branch must flag it (before the any-across-lines
        # fix this whole branch was dead code and the tamper passed).
        task3 = ids.make_task_id("tb", "t3")
        exhausted_id = ids.make_skill_id(ids.make_subtask_id(
            ids.make_trajectory_id(task3, "strong", 0), 0))
        exhausted_skill = store.load("skills", exhausted_id, Skill)
        exhausted_skill.status = "accepted"
        exhausted_skill.discard_reason = None
        store.save(exhausted_skill, "skills")
        audit = audit_run(run_dir)
        if not any(exhausted_id in f["message"]
                   and "G10 budget exhausted" in f["message"]
                   for f in audit.errors()):
            print("SELF-TEST FAIL: tamper 4 (tampered exhausted-G10 "
                  "member) not caught")
            return 1
        print("self-test: tampered exhausted-G10 member caught")
    print("self-test: all checks passed")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only integrity audit of a stage-4 live_run "
                    "acceptance directory (AGENTS.md 6.7).")
    parser.add_argument("--run-dir", type=Path, default=None,
                         help="the run directory to audit (read-only)")
    parser.add_argument("--n-attempts", type=int, default=None,
                         help="override n_attempts (default: the frozen "
                              "manifest's config.methods.acceptance."
                              "n_attempts)")
    parser.add_argument("--out", type=Path, default=None,
                         help="write the machine-readable report (JSON) to "
                              "this path (must live OUTSIDE the run dir)")
    parser.add_argument("--self-test", action="store_true",
                        help="build a fake run directory, audit it clean, "
                             "then tamper it three ways and assert each "
                             "tamper is caught")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.run_dir is None:
        parser.error("--run-dir is required (or use --self-test)")
    run_dir = args.run_dir.resolve()
    if not run_dir.exists():
        print(f"error: no such run directory: {run_dir}", file=sys.stderr)
        return 2
    if args.out is not None:
        out = args.out.resolve()
        try:
            out.relative_to(run_dir)
        except ValueError:
            pass  # outside the run dir: good
        else:
            print("error: --out must live outside the run directory (the "
                  "audit never writes into the run dir)", file=sys.stderr)
            return 2

    audit = audit_run(run_dir, args.n_attempts)
    report, machine = render_report(audit)
    print(report)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(machine, indent=2), encoding="utf-8")
        print(f"machine-readable report: {args.out}")
    return 1 if audit.errors() else 0


if __name__ == "__main__":
    sys.exit(main())
