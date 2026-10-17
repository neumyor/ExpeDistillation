#!/usr/bin/env python3
"""Integrity audit of a live_run acceptance run directory (AGENTS 6.7).

The live_run acceptance mode (docs/decisions/acceptance_live_run_20261014.md)
derives ALL of its state from disk -- the ledgers under
``stage4_acceptance/live_run/``, the per-round frozen injection_audit rows and
stores, the accepted-shortfall records and the skill version numbers -- so
the audit can re-derive every decision from the same files the stage reads
and cross-check them against each other. This script is that cross-check;
it is the T5 deliverable of decision section 2.8 ("live run 完整性审计模板").

The read paths dispatch on the run's frozen schema version (R10): a
schema-v4 run (the phase sequence) is audited through its phase
directories, a pre-v4 run through the historical stage paths -- the
runs frozen before the phase world are still audited with the exact
code that audited them when they ran, so their numbers stay
checkable on today's checkout. The acceptance MODE of a v4 run comes
from the frozen sequence, not from a config slot: a sequence that
carries a live phase is live_run mode, else one with an exam phase
is exam mode (live wins when both appear -- the upgrade-mapped
sequences never mix, and a hand-written mixed sequence is graded by
its live subtree; the Wave 3 A-review's D2 correction, 2026-10-16).

What it verifies on the live subtree (five checks, each reported
separately; a pre-v4 run gets exactly these, a v4 run gets their
phase-world counterparts):

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

What it additionally verifies on a v4 run's phase directories (the
five classes' phase-world counterparts, "对象一致 / 重复执行 / 统计
口径" for the ``phases/<label>/`` tree):

  6. phase-dirs: every phase directory's ``phase.json`` agrees with the
     frozen sequence (label, index, name, role) -- the run directory
     cannot drift from its frozen identity;
  7. units-report: the resolved unit list has no duplicate unit (one
     resolution per phase), every report row names a planned unit and
     carries a status from the closed vocabulary, a summary that says
     "complete" leaves no unit without a report row, a final-test phase
     never wrote a report, and the redispatch bookkeeping only names
     planned units with sane pass counts;
  8. learn-ledgers: the learn phases' three ledgers reconcile with the
     skill store (receipts land as real statuses, the revision chain
     from_version -> version is intact, one receipt per (target,
     skill) -- an "already_accepted" echo of an earlier receipt is the
     in-phase resume idiom, an action receipt twice is a replayed
     landing) and the bank artifact exists exactly when the phase's
     summary committed it (an artifact without its commit summary is an
     interrupted commit -- WARN, not a settled state);
  9. final-test: a complete final-test summary's both arms hold every
     expected test unit (its own phase store, or the run-level store
     for a reused baseline control), minus the accepted holes.

Findings are graded ERROR (the run's numbers must not be used; AGENTS 6.7
"有问题的 run 一律作废"), WARN (needs a human look, e.g. an interrupted
commit the resume's finalization sweep would finish) and INFO (expected
states of an unfinished run). The script is READ-ONLY on the run directory
(analysis scripts never write into a run dir); the report goes to stdout,
plus a machine-readable JSON copy to --out, which must live OUTSIDE the run
directory. Exit code: 0 with no ERROR findings, 1 with any. On an exam-mode
directory (no live_run subtree) it says so and exits 0.

Inputs : the run directory (manifest.json for the phase sequence, the
         acceptance mode and n_attempts -- overridable with
         --n-attempts). Read-only.
Output : the report on stdout and, with --out, a JSON copy outside the run.
Self-test: --self-test builds fake run directories in a temporary folder
         (the hand-built shapes the composites tests use), audits each
         clean, then tampers with them (a verdict-contradicting status,
         a dropped round-0 trajectory, an invented shortfall unit on the
         legacy shape; a phase.json role flip, a duplicated unit, an
         invented report row, a receipt the store contradicts, a dropped
         final-test trajectory, a verdict-contradicting status on the v4
         shape) and asserts each tamper is caught. No network, no model.

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
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

# The script lives in scripts/audit/; make "import expedistill" work when it
# is run in place (src layout). The analysis scripts do the same.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from expedistill.core import ids  # noqa: E402
from expedistill.core.config import (  # noqa: E402
    PHASE_ROLE_EXAM,
    PHASE_ROLE_LIVE,
    PHASE_ROLE_TEST,
    phase_label,
)
from expedistill.core.models import (  # noqa: E402
    Skill,
    Trajectory,
    migrate_skill_rows,
)
from expedistill.composites.decision import LIVE_REVISION_DELETED  # noqa: E402
from expedistill.composites.phase_bank import phase_bank_dir  # noqa: E402
from expedistill.composites.verdicts import UNIT_STATUSES  # noqa: E402
from expedistill.infra.reliability import ConfigError  # noqa: E402
from expedistill.infra.store import (  # noqa: E402
    Store,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
)
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

# The phase-world file names inside ``phases/<label>/`` (the v4 read
# paths). Restated here rather than imported from the stage layer: the
# on-disk layout is the contract this audit reads, and the script must
# not reach into stages/ private names (AGENTS section 5).
PHASE_RECORD_NAME = "phase.json"
UNITS_NAME = "units.jsonl"
REPORT_NAME = "acceptance_report.jsonl"
REDISPATCH_NAME = "redispatch.json"
EXEC_SUMMARY_NAME = "phase_summary.json"
TASK_LIST_NAME = "task_list.jsonl"
REVISIONS_NAME = "revisions.jsonl"
OUTCOMES_NAME = "outcomes.jsonl"
LEARN_SUMMARY_NAME = "learn_summary.json"
BANK_NAME = "skill_bank.jsonl"
#: The final-test phase's two arms, each with its own store directory.
ARM_NAMES = ("with_bank", "control")
#: The learn receipt ledger's schema tag (stages/learn_tasks.py).
LEARN_OUTCOME_SCHEMA = "learn-outcome-v1"
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


def _load_skills(store: Store) -> Dict[str, Skill]:
    """Every stored skill at the current schema, by id.

    Full-store reads route through the v1->v2 migration (the M1
    acceptance ruling): every formal run of today predates schema v2 and
    its merge products cannot go through from_dict alone -- their
    source_subtasks needs the sibling records -- so the audit, the
    AGENTS 6.7 standard tool, must load them via migrate_skill_rows. A
    v2 row passes through unchanged (the same object).
    """
    skill_ids = store.ids("skills")
    rows = [read_json(store.path_for("skills", skill_id))
            for skill_id in skill_ids]
    return {skill_id: Skill.from_dict(row)
            for skill_id, row in zip(skill_ids, migrate_skill_rows(rows))}


def _candidate_groups(skills: Dict[str, Skill]) -> Dict[str, List[Skill]]:
    """The group universe: stage-3 candidates by task (pending + terminal).

    The same filter the live stage uses: a candidate's skill_id is the
    stage-3 id of its own subtask, so bank/merge products are excluded.
    ``skills`` is the migration-aware full load (_load_skills), in
    sorted-id order.
    """
    groups: Dict[str, List[Skill]] = {}
    for skill in skills.values():
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
        stage1_holes: Sequence[str], rounds: Sequence[int],
        depth_of: "Callable[[int], int]") -> None:
    for round_ in rounds:
        n_attempts = depth_of(round_)
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

#: The decision ledgers the two worlds carry: file name, the append-once
#: key, and the frozen schema fields every row must carry. A v4 run has
#: no attributions ledger -- the learn phases' outcomes.jsonl is its
#: successor (checked separately, in the learn-ledgers check) -- so the
#: v4 audit passes only the verdicts entry.
_LEDGER_SPECS = {
    "verdicts": (VERDICTS, ("task_id", "round"),
                 ("schema", "task_id", "round", "rule", "baseline",
                  "live", "better", "injection_tokens",
                  "judged_with_shortfall", "aux")),
    "attributions": (ATTRIBUTIONS, ("task_id", "round"),
                     ("schema", "task_id", "round", "diagnosis",
                      "skill_actions", "injection_block_sha256")),
}


def check_ledgers(audit: Audit, live_dir: Path,
                  names: Sequence[str]) -> None:
    for name in names:
        file_name, key_fields, schema_fields = _LEDGER_SPECS[name]
        seen: Set[Tuple[Any, ...]] = set()
        first: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        for row in read_jsonl(live_dir / file_name):
            key = tuple(row.get(field) for field in key_fields)
            if key in seen:
                audit.add(
                    ERROR, "ledgers",
                    f"{file_name}: duplicate row for {key} (append-once "
                    "discipline: exactly one row per (task, round))")
            first.setdefault(key, row)
            seen.add(key)
        for key, row in sorted(first.items(), key=lambda kv: str(kv[0])):
            for field in schema_fields:
                if field not in row:
                    audit.add(ERROR, "ledgers",
                              f"{file_name}: row {key} lacks field "
                              f"{field!r}")


# ---------------------------------------------------------------------------
# Check 4: shortfall records vs failed_units
# ---------------------------------------------------------------------------

def check_shortfall(
        audit: Audit, run_dir: Path, groups: Dict[str, List[Skill]],
        rounds: Sequence[int], depth_of: "Callable[[int], int]",
        stage_names: Sequence[str]) -> None:
    group_tasks = set(groups)
    for round_ in rounds:
        n_attempts = depth_of(round_)
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
    # Stage-level failures live in the run root's ledger under the
    # stage label; report them for context. The names differ per world:
    # the pre-v4 stage wrote "stage4_live", a v4 run's learn phases
    # write their pNN labels.
    root_failures = run_dir / "failed_units.jsonl"
    if root_failures.exists():
        stage_rows = [row for row in read_jsonl(root_failures)
                      if row.get("stage") in frozenset(stage_names)]
        if stage_rows:
            audit.add(
                INFO, "shortfall",
                f"{len(stage_rows)} stage-level infra failure(s) in the "
                "run-root failed_units.jsonl under the acceptance stage "
                f"label(s) {sorted(stage_names)} (evidence/author/revision "
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
        audit: Audit, store: Store, skills: Dict[str, Skill],
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
            # The migration-aware full load (_load_skills): a pre-v2 run's
            # bank members include merge products that from_dict alone
            # would reject.
            skill = skills.get(str(skill_id))
            if skill is None:
                audit.add(ERROR, "injection-audit",
                          f"{task_id} round {round_}: audit row names "
                          f"unknown skill {skill_id!r}")
                continue
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

# ---------------------------------------------------------------------------
# The v4 phase-directory checks
# ---------------------------------------------------------------------------

def _phase_specs(config: Dict[str, Any]) -> List[Tuple[int, str, Dict[str, Any]]]:
    """The frozen sequence as (index, name, raw spec) triples."""
    return [(index, name, config["phases"].get(name))
            for index, name in enumerate(config["sequence"])]


def _phase_dir_of(run_dir: Path, index: int, name: str) -> Optional[Path]:
    """The phase's directory, None when the label cannot name one (a
    tampered manifest is reported by the caller, not a crash)."""
    try:
        return phase_bank_dir(run_dir, phase_label(index, name))
    except ConfigError:
        return None


def check_phase_dirs(audit: Audit, run_dir: Path, specs: Sequence[Tuple]) \
        -> None:
    """Check 6: every phase directory's record agrees with the frozen
    sequence -- the run directory cannot drift from its frozen identity."""
    for index, name, spec in specs:
        label = phase_label(index, name)
        directory = _phase_dir_of(run_dir, index, name)
        if directory is None:
            audit.add(ERROR, "phase-dirs",
                      f"sequence entry {index} is named {name!r}, which "
                      "cannot form a 'pNN:<name>' phase label; the manifest "
                      "is not a frozen v4 identity")
            continue
        if not directory.exists():
            audit.add(INFO, "phase-dirs",
                      f"phase {label} is not on disk yet (not run)")
            continue
        if not isinstance(spec, dict) or spec.get("kind") != "execute":
            # A learn phase writes no phase.json (its commit signal is the
            # summary's bank entry, checked in the learn-ledgers check).
            continue
        record_path = directory / PHASE_RECORD_NAME
        if not record_path.exists():
            audit.add(WARN, "phase-dirs",
                      f"phase {label}: directory exists but carries no "
                      "phase.json (an interrupted first write)")
            continue
        record = read_json(record_path)
        expected = {"label": label, "index": index, "name": name,
                    "role": spec.get("role")}
        for key, value in expected.items():
            if record.get(key) != value:
                audit.add(
                    ERROR, "phase-dirs",
                    f"phase {label}: phase.json says {key}="
                    f"{record.get(key)!r} but the frozen sequence says "
                    f"{value!r} -- the run directory drifted from its "
                    "frozen identity")


def check_units_report(audit: Audit, run_dir: Path,
                       specs: Sequence[Tuple]) -> None:
    """Check 7: the per-phase unit list and acceptance report agree --
    no duplicate execution, no invented report row, closed status
    vocabulary, a complete summary leaves no unit unreported."""
    for index, name, spec in specs:
        if not isinstance(spec, dict) or spec.get("kind") != "execute":
            continue
        label = phase_label(index, name)
        directory = _phase_dir_of(run_dir, index, name)
        if directory is None or not directory.exists():
            continue
        unit_ids: List[str] = []
        units_path = directory / UNITS_NAME
        if units_path.exists():
            for row in read_jsonl(units_path):
                unit_id = row.get("unit_id")
                if unit_id in unit_ids:
                    audit.add(
                        ERROR, "units-report",
                        f"phase {label}: duplicate unit row {unit_id!r} "
                        "(one resolution per phase; a second copy means "
                        "the phase directory drifted)")
                unit_ids.append(unit_id)
        unit_set = set(unit_ids)

        report_path = directory / REPORT_NAME
        is_test = spec.get("role") == PHASE_ROLE_TEST
        if is_test and report_path.exists():
            audit.add(
                ERROR, "units-report",
                f"phase {label}: a final-test phase wrote an acceptance "
                "report (its numbers go to the summary alone; test results "
                "never flow into learning)")
        if not is_test and report_path.exists():
            reported: Set[str] = set()
            for row in read_jsonl(report_path):
                unit_id = row.get("unit_id")
                if unit_id not in unit_set:
                    audit.add(
                        ERROR, "units-report",
                        f"phase {label}: report row names unit {unit_id!r} "
                        "the phase never resolved (an invented row)")
                if row.get("status") not in UNIT_STATUSES:
                    audit.add(
                        ERROR, "units-report",
                        f"phase {label}: report row {unit_id!r} carries "
                        f"status {row.get('status')!r} outside the closed "
                        f"vocabulary {list(UNIT_STATUSES)}")
                reported.add(unit_id)
            summary_path = directory / EXEC_SUMMARY_NAME
            if summary_path.exists():
                summary = read_json(summary_path)
                if summary.get("complete") is True:
                    missing = [uid for uid in unit_set if uid not in reported]
                    if missing:
                        audit.add(
                            ERROR, "units-report",
                            f"phase {label}: the summary says complete but "
                            f"{len(missing)} resolved unit(s) have no report "
                            f"row ({missing})")
        elif units_path.exists() and not is_test:
            audit.add(
                INFO, "units-report",
                f"phase {label}: units resolved but no acceptance report "
                "yet (the phase is in flight or was interrupted)")

        redispatch_path = directory / REDISPATCH_NAME
        if redispatch_path.exists():
            state = read_json(redispatch_path)
            if not isinstance(state, dict):
                audit.add(ERROR, "units-report",
                          f"phase {label}: {REDISPATCH_NAME} is not a "
                          "per-unit mapping")
                continue
            for unit_id, entry in sorted(state.items(), key=str):
                if unit_id not in unit_set:
                    audit.add(
                        ERROR, "units-report",
                        f"phase {label}: the redispatch bookkeeping names "
                        f"unit {unit_id!r} the phase never resolved")
                passes = entry.get("passes") if isinstance(entry, dict) \
                    else None
                if not isinstance(passes, int) or isinstance(passes, bool) \
                        or passes < 0:
                    audit.add(
                        ERROR, "units-report",
                        f"phase {label}: the redispatch bookkeeping of unit "
                        f"{unit_id!r} carries a non-natural pass count "
                        f"({passes!r})")


def check_learn_ledgers(audit: Audit, run_dir: Path,
                        specs: Sequence[Tuple],
                        skills: Dict[str, Skill]) -> None:
    """Check 8: the learn phases' ledgers reconcile with the skill store
    and the bank commit point is exactly the summary's "bank" entry."""
    for index, name, spec in specs:
        if not isinstance(spec, dict) or spec.get("kind") != "learn":
            continue
        label = phase_label(index, name)
        directory = _phase_dir_of(run_dir, index, name)
        if directory is None or not directory.exists():
            continue  # the phase-dirs check already reported not-run

        for row in read_jsonl(directory / TASK_LIST_NAME):
            if row.get("phase_label") != label:
                audit.add(
                    ERROR, "learn-ledgers",
                    f"phase {label}: a task_list row carries phase_label "
                    f"{row.get('phase_label')!r} (a ledger of another phase "
                    "mixed into this directory)")

        # The revision chain: the first plan row per (target, skill)
        # sticks; the store can only have moved FORWARD from it (this
        # phase's revision, then later phases').
        first_from: Dict[Tuple[Any, Any], Any] = {}
        for row in read_jsonl(directory / REVISIONS_NAME):
            if row.get("phase_label") != label:
                audit.add(
                    ERROR, "learn-ledgers",
                    f"phase {label}: a revisions row carries phase_label "
                    f"{row.get('phase_label')!r}")
                continue
            first_from.setdefault((row.get("target_id"),
                                   row.get("skill_id")),
                                  row.get("from_version"))
        for (target_id, skill_id), from_version in sorted(
                first_from.items(), key=str):
            skill = skills.get(str(skill_id))
            if skill is None:
                audit.add(
                    ERROR, "learn-ledgers",
                    f"phase {label}: the revision ledger plans a revision "
                    f"of unknown skill {skill_id!r}")
            elif not isinstance(from_version, int) \
                    or skill.version < from_version:
                audit.add(
                    ERROR, "learn-ledgers",
                    f"phase {label}: the revision ledger recorded skill "
                    f"{skill_id!r} at from_version {from_version!r} but "
                    f"the store holds version {skill.version} -- the store "
                    "drifted from the ledger")

        # The receipts: one per (target, skill); every outcome that claims
        # a state must match the store (a later phase may have moved the
        # skill further, never backwards). ONE repeat is legal: a resumed
        # phase re-derives the same keep row and lands it on the now-
        # accepted member, writing "already_accepted" after the first
        # "accepted" -- the in-phase resume idiom (Wave 3 review O2,
        # 2026-10-16 ruling). A second receipt that CARRIES AN ACTION
        # (accepted / revised / discarded / ...) is still a replayed
        # landing and an ERROR.
        seen: Dict[Tuple[Any, Any], str] = {}
        for row in read_jsonl(directory / OUTCOMES_NAME):
            if row.get("phase_label") != label:
                audit.add(
                    ERROR, "learn-ledgers",
                    f"phase {label}: an outcomes row carries phase_label "
                    f"{row.get('phase_label')!r}")
                continue
            if row.get("schema") != LEARN_OUTCOME_SCHEMA:
                audit.add(
                    ERROR, "learn-ledgers",
                    f"phase {label}: an outcomes row carries schema "
                    f"{row.get('schema')!r}, not {LEARN_OUTCOME_SCHEMA!r}")
            skill_id = row.get("skill_id")
            outcome = row.get("outcome")
            if skill_id is None:
                continue  # target-level rows (skips) name no skill
            key = (row.get("target_id"), skill_id)
            if key in seen:
                if outcome == "already_accepted":
                    # The resume idiom: the first receipt landed the
                    # member, this one re-landed the same (now accepted)
                    # row. Not a duplicate landing.
                    continue
                audit.add(
                    ERROR, "learn-ledgers",
                    f"phase {label}: duplicate receipt for target "
                    f"{key[0]!r} skill {skill_id!r} (the receipt is the "
                    "proof of completion; a second one that carries an "
                    "action means a landing replayed)")
            seen[key] = outcome
            skill = skills.get(str(skill_id))
            if skill is None:
                audit.add(
                    ERROR, "learn-ledgers",
                    f"phase {label}: a receipt names unknown skill "
                    f"{skill_id!r}")
                continue
            if outcome in ("accepted", "already_accepted"):
                if skill.status not in ("accepted", "merged"):
                    audit.add(
                        ERROR, "learn-ledgers",
                        f"phase {label}: receipt says {outcome} but skill "
                        f"{skill_id!r} is {skill.status}"
                        f"{'(' + skill.discard_reason + ')' if skill.discard_reason else ''}")
            elif outcome in ("discarded", "deleted"):
                if skill.status != "discarded":
                    audit.add(
                        ERROR, "learn-ledgers",
                        f"phase {label}: receipt says {outcome} but skill "
                        f"{skill_id!r} is {skill.status} (a delete lands "
                        "immediately; a pending member means it never "
                        "landed)")
            elif outcome == "revised":
                version = row.get("version")
                if not isinstance(version, int) \
                        or skill.version < version:
                    audit.add(
                        ERROR, "learn-ledgers",
                        f"phase {label}: receipt says revised to version "
                        f"{version!r} but the store holds skill "
                        f"{skill_id!r} at version {skill.version}")

        # The bank commit point: the artifact first, the summary (with
        # its "bank" entry) last -- one without the other is drift.
        summary_path = directory / LEARN_SUMMARY_NAME
        bank_path = directory / BANK_NAME
        committed = False
        if summary_path.exists():
            summary = read_json(summary_path)
            committed = isinstance(summary, dict) and "bank" in summary
        if committed and not bank_path.exists():
            audit.add(
                ERROR, "learn-ledgers",
                f"phase {label}: the summary committed its bank entry but "
                f"{BANK_NAME} is missing -- the artifact was deleted or "
                "the summary was hand-edited")
        elif bank_path.exists() and not committed:
            # WARN, not ERROR (Wave 3 review O3, 2026-10-16 ruling): this
            # is the normal state of every interrupted commit the resume's
            # finalization sweep finishes AND of every assembled run before
            # its last learn phase runs (the pre-seeded artifact). The
            # module's own grading docstring puts "an interrupted commit
            # the resume would finish" under WARN; the legacy terminals
            # check grades the same shape WARN too.
            audit.add(
                WARN, "learn-ledgers",
                f"phase {label}: {BANK_NAME} exists but the summary never "
                "committed it (the artifact is written first, the summary "
                "last; one without the other is an interrupted commit "
                "the resume must finish, not a settled state)")
        elif not summary_path.exists() and not bank_path.exists():
            audit.add(
                INFO, "learn-ledgers",
                f"phase {label}: not finished (no summary, no bank "
                "artifact; in flight or interrupted)")


def check_final_test(audit: Audit, run_dir: Path, manifest: Dict[str, Any],
                     specs: Sequence[Tuple]) -> None:
    """Check 9: a complete final-test summary's arms hold every expected
    test unit (its own phase store, or the run-level store for a reused
    baseline control), minus the accepted holes. An external-campaign
    control has no units anywhere by construction -- for it the check is
    the phase directory's byte copy of the pinned score file hashing to
    the frozen sha256 (the numbers the delta consumed must be the
    campaign's exact bytes, not a drifted re-export)."""
    benchmark = manifest.get("benchmark")
    test_raw = (manifest.get("split") or {}).get("test")
    n_weak = (manifest.get("config") or {}).get("n_attempts_weak")
    for index, name, spec in specs:
        if not isinstance(spec, dict) or spec.get("kind") != "execute" \
                or spec.get("role") != PHASE_ROLE_TEST:
            continue
        label = phase_label(index, name)
        directory = _phase_dir_of(run_dir, index, name)
        if directory is None or not directory.exists():
            continue
        summary_path = directory / EXEC_SUMMARY_NAME
        if not summary_path.exists():
            audit.add(INFO, "final-test",
                      f"phase {label}: no summary yet (in flight or "
                      "interrupted)")
            continue
        if read_json(summary_path).get("complete") is not True:
            audit.add(INFO, "final-test",
                      f"phase {label}: incomplete (infra holes; the "
                      "summary lists them under infra_missing)")
            continue
        if not isinstance(test_raw, list) or not test_raw \
                or not isinstance(n_weak, int) or not benchmark:
            audit.add(ERROR, "final-test",
                      f"phase {label}: cannot derive the expected test "
                      "units from the manifest (benchmark / split.test / "
                      "n_attempts_weak)")
            continue
        compare = (spec.get("compare") or {})
        external = compare.get("external") if isinstance(compare, dict) \
            else None
        expected = [ids.make_trajectory_id(
                        ids.make_task_id(benchmark, raw), "weak", attempt)
                    for raw in test_raw for attempt in range(n_weak)]
        arms = ("with_bank",) if isinstance(external, dict) else ARM_NAMES
        if isinstance(external, dict):
            copy_path = directory / "external_control.json"
            if not copy_path.is_file():
                audit.add(ERROR, "final-test",
                          f"phase {label}: the summary says complete but "
                          "the external control's score-file copy is "
                          "missing")
            else:
                actual = hashlib.sha256(
                    copy_path.read_bytes()).hexdigest()
                pinned = str(external.get("sha256") or "")
                if actual != pinned:
                    audit.add(
                        ERROR, "final-test",
                        f"phase {label}: the external control's score-file "
                        f"copy hashes to sha256:{actual} but the frozen "
                        f"config pins sha256:{pinned}")
        for arm in arms:
            arm_dir = directory / arm
            if arm_dir.exists() and (arm_dir / "store").exists():
                stored = set(Store(arm_dir / "store").ids("trajectories"))
                accepted = set(_read_shortfall(arm_dir / SHORTFALL))
                missing = [unit for unit in expected
                           if unit not in stored and unit not in accepted]
                if missing:
                    audit.add(
                        ERROR, "final-test",
                        f"phase {label}: the summary says complete but the "
                        f"{arm} arm is missing {len(missing)} expected "
                        f"unit(s) ({missing[:5]}{'...' if len(missing) > 5 else ''})")
            elif arm == "with_bank":
                audit.add(
                    ERROR, "final-test",
                    f"phase {label}: the summary says complete but the "
                    "with_bank arm store is missing")
            else:
                # A reused baseline control: the units live in the
                # run-level store (the D phase's bare weak arm).
                run_store = Store(run_dir)
                stage1 = set(_read_shortfall(run_dir / STAGE1_SHORTFALL))
                missing = [unit for unit in expected
                           if not run_store.exists("trajectories", unit)
                           and unit not in stage1]
                if missing:
                    audit.add(
                        ERROR, "final-test",
                        f"phase {label}: the summary says complete but the "
                        f"reused control is missing {len(missing)} "
                        f"expected unit(s) ({missing[:5]})")


def check_group_terminals_v4(
        audit: Audit, store: Store, groups: Dict[str, List[Skill]],
        verdict_rows: Dict[Tuple[Any, ...], Dict[str, Any]],
        delete_receipts: Dict[str, str]) -> None:
    """Check 1's phase-world shape: the same finalize_group precedence
    over the verdict rows, with the learn phases' outcome receipts (the
    successor of the old attributions ledger) explaining the terminals
    an earlier action landed.

    ``delete_receipts`` maps skill_id -> outcome for every learn phase's
    receipt with outcome "discarded" or "deleted" (a task-list delete or
    an author delete). The G10/G11 exhaustion branches of the pre-v4
    world have no phase-world counterpart: a revision that produced no
    new version leaves the skill exactly as it is, and model-output
    failures are recorded per target in the run-root failed_units
    ledger, never as a discard."""
    for task_id, members in sorted(groups.items()):
        stored = {m.skill_id: store.load("skills", m.skill_id, Skill)
                  for m in members}
        pending = [s for s in stored.values() if s.status == "pending"]

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

        if decided is not None:
            direction, reason = decided
            # A learn-phase delete (task-list or author) is the earlier
            # action that may have terminalized a member before the
            # verdict committed the rest.
            tolerated = ({LIVE_REVISION_DELETED} if reason is None
                         else {LIVE_REVISION_DELETED, reason})
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
                elif (skill.discard_reason not in tolerated
                        and skill.skill_id not in delete_receipts):
                    audit.add(
                        ERROR, "terminals",
                        f"{task_id}: verdict says {direction} ({reason}) "
                        f"but skill {skill.skill_id} carries discard "
                        f"reason {skill.discard_reason!r} (no delete "
                        "receipt explains it)")
        elif pending:
            audit.add(
                INFO, "terminals",
                f"{task_id}: {len(pending)}/{len(stored)} member(s) "
                "pending (group still in flight, awaiting its learn "
                "phase or a resume)")
        else:
            unexplained = [
                skill for skill in stored.values()
                if skill.discard_reason != "live_no_improvement"
                and skill.skill_id not in delete_receipts]
            if unexplained:
                audit.add(
                    WARN, "terminals",
                    f"{task_id}: terminal group whose verdict rows decide "
                    "nothing and whose discards are neither the no-op "
                    "guard's live_no_improvement nor covered by a learn-"
                    "phase delete receipt (interrupted commit or store "
                    "drift; compare the learn ledgers by hand)")


def _learn_delete_receipts(run_dir: Path,
                           specs: Sequence[Tuple]) -> Dict[str, str]:
    """Every learn phase's delete receipts: skill_id -> outcome."""
    receipts: Dict[str, str] = {}
    for index, name, spec in specs:
        if not isinstance(spec, dict) or spec.get("kind") != "learn":
            continue
        directory = _phase_dir_of(run_dir, index, name)
        if directory is None or not directory.exists():
            continue
        for row in read_jsonl(directory / OUTCOMES_NAME):
            if row.get("outcome") in ("discarded", "deleted"):
                receipts.setdefault(str(row.get("skill_id")),
                                   row.get("outcome"))
    return receipts


def _live_depths(config: Dict[str, Any],
                 specs: Sequence[Tuple]) -> Dict[int, int]:
    """The weak depth each live round measures: the k-th live phase of
    the sequence (0-based k) carries round k's own execution.n_attempts,
    else the global n_attempts_weak."""
    depths: Dict[int, int] = {}
    for _, name, spec in specs:
        if not isinstance(spec, dict) or spec.get("kind") != "execute" \
                or spec.get("role") != PHASE_ROLE_LIVE:
            continue
        execution = spec.get("execution") or {}
        depth = execution.get("n_attempts") or config.get("n_attempts_weak")
        depths[len(depths)] = int(depth)
    return depths


def _audit_v4(audit: Audit, run_dir: Path, manifest: Dict[str, Any],
              n_attempts: Optional[int]) -> Audit:
    """The phase-world audit: the phase-directory checks always, the
    live-subtree checks when the frozen sequence carries a live phase."""
    config = manifest["config"]
    sequence = config.get("sequence")
    phases_raw = config.get("phases")
    if not isinstance(sequence, list) or not sequence \
            or not isinstance(phases_raw, dict):
        audit.add(
            ERROR, "layout",
            f"cannot read the frozen phase sequence from "
            f"{run_dir / 'manifest.json'} (config.sequence / config.phases); "
            "a v4 run's identity is its sequence")
        return audit
    specs = _phase_specs(config)
    for index, name, spec in specs:
        if not isinstance(spec, dict):
            audit.add(ERROR, "layout",
                      f"the frozen sequence names phase {name!r} with no "
                      "definition under config.phases")

    roles = [spec.get("role") for _, _, spec in specs
             if isinstance(spec, dict) and spec.get("kind") == "execute"]
    if PHASE_ROLE_LIVE in roles:
        mode = "live_run"
    elif PHASE_ROLE_EXAM in roles:
        mode = "exam"
    else:
        mode = None

    check_phase_dirs(audit, run_dir, specs)
    check_units_report(audit, run_dir, specs)
    store = Store(run_dir)
    skills = _load_skills(store)
    check_learn_ledgers(audit, run_dir, specs, skills)
    check_final_test(audit, run_dir, manifest, specs)

    if mode is None:
        audit.add(INFO, "layout",
                  "the frozen sequence carries no acceptance phase (no "
                  "exam, no live): the live-acceptance audit has nothing "
                  "to check here")
        return audit
    live_dir = run_dir / LIVE_SUBTREE
    if not live_dir.exists():
        if mode == "live_run":
            audit.add(ERROR, "layout",
                      "the frozen sequence carries live phases but there "
                      "is no stage4_acceptance/live_run/ subtree -- a "
                      "live run without its ledgers and round stores")
            return audit
        audit.add(INFO, "layout",
                  "exam-mode (or pre-acceptance) run: no "
                  "stage4_acceptance/live_run/ subtree -- nothing to "
                  "audit here")
        return audit
    if mode == "exam":
        audit.add(ERROR, "layout",
                  "the frozen sequence froze the exam mode but a live_run "
                  "subtree exists -- the run directory does not match its "
                  "frozen identity")
        return audit

    try:
        depths = _live_depths(config, specs)
    except (TypeError, ValueError):
        audit.add(ERROR, "layout",
                  "cannot read the live rounds' weak depth from the frozen "
                  "manifest (the live phases' execution.n_attempts / the "
                  "global n_attempts_weak); pass --n-attempts")
        return audit
    if n_attempts is not None:
        depths = {round_: n_attempts for round_ in depths}
    if not depths:
        return audit
    groups = _candidate_groups(skills)
    stage1_holes = _read_shortfall(run_dir / STAGE1_SHORTFALL)
    rounds = tuple(sorted(depths))
    verdict_rows = _first_row_per_key(
        read_jsonl(live_dir / VERDICTS), "task_id", "round")
    audit_rows: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for round_ in rounds:
        for row in read_jsonl(_round_dir(run_dir, round_) / INJECTION_AUDIT):
            audit_rows.setdefault((row.get("task_id"), round_), row)
    delete_receipts = _learn_delete_receipts(run_dir, specs)
    learn_labels = [phase_label(index, name) for index, name, spec in specs
                   if isinstance(spec, dict) and spec.get("kind") == "learn"]

    check_group_terminals_v4(audit, store, groups, verdict_rows,
                             delete_receipts)
    check_round_stores(audit, run_dir, groups, verdict_rows, audit_rows,
                       stage1_holes, rounds, depths.get)
    check_ledgers(audit, live_dir, ("verdicts",))
    check_shortfall(audit, run_dir, groups, rounds, depths.get,
                    learn_labels or ("stage4_live",))
    check_injection_audit(audit, store, skills, audit_rows)
    return audit


def _audit_legacy(audit: Audit, run_dir: Path,
                  n_attempts: Optional[int]) -> Audit:
    """The pre-v4 read paths, unchanged: the runs frozen under config
    schema v2/v3 stay auditable with the code that audited them when
    they ran (R10's hard requirement)."""
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
    skills = _load_skills(store)
    groups = _candidate_groups(skills)
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
                       stage1_holes, (0, 1), lambda round_: n_attempts)
    check_ledgers(audit, live_dir, ("verdicts", "attributions"))
    check_shortfall(audit, run_dir, groups, (0, 1),
                    lambda round_: n_attempts, ("stage4_live",))
    check_injection_audit(audit, store, skills, audit_rows)
    return audit


def audit_run(run_dir: Path, n_attempts: Optional[int] = None) -> Audit:
    """Run every check over one run directory; return the findings.

    The read paths dispatch on the run's frozen schema version: a v4
    manifest (the phase sequence) goes to the phase-world audit, anything
    else to the legacy paths (R10 -- history stays auditable)."""
    audit = Audit(run_dir)
    manifest_path = run_dir / "manifest.json"
    manifest = None
    if manifest_path.exists():
        try:
            manifest = read_json(manifest_path)
        except (OSError, ValueError):
            manifest = None
    config = manifest.get("config") if isinstance(manifest, dict) else None
    version = config.get("schema_version") if isinstance(config, dict) \
        else None
    if version == 4 or (isinstance(config, dict)
                        and "sequence" in config and "phases" in config):
        return _audit_v4(audit, run_dir, manifest, n_attempts)
    return _audit_legacy(audit, run_dir, n_attempts)


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
# Self-test: hand-built fake run directories (the composites-test shape)
# ---------------------------------------------------------------------------

def _make_unit_id(phase: str, task_ref: str, role: str,
                  skills_injected: Any) -> str:
    """The frozen unit-id rule (verdicts.make_unit_id), for the fake
    phase directories' units / report rows."""
    from expedistill.composites.verdicts import make_unit_id
    return make_unit_id(phase_label=phase, benchmark="tb",
                        task_ref=task_ref, role=role,
                        skills_injected=skills_injected)


def _report_row(unit_id: str, phase: str, role: str, injected: Any,
                valid: int, success: int) -> Dict[str, Any]:
    """One acceptance-report row in the frozen AcceptanceReportRow shape
    (a normal row; the fake runs are all-miss)."""
    return {"unit_id": unit_id, "phase_label": phase, "role": role,
            "injected_skills": injected, "runs": [],
            "valid_count": valid, "success_count": success,
            "status": "normal", "cause": "", "artifacts": {}}


def _unit_row(unit_id: str, task_ref: str, injected: Any,
              block: str = "") -> Dict[str, Any]:
    """One units.jsonl row in the frozen ExecutionUnitRow shape."""
    return {"unit_id": unit_id, "task_ref": task_ref,
            "skills_injected": injected, "skills_block": block, "aux": {}}


def _write_phase_record(run_dir: Path, label: str, index: int, name: str,
                        role: str) -> Path:
    """Create the phase directory with its phase.json; return it."""
    directory = phase_bank_dir(run_dir, label)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / PHASE_RECORD_NAME,
               {"label": label, "index": index, "name": name, "role": role},
               indent=2)
    return directory


def _bump_skill(store: Store, skill_id: str, new_content: str) -> None:
    """One G6-style revision: the old content joins the history, the
    version moves (the same bytes the author driver lands)."""
    skill = store.load("skills", skill_id, Skill)
    skill.rewrite_history.append({
        "content": skill.content, "error_category": "misread",
        "reason": "spelled out the trigger"})
    skill.content = new_content
    skill.version += 1
    store.save(skill, "skills")


def _build_fake_v4_run(root: Path) -> Path:
    """A complete v4 live run: sequence D, Lseed, B1, Lmid, B2, Lfinal,
    C over one train task (group of two candidates) and one test task.
    Both live rounds miss; the mid learn phase revises both members; the
    final learn phase lands nothing new; the final test's two arms hold
    every expected unit."""
    run_dir = root / "run_v4"
    store = Store(run_dir)
    task_id = ids.make_task_id("tb", "t1")
    test_id = ids.make_task_id("tb", "tt")
    n_attempts = 3

    sequence = ["D", "Lseed", "B1", "Lmid", "B2", "Lfinal", "C"]
    learn_spec = {"kind": "learn", "evidence": {"name": "segment", "K": 1},
                  "author": {"name": "distill_diagnose"},
                  "curate": {"name": "none"}}
    phases = {
        "D": {"kind": "execute", "role": "baseline", "tasks": "train",
              "delivery": {"name": "none"},
              "execution": {"name": "real", "arms": ["strong", "weak"]},
              "compare": None, "acceptance": None},
        "Lseed": dict(learn_spec),
        "B1": {"kind": "execute", "role": "live", "tasks": "train",
               "delivery": {"name": "group_by_task"},
               "execution": {"name": "real", "n_attempts": n_attempts},
               "compare": {"against": "D", "rule": "strict_rate_gain"},
               "acceptance": None},
        "Lmid": dict(learn_spec),
        "B2": {"kind": "execute", "role": "live", "tasks": "train",
               "delivery": {"name": "group_by_task"},
               "execution": {"name": "real", "n_attempts": n_attempts},
               "compare": {"against": "D", "rule": "strict_rate_gain"},
               "acceptance": None},
        "Lfinal": dict(learn_spec),
        "C": {"kind": "execute", "role": "test", "tasks": "test",
              "delivery": {"name": "whole_bank"},
              "execution": {"name": "real"},
              "compare": {"against": "fresh_bare"}, "acceptance": None},
    }
    (run_dir / "manifest.json").write_text(json.dumps({
        "benchmark": "tb", "mode": "split",
        "split": {"train": ["t1"], "test": ["tt"], "reserved": []},
        "config": {"schema_version": 4, "benchmark": "tb", "mode": "split",
                   "sequence": sequence, "phases": phases,
                   "n_attempts_strong": 1, "n_attempts_weak": n_attempts,
                   "models": {"executor_weak": "weak-model",
                              "executor_strong": "strong-model"}},
    }, indent=2), encoding="utf-8")

    # -- p01 D: both bare arms in the run-level store -------------------
    for attempt in range(n_attempts):
        store.save(_fake_trajectory(
            ids.make_trajectory_id(task_id, "weak", attempt), False),
            "trajectories")
    store.save(_fake_trajectory(
        ids.make_trajectory_id(task_id, "strong", 0), False), "trajectories")
    d_dir = _write_phase_record(run_dir, "p01:D", 0, "D", "baseline")
    d_weak = _make_unit_id("p01:D", task_id, "weak", None)
    d_strong = _make_unit_id("p01:D", task_id, "strong", None)
    write_jsonl(d_dir / UNITS_NAME, [
        _unit_row(d_weak, task_id, None),
        _unit_row(d_strong, task_id, None)])
    write_jsonl(d_dir / REPORT_NAME, [
        _report_row(d_weak, "p01:D", "weak", None, n_attempts, 0),
        _report_row(d_strong, "p01:D", "strong", None, 1, 0)])
    write_json(d_dir / EXEC_SUMMARY_NAME,
               {"role": "baseline", "complete": True, "report_rows": 2,
                "infra_missing": [], "infra_accepted": []}, indent=2)
    write_json(d_dir / REDISPATCH_NAME, {}, indent=2)

    # -- p02 Lseed: the add that creates the two-candidate group -------
    members = [_fake_group_skill(task_id, 0), _fake_group_skill(task_id, 1)]
    for skill in members:
        store.save(skill, "skills")
    seed_dir = phase_bank_dir(run_dir, "p02:Lseed")
    seed_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(seed_dir / TASK_LIST_NAME, [{
        "schema": "learn-task-list-v1", "phase_label": "p02:Lseed",
        "target_kind": "task", "target_id": task_id, "action": "add",
        "reason": "weak_failed_no_skill", "basis_unit": d_weak}])
    for skill in members:
        _append_jsonl_raw(seed_dir / OUTCOMES_NAME, {
            "schema": LEARN_OUTCOME_SCHEMA, "phase_label": "p02:Lseed",
            "target_kind": "task", "target_id": task_id, "action": "add",
            "skill_id": skill.skill_id, "outcome": "added", "version": 1})
    write_jsonl(seed_dir / BANK_NAME,
               [skill.to_dict() for skill in members])
    write_json(seed_dir / LEARN_SUMMARY_NAME,
               {"role": "learn", "current_phase": "D",
                "task_list": {"add": 1}, "outcomes": {"added": 2},
                "bank": {"members": 2}}, indent=2)

    def live_round(b_label: str, b_name: str, b_index: int, round_: int,
                   refs: List[Dict[str, Any]],
                   contents: List[str]) -> str:
        """One B phase: frozen audit row, executed round store, a
        not-better verdict row, the phase directory's units / report /
        summary. Returns the phase's unit id."""
        unit_id = _make_unit_id(b_label, task_id, "weak", refs)
        row = _frozen_row(task_id, round_, refs, contents)
        _append_jsonl_raw(_round_dir(run_dir, round_) / INJECTION_AUDIT, row)
        round_store = Store(_round_dir(run_dir, round_))
        for attempt in range(n_attempts):
            round_store.save(_fake_trajectory(
                ids.make_trajectory_id(task_id, "weak", attempt), False,
                refs), "trajectories")
        _append_jsonl_raw(run_dir / LIVE_SUBTREE / VERDICTS, {
            "schema": "live-verdict-v1", "task_id": task_id,
            "round": round_, "rule": "strict_rate_gain",
            "baseline": {"valid": n_attempts, "success": 0},
            "live": {"valid": n_attempts, "success": 0}, "better": False,
            "injection_tokens": row["injection_tokens"],
            "judged_with_shortfall": False, "aux": None,
        })
        directory = _write_phase_record(run_dir, b_label, b_index,
                                        b_name, "live")
        write_jsonl(directory / UNITS_NAME,
                   [_unit_row(unit_id, task_id, refs)])
        write_jsonl(directory / REPORT_NAME, [
            _report_row(unit_id, b_label, "weak", refs, n_attempts, 0)])
        write_json(directory / EXEC_SUMMARY_NAME,
                   {"role": "live", "round": round_, "complete": True,
                    "report_rows": 1, "units": [unit_id],
                    "dispatched": [task_id], "infra_missing": [],
                    "infra_accepted": []}, indent=2)
        write_json(directory / REDISPATCH_NAME, {}, indent=2)
        return unit_id

    # -- p03 B1 (round 0) over both members at version 1 -----------------
    refs1 = [{"skill_id": skill.skill_id, "version": 1} for skill in members]
    b1_unit = live_round("p03:B1", "B1", 2, 0, refs1,
                         [skill.content for skill in members])

    # -- p04 Lmid: revise both members to version 2 ----------------------
    mid_dir = phase_bank_dir(run_dir, "p04:Lmid")
    mid_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(mid_dir / TASK_LIST_NAME, [{
        "schema": "learn-task-list-v1", "phase_label": "p04:Lmid",
        "target_kind": "group", "target_id": task_id, "action": "revise",
        "reason": "live_no_improvement", "basis_unit": b1_unit}])
    for skill in members:
        _append_jsonl_raw(mid_dir / REVISIONS_NAME, {
            "schema": "learn-revision-v1", "phase_label": "p04:Lmid",
            "target_kind": "group", "target_id": task_id,
            "skill_id": skill.skill_id, "from_version": 1})
        _bump_skill(store, skill.skill_id,
                    f"[When to use] x2\n[Steps]\n1. do\n"
                    f"[Verify] y\n[Do not use when] z{skill.skill_id[-1]}")
        _append_jsonl_raw(mid_dir / OUTCOMES_NAME, {
            "schema": LEARN_OUTCOME_SCHEMA, "phase_label": "p04:Lmid",
            "target_kind": "group", "target_id": task_id,
            "action": "revise", "skill_id": skill.skill_id,
            "outcome": "revised", "version": 2})
    write_jsonl(mid_dir / BANK_NAME,
               [store.load("skills", skill.skill_id, Skill).to_dict()
                for skill in members])
    write_json(mid_dir / LEARN_SUMMARY_NAME,
               {"role": "learn", "current_phase": "p03:B1",
                "task_list": {"revise": 1}, "outcomes": {"revised": 2},
                "bank": {"members": 2}}, indent=2)

    # -- p05 B2 (round 1) over both members at version 2 ----------------
    refs2 = [{"skill_id": skill.skill_id, "version": 2} for skill in members]
    b2_unit = live_round("p05:B2", "B2", 4, 1, refs2,
                         [store.load("skills", skill.skill_id, Skill).content
                          for skill in members])
    # The round-1 miss verdict: finalize_group discards both survivors.
    for skill_id in (members[0].skill_id, members[1].skill_id):
        skill = store.load("skills", skill_id, Skill)
        skill.status = "discarded"
        skill.discard_reason = "live_no_improvement"
        store.save(skill, "skills")

    # -- p06 Lfinal: the decision's deletes find everything terminal ----
    final_dir = phase_bank_dir(run_dir, "p06:Lfinal")
    final_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(final_dir / TASK_LIST_NAME, [{
        "schema": "learn-task-list-v1", "phase_label": "p06:Lfinal",
        "target_kind": "group", "target_id": task_id, "action": "delete",
        "reason": "live_no_improvement", "basis_unit": b2_unit}])
    for skill in members:
        _append_jsonl_raw(final_dir / OUTCOMES_NAME, {
            "schema": LEARN_OUTCOME_SCHEMA, "phase_label": "p06:Lfinal",
            "target_kind": "group", "target_id": task_id,
            "action": "delete", "skill_id": None,
            "outcome": "skipped_terminal", "version": 2})
    write_jsonl(final_dir / BANK_NAME, [])
    write_json(final_dir / LEARN_SUMMARY_NAME,
               {"role": "learn", "current_phase": "p05:B2",
                "task_list": {"delete": 1},
                "outcomes": {"skipped_terminal": 2},
                "bank": {"members": 0}}, indent=2)

    # -- p07 C: both arms hold every expected test unit ------------------
    c_dir = _write_phase_record(run_dir, "p07:C", 6, "C", "test")
    c_unit = _make_unit_id("p07:C", test_id, "weak", "bank-digest")
    write_jsonl(c_dir / UNITS_NAME,
               [_unit_row(c_unit, test_id, "bank-digest")])
    write_json(c_dir / REDISPATCH_NAME, {}, indent=2)
    for arm, injected in (("with_bank", [{"bank": "digest"}]),
                          ("control", None)):
        arm_store = Store(c_dir / arm / "store")
        for attempt in range(n_attempts):
            arm_store.save(_fake_trajectory(
                ids.make_trajectory_id(test_id, "weak", attempt), False,
                injected), "trajectories")
    write_json(c_dir / EXEC_SUMMARY_NAME,
               {"role": "test", "mode": "split", "complete": True,
                "with_bank": {"avg": 0.0}, "control": {"avg": 0.0},
                "delta": {}, "infra_missing": {},
                "infra_accepted": {"with_bank": [], "control": []}},
               indent=2)

    # One stage-level infra failure under a learn phase's label (the
    # resume retries it; the audit reports it for context as INFO).
    _append_jsonl_raw(run_dir / "failed_units.jsonl", {
        "schema": "unit-failure-v1", "unit_id": f"{task_id}:revise:0",
        "stage": "p04:Lmid", "error_class": "ModelOutputError",
        "message": "the revision call returned unusable json"})
    return run_dir


def _self_test_v4(root: Path) -> int:
    """Audit a clean v4 fake run, then six tampers; each must be caught."""
    run_dir = _build_fake_v4_run(root)
    task_id = ids.make_task_id("tb", "t1")

    audit = audit_run(run_dir)
    errors = [f["message"] for f in audit.errors()]
    if errors:
        print("SELF-TEST FAIL: clean v4 run reported errors:")
        for message in errors:
            print(f"  {message}")
        return 1
    print(f"self-test: clean fake v4 run -> 0 errors "
          f"({len(audit.findings)} finding(s))")

    # Tamper 5: flip a phase record's role -> the directory drifted from
    # the frozen sequence.
    b1_dir = phase_bank_dir(run_dir, "p03:B1")
    record = read_json(b1_dir / PHASE_RECORD_NAME)
    record["role"] = "exam"
    write_json(b1_dir / PHASE_RECORD_NAME, record, indent=2)
    audit = audit_run(run_dir)
    if not any("phase.json says role=" in f["message"]
               for f in audit.errors()):
        print("SELF-TEST FAIL: tamper 5 (phase record role flip) not caught")
        return 1
    print("self-test: phase record role flip caught")

    # Tamper 6: a duplicated unit row -> a second resolution of one unit.
    units = read_jsonl(b1_dir / UNITS_NAME)
    write_jsonl(b1_dir / UNITS_NAME, units + [dict(units[0])])
    audit = audit_run(run_dir)
    if not any("duplicate unit row" in f["message"]
               for f in audit.errors()):
        print("SELF-TEST FAIL: tamper 6 (duplicated unit row) not caught")
        return 1
    print("self-test: duplicated unit row caught")

    # Tamper 7: an invented report row -> a verdict for a unit the phase
    # never resolved.
    report = read_jsonl(b1_dir / REPORT_NAME)
    report.append(_report_row("p03:B1:tb:tb:t1:weak:deadbeef",
                              "p03:B1", "weak", [], 3, 3))
    write_jsonl(b1_dir / REPORT_NAME, report)
    audit = audit_run(run_dir)
    if not any("never resolved" in f["message"]
               for f in audit.errors()):
        print("SELF-TEST FAIL: tamper 7 (invented report row) not caught")
        return 1
    print("self-test: invented report row caught")

    # Tamper 8: a receipt the store contradicts -> the landing never
    # landed (the member is discarded, the receipt says accepted).
    _append_jsonl_raw(phase_bank_dir(run_dir, "p04:Lmid") / OUTCOMES_NAME, {
        "schema": LEARN_OUTCOME_SCHEMA, "phase_label": "p04:Lmid",
        "target_kind": "group", "target_id": task_id, "action": "keep",
        "skill_id": ids.make_skill_id(ids.make_subtask_id(
            ids.make_trajectory_id(task_id, "strong", 0), 0)),
        "outcome": "accepted", "version": 2})
    audit = audit_run(run_dir)
    if not any("receipt says accepted" in f["message"]
               for f in audit.errors()):
        print("SELF-TEST FAIL: tamper 8 (store-contradicting receipt) "
              "not caught")
        return 1
    print("self-test: store-contradicting receipt caught")

    # Tamper 9: drop one final-test trajectory -> a complete summary with
    # a missing expected unit.
    victim = Store(phase_bank_dir(run_dir, "p07:C") / "with_bank"
                   / "store").path_for(
        "trajectories", ids.make_trajectory_id(
            ids.make_task_id("tb", "tt"), "weak", 1))
    victim.unlink()
    audit = audit_run(run_dir)
    if not any("with_bank arm is missing" in f["message"]
               for f in audit.errors()):
        print("SELF-TEST FAIL: tamper 9 (dropped final-test trajectory) "
              "not caught")
        return 1
    print("self-test: dropped final-test trajectory caught")

    # Tamper 10: a verdict-contradicting status on a live group member
    # (the round-1 miss verdict decided discarded).
    store = Store(run_dir)
    survivor_id = ids.make_skill_id(ids.make_subtask_id(
        ids.make_trajectory_id(task_id, "strong", 0), 1))
    survivor = store.load("skills", survivor_id, Skill)
    survivor.status = "accepted"
    survivor.discard_reason = None
    store.save(survivor, "skills")
    audit = audit_run(run_dir)
    if not any(survivor_id in f["message"] and "verdict says" in f["message"]
               for f in audit.errors()):
        print("SELF-TEST FAIL: tamper 10 (verdict-contradicting status on "
              "the v4 run) not caught")
        return 1
    print("self-test: verdict-contradicting status caught")
    return 0

def _fake_trajectory(unit_id: str, correctness: Optional[bool],
                     refs: Optional[List[Dict[str, Any]]] = None) -> Trajectory:
    task_id, role, attempt = ids.parse_trajectory_id(unit_id)
    return Trajectory(
        trajectory_id=unit_id, task_id=task_id, benchmark="tb",
        model_role=role, model_name="weak-model", attempt_index=attempt,
        steps=[{"action": "do", "observation": "obs"}],
        correctness=correctness, skills_injected=refs)


def _fake_group_skill(task_id: str, segment: int) -> Skill:
    subtask_id = ids.make_subtask_id(
        ids.make_trajectory_id(task_id, "strong", 0), segment)
    return Skill(skill_id=ids.make_skill_id(subtask_id),
                 content="[When to use] x\n[Steps]\n1. do\n"
                         "[Verify] y\n[Do not use when] z",
                 subtask_id=subtask_id, source_subtasks=[subtask_id],
                 version=1)


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
            ids.make_trajectory_id(task_id, "weak", attempt), False),
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
            ids.make_trajectory_id(task_id, "weak", attempt), False,
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
            ids.make_trajectory_id(task_id, "weak", attempt), False,
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
            ids.make_trajectory_id(task2, "weak", attempt), False),
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
            ids.make_trajectory_id(task3, "weak", attempt), False),
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
            ids.make_trajectory_id(task3, "weak", attempt), False,
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

        # The v4 phase-world scenarios (a separate fake run: the same
        # tamper classes against the phase-directory read paths).
        if _self_test_v4(root) != 0:
            return 1
    print("self-test: all checks passed")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only integrity audit of a live_run acceptance "
                    "run directory (AGENTS.md 6.7); the read paths "
                    "follow the run's frozen schema version (pre-v4 "
                    "stage paths, v4 phase directories).")
    parser.add_argument("--run-dir", type=Path, default=None,
                         help="the run directory to audit (read-only)")
    parser.add_argument("--n-attempts", type=int, default=None,
                         help="override the live rounds' weak depth "
                              "(default: the frozen manifest -- a v4 "
                              "run's live phases' execution.n_attempts, "
                              "a pre-v4 run's config.methods.acceptance."
                              "n_attempts)")
    parser.add_argument("--out", type=Path, default=None,
                         help="write the machine-readable report (JSON) to "
                              "this path (must live OUTSIDE the run dir)")
    parser.add_argument("--self-test", action="store_true",
                        help="build fake run directories, audit each clean, "
                             "then tamper them and assert each tamper is "
                             "caught")
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
