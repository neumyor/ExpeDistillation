"""K6 measurement: the live-run paths, readers and dispatch-set inputs
(layer 5).

The measurement part of the former composites/live_acceptance.py, split
out mechanically in R0 (docs/decisions/execute_learn_repair_plan_20261016.md):
everything that derives a round's inputs and reads its stored state -- the
live-run subtree and the ledger file names, the per-round store path, the
ledger and audit-row readers, the group derivation (which tasks still
have pending stage-3 candidates), the stage-1 baseline reader, the D3
expected set, the round skip set, the frozen injection-audit row, and the
survivors-and-block derivation that round 1 and the group_by_task
delivery inject. No judgment, no attribution, no skill-status write:
judgment lives in composites/live_verdicts.py; attribution and revision
are the phase world's evidence/author families (methods/evidence,
methods/author).

State is derived from disk only (decision section 2.4 -- no new state
objects): a dispatched round's directory exists even when every unit was
an accepted hole; the stage freezes the injection-audit row BEFORE the
round's dispatch and a re-derivation before a re-dispatch must equal it
byte for byte (``audit_row`` is the single constructor for both sides).
"""

import hashlib
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Tuple,
)

from expedistill.blocks.metrics import compute_task_metrics
from expedistill.core import ids
from expedistill.core.models import Skill, Trajectory
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
)
from expedistill.infra.store import Store, read_json, read_jsonl
from expedistill.inject.skills import estimate_tokens, format_skills_block

__all__ = [
    "ATTRIBUTION_FAILURES_LEDGER",
    "ATTRIBUTIONS_LEDGER",
    "INJECTION_AUDIT_NAME",
    "REVISION_FAILURES_LEDGER",
    "VERDICTS_LEDGER",
    "audit_row",
    "expected_units",
    "load_baseline",
    "pending_groups",
    "round_dir",
    "skip_ids_for",
    "survivors_and_block",
]

#: Live-run subtree inside the stage-4 directory (decision section 2.2):
#: round stores and the ledgers all live under it. Shell-layer constant:
#: the measurement core takes its paths as explicit parameters and never
#: reads this; the path derivation dies with the shells in M4.
LIVE_DIR_NAME = "stage4_acceptance/live_run"

#: The decision ledgers plus the two repair-failure ledgers (decision
#: section 2.4), written under the live-run directory.
VERDICTS_LEDGER = "verdicts.jsonl"
ATTRIBUTIONS_LEDGER = "attributions.jsonl"
ATTRIBUTION_FAILURES_LEDGER = "attribution_failures.jsonl"
REVISION_FAILURES_LEDGER = "revision_failures.jsonl"

#: Per-round files, inside ``round00`` / ``round01``: the frozen injection
#: audit (written by the stage through ``audit_row``) and the accepted
#: shortfall record (written by the stage; ``skip_ids_for`` consumes its
#: contents, this module never writes it).
INJECTION_AUDIT_NAME = "injection_audit.jsonl"

#: Rounds are pinned to two (user ruling 8 -- no max_revision_rounds
#: config), so every round-taking shell rejects anything else loudly
#: instead of silently inventing a third round the design never had.
#: Shell-layer constant: the measurement core takes its round labels as
#: parameters and never reads this; the pin and its check die with the
#: shells in M4.
_ROUNDS = (0, 1)


def _live_dir(store: Store) -> Path:
    return store.run_dir / LIVE_DIR_NAME


def _ledger_path(store: Store, name: str) -> Path:
    return _live_dir(store) / name


def round_dir(store: Store, round_: int) -> Path:
    """One round's directory (its B3 store, the frozen injection_audit
    line and the stage's accepted-shortfall record all live here). The
    naming is part of the T3/T4 seam: the stage builds its round store,
    writes the audit and the shortfall record through THIS path helper
    so the layout can never drift between the two files."""
    return _live_dir(store) / f"round{round_:02d}"


def _check_round(round_: int) -> None:
    if round_ not in _ROUNDS:
        raise CodeBugError(
            f"live rounds are pinned to {list(_ROUNDS)} (user ruling 8), "
            f"got {round_!r}")


def _rows_from(path: Path, task_id: str,
               round_: Optional[int] = None) -> List[Dict[str, Any]]:
    """One task's rows of a ledger file, in append order (core reader:
    the path is the caller's, never derived here)."""
    rows = [row for row in read_jsonl(path)
            if row.get("task_id") == task_id]
    if round_ is not None:
        rows = [row for row in rows if row.get("round") == round_]
    return rows


def _first_row_from(path: Path, task_id: str,
                    round_: int) -> Optional[Dict[str, Any]]:
    """The first ledger row of a (task, round) -- the one that sticks.

    Only one row is ever appended per (task, round), but reading the first
    (not the last) is what makes a hand-edited duplicate ledger fail
    loudly in the recompute check instead of silently switching verdicts.
    """
    for row in _rows_from(path, task_id, round_):
        return row
    return None


def _audit_row_from(audit_path: Path, round_: int,
                    task_id: str) -> Optional[Dict[str, Any]]:
    """The frozen injection-audit row of one group in one round's audit
    file (the stage wrote it before the round's dispatch); None when the
    round never got that far."""
    for row in read_jsonl(audit_path):
        if row.get("task_id") == task_id:
            return row
    return None


def _require_audit_row_from(audit_path: Path, round_: int,
                            task_id: str) -> Dict[str, Any]:
    """The frozen audit row, or a CodeBugError: judging or guarding a
    round that has no frozen audit row means the first write and the
    dispatch disagree -- exactly the hole the freeze exists to close."""
    row = _audit_row_from(audit_path, round_, task_id)
    if row is None:
        raise CodeBugError(
            f"no frozen injection_audit row for task {task_id!r} in "
            f"round {round_}: the stage must freeze the audit row before "
            "dispatching the round")
    return row


def _parse_unit(unit_id: str) -> Tuple[str, str, int]:
    """(task_id, role, attempt) of one trajectory id; a malformed id is a
    corrupt record, not data to silently drop."""
    try:
        return ids.parse_trajectory_id(unit_id)
    except ValueError:
        raise CodeBugError(
            f"malformed trajectory unit id {unit_id!r} in a shortfall "
            "record") from None


def _read_stage1_accepted(store: Store) -> List[str]:
    """Stage 1's accepted-hole unit ids (both arms, all tasks) from the
    durable record. Composites may not import the stage that owns the
    writer, so the reader is restated here with the same fail-loud
    contract."""
    path = store.run_dir / "stage1_baseline" / "accepted_infra_shortfall.json"
    if not path.exists():
        return []
    try:
        unit_ids = read_json(path)["unit_ids"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CodeBugError(
            f"unreadable accepted-shortfall record {path}: {exc}") from None
    if not isinstance(unit_ids, list) or not all(
            isinstance(unit_id, str) for unit_id in unit_ids):
        raise CodeBugError(f"invalid accepted-shortfall record {path}")
    return unit_ids


def _unit_ids(task_id: str, n_attempts: int) -> List[str]:
    """The full unit-id set of one group's round, in attempt order."""
    return [ids.make_trajectory_id(task_id, "weak", attempt)
            for attempt in range(n_attempts)]


def _trajectories_from(round_store_dir: Path, task_id: str,
                       round_: int) -> List[Trajectory]:
    """Every trajectory the round store at ``round_store_dir`` holds for
    one task, attempt order (core reader: the directory is the caller's,
    the round label only names it in errors).

    Attribution, revision and judging all read the same stored views, so
    the read lives once. A round directory that does not exist means the
    round was never dispatched -- the caller is ahead of the stage, which
    is a bug, not a resume state (a dispatched round's directory exists
    even when every unit was an accepted hole)."""
    if not round_store_dir.exists():
        raise CodeBugError(
            f"round {round_} of task {task_id!r} was never dispatched: "
            "attribution, revision and judging read its stored "
            "trajectories")
    round_store = Store(round_store_dir)
    trajectories = [
        round_store.load("trajectories", trajectory_id, Trajectory)
        for trajectory_id in round_store.ids("trajectories")]
    trajectories = [t for t in trajectories if t.task_id == task_id]
    trajectories.sort(key=lambda t: t.attempt_index)
    return trajectories


# ---------------------------------------------------------------------------
# Group derivation
# ---------------------------------------------------------------------------

def _task_of_skill(skill: Skill) -> Optional[str]:
    """The prefixed task id in a skill's ID chain, None for ids that do
    not carry one (never true of stage-3 candidates)."""
    try:
        trajectory_id, _ = ids.parse_subtask_id(skill.subtask_id)
        return ids.parse_trajectory_id(trajectory_id)[0]
    except ValueError:
        return None


def _pending_group(store: Store, task_id: str) -> List[Skill]:
    """The group of one task: its pending stage-3 candidate skills, in
    skill_id order.

    The candidate filter lives in ``pending_groups`` (decision section
    2.2: the live side rewrites it locally instead of sharing the exam
    stage's helper): a candidate's id is the stage-3 id of its own
    subtask, so ``bank/merge/NNNN`` products are excluded -- they are
    K5's, proven by regression exams, never re-accepted here. Pending
    only: terminal members are out of the group, which is what makes this
    one derivation the round-0 set (everything pending), the survivor set
    after revisions (keeps and revised stay pending, deletes and
    exhausted are gone) and the sweep's commit target."""
    return pending_groups(store).get(task_id, [])


def _require_group(store: Store, task_id: str) -> List[Skill]:
    members = _pending_group(store, task_id)
    if not members:
        raise CodeBugError(
            f"task {task_id!r} has no pending stage-3 candidate skills: "
            "there is no live-acceptance group to process")
    return members


def pending_groups(store: Store) -> Dict[str, List[Skill]]:
    """The live-run universe: every task that still has pending stage-3
    candidates, as ``{task_id: members}`` (skill_id order within each).

    This is the one place the group filter lives (decision section 2.2:
    the live side rewrites it locally instead of sharing the exam stage's
    helper). A candidate's id is the stage-3 id of its own subtask, so
    ``bank/merge/NNNN`` products are excluded -- they are K5's, proven by
    regression exams, never re-accepted here; the task key comes from the
    ID chain (skill -> subtask -> trajectory -> task), never from model
    output. The universe is derived from the store, never assumed (no
    F=28, no requirement that candidates cover every train task): a task
    with no pending candidate is not a unit. The stage enumerates its
    units through this helper so its dispatch sets and this module's
    derivations can never disagree about what a group is."""
    groups: Dict[str, List[Skill]] = {}
    for skill_id in store.ids("skills"):
        skill = store.load("skills", skill_id, Skill)
        if not ids.is_stage3_candidate(skill):
            continue
        if skill.status != "pending":
            continue
        task_id = _task_of_skill(skill)
        if task_id is None:
            continue
        groups.setdefault(task_id, []).append(skill)
    return groups


# ---------------------------------------------------------------------------
# Baseline, expected units, skip set
# ---------------------------------------------------------------------------

def load_baseline(
    store: Store,
    task_id: str,
    n_attempts: int,
    *,
    aux_scorer: Optional[Callable[[Trajectory], bool]] = None,
) -> Tuple[List[Trajectory], Dict[str, int]]:
    """Load the stage-1 weak bare trajectories of one task by deterministic
    id (the stage-6 ``_bare_stage1_trajectories`` shape -- the baseline is
    read, never re-run) and return ``(trajectories, counts)``.

    ``task_id`` is the prefixed id (``benchmark:raw``) that the group key
    and every trajectory id carry. Units that stage 1 accepted as infra
    holes are skipped and stay holes; any other missing unit raises
    InfrastructureError -- an unreachable guard that points at the fix
    (resume stage 1; a hole can only be accepted there). A baseline with
    no valid attempt at all is NOT an error since R2b: unscored trials
    (``correctness`` None) are a normal stored state, so valid == 0 is
    returned as counts and the group is judged not_testable up the
    stack (the live driver's entry guard terminalizes it live_undetermined
    with zero executions; judge_round verifies the fact before writing
    the row).

    The counts are D3's single-task counts under the FULL expected set
    (``expected_units``): accepted holes and attempts beyond the stored
    stage-1 depth surface as ``infra``, never as a silently shrunken
    denominator, and the live arm is judged under the same full set so
    the rate comparison stays aligned (decision section 2.4).
    """
    accepted = set()
    for unit_id in _read_stage1_accepted(store):
        unit_task, role, _ = _parse_unit(unit_id)
        if role == "weak" and unit_task == task_id:
            accepted.add(unit_id)
    trajectories: List[Trajectory] = []
    missing: List[str] = []
    for trajectory_id in _unit_ids(task_id, n_attempts):
        if trajectory_id in accepted:
            continue
        if store.exists("trajectories", trajectory_id):
            trajectories.append(
                store.load("trajectories", trajectory_id, Trajectory))
        else:
            missing.append(trajectory_id)
    if missing:
        raise InfrastructureError(
            f"live-run baseline is incomplete: stage 1 weak bare "
            f"trajectories missing for {len(missing)} unit(s) of task "
            f"{task_id!r}: {missing}; resume the run to retry them "
            "(stage 1 resumes first; a hole can only be accepted there, "
            "with --allow-infra-shortfall)")
    counts = compute_task_metrics(
        trajectories, expected_units(task_id, n_attempts),
        aux_scorer=aux_scorer)[task_id]
    return trajectories, counts


def expected_units(task_id: str, n_attempts: int) -> Dict[str, int]:
    """The D3 expected mapping of one task: ``{task_id: n_attempts}``.

    Deliberately NOT reduced by the skip set (decision section 2.4):
    both arms are judged under the full set, skipped units have no
    trajectory and D3's expected accounting counts them as infra -- the
    verdict row keeps its self-describing holes instead of a denominator
    that quietly shrank, and passing a skip-reduced set here is the
    exact mistake this helper exists to make impossible.
    """
    return {task_id: n_attempts}


def skip_ids_for(stage1_shortfall: Iterable[str],
                 live_shortfall: Iterable[str], task_id: str) -> List[str]:
    """This task's skip set for one round's K1 dispatch (decision rulings
    A9 / A8): stage 1's accepted weak-arm holes of this task plus this
    round's own accepted live holes, as sorted trajectory unit ids.

    The stage reads both records and passes their contents in (the files
    are the stage's); the DERIVATION lives here so the stage and the
    composite can never disagree about what a skip is. The stage-1
    record covers both arms and every task, so it is filtered to this
    task's weak arm; the live record is round-local but round-wide
    across groups, so it is filtered to this task as well (its units
    are weak-arm by construction -- a live run never executes the
    strong arm).
    """
    skipped = set()
    for source in (stage1_shortfall, live_shortfall):
        for unit_id in source:
            unit_task, role, _ = _parse_unit(unit_id)
            if unit_task == task_id and role == "weak":
                skipped.add(unit_id)
    return sorted(skipped)


# ---------------------------------------------------------------------------
# Injection audit row
# ---------------------------------------------------------------------------

def _audit_row(store: Store, task_id: str, round_: int) -> Dict[str, Any]:
    """The round-agnostic core of ``audit_row``: the row for one round
    label, with no pinned-round check (that is the shell's, and dies
    with it in M4)."""
    members = _require_group(store, task_id)
    block = format_skills_block(members)
    return {
        "schema": "injection-audit-v1",
        "task_id": task_id,
        "round": round_,
        "members": [{"skill_id": skill.skill_id, "version": skill.version}
                    for skill in members],
        "injection_block_sha256": _block_sha(block),
        "injection_tokens": estimate_tokens(block),
    }


def audit_row(store: Store, task_id: str, round_: int) -> Dict[str, Any]:
    """Construct the group's injection-audit row for one round.

    The row is what the stage freezes into
    ``live_run/round{NN}/injection_audit.jsonl`` BEFORE dispatching the
    round, and what a resumed stage re-derives to compare against the
    frozen line byte for byte -- one constructor for the writer and the
    checker, so they can never drift apart (decision ruling A10). The
    members are the pending candidates (the same derivation that yields
    the survivors after revisions), the sha256 covers the D9-rendered
    block -- the exact bytes K1 injects -- and the token count is the
    D10 estimate the overflow check reads. An empty group is a
    CodeBugError: an empty injection is a bare run and is never
    dispatched or audited.

    Thin shell over ``_audit_row`` (module docstring): checks the pinned
    round; deleted in M4.
    """
    _check_round(round_)
    return _audit_row(store, task_id, round_)


def _block_sha(block: str) -> str:
    return hashlib.sha256(block.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Survivors
# ---------------------------------------------------------------------------

def survivors_and_block(store: Store,
                        task_id: str) -> Tuple[List[Skill], str]:
    """The round-1 injection set: the group's pending members at their
    CURRENT stored versions, and the D9 block those versions render to.

    Pure derivation from the store -- keeps and revised skills are
    pending, deletes and exhausted ones are terminal, so "pending" is
    exactly "survivor". The block is byte-identical to what
    ``audit_row`` would freeze for round 1, which is what lets the no-op
    guard compare round 1's would-be injection against round 0's frozen
    line."""
    members = _pending_group(store, task_id)
    return members, format_skills_block(members)


# ---------------------------------------------------------------------------
# The view-budget fender (moved here from composites/live_attribution.py
# when the attribution shell retired; the readers' family owns it)
# ---------------------------------------------------------------------------

#: Hard char budgets on the G10 / G11 trajectory-view inputs (review fix
#: 2026-10-14, three-reviewer round; re-anchored 2026-10-15 by reviewer C's
#: envelope accounting). The views are otherwise unbounded: the pilot's
#: real mteb group rendered 562k chars across three views (~140k
#: est-tokens), and a context overflow comes back as 400/413 -- a
#: permanent provider refusal that is neither a repair-budget item nor an
#: InfrastructureError, so the first failed group with long trajectories
#: would hard-stop the whole stage (and re-hit it on every resume).
#: 140k chars targets the REQUEST ENVELOPE, not just the input: ~35k
#: est-tokens, calibrated with the endpoint's measured low-estimate
#: factor (~1.38) to ~48k real input tokens, plus the author/rewriter
#: roles' max_tokens=65536 lands the whole request at ~120k tokens --
#: within the largest request envelope this endpoint has ever accepted
#: (~119k) whether or not it counts max_tokens against the window.
#: Clipping keeps each view's head and tail (the tail is where terminal-
#: benchmark failures actually show) and marks the elided middle. The
#: values are pre-registered in the runbook and deliberately not
#: config keys: they are a fender, not an experimental variable (same
#: discipline as the ViewRules constants in the benchmark plugins).
_LIVE_VIEWS_BUDGET_CHARS = 140_000

#: Marker inserted where a view was clipped (both budget constants feed
#: _clip_view; the marker's own length is accounted for in the clip).
_ELIDED_NOTE = "\n[... {n} chars elided to fit the attribution view budget ...]\n"


def _clip_view(text: str, budget: int) -> str:
    """Clip one rendered trajectory view to ``budget`` chars, keeping the
    head (setup) and tail (where TB failures evidence themselves) and
    marking the elided middle. A no-op under budget."""
    if len(text) <= budget:
        return text
    head = max(1, int(budget * 0.6))
    note = _ELIDED_NOTE.format(n=len(text) - budget)
    tail = max(0, budget - head - len(note))
    return text[:head] + note + text[len(text) - tail:]
