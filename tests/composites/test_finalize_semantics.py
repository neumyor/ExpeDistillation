"""Pin the de-rounded ``_finalize_group`` decision semantics (K6 core).

M2 (6b86e65) generalized the finalization decision from the pinned
(0, 1) rounds to an arbitrary ``rounds`` sequence, and the equivalence
check that landed it (821 ledger-row combinations) was a one-off script
-- review C-1 (docs/reviews/execute_learn_m2_review_20261016_C.md):
a conclusion the commit message depends on must live in version
control. This file is the standing replacement, and the guard M4 wants
when it deletes the shells: every test drives the CORE directly, with
an explicit verdicts path and explicit round labels, so deleting the
shell layer never touches this file.

Every expected value is HAND-DERIVED from the decision rules the module
docstrings state (``finalize_group``'s precedence list and
``_finalize_group``'s core docstring) and written as a literal in the
case tables -- never as "whatever the implementation returns":

* D1 any zero-execution row (a row carrying ``verdict``) discards every
  pending member with that reason; the scan follows append order and
  ignores round labels entirely;
* D2 the latest label of ``rounds`` that owns at least one row is
  decisive -- earlier labels with rows are ignored;
* D3 on the LAST label a better row accepts the group, a miss discards
  it as ``live_no_improvement``;
* D4 on an earlier label a better row accepts, a miss decides nothing
  (the revision pipeline still owes the group work);
* D5 within the decisive label the FIRST row in append order is the
  judgment;
* D6 rows of rounds outside ``rounds`` (and rows carrying no round key)
  never join the round decision -- only D1 ever sees them.

Each case asserts the returned dict, every member's stored terminal
state, that the verdicts ledger it read is identical afterwards
(finalize never rewrites its input), and that a second sweep commits
nothing and changes nothing (commits target pending members only). The
public shell over the pinned rounds stays pinned in
test_live_acceptance.py; this file owns the core. No model handles are
needed -- finalize reads ledgers and the store only.
"""

import pytest

from expedistill.composites import (
    live_verdicts as lv,
)
from expedistill.composites.skill_status import discard_skill
from expedistill.core.ids import (
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill
from expedistill.infra.store import Store, append_jsonl, read_jsonl

BENCHMARK = "tb"
TASK_ID = make_task_id(BENCHMARK, "t1")
OTHER_TASK_ID = make_task_id(BENCHMARK, "t2")
RULE = "strict_rate_gain"

#: The rounds today's shell pins (user ruling 8). The core takes any
#: sequence; the non-(0, 1) suites below are what review B's gap note
#: asked to have pinned before M4 consumes them.
ROUNDS = (0, 1)

MARKED = "\n".join([
    "[When to use] The service rejects connections.",
    "[Steps]",
    "1. Check the service status.",
    "2. Restart the service.",
    "[Verify] The status command reports running.",
    "[Do not use when] The status command already reports running.",
])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def seed_group(store, task_id=TASK_ID, count=2):
    """Store one task's pending candidate group (stage-3 ids, skill_id
    order). Finalize derives everything from the ledger and the group,
    so the run directory needs nothing else."""
    members = []
    for segment in range(count):
        subtask_id = make_subtask_id(
            make_trajectory_id(task_id, "strong", 0), segment)
        skill = Skill(skill_id=make_skill_id(subtask_id), content=MARKED,
                      subtask_id=subtask_id, source_subtasks=[subtask_id])
        store.save(skill, "skills")
        members.append(skill)
    return members


def ledger_row(round_, judgment, task_id=TASK_ID):
    """One verdicts row in the frozen ``live-verdict-v1`` shape.

    ``judgment`` True/False builds a judged row (its ``better`` decides
    it); a zero-execution verdict string builds the terminal row
    (``better`` null, the string in ``verdict`` -- key presence is what
    tells the reader which kind it is). ``round_`` of None builds the
    no-round-key shape."""
    zero_execution = isinstance(judgment, str)
    row = {
        "schema": "live-verdict-v1",
        "task_id": task_id,
        "rule": RULE,
        "baseline": {"valid": 3, "success": 1},
        "live": ({"valid": 3, "success": 2} if judgment is True
                 else {"valid": 3, "success": 1}),
        "better": None if zero_execution else judgment,
        "injection_tokens": 40,
        "judged_with_shortfall": False,
        "aux": None,
    }
    if zero_execution:
        row["baseline"] = None
        row["live"] = None
        row["injection_tokens"] = None
        row["verdict"] = judgment
    if round_ is not None:
        row["round"] = round_
    return row


def rows_of(specs, task_id=TASK_ID):
    """A ledger's rows from (round, judgment) specs, in append order."""
    return [ledger_row(round_, judgment, task_id=task_id)
            for round_, judgment in specs]


def assert_decision(tmp_path, rounds, rows, outcome, reason):
    """Drive the core over one hand-built ledger and assert its full
    contract: the returned dict, every member's stored terminal state,
    the ledger being untouched, and the second sweep committing
    nothing."""
    store = Store(tmp_path / "run")
    group = seed_group(store)
    path = tmp_path / "verdicts.jsonl"
    for record in rows:
        append_jsonl(path, record)

    result = lv._finalize_group(store, TASK_ID, verdicts_path=path,
                                rounds=rounds)

    committed = (sorted(skill.skill_id for skill in group)
                 if outcome != "pending" else [])
    assert result == {"outcome": outcome, "reason": reason,
                      "committed": committed}
    # The decision ledger is read-only input to the sweep.
    assert read_jsonl(path) == rows
    for skill in group:
        stored = store.load("skills", skill.skill_id, Skill)
        if outcome == "accepted":
            assert (stored.status, stored.discard_reason) == ("accepted",
                                                              None)
        elif outcome == "discarded":
            assert (stored.status, stored.discard_reason) == ("discarded",
                                                              reason)
        else:
            assert (stored.status, stored.discard_reason) == ("pending",
                                                              None)

    # Commits target pending members only, so the second sweep is a
    # no-op on the store.
    snapshot = {skill.skill_id: store.load("skills", skill.skill_id, Skill)
                for skill in group}
    assert lv._finalize_group(store, TASK_ID, verdicts_path=path,
                              rounds=rounds) == {
        "outcome": outcome, "reason": reason, "committed": []}
    for skill_id, before in snapshot.items():
        assert store.load("skills", skill_id, Skill).to_dict() == \
            before.to_dict()
    assert read_jsonl(path) == rows


# ---------------------------------------------------------------------------
# One row at a time: the base mapping of every row kind, rounds=(0, 1)
# ---------------------------------------------------------------------------

ONE_ROW_CASES = [
    # D2: no label owns a row -> the sweep owes the group nothing.
    pytest.param([], "pending", None,
                 id="no-rows"),
    # D4: round 0 is the earlier label -- better accepts, a miss and a
    # round outside the set decide nothing.
    pytest.param([(0, True)], "accepted", None,
                 id="r0-better"),
    pytest.param([(0, False)], "pending", None,
                 id="r0-miss"),
    pytest.param([(0, lv.WEAK_ALREADY_KNOWS)], "discarded",
                 lv.WEAK_ALREADY_KNOWS,
                 id="r0-verdict"),
    # D3: round 1 is the last label -- better accepts, a miss discards.
    pytest.param([(1, True)], "accepted", None,
                 id="r1-better"),
    pytest.param([(1, False)], "discarded", lv.LIVE_NO_IMPROVEMENT,
                 id="r1-miss"),
    pytest.param([(1, lv.LIVE_INJECTION_OVERFLOW)], "discarded",
                 lv.LIVE_INJECTION_OVERFLOW,
                 id="r1-verdict"),
    # D6: round 2 is outside the pinned set. Its judged rows never join
    # the round decision ...
    pytest.param([(2, True)], "pending", None,
                 id="r2-better"),
    pytest.param([(2, False)], "pending", None,
                 id="r2-miss"),
    # ... but D1's scan ignores round labels, so its verdict still fires.
    pytest.param([(2, lv.LIVE_UNDETERMINED)], "discarded",
                 lv.LIVE_UNDETERMINED,
                 id="r2-verdict"),
    # D6, same two-sided rule for rows carrying no round key at all.
    pytest.param([(None, True)], "pending", None,
                 id="no-round-better"),
    pytest.param([(None, False)], "pending", None,
                 id="no-round-miss"),
    pytest.param([(None, lv.LIVE_UNDETERMINED)], "discarded",
                 lv.LIVE_UNDETERMINED,
                 id="no-round-verdict"),
]


@pytest.mark.parametrize("specs, outcome, reason", ONE_ROW_CASES)
def test_each_row_kind_alone(tmp_path, specs, outcome, reason):
    assert_decision(tmp_path, ROUNDS, rows_of(specs), outcome, reason)


# ---------------------------------------------------------------------------
# Round-0 / round-1 boundary pairs (review A's named combinations)
# ---------------------------------------------------------------------------

BOTH_ROUND_CASES = [
    # D2 + D3: round 1 owns rows and is the last label, so IT decides
    # whatever round 0's rows say (review A: "round 0 better + round 1
    # miss" and "round 0 miss + round 1 miss" both discard; the
    # survivors' round-1 miss buries the earlier verdicts).
    pytest.param([(0, True), (1, False)], "discarded",
                 lv.LIVE_NO_IMPROVEMENT,
                 id="r0-better-r1-miss"),
    pytest.param([(0, False), (1, False)], "discarded",
                 lv.LIVE_NO_IMPROVEMENT,
                 id="r0-miss-r1-miss"),
    pytest.param([(0, False), (1, True)], "accepted", None,
                 id="r0-miss-r1-better"),
    pytest.param([(0, True), (1, True)], "accepted", None,
                 id="r0-better-r1-better"),
]


@pytest.mark.parametrize("specs, outcome, reason", BOTH_ROUND_CASES)
def test_round0_round1_boundary_pairs(tmp_path, specs, outcome, reason):
    assert_decision(tmp_path, ROUNDS, rows_of(specs), outcome, reason)


# ---------------------------------------------------------------------------
# Verdict rows arriving out of order (append-order sensitivity, D1)
# ---------------------------------------------------------------------------

VERDICT_ORDER_CASES = [
    # D1 runs before any round decision and follows append order: a
    # verdict row decides even when a better row precedes it (review A:
    # "terminal rows out of order").
    pytest.param([(0, lv.WEAK_ALREADY_KNOWS), (1, True)], "discarded",
                 lv.WEAK_ALREADY_KNOWS,
                 id="verdict-then-better"),
    pytest.param([(1, True), (0, lv.WEAK_ALREADY_KNOWS)], "discarded",
                 lv.WEAK_ALREADY_KNOWS,
                 id="better-then-verdict"),
    # Two verdict rows: the FIRST in append order is the judgment.
    pytest.param([(0, lv.WEAK_ALREADY_KNOWS), (1, lv.LIVE_INJECTION_OVERFLOW)],
                 "discarded", lv.WEAK_ALREADY_KNOWS,
                 id="two-verdicts"),
    pytest.param([(1, lv.LIVE_INJECTION_OVERFLOW), (0, lv.WEAK_ALREADY_KNOWS)],
                 "discarded", lv.LIVE_INJECTION_OVERFLOW,
                 id="two-verdicts-reversed"),
]


@pytest.mark.parametrize("specs, outcome, reason", VERDICT_ORDER_CASES)
def test_verdict_row_order_sensitivity(tmp_path, specs, outcome, reason):
    assert_decision(tmp_path, ROUNDS, rows_of(specs), outcome, reason)


# ---------------------------------------------------------------------------
# First-row stickiness inside the decisive round (D5)
# ---------------------------------------------------------------------------

FIRST_ROW_CASES = [
    # D5: the decisive round's FIRST row is its judgment -- a later row
    # of the same round never overwrites it.
    pytest.param([(1, False), (1, True)], "discarded",
                 lv.LIVE_NO_IMPROVEMENT,
                 id="r1-miss-then-better"),
    pytest.param([(1, True), (1, False)], "accepted", None,
                 id="r1-better-then-miss"),
    # D5 + D4: the earlier label's first-row miss still decides
    # nothing; its later better row does not rescue the group either.
    pytest.param([(0, False), (0, True)], "pending", None,
                 id="r0-miss-then-better"),
    pytest.param([(0, True), (0, False)], "accepted", None,
                 id="r0-better-then-miss"),
]


@pytest.mark.parametrize("specs, outcome, reason", FIRST_ROW_CASES)
def test_first_row_of_the_decisive_round_wins(tmp_path, specs, outcome,
                                              reason):
    assert_decision(tmp_path, ROUNDS, rows_of(specs), outcome, reason)


# ---------------------------------------------------------------------------
# Out-of-set rows interleaved with in-set rows (D6, lengths up to 4)
# ---------------------------------------------------------------------------

INTERLEAVED_CASES = [
    # The round-2 row is invisible to the round decision (D6): round 0's
    # better row decides; the round-1 miss stands.
    pytest.param([(2, True), (0, True)], "accepted", None,
                 id="r2-better-r0-better"),
    pytest.param([(1, False), (2, True)], "discarded",
                 lv.LIVE_NO_IMPROVEMENT,
                 id="r1-miss-r2-better"),
    # D2: round 1 is the latest label with rows, whatever else is
    # interleaved.
    pytest.param([(0, True), (2, False), (1, True)], "accepted", None,
                 id="r0-b-r2-m-r1-b"),
    pytest.param([(0, False), (2, False), (1, False)], "discarded",
                 lv.LIVE_NO_IMPROVEMENT,
                 id="r0-m-r2-m-r1-m"),
    # D1 beats the round decision from any position, whatever the row's
    # round label.
    pytest.param([(2, lv.LIVE_UNDETERMINED), (0, True), (1, True)],
                 "discarded", lv.LIVE_UNDETERMINED,
                 id="r2-verdict-first"),
    pytest.param([(0, True), (2, lv.LIVE_UNDETERMINED), (1, True)],
                 "discarded", lv.LIVE_UNDETERMINED,
                 id="r2-verdict-middle"),
    # No-round-key rows: ignored by the round decision, still fire D1.
    pytest.param([(None, True), (0, False), (1, True)], "accepted", None,
                 id="no-round-row-ignored"),
    pytest.param([(None, lv.LIVE_UNDETERMINED), (1, True)], "discarded",
                 lv.LIVE_UNDETERMINED,
                 id="no-round-verdict-fires"),
    # Length 4 (D2 + D5): a round-1 miss is terminal for the group --
    # an appended round-0 better row cannot rescue it.
    pytest.param([(0, False), (2, False), (1, False), (0, True)],
                 "discarded", lv.LIVE_NO_IMPROVEMENT,
                 id="r1-miss-sticks"),
]


@pytest.mark.parametrize("specs, outcome, reason", INTERLEAVED_CASES)
def test_out_of_set_rows_interleaved(tmp_path, specs, outcome, reason):
    assert_decision(tmp_path, ROUNDS, rows_of(specs), outcome, reason)


# ---------------------------------------------------------------------------
# A three-round sequence: the generalized branch M4 consumes
# ---------------------------------------------------------------------------

THREE_ROUND_CASES = [
    # Round 1 is no longer the last label: D4 -- better accepts, a miss
    # decides nothing (the group still owes the pipeline a round 2).
    pytest.param([(1, True)], "accepted", None,
                 id="r1-better-earlier"),
    pytest.param([(1, False)], "pending", None,
                 id="r1-miss-earlier"),
    # Round 2 is the last label: D3.
    pytest.param([(2, True)], "accepted", None,
                 id="r2-better-last"),
    pytest.param([(2, False)], "discarded", lv.LIVE_NO_IMPROVEMENT,
                 id="r2-miss-last"),
    pytest.param([(2, lv.LIVE_UNDETERMINED)], "discarded",
                 lv.LIVE_UNDETERMINED,
                 id="r2-verdict-last"),
    # D2: the latest label owning a row decides.
    pytest.param([(0, True), (2, False)], "discarded",
                 lv.LIVE_NO_IMPROVEMENT,
                 id="r0-b-r2-m"),
    pytest.param([(0, False), (1, True)], "accepted", None,
                 id="r0-m-r1-b"),
    # Differential pair with r0-better-r1-miss above: the SAME rows
    # decide nothing under three labels -- round 1's miss is an
    # earlier-label miss (D4), not a last-label one (D3).
    pytest.param([(0, True), (1, False)], "pending", None,
                 id="r0-b-r1-m-earlier"),
    # Differential pair with r1-miss-r2-better above: under three
    # labels the round-2 row is in-set and round 2's better decides.
    pytest.param([(1, False), (2, True)], "accepted", None,
                 id="r1-m-r2-b"),
]


@pytest.mark.parametrize("specs, outcome, reason", THREE_ROUND_CASES)
def test_three_round_sequence(tmp_path, specs, outcome, reason):
    assert_decision(tmp_path, (0, 1, 2), rows_of(specs), outcome, reason)


# ---------------------------------------------------------------------------
# A single-round sequence: the last label is also the first
# ---------------------------------------------------------------------------

SINGLE_ROUND_CASES = [
    # Round 0 is now the LAST label: D3 -- better accepts, a miss
    # discards. The miss case is the differential pair with r0-miss
    # above: under (0, 1) the same row decides nothing (D4).
    pytest.param([(0, True)], "accepted", None,
                 id="r0-better-last"),
    pytest.param([(0, False)], "discarded", lv.LIVE_NO_IMPROVEMENT,
                 id="r0-miss-last"),
    # Round 1 is now outside the set (D6): its rows decide nothing,
    # except through D1's label-blind verdict scan.
    pytest.param([(1, False)], "pending", None,
                 id="r1-miss-out-of-set"),
    pytest.param([(1, lv.LIVE_INJECTION_OVERFLOW)], "discarded",
                 lv.LIVE_INJECTION_OVERFLOW,
                 id="r1-verdict-fires"),
]


@pytest.mark.parametrize("specs, outcome, reason", SINGLE_ROUND_CASES)
def test_single_round_sequence(tmp_path, specs, outcome, reason):
    assert_decision(tmp_path, (0,), rows_of(specs), outcome, reason)


# ---------------------------------------------------------------------------
# Reader and commit-block properties outside the case tables
# ---------------------------------------------------------------------------

def test_rows_of_other_tasks_never_decide_this_group(tmp_path):
    # The ledger is shared by every task; the decision reads only this
    # task's rows, so another task's round-1 miss must not bury this
    # task's round-0 better row.
    store = Store(tmp_path / "run")
    seed_group(store)
    path = tmp_path / "verdicts.jsonl"
    append_jsonl(path, ledger_row(1, False, task_id=OTHER_TASK_ID))
    append_jsonl(path, ledger_row(0, True))

    result = lv._finalize_group(store, TASK_ID, verdicts_path=path,
                                rounds=ROUNDS)

    assert result == {"outcome": "accepted", "reason": None,
                      "committed": sorted(store.ids("skills"))}


@pytest.mark.parametrize("judgment, outcome, reason", [
    pytest.param(True, "accepted", None, id="accepted"),
    pytest.param(False, "discarded", lv.LIVE_NO_IMPROVEMENT,
                id="discarded"),
])
def test_commit_touches_pending_members_only(tmp_path, judgment, outcome,
                                             reason):
    # An already-terminal member keeps its own truth (the shell
    # docstring: "already terminal members keep their own truth"): the
    # sweep commits the pending member and lists it alone.
    store = Store(tmp_path / "run")
    group = seed_group(store)
    discard_skill(store, group[1], lv.LIVE_REVISION_DELETED)
    path = tmp_path / "verdicts.jsonl"
    append_jsonl(path, ledger_row(1, judgment))

    result = lv._finalize_group(store, TASK_ID, verdicts_path=path,
                                rounds=ROUNDS)

    assert result == {"outcome": outcome, "reason": reason,
                      "committed": [group[0].skill_id]}
    survivor = store.load("skills", group[0].skill_id, Skill)
    assert (survivor.status, survivor.discard_reason) == (outcome, reason)
    terminal = store.load("skills", group[1].skill_id, Skill)
    assert (terminal.status, terminal.discard_reason) == (
        "discarded", lv.LIVE_REVISION_DELETED)
