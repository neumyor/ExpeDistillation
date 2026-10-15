"""K6: live-run acceptance, one stage function per group step (layer 5).

The live_run companion of composites/acceptance.py (K4): instead of a text
exam, a task's whole group of stage-3 candidate skills is injected into the
weak model's real execution, run ``n_attempts`` times, and compared against
the stage-1 weak bare baseline by a named improvement rule. Two rounds are
pinned (user ruling 8): round 0 on the full group; if the group does not
beat the baseline, one G10 group attribution plus per-skill G11 revisions,
then round 1 on the survivors; a second miss discards them.

This module is a LIBRARY of per-group phase functions, not a driver: the
round sequencing, the K1 dispatch, the re-dispatch set (non-terminal AND
incomplete groups), the injection_audit first write (its row CONTENT comes
from ``audit_row``), the accepted-shortfall records and the summary belong
to stages/stage4_live_run.py, which calls each function with explicit
parameters. Every function here derives its state from disk and is
idempotent, so a resumed stage re-runs the same calls and only the work
that was never persisted happens again.

State is derived from disk only (decision section 2.4 -- no new state
objects):

* round r of a group is executed: every expected unit (task x n_attempts,
  the FULL set -- a skip-reduced expected set is forbidden, the D3
  expected-accounting must see the holes as infra) is stored in the
  round store or is an accepted hole;
* the verdict of round r: recomputed from the baseline counts x the
  round store x the named rule; a stored verdicts.jsonl first row for
  the (task, round) that disagrees with the recomputation is a
  CodeBugError (anti-tamper on the store);
* an attribution happened: an attributions.jsonl row for the (task,
  round) exists -- first judgment sticks, G10 is never asked twice;
* a revision was applied: the stored version moved past the attribution
  row's recorded version (version == from_version -> apply,
  from_version + 1 -> skip, anything else -> CodeBugError);
* a group's terminal state: its members' statuses, completed idempotently
  by ``finalize_group`` (the finalization sweep's execution body).

Persistence order is what makes resume equal an uninterrupted run:

* the verdict row is appended BEFORE the group's statuses are committed,
  so the sweep can finish any commit the crash cut short;
* the attribution row is appended atomically BEFORE any revision acts
  on it (a plan that never landed is asked again -- see below);
* a successful revision is store.save'd immediately (the version number
  is the proof of completion), and so is every discard;
* the injection_audit row is written once by the stage BEFORE the
  round's dispatch (first-write frozen); every later read -- the no-op
  guard's sha, the verdict row's token count -- goes back to that frozen
  row, and a re-derivation before a re-dispatch must equal it byte for
  byte (``audit_row`` is the single constructor for both sides).

Known non-equivalence (the standing K3/K4 exemption): a model call whose
result was not yet persisted is asked again on resume. That covers a G10
plan lost between the call and its ledger append, a G11 rewrite lost
between the call and its save, and -- because the attribution budget's
exhausting ledger line is deliberately written AFTER the group's terminal
discards (the discard is the truth, the line is its receipt; decision
section 2.5) -- the last attribution try of a run that crashed between
the exhausting failure and the completed discards.

Discard-reason vocabulary (decision ruling A13; the free-form
Skill.discard_reason field carries it):
``weak_already_knows`` / ``live_no_improvement`` / ``live_revision_deleted``
/ ``model_output_error`` / ``live_undetermined`` / ``live_injection_overflow``.
"""

import hashlib
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

from expedistill.blocks.attribution import attribute_failure, revise_skill
from expedistill.blocks.author import skill_markers
from expedistill.blocks.metrics import compute_task_metrics
from expedistill.blocks.rules import get_acceptance_improve
from expedistill.blocks.view import render_trajectory_view
from expedistill.composites.skill_status import discard_skill
from expedistill.core import ids
from expedistill.core.models import Skill, Trajectory
from expedistill.infra.reliability import (
    CodeBugError,
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import (
    Store,
    append_jsonl,
    read_json,
    read_jsonl,
)
from expedistill.inject.skills import estimate_tokens, format_skills_block
from expedistill.llm.call import ModelHandle

__all__ = [
    "ATTRIBUTION_FAILURES_LEDGER",
    "ATTRIBUTIONS_LEDGER",
    "FAILURE_STAGE",
    "INJECTION_AUDIT_NAME",
    "LIVE_INJECTION_OVERFLOW",
    "LIVE_NO_IMPROVEMENT",
    "LIVE_REVISION_DELETED",
    "LIVE_UNDETERMINED",
    "MODEL_OUTPUT_ERROR",
    "REVISION_FAILURES_LEDGER",
    "VERDICTS_LEDGER",
    "WEAK_ALREADY_KNOWS",
    "apply_revisions",
    "audit_row",
    "attribute_group",
    "expected_units",
    "finalize_group",
    "judge_round",
    "load_baseline",
    "no_op_guard",
    "pending_groups",
    "round_dir",
    "skip_ids_for",
    "survivors_and_block",
]

#: Live-run subtree inside the stage-4 directory (decision section 2.2):
#: round stores and the ledgers all live under it.
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

#: discard_reason values (decision ruling A13). ``weak_already_knows``
#: crosses modes (exam: the bare answer was right; live: the baseline was
#: all-success -- the evidence base differs, the README vocabulary notes
#: it); ``live_no_improvement`` is dual (no-op guard vs round-1 miss) and
#: the verdicts row's round tells them apart.
WEAK_ALREADY_KNOWS = "weak_already_knows"
LIVE_INJECTION_OVERFLOW = "live_injection_overflow"
LIVE_UNDETERMINED = "live_undetermined"
LIVE_NO_IMPROVEMENT = "live_no_improvement"
LIVE_REVISION_DELETED = "live_revision_deleted"
MODEL_OUTPUT_ERROR = "model_output_error"

#: Stage label for this module's failed_units.jsonl lines (an infra
#: failure of one skill's revision); the stage reuses it for group-level
#: units so one grep finds the whole stage's failures.
FAILURE_STAGE = "stage4_live"

#: Composite-level repair budget of one G10 call (decision section 2.3),
#: on top of call_model's own format repairs. The same budget as K4's
#: rewrites; each failed try is ledgered as it happens so a resumed run
#: continues the same budget.
_ATTRIBUTION_ATTEMPTS = 2

#: Composite-level repair budget of one G11 revision for one skill.
_REVISION_ATTEMPTS = 2

#: Rounds are pinned to two (user ruling 8 -- no max_revision_rounds
#: config), so every round-taking function rejects anything else loudly
#: instead of silently inventing a third round the design never had.
_ROUNDS = (0, 1)

#: Rules for which "the baseline is all-success" mathematically implies
#: "the live arm cannot be better", so the weak_already_knows zero-
#: execution short-circuit may fire (decision section 2.2: strict-family
#: only). Registering a new improve rule must consciously revisit this
#: set -- an unconditional guard would short-circuit groups under a rule
#: where a rate-1.0 baseline can still lose (pass@N-style rules).
_ALL_SUCCESS_SAFE_RULES = frozenset({"strict_rate_gain",
                                     "first_success_gain"})

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
_FAILURE_VIEW_BUDGET_CHARS = 80_000

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


# ---------------------------------------------------------------------------
# Paths and small readers
# ---------------------------------------------------------------------------

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


def _round_store(store: Store, round_: int) -> Store:
    """The round's B3 store (the stage-6 arm-store pattern: one store per
    round, so round 1's re-execution never collides with round 0's
    trajectories)."""
    return Store(round_dir(store, round_))


def _check_round(round_: int) -> None:
    if round_ not in _ROUNDS:
        raise CodeBugError(
            f"live rounds are pinned to {list(_ROUNDS)} (user ruling 8), "
            f"got {round_!r}")


def _rows_of(store: Store, ledger: str, task_id: str,
             round_: Optional[int] = None) -> List[Dict[str, Any]]:
    """One task's rows of a live-run ledger, in append order."""
    rows = [row for row in read_jsonl(_ledger_path(store, ledger))
            if row.get("task_id") == task_id]
    if round_ is not None:
        rows = [row for row in rows if row.get("round") == round_]
    return rows


def _first_row(store: Store, ledger: str, task_id: str,
               round_: int) -> Optional[Dict[str, Any]]:
    """The first ledger row of a (task, round) -- the one that sticks.

    Only one row is ever appended per (task, round), but reading the first
    (not the last) is what makes a hand-edited duplicate ledger fail
    loudly in the recompute check instead of silently switching verdicts.
    """
    for row in _rows_of(store, ledger, task_id, round_):
        return row
    return None


def _audit_row_of(store: Store, round_: int,
                  task_id: str) -> Optional[Dict[str, Any]]:
    """The frozen injection-audit row of one group (the stage wrote it
    before the round's dispatch); None when the round never got that
    far."""
    for row in read_jsonl(round_dir(store, round_) / INJECTION_AUDIT_NAME):
        if row.get("task_id") == task_id:
            return row
    return None


def _require_audit_row(store: Store, round_: int,
                       task_id: str) -> Dict[str, Any]:
    """The frozen audit row, or a CodeBugError: judging or guarding a
    round that has no frozen audit row means the first write and the
    dispatch disagree -- exactly the hole the freeze exists to close."""
    row = _audit_row_of(store, round_, task_id)
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


def _round_trajectories(store: Store, task_id: str,
                        round_: int) -> List[Trajectory]:
    """Every trajectory one round stored for one task, attempt order.

    Attribution, revision and judging all read the same stored views, so
    the read lives once. A round directory that does not exist means the
    round was never dispatched -- the caller is ahead of the stage, which
    is a bug, not a resume state (a dispatched round's directory exists
    even when every unit was an accepted hole)."""
    if not round_dir(store, round_).exists():
        raise CodeBugError(
            f"round {round_} of task {task_id!r} was never dispatched: "
            "attribution, revision and judging read its stored "
            "trajectories")
    round_store = _round_store(store, round_)
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
    no valid attempt at all is a ConfigError: both registered task_select
    rules require ``valid > 0``, so the data contradicts the frozen split
    (decision ruling A17 -- the guard's unreachability depends on the
    registered rules; swapping in a rule that admits valid==0 makes it
    reachable).

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
    if counts["valid"] == 0:
        raise ConfigError(
            f"live-run baseline of task {task_id!r} has no valid attempt "
            f"({counts}); every registered task_select rule requires "
            "valid > 0, so the frozen split and the store disagree -- "
            "the run's task set must be rebuilt in a new run directory")
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
    """
    _check_round(round_)
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


def _block_sha(block: str) -> str:
    return hashlib.sha256(block.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Judging
# ---------------------------------------------------------------------------

def judge_round(
    *,
    store: Store,
    task_id: str,
    round_: int,
    rule_name: str,
    baseline_counts: Dict[str, int],
    n_attempts: int,
    skip_ids: Iterable[str],
    aux_scorer: Optional[Callable[[Trajectory], bool]] = None,
    terminal: Optional[str] = None,
    max_injection_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Judge one group's round and append its verdicts.jsonl row; return
    the stored row (the first row for this (task, round) once written).

    The judgment itself is a pure function -- baseline counts (from
    ``load_baseline``) x the round store's counts (D3 under the full
    expected set, accepted holes included as infra) x the named rule from
    ACCEPTANCE_IMPROVE. A stored first row for the (task, round) is
    recomputed and compared: agreement returns the stored row unchanged
    (append-once, idempotent), disagreement is a CodeBugError -- the
    store was tampered with or the caller's inputs drifted, and either
    way silently re-judging would bury it (decision section 2.4).

    ``terminal`` records a zero-execution terminal instead of judging:
    ``weak_already_knows`` (the entry check found the baseline
    all-success AND the rule is one a rate-1.0 baseline can never lose
    to -- both verified here, else CodeBugError, so the strict-family
    limitation cannot be forgotten by a future rule) or
    ``live_injection_overflow`` (the frozen audit row's estimate exceeds
    ``max_injection_tokens`` -- verified here against the audit row, so
    the verdict row never claims an overflow its evidence does not
    show). ``live_undetermined`` is NOT a caller input: with no terminal
    given, a live sample that is nothing but accepted holes
    short-circuits to it before any rule runs -- no judgment without a
    live denominator. Zero-execution rows keep the schema's field set
    with the counts and ``better`` null and the terminal's name in a
    ``verdict`` field -- the verdicts file is the audit's complete fact
    source (decision section 2.4).

    The row is appended BEFORE any status is committed (``finalize_group``
    does the committing), so the finalization sweep can always finish a
    commit the crash cut short.
    """
    _check_round(round_)
    get_acceptance_improve(rule_name)  # unknown rule names fail loudly here
    skip = set(skip_ids)
    expected_ids = _unit_ids(task_id, n_attempts)
    judged_with_shortfall = bool(skip.intersection(expected_ids))

    if terminal == LIVE_UNDETERMINED:
        raise CodeBugError(
            "live_undetermined is derived by judge_round (a live sample "
            "that is all accepted holes), never asserted by the caller")
    if terminal is not None and terminal not in (WEAK_ALREADY_KNOWS,
                                                 LIVE_INJECTION_OVERFLOW):
        raise CodeBugError(
            f"unknown zero-execution terminal {terminal!r}; expected one "
            f"of {(WEAK_ALREADY_KNOWS, LIVE_INJECTION_OVERFLOW)}")

    if terminal == WEAK_ALREADY_KNOWS:
        if rule_name not in _ALL_SUCCESS_SAFE_RULES:
            raise CodeBugError(
                f"weak_already_knows short-circuit is limited to the "
                f"strict-family rules {sorted(_ALL_SUCCESS_SAFE_RULES)} "
                f"(decision section 2.2); rule {rule_name!r} may still "
                "improve on an all-success baseline, so the round must "
                "be executed")
        if (baseline_counts["valid"] <= 0
                or baseline_counts["success"] != baseline_counts["valid"]):
            raise CodeBugError(
                f"weak_already_knows asserted for task {task_id!r} with "
                f"baseline {baseline_counts}: the baseline is not "
                "all-success, so there is something to prove and the "
                "round must be executed")
        row = _verdict_row(task_id, round_, rule_name, baseline=None,
                           live=None, better=None, verdict=WEAK_ALREADY_KNOWS,
                           injection_tokens=None,
                           judged_with_shortfall=False, aux=None)
    elif terminal == LIVE_INJECTION_OVERFLOW:
        audit = _require_audit_row(store, round_, task_id)
        if (max_injection_tokens is None
                or audit["injection_tokens"] <= max_injection_tokens):
            raise CodeBugError(
                f"live_injection_overflow asserted for task {task_id!r} "
                f"but the frozen audit row shows {audit['injection_tokens']}"
                f" tokens against a limit of {max_injection_tokens!r}: "
                "the overflow must be the audit row's own evidence")
        row = _verdict_row(task_id, round_, rule_name, baseline=None,
                           live=None, better=None,
                           verdict=LIVE_INJECTION_OVERFLOW,
                           injection_tokens=audit["injection_tokens"],
                           judged_with_shortfall=False, aux=None)
    else:
        trajectories = _round_trajectories(store, task_id, round_)
        stored = {t.trajectory_id for t in trajectories}
        incomplete = [unit for unit in expected_ids
                      if unit not in stored and unit not in skip]
        if incomplete:
            # Judging an incomplete group would count its missing units as
            # infra and silently shrink the verdict; the stage judges only
            # complete groups (resume re-dispatches the rest).
            raise CodeBugError(
                f"round {round_} of task {task_id!r} is incomplete: "
                f"units {incomplete} are neither stored nor accepted "
                "holes")
        live_counts = compute_task_metrics(
            trajectories, expected_units(task_id, n_attempts),
            aux_scorer=aux_scorer)[task_id]
        audit = _require_audit_row(store, round_, task_id)
        if all(unit in skip for unit in expected_ids):
            # The precise condition (decision section 2.5): every expected
            # unit is an accepted hole -- nothing was executed, so there is
            # no live denominator and no rule to run. (Completeness passed
            # above, and K1 never stores an infra-exhausted unit, so this
            # is also exactly the live_counts["valid"] == 0 case.)
            row = _verdict_row(task_id, round_, rule_name, baseline=None,
                               live=None, better=None,
                               verdict=LIVE_UNDETERMINED,
                               injection_tokens=audit["injection_tokens"],
                               judged_with_shortfall=True, aux=None)
        else:
            rule = get_acceptance_improve(rule_name)
            better = bool(rule(baseline_counts, live_counts, n_attempts))
            row = _verdict_row(
                task_id, round_, rule_name,
                baseline={"valid": baseline_counts["valid"],
                          "success": baseline_counts["success"]},
                live={"valid": live_counts["valid"],
                      "success": live_counts["success"]},
                better=better, verdict=None,
                injection_tokens=audit["injection_tokens"],
                judged_with_shortfall=judged_with_shortfall,
                aux=_aux_slot(aux_scorer, baseline_counts, live_counts))

    stored_row = _first_row(store, VERDICTS_LEDGER, task_id, round_)
    if stored_row is not None:
        if stored_row != row:
            raise CodeBugError(
                f"recomputed verdict for task {task_id!r} round {round_} "
                f"disagrees with the stored first row:\nstored: "
                f"{stored_row}\nrecomputed: {row}\nThe store or the "
                "caller's inputs changed under the ledger; refusing to "
                "re-judge")
        return stored_row
    append_jsonl(_ledger_path(store, VERDICTS_LEDGER), row)
    return row


def _verdict_row(task_id: str, round_: int, rule_name: str, *,
                 baseline: Optional[Dict[str, int]],
                 live: Optional[Dict[str, int]], better: Optional[bool],
                 verdict: Optional[str], injection_tokens: Optional[int],
                 judged_with_shortfall: bool,
                 aux: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """One verdicts.jsonl row in the frozen shape (decision section 2.4).

    Judged rows carry exactly the schema's ten fields (``better`` decides
    them); zero-execution rows carry the same fields with the counts and
    ``better`` null plus the terminal's name in ``verdict`` -- key
    presence is what tells a consumer which kind it is reading."""
    row: Dict[str, Any] = {
        "schema": "live-verdict-v1",
        "task_id": task_id,
        "round": round_,
        "rule": rule_name,
        "baseline": baseline,
        "live": live,
        "better": better,
        "injection_tokens": injection_tokens,
        "judged_with_shortfall": judged_with_shortfall,
        "aux": aux,
    }
    if verdict is not None:
        row["verdict"] = verdict
    return row


def _aux_slot(
    aux_scorer: Optional[Callable[[Trajectory], bool]],
    baseline_counts: Dict[str, int],
    live_counts: Dict[str, int],
) -> Optional[Dict[str, Any]]:
    """The verdict row's aux slot (user ruling 11): the plugin-declared
    auxiliary metric's counts recorded ALONGSIDE the primary, never part
    of the criterion; null when the run declared no aux metric. Both
    arms are recorded so the audit can read either side's aux rate from
    the row alone."""
    if aux_scorer is None:
        return None
    if "aux_success" not in baseline_counts:
        # The wiring forgot the scorer on the baseline side; a null there
        # would fake a measured zero.
        raise CodeBugError(
            "judge_round got baseline counts without aux_success while an "
            "aux_scorer was given: load_baseline must be called with the "
            "same scorer")
    return {
        "baseline": {"valid": baseline_counts["valid"],
                     "aux_success": baseline_counts["aux_success"]},
        "live": {"valid": live_counts["valid"],
                 "aux_success": live_counts["aux_success"]},
    }


# ---------------------------------------------------------------------------
# Attribution (G10)
# ---------------------------------------------------------------------------

def attribute_group(
    *,
    store: Store,
    task_id: str,
    round_: int,
    n_attempts: int,
    task_instruction: str,
    weak_profile: str,
    author_handle: ModelHandle,
    view_rules: Any = None,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Optional[Dict[str, Any]]:
    """Attribute one group's failed round (G10) and append its
    attributions.jsonl row; return the row, or None when the repair budget
    ran out (the group was terminally discarded as model_output_error).

    First judgment sticks: a stored row for the (task, round) is returned
    without any model call. The G10 inputs are composed here so every
    call sees the same bytes -- ``baseline_summary`` from the re-loaded
    stage-1 baseline (one status line per attempt), ``live_views`` from
    every trajectory the round stored for this task (successes included:
    the attributor must see what the skills did, not only that they
    failed), and the frozen audit row's injection sha. The skill versions
    in the row are read from the store at append time, never taken from
    the model (decision ruling A3) -- the plan is applied against exactly
    the versions recorded here. ``task_instruction`` is the rendered task
    prompt the student actually saw (the stage renders it; under TB the
    Task record's instruction is just the name, decision section 2.3).

    The repair budget is ``_ATTRIBUTION_ATTEMPTS`` calls. Each failed try
    is ledgered to attribution_failures.jsonl as it happens, so a
    resumed run continues the same budget. On exhaustion the group's
    terminal discards land FIRST (the discard is the truth a resume
    reads; the failure line is its receipt -- decision section 2.5),
    then the exhausting row with ``exhausted: true`` is appended.
    Infrastructure errors are not budget items: they propagate and the
    group stays pending for the stage to record and retry.
    """
    _check_round(round_)
    stored_row = _first_row(store, ATTRIBUTIONS_LEDGER, task_id, round_)
    if stored_row is not None:
        return stored_row
    members = _require_group(store, task_id)

    failures = _rows_of(store, ATTRIBUTION_FAILURES_LEDGER, task_id,
                        round_)
    if (any(row.get("exhausted") for row in failures)
            or len(failures) >= _ATTRIBUTION_ATTEMPTS):
        # Unreachable through the stage (the exhausted row follows the
        # discards, so exhaustion with pending members only happens when
        # a crash ate the discards' tail); completing the discards here
        # keeps this function total and idempotent.
        _discard_group(store, members, MODEL_OUTPUT_ERROR)
        return None

    baseline_trajectories, _ = load_baseline(store, task_id, n_attempts)
    baseline_summary = "\n".join(
        f"attempt {traj.attempt_index}: {traj.status}"
        for traj in baseline_trajectories)
    round_trajs = _round_trajectories(store, task_id, round_)
    per_view_budget = _LIVE_VIEWS_BUDGET_CHARS // max(1, len(round_trajs))
    live_views = "\n\n".join(
        _clip_view(render_trajectory_view(traj, view_rules),
                   per_view_budget)
        for traj in round_trajs)
    audit = _require_audit_row(store, round_, task_id)

    for try_index in range(len(failures), _ATTRIBUTION_ATTEMPTS):
        try:
            plan = attribute_failure(
                members, task_instruction, baseline_summary, live_views,
                weak_profile, author_handle, usage=usage, run_dir=run_dir)
        except ModelOutputError as exc:
            if try_index + 1 >= _ATTRIBUTION_ATTEMPTS:
                # Terminal state first, ledger row second (module
                # docstring): a crash in between leaves a partially
                # discarded group whose budget rows say "one try left",
                # and the last try is simply asked again.
                _discard_group(store, members, MODEL_OUTPUT_ERROR)
                append_jsonl(
                    _ledger_path(store, ATTRIBUTION_FAILURES_LEDGER),
                    _failure_row(task_id, round_, try_index=try_index,
                                 skill_id=None, skill_version=None,
                                 exhausted=True, message=str(exc)))
                return None
            append_jsonl(
                _ledger_path(store, ATTRIBUTION_FAILURES_LEDGER),
                _failure_row(task_id, round_, try_index=try_index,
                             skill_id=None, skill_version=None,
                             exhausted=False, message=str(exc)))
            continue
        versions = {skill.skill_id: store.load(
                        "skills", skill.skill_id, Skill).version
                    for skill in members}
        row = {
            "schema": "live-attribution-v1",
            "task_id": task_id,
            "round": round_,
            "diagnosis": plan["diagnosis"],
            "skill_actions": [
                {"skill_id": entry["skill_id"],
                 "version": versions[entry["skill_id"]],
                 "action": entry["action"],
                 "problem": entry["problem"]}
                for entry in plan["skill_actions"]],
            "injection_block_sha256": audit["injection_block_sha256"],
        }
        append_jsonl(_ledger_path(store, ATTRIBUTIONS_LEDGER), row)
        return row

    raise CodeBugError(  # pragma: no cover - the loop always returns
        f"attribution budget loop for {task_id!r} fell through")


def _discard_group(store: Store, members: List[Skill], reason: str) -> None:
    """Terminalize every member that is still pending, in skill_id order;
    already-terminal members keep their own (earlier) truth."""
    for skill in members:
        stored = store.load("skills", skill.skill_id, Skill)
        if stored.status == "pending":
            discard_skill(store, stored, reason)


def _failure_row(task_id: str, round_: int, *, try_index: int,
                 skill_id: Optional[str], skill_version: Optional[int],
                 exhausted: bool, message: str) -> Dict[str, Any]:
    """One attribution_failures / revision_failures line (K4's
    rewrite_failures shape): one row per failed repair try, the
    exhausting try marked, so the budget survives crashes and the audit
    sees every spent call."""
    return {
        "schema": "live-repair-failure-v1",
        "task_id": task_id,
        "round": round_,
        "skill_id": skill_id,
        "skill_version": skill_version,
        "try_index": try_index,
        "exhausted": exhausted,
        "message": " ".join(str(message).split()),
    }


# ---------------------------------------------------------------------------
# Applying a plan (G11)
# ---------------------------------------------------------------------------

def apply_revisions(
    *,
    store: Store,
    task_id: str,
    round_: int,
    rewriter_handle: ModelHandle,
    weak_profile: str,
    view_rules: Any = None,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> List[Dict[str, str]]:
    """Apply the group's stored attribution plan to its skills, one skill
    at a time; return the per-skill outcomes (``{"skill_id", "action",
    "outcome"}``, in plan order).

    The plan is read back from attributions.jsonl (first judgment sticks)
    -- never re-asked. keep leaves the skill alone; delete terminalizes it
    as ``live_revision_deleted`` immediately; revise runs G11 with the
    composite-level marker check (``skill_markers(new) ==
    skill_markers(old)``; an unmarked original is passed through, the
    K4/K5 precedent). A marker failure is a ModelOutputError inside the
    skill's own repair budget of ``_REVISION_ATTEMPTS`` tries, and a
    budget exhaustion discards ONLY that skill (model_output_error)
    while the group continues (decision section 2.5). A successful
    revision bumps the version, appends the rewrite_history entry and
    saves immediately -- the version number is the proof of completion.

    Replay guard per plan entry (decision section 2.4): version ==
    from_version means apply, == from_version + 1 means the revision
    already landed (skip -- the version number is the completion
    proof), anything else is a CodeBugError. Terminal skills are
    skipped with ``skipped_terminal`` -- a discard that an interrupted
    budget run landed is the truth, and no plan entry rewrites it.

    An InfrastructureError from one skill's revision is recorded to
    failed_units.jsonl (stage label ``FAILURE_STAGE``) and that skill
    stays pending for the resume while the rest of the group continues
    (decision section 2.5); the ``infra_failed`` outcome is what tells
    the stage to raise at its end.
    """
    _check_round(round_)
    row = _first_row(store, ATTRIBUTIONS_LEDGER, task_id, round_)
    if row is None:
        raise CodeBugError(
            f"no attribution row for task {task_id!r} round {round_}: "
            "attribute the group before applying its plan")
    failed = [traj for traj in _round_trajectories(store, task_id, round_)
              if traj.status == "failure"]
    per_view_budget = _FAILURE_VIEW_BUDGET_CHARS // max(1, len(failed))
    failure_view = "\n\n".join(
        _clip_view(render_trajectory_view(traj, view_rules),
                   per_view_budget)
        for traj in failed)
    recorder = UnitFailureRecorder(
        Path(run_dir) if run_dir is not None else store.run_dir)

    outcomes: List[Dict[str, str]] = []
    for entry in row["skill_actions"]:
        skill_id = entry["skill_id"]
        action = entry["action"]
        try:
            stored = store.load("skills", skill_id, Skill)
        except FileNotFoundError:
            raise CodeBugError(
                f"attribution row names skill {skill_id!r} but the store "
                "has no such skill") from None
        if stored.status != "pending":
            # Already terminal: a delete applied before a crash, or a
            # discard an interrupted exhaustion run landed. The stored
            # status is the truth; no plan entry rewrites it.
            outcomes.append({"skill_id": skill_id, "action": action,
                             "outcome": "skipped_terminal"})
            continue
        if action == "keep":
            outcomes.append({"skill_id": skill_id, "action": action,
                             "outcome": "kept"})
            continue
        if action == "delete":
            discard_skill(store, stored, LIVE_REVISION_DELETED)
            outcomes.append({"skill_id": skill_id, "action": action,
                             "outcome": "deleted"})
            continue
        # action == "revise"
        from_version = entry["version"]
        if stored.version == from_version + 1:
            outcomes.append({"skill_id": skill_id, "action": action,
                             "outcome": "already_revised"})
            continue
        if stored.version != from_version:
            raise CodeBugError(
                f"skill {skill_id!r} is at version {stored.version} but "
                f"the attribution row recorded {from_version}: neither "
                "the pre-revision nor the post-revision version -- the "
                "store drifted from the ledger")
        attribution_report = (f"{row['diagnosis']}\n\n"
                              f"This skill's problem: {entry['problem']}")
        outcome = _revise_one(
            store, task_id, round_, stored, from_version,
            attribution_report, failure_view, weak_profile,
            rewriter_handle, recorder, usage=usage, run_dir=run_dir)
        outcomes.append({"skill_id": skill_id, "action": action,
                         "outcome": outcome})
    return outcomes


def _revise_one(
    store: Store,
    task_id: str,
    round_: int,
    stored: Skill,
    from_version: int,
    attribution_report: str,
    failure_view: str,
    weak_profile: str,
    rewriter_handle: ModelHandle,
    recorder: UnitFailureRecorder,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> str:
    """Run one skill's G11 revision inside its own repair budget; return
    the outcome recorded for it."""
    prior = [row for row in _rows_of(store, REVISION_FAILURES_LEDGER,
                                     task_id, round_)
             if row.get("skill_id") == stored.skill_id
             and row.get("skill_version") == from_version]
    for try_index in range(len(prior), _REVISION_ATTEMPTS):
        try:
            result = revise_skill(
                stored.content, attribution_report, failure_view,
                weak_profile, rewriter_handle, usage=usage, run_dir=run_dir)
            new_text = result["skill_text"]
            markers = skill_markers(stored.content)
            # The marker-preservation check is composite-level on purpose
            # (blocks may not import each other, and G11's block stays
            # single-purpose); and a structural failure must ride the
            # SAME ModelOutputError budget as a format failure -- the K5
            # lesson that a structural raise outside the budget's scope
            # kills whole units.
            if markers is not None and skill_markers(new_text) != markers:
                raise ModelOutputError(
                    "LIVE_SKILL_REVISION: the rewritten skill must keep "
                    f"the same four bracket sections as the original "
                    f"({' / '.join(markers)}), got "
                    f"{skill_markers(new_text)!r}")
        except InfrastructureError as exc:
            # Not a budget item: the work is valid, the service was not.
            # The skill stays pending; the resume re-enters this function
            # and the replay guard keeps the already-landed siblings.
            recorder.record(stored.skill_id, FAILURE_STAGE, exc, exc)
            return "infra_failed"
        except ModelOutputError as exc:
            if try_index + 1 >= _REVISION_ATTEMPTS:
                # Only this skill is discarded; the group continues
                # (decision section 2.5). Terminal state first, then the
                # closing ledger row, like the attribution path.
                discard_skill(store, stored, MODEL_OUTPUT_ERROR)
                append_jsonl(
                    _ledger_path(store, REVISION_FAILURES_LEDGER),
                    _failure_row(task_id, round_, try_index=try_index,
                                 skill_id=stored.skill_id,
                                 skill_version=from_version, exhausted=True,
                                 message=str(exc)))
                return "discarded_model_output_error"
            append_jsonl(
                _ledger_path(store, REVISION_FAILURES_LEDGER),
                _failure_row(task_id, round_, try_index=try_index,
                             skill_id=stored.skill_id,
                             skill_version=from_version, exhausted=False,
                             message=str(exc)))
            continue
        stored.rewrite_history.append({
            "content": stored.content,
            "error_category": result["failure_category"],
            "reason": result["revision_reason"],
        })
        stored.content = new_text
        stored.version = from_version + 1
        store.save(stored, "skills")
        return "revised"
    raise CodeBugError(  # pragma: no cover - the loop always returns
        f"revision budget loop for {stored.skill_id!r} fell through")


# ---------------------------------------------------------------------------
# No-op guard, survivors, finalization
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


def no_op_guard(store: Store, task_id: str, round_: int) -> bool:
    """Fire the no-op guard after round ``round_``'s revisions: True when
    the survivors' current block is byte-identical to the round's FROZEN
    audit row, in which case every survivor is discarded as
    ``live_no_improvement`` and round 1 must not run.

    The comparison's data source is the frozen audit line, never a
    re-render of the round's pre-revision state -- that state is gone
    (rewrites bump in place), and re-sampling a changed group until it
    round-trips to a lucky pass is exactly what the guard exists to
    prevent (decision ruling A4). A group with no survivors returns
    False: there is no round 1 to skip, the members' own discards are
    the terminal state (and an empty block can never equal the frozen
    row of a dispatched round, so the empty case needs no special
    form)."""
    _check_round(round_)
    survivors, block = survivors_and_block(store, task_id)
    audit = _require_audit_row(store, round_, task_id)
    if _block_sha(block) != audit["injection_block_sha256"]:
        return False
    for skill in survivors:
        discard_skill(store, skill, LIVE_NO_IMPROVEMENT)
    return True


def finalize_group(store: Store, task_id: str) -> Dict[str, Any]:
    """Commit one group's decided terminal state idempotently; return
    ``{"outcome", "reason", "committed"}``.

    This is the finalization sweep's execution body (decision section
    2.4): a group whose verdict row already decided its fate but whose
    status commits a crash cut short is finished here, and the in-flow
    calls after each verdict use the same function. The decision is
    read from the verdicts ledger, in this precedence:

    * a zero-execution row (weak_already_knows / live_injection_overflow
      / live_undetermined) discards every pending member with that
      reason;
    * a round-1 row accepts the survivors (better) or discards them as
      ``live_no_improvement`` (not better -- the round field is what
      tells this discard apart from the no-op guard's, ruling A13);
    * a round-0 row that is better accepts every pending member;
    * a round-0 row that is not better decides nothing -- the
      attribution/revision pipeline still owes the group work, and a
      sweep that "finished" it here would bury pending revisions.

    Commits land in skill_id order, pending members only: already
    terminal members keep their own truth, which is what makes a second
    call a no-op."""
    rows = _rows_of(store, VERDICTS_LEDGER, task_id)
    decision: Optional[Tuple[str, str]] = None
    for row in rows:
        if "verdict" in row:
            decision = ("discarded", row["verdict"])
            break
    if decision is None:
        round1 = [row for row in rows if row.get("round") == 1]
        if round1:
            if round1[0]["better"]:
                decision = ("accepted", "")
            else:
                decision = ("discarded", LIVE_NO_IMPROVEMENT)
        else:
            round0 = [row for row in rows if row.get("round") == 0]
            if round0 and round0[0]["better"]:
                decision = ("accepted", "")
    if decision is None:
        return {"outcome": "pending", "reason": None, "committed": []}

    direction, reason = decision
    committed: List[str] = []
    for skill in _pending_group(store, task_id):
        if direction == "accepted":
            skill.status = "accepted"
            store.save(skill, "skills")
        else:
            discard_skill(store, skill, reason)
        committed.append(skill.skill_id)
    return {"outcome": direction, "reason": reason if reason else None,
            "committed": committed}
