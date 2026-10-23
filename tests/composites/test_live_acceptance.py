"""K6 live-acceptance phase-function tests (llm.fake + tmp run dir).

Every test drives the composites-level functions directly with explicit
parameters -- the phase driver (stages/phase_execute.py) has its own
suite. The run directory is hand-built the way the phase would have left
it: stage-1 weak bare trajectories in the main store, the group's
pending candidate skills, the frozen injection_audit rows, and the
round stores' trajectories. Model verdicts and plans are FakeClient
scripts on the author / rewriter handles (AGENTS.md section 8: no
network, deterministic, assertable). The retired G10/G11 composite
(attribution + revision application) went with the K6 shell; its
phase-world heirs are methods/attribution/group (facts) and
methods/author/write (decisions), tested under tests/unit.
"""

import hashlib
import json

import pytest

from expedistill.composites import (
    live_measurement as lm,
    live_verdicts as lv,
)
from expedistill.composites.skill_status import discard_skill
from expedistill.composites.live_measurement import (
    audit_row,
    expected_units,
    load_baseline,
    pending_groups,
    skip_ids_for,
)
from expedistill.composites.live_verdicts import (
    finalize_group,
    judge_round,
)
from expedistill.core.ids import (
    make_merged_skill_id,
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill, Step, Trajectory
from expedistill.blocks.rules import register_acceptance_improve
from expedistill.infra.reliability import (
    CodeBugError,
    ConfigError,
    InfrastructureError,
)
from expedistill.infra.store import Store, append_jsonl, read_jsonl, write_json
from expedistill.inject.skills import estimate_tokens, format_skills_block

BENCHMARK = "tb"
TASK_ID = make_task_id(BENCHMARK, "t1")
OTHER_TASK_ID = make_task_id(BENCHMARK, "t2")
N_ATTEMPTS = 3
RULE = "strict_rate_gain"

# Protocol-v2 contents: four bracket sections at line starts, so
# blocks.author.skill_markers reads the procedure set off them.
MARKED_A = "\n".join([
    "[When to use] The service rejects connections and needs a restart.",
    "[Steps]",
    "1. Check the service status with the status command.",
    "2. Restart the service with the restart command.",
    "[Verify] The status command reports the service as running.",
    "[Do not use when] The status command already reports running.",
])
#: Same four sections, reworded: passes the keep-structure check.
MARKED_A_REVISED = "\n".join([
    "[When to use] The service rejects connections and a restart is due.",
    "[Steps]",
    "1. Run the status command to see the service state.",
    "2. Restart the service with its restart command.",
    "3. Re-run the status command to confirm.",
    "[Verify] The second status command reports the service as running.",
    "[Do not use when] The status command already reports running.",
])
PROSE = "Restart the service after editing its configuration."

#: A rule a rate-1.0 baseline CAN lose to (pass@N shape), registered to
#: prove the weak_already_knows short-circuit is limited to the strict
#: family instead of trusting every registered rule.
@register_acceptance_improve("live_test_pass_at_n")
def _pass_at_n(baseline, live, n_attempts):
    return live["success"] >= 1 and baseline["success"] == 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def group_skill_ids(task_id=TASK_ID, count=3):
    trajectory_id = make_trajectory_id(task_id, "strong", 0)
    return [make_skill_id(make_subtask_id(trajectory_id, segment))
            for segment in range(count)]


def make_group_skill(task_id, segment, content, version=1):
    subtask_id = make_subtask_id(make_trajectory_id(task_id, "strong", 0),
                                 segment)
    return Skill(skill_id=make_skill_id(subtask_id), content=content,
                 subtask_id=subtask_id, source_subtasks=[subtask_id],
                 version=version)


def store_group(store, contents, task_id=TASK_ID):
    skills = [make_group_skill(task_id, segment, content)
             for segment, content in enumerate(contents)]
    for skill in skills:
        store.save(skill, "skills")
    return skills


def make_traj(attempt, correctness, task_id=TASK_ID, role="weak", marker="x"):
    return Trajectory(
        trajectory_id=make_trajectory_id(task_id, role, attempt),
        task_id=task_id, benchmark=BENCHMARK, model_role=role,
        model_name="weak-model", attempt_index=attempt,
        steps=[Step(action=f"do {marker} {attempt}",
                    observation=f"obs {marker} {attempt}")],
        correctness=correctness,
    )


def seed_baseline(store, statuses, task_id=TASK_ID):
    for attempt, correctness in enumerate(statuses):
        store.save(make_traj(attempt, correctness, task_id), "trajectories")


def round_dir(store, round_):
    return store.run_dir / "stage4_acceptance" / "live_run" / f"round{round_:02d}"


def round_store(store, round_):
    return Store(round_dir(store, round_))


def seed_round(store, round_, statuses, task_id=TASK_ID):
    rs = round_store(store, round_)
    for attempt, correctness in enumerate(statuses):
        rs.save(make_traj(attempt, correctness, task_id), "trajectories")
    return rs


def freeze_audit(store, round_=0, task_id=TASK_ID, row=None):
    """Freeze the group's audit line. The stage appends once per group;
    the fixture keeps ONE line per task so a test can re-freeze a
    tampered row without stacking copies (readers take the first line
    of the group either way)."""
    if row is None:
        row = audit_row(store, task_id, round_)
    path = round_dir(store, round_) / lm.INJECTION_AUDIT_NAME
    rows = [r for r in read_jsonl(path) if r.get("task_id") != task_id]
    rows.append(row)
    path.write_text("".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
        for r in rows), encoding="utf-8")
    return row


def write_stage1_shortfall(store, unit_ids):
    write_json(store.run_dir / "stage1_baseline"
               / "accepted_infra_shortfall.json",
               {"unit_ids": sorted(unit_ids)}, indent=2)


def counts(valid, success, infra=0, aux_success=None):
    base = {"attempts": valid + infra, "valid": valid, "success": success,
            "infra": infra}
    if aux_success is not None:
        base["aux_success"] = aux_success
    return base


def seeded_run(tmp_path, *, baseline=(False, False, False),
               live=(False, False, False),
               contents=(MARKED_A, MARKED_A, MARKED_A), round_=0):
    """A run directory the stage would hand to round-0 judging."""
    store = Store(tmp_path / "run")
    seed_baseline(store, baseline)
    seed_round(store, round_, live)
    skills = store_group(store, list(contents))
    freeze_audit(store, round_)
    return store, skills


def stored_skills(store):
    return {skill_id: store.load("skills", skill_id, Skill).to_dict()
            for skill_id in store.ids("skills")}


# ---------------------------------------------------------------------------
# Vocabulary and small helpers
# ---------------------------------------------------------------------------

def test_discard_reason_vocabulary_is_pinned():
    assert (lv.WEAK_ALREADY_KNOWS, lv.LIVE_NO_IMPROVEMENT,
            lv.LIVE_REVISION_DELETED, lv.MODEL_OUTPUT_ERROR,
            lv.LIVE_UNDETERMINED, lv.LIVE_INJECTION_OVERFLOW) == (
        "weak_already_knows", "live_no_improvement", "live_revision_deleted",
        "model_output_error", "live_undetermined", "live_injection_overflow")


def test_expected_units_is_the_full_set(tmp_path):
    assert expected_units(TASK_ID, N_ATTEMPTS) == {TASK_ID: N_ATTEMPTS}


# ---------------------------------------------------------------------------
# load_baseline
# ---------------------------------------------------------------------------

def test_load_baseline_reads_only_the_weak_arm_by_id(tmp_path):
    store = Store(tmp_path / "run")
    seed_baseline(store, (False, True, False))
    for attempt in range(2):
        store.save(make_traj(attempt, True, role="strong"),
                   "trajectories")

    trajectories, baseline = load_baseline(store, TASK_ID, N_ATTEMPTS)

    assert [t.trajectory_id for t in trajectories] == [
        make_trajectory_id(TASK_ID, "weak", i) for i in range(N_ATTEMPTS)]
    assert baseline == counts(3, 1)


def test_load_baseline_counts_accepted_holes_as_infra(tmp_path):
    store = Store(tmp_path / "run")
    seed_baseline(store, (False, True))
    hole = make_trajectory_id(TASK_ID, "weak", 2)
    strong_hole = make_trajectory_id(TASK_ID, "strong", 2)
    other_hole = make_trajectory_id(OTHER_TASK_ID, "weak", 0)
    write_stage1_shortfall(store, {hole, strong_hole, other_hole})

    trajectories, baseline = load_baseline(store, TASK_ID, N_ATTEMPTS)

    # The weak hole of THIS task is the only one that counts; the strong
    # and other-task holes stay where they belong.
    assert hole not in {t.trajectory_id for t in trajectories}
    assert baseline == counts(2, 1, infra=1)


def test_load_baseline_missing_unit_is_infrastructure_error(tmp_path):
    store = Store(tmp_path / "run")
    seed_baseline(store, (False, False))

    with pytest.raises(InfrastructureError, match="stage 1 resumes first"):
        load_baseline(store, TASK_ID, N_ATTEMPTS)


def test_load_baseline_zero_valid_is_returned_not_raised(tmp_path):
    # R2b / ruling 2: a baseline with no valid attempt is a normal
    # reachable state (all its trials unscored, or all accepted holes),
    # never a ConfigError -- the group is judged not_testable up the
    # stack. The counts keep the full expected set in the denominator.
    store = Store(tmp_path / "run")
    seed_baseline(store, (None, None, None))

    _, baseline = load_baseline(store, TASK_ID, N_ATTEMPTS)

    assert baseline == counts(0, 0, infra=3)

    hole0 = make_trajectory_id(TASK_ID, "weak", 0)
    hole1 = make_trajectory_id(TASK_ID, "weak", 1)
    hole2 = make_trajectory_id(TASK_ID, "weak", 2)
    write_stage1_shortfall(store, {hole0, hole1, hole2})
    _, all_holes = load_baseline(store, TASK_ID, N_ATTEMPTS)
    assert all_holes == counts(0, 0, infra=3)


def test_load_baseline_aux_scorer_counts(tmp_path):
    store = Store(tmp_path / "run")
    seed_baseline(store, (False, True, False))

    _, baseline = load_baseline(store, TASK_ID, N_ATTEMPTS,
                                aux_scorer=lambda t: t.correctness is True)

    assert baseline["aux_success"] == 1


# ---------------------------------------------------------------------------
# skip_ids_for
# ---------------------------------------------------------------------------

def test_skip_ids_union_filters_weak_arm_and_task():
    stage1 = [
        make_trajectory_id(TASK_ID, "weak", 0),        # inherited
        make_trajectory_id(TASK_ID, "strong", 1),      # strong arm: not ours
        make_trajectory_id(OTHER_TASK_ID, "weak", 0),  # other task
    ]
    live = [
        make_trajectory_id(TASK_ID, "weak", 1),        # this round's hole
        make_trajectory_id(OTHER_TASK_ID, "weak", 2),
    ]

    assert skip_ids_for(stage1, live, TASK_ID) == [
        make_trajectory_id(TASK_ID, "weak", 0),
        make_trajectory_id(TASK_ID, "weak", 1),
    ]


def test_skip_ids_malformed_unit_is_code_bug():
    with pytest.raises(CodeBugError, match="malformed trajectory"):
        skip_ids_for(["garbage"], [], TASK_ID)


# ---------------------------------------------------------------------------
# audit_row
# ---------------------------------------------------------------------------

def test_pending_groups_derives_the_universe(tmp_path):
    store = Store(tmp_path / "run")
    keep = make_group_skill(TASK_ID, 0, MARKED_A)
    gone = make_group_skill(TASK_ID, 1, MARKED_A)
    gone.status = "discarded"
    gone.discard_reason = lv.LIVE_REVISION_DELETED
    other = make_group_skill(OTHER_TASK_ID, 0, MARKED_A)
    merged = Skill(skill_id=make_merged_skill_id(0), content=MARKED_A,
                   subtask_id=keep.subtask_id,
                   source_subtasks=[keep.subtask_id])
    for skill in (keep, gone, other, merged):
        store.save(skill, "skills")

    groups = pending_groups(store)

    # Terminal members, merged products and unknown-chain ids are not
    # units; every pending candidate group is, keyed by its task id.
    assert groups == {TASK_ID: [keep], OTHER_TASK_ID: [other]}
    assert lm._pending_group(store, TASK_ID) == [keep]
    assert lm._pending_group(store, make_task_id("tb", "nobody")) == []


def test_audit_row_members_sha_and_tokens(tmp_path):
    store = Store(tmp_path / "run")
    skills = store_group(store, [MARKED_A, PROSE])

    row = audit_row(store, TASK_ID, 0)

    block = format_skills_block(skills)
    assert row == {
        "schema": "injection-audit-v1",
        "task_id": TASK_ID,
        "round": 0,
        "members": [{"skill_id": skills[0].skill_id, "version": 1},
                    {"skill_id": skills[1].skill_id, "version": 1}],
        "injection_block_sha256":
            hashlib.sha256(block.encode("utf-8")).hexdigest(),
        "injection_tokens": estimate_tokens(block),
    }
    # The same derivation twice is byte-equal: the freeze and the resume
    # comparison share this one constructor.
    assert audit_row(store, TASK_ID, 0) == row


def test_audit_row_excludes_terminal_merged_and_other_tasks(tmp_path):
    store = Store(tmp_path / "run")
    keep = make_group_skill(TASK_ID, 0, MARKED_A)
    gone = make_group_skill(TASK_ID, 1, MARKED_A)
    gone.status = "discarded"
    gone.discard_reason = lv.LIVE_REVISION_DELETED
    elsewhere = make_group_skill(OTHER_TASK_ID, 0, MARKED_A)
    merged = Skill(skill_id=make_merged_skill_id(0), content=MARKED_A,
                   subtask_id=keep.subtask_id,
                   source_subtasks=[keep.subtask_id])
    for skill in (keep, gone, elsewhere, merged):
        store.save(skill, "skills")

    row = audit_row(store, TASK_ID, 0)

    assert [m["skill_id"] for m in row["members"]] == [keep.skill_id]


def test_audit_row_empty_group_is_code_bug(tmp_path):
    store = Store(tmp_path / "run")
    with pytest.raises(CodeBugError, match="no pending"):
        audit_row(store, TASK_ID, 0)


def test_audit_row_rejects_a_third_round(tmp_path):
    store, skills = seeded_run(tmp_path)
    with pytest.raises(CodeBugError, match="pinned to"):
        audit_row(store, TASK_ID, 2)


# ---------------------------------------------------------------------------
# judge_round: the truth table
# ---------------------------------------------------------------------------

JUDGE_CASES = [
    # (baseline valid, baseline success, live valid, live success, better)
    (3, 1, 3, 2, True),    # a real gain
    (3, 1, 3, 1, False),   # flat is not a gain (strict)
    (3, 2, 3, 1, False),   # worse
    (3, 2, 5, 3, False),   # more successes, worse RATE: cross-multiplied
    (3, 2, 5, 4, True),    # unequal depths, better rate
    (3, 2, 2, 2, True),    # an accepted hole shrank the live denominator
    (3, 0, 3, 1, True),    # first_success_gain territory, still a rate gain
]


@pytest.mark.parametrize("bv,bs,lv,ls,better", JUDGE_CASES)
def test_judge_truth_table(tmp_path, bv, bs, lv, ls, better):
    # The live store realizes the case's counts: ls successes, lv valid,
    # and the units beyond lv are accepted holes (skip).
    statuses = [True] * ls + [False] * (lv - ls)
    skip = _unit_list()[lv:]
    store, _ = seeded_run(tmp_path, live=tuple(statuses))
    verdict = judge_round(
        store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
        baseline_counts=counts(bv, bs), n_attempts=N_ATTEMPTS,
        skip_ids=skip)

    assert verdict["better"] is better
    assert verdict["baseline"] == {"valid": bv, "success": bs}
    assert verdict["live"] == {"valid": lv, "success": ls}
    # The counts behind the row really are D3's: holes surface as infra.
    assert verdict["judged_with_shortfall"] is bool(skip)


def test_judge_row_shape(tmp_path):
    store, skills = seeded_run(tmp_path, baseline=(False, True, False))
    audit = audit_row(store, TASK_ID, 0)

    verdict = judge_round(
        store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
        baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS, skip_ids=[])

    assert verdict == {
        "schema": "live-verdict-v1",
        "task_id": TASK_ID,
        "round": 0,
        "rule": RULE,
        "baseline": {"valid": 3, "success": 1},
        "live": {"valid": 3, "success": 0},
        "better": False,
        "injection_tokens": audit["injection_tokens"],
        "judged_with_shortfall": False,
        "aux": None,
    }
    rows = read_jsonl(store.run_dir / "stage4_acceptance" / "live_run"
                      / lm.VERDICTS_LEDGER)
    assert rows == [verdict]


def test_judge_is_append_once_and_idempotent(tmp_path):
    store, _ = seeded_run(tmp_path)
    kwargs = dict(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                  baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
                  skip_ids=[])

    first = judge_round(**kwargs)
    second = judge_round(**kwargs)

    assert second == first
    rows = read_jsonl(store.run_dir / "stage4_acceptance" / "live_run"
                      / lm.VERDICTS_LEDGER)
    assert len(rows) == 1


def test_judge_recompute_mismatch_is_code_bug(tmp_path):
    store, _ = seeded_run(tmp_path)
    judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
                skip_ids=[])

    # The caller's inputs drifted under the stored row (the anti-tamper
    # the decision demands: recompute disagreement is a CodeBugError).
    with pytest.raises(CodeBugError, match="disagrees"):
        judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                    baseline_counts=counts(3, 2), n_attempts=N_ATTEMPTS,
                    skip_ids=[])

    # A tampered round store is caught the same way: an extra trajectory
    # changes the recomputed live counts under the stored row.
    seed_round(store, 0, [False] * N_ATTEMPTS + [True])
    with pytest.raises(CodeBugError, match="disagrees"):
        judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                    baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
                    skip_ids=[])


def test_judge_tampered_verdict_row_is_code_bug(tmp_path):
    """The §2.7 anti-tamper case: someone edited the ledger's first row
    by hand; the recomputation must refuse to bless it."""
    store, _ = seeded_run(tmp_path)
    judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
                skip_ids=[])
    rows = read_jsonl(verdicts_path(store))
    rows[0]["better"] = True
    verdicts_path(store).write_text("".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
        for r in rows), encoding="utf-8")

    with pytest.raises(CodeBugError, match="disagrees"):
        judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                    baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
                    skip_ids=[])


def test_judge_ignores_other_tasks_trajectories(tmp_path):
    store, _ = seeded_run(tmp_path, live=(True, False, False))
    seed_round(store, 0, (True,) * N_ATTEMPTS, task_id=OTHER_TASK_ID)

    verdict = judge_round(
        store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
        baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS, skip_ids=[])

    assert verdict["live"] == {"valid": 3, "success": 1}


def test_judge_counts_holes_and_flags_shortfall(tmp_path):
    store, _ = seeded_run(tmp_path, live=(True, True))
    hole = make_trajectory_id(TASK_ID, "weak", 2)

    verdict = judge_round(
        store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
        baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
        skip_ids=[hole])

    # The hole is infra under the full expected set (2/2 beats 1/3).
    assert verdict["live"] == {"valid": 2, "success": 2}
    assert verdict["better"] is True
    assert verdict["judged_with_shortfall"] is True


def test_judge_incomplete_group_is_code_bug(tmp_path):
    store, _ = seeded_run(tmp_path, live=(False, False))

    with pytest.raises(CodeBugError, match="incomplete"):
        judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                    baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
                    skip_ids=[])


def test_judge_requires_a_dispatched_round_store(tmp_path):
    store = Store(tmp_path / "run")
    seed_baseline(store, (False,) * N_ATTEMPTS)
    store_group(store, [MARKED_A])

    # No audit frozen and no round store: the guard that fires first is
    # the dispatch one (judging reads the round store before the audit).
    with pytest.raises(CodeBugError, match="never dispatched"):
        judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                    baseline_counts=counts(3, 0), n_attempts=N_ATTEMPTS,
                    skip_ids=[])


def test_judge_requires_the_frozen_audit_row(tmp_path):
    store, _ = seeded_run(tmp_path)
    (round_dir(store, 0) / lm.INJECTION_AUDIT_NAME).unlink()

    with pytest.raises(CodeBugError, match="frozen injection_audit"):
        judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                    baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
                    skip_ids=[])


def test_judge_unknown_rule_is_config_error(tmp_path):
    store, _ = seeded_run(tmp_path)
    with pytest.raises(ConfigError,
                       match="unknown acceptance improve rule"):
        judge_round(store=store, task_id=TASK_ID, round_=0,
                    rule_name="no_such_rule",
                    baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
                    skip_ids=[])


# ---------------------------------------------------------------------------
# judge_round: zero-execution terminals
# ---------------------------------------------------------------------------

def test_judge_all_holes_live_sample_is_undetermined(tmp_path):
    store, skills = seeded_run(tmp_path, live=())
    audit = freeze_audit(store, 0)
    skip = skip_ids_for([], _unit_list(), TASK_ID)

    verdict = judge_round(
        store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
        baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
        skip_ids=skip)

    assert verdict["verdict"] == lv.LIVE_UNDETERMINED
    assert verdict["baseline"] is None and verdict["live"] is None
    assert verdict["better"] is None
    assert verdict["injection_tokens"] == audit["injection_tokens"]
    assert verdict["judged_with_shortfall"] is True
    # The group terminalizes from the row alone.
    result = finalize_group(store, TASK_ID)
    assert result["outcome"] == "discarded"
    assert result["reason"] == lv.LIVE_UNDETERMINED
    assert all(s["status"] == "discarded"
               and s["discard_reason"] == lv.LIVE_UNDETERMINED
               for s in stored_skills(store).values())


def _unit_list():
    return [make_trajectory_id(TASK_ID, "weak", i)
            for i in range(N_ATTEMPTS)]


def test_judge_stored_infra_trajectory_is_not_undetermined(tmp_path):
    """A round store holding an unscored trajectory (a normal R2b state:
    an unscored trial is stored, never retried) beside SCORED ones is
    NOT the zero-measurement case: the group still has a live
    denominator (2 valid), so it flows to the rule, whose defensive
    branch reads the failures as not better instead of crashing."""
    store, _ = seeded_run(tmp_path, live=(None, False, False))

    verdict = judge_round(
        store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
        baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS, skip_ids=[])

    assert "verdict" not in verdict
    assert verdict["better"] is False
    assert verdict["live"] == {"valid": 2, "success": 0}


def test_judge_all_unscored_live_group_is_undetermined(tmp_path):
    """The complete-but-all-unscored group (every stored run came back
    without a scorer verdict, R2b's normal reachable state) is
    live_undetermined: no comparison, no attribution budget, and the
    group terminalizes from the row alone (2026-10-16 ruling 1)."""
    store, skills = seeded_run(tmp_path, live=(None, None, None))
    audit = freeze_audit(store, 0)

    verdict = judge_round(
        store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
        baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS, skip_ids=[])

    assert verdict["verdict"] == lv.LIVE_UNDETERMINED
    assert verdict["baseline"] is None and verdict["live"] is None
    assert verdict["better"] is None
    assert verdict["injection_tokens"] == audit["injection_tokens"]
    assert verdict["judged_with_shortfall"] is True
    result = finalize_group(store, TASK_ID)
    assert result["outcome"] == "discarded"
    assert result["reason"] == lv.LIVE_UNDETERMINED
    assert all(s["status"] == "discarded"
               and s["discard_reason"] == lv.LIVE_UNDETERMINED
               for s in stored_skills(store).values())


def test_judge_undetermined_cannot_be_asserted_for_a_measured_baseline(
        tmp_path):
    store, _ = seeded_run(tmp_path)
    with pytest.raises(CodeBugError, match="derived by judge_round"):
        judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                    baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
                    skip_ids=[], terminal=lv.LIVE_UNDETERMINED)


def test_judge_undetermined_asserted_for_an_unmeasured_baseline(tmp_path):
    """The entry guard's zero-execution path (ruling 2): a baseline with
    no valid attempt may assert live_undetermined -- verified by
    judge_round -- and the group terminalizes without a round store
    ever existing."""
    store = Store(tmp_path / "run")
    seed_baseline(store, (None, None, None))
    store_group(store, [MARKED_A])

    verdict = judge_round(
        store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
        baseline_counts=counts(0, 0, infra=3), n_attempts=N_ATTEMPTS,
        skip_ids=[], terminal=lv.LIVE_UNDETERMINED)

    assert verdict["verdict"] == lv.LIVE_UNDETERMINED
    assert verdict["baseline"] is None and verdict["live"] is None
    assert verdict["better"] is None
    assert verdict["injection_tokens"] is None
    assert verdict["judged_with_shortfall"] is False
    assert not round_dir(store, 0).exists()
    result = finalize_group(store, TASK_ID)
    assert result["outcome"] == "discarded"
    assert result["reason"] == lv.LIVE_UNDETERMINED


def test_judge_weak_already_knows_zero_execution(tmp_path):
    store = Store(tmp_path / "run")
    seed_baseline(store, (True, True, True))
    store_group(store, [MARKED_A, PROSE])

    verdict = judge_round(
        store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
        baseline_counts=counts(3, 3), n_attempts=N_ATTEMPTS,
        skip_ids=[], terminal=lv.WEAK_ALREADY_KNOWS)

    assert verdict["verdict"] == lv.WEAK_ALREADY_KNOWS
    assert verdict["baseline"] is None and verdict["live"] is None
    assert verdict["better"] is None
    assert verdict["injection_tokens"] is None   # nothing was ever injected
    assert verdict["judged_with_shortfall"] is False
    # Zero execution means zero dispatch: no round store came to exist.
    assert not round_dir(store, 0).exists()
    finalize_group(store, TASK_ID)
    assert all(s["status"] == "discarded"
               and s["discard_reason"] == lv.WEAK_ALREADY_KNOWS
               for s in stored_skills(store).values())


def test_judge_weak_already_knows_requires_all_success(tmp_path):
    store, _ = seeded_run(tmp_path)
    with pytest.raises(CodeBugError, match="not all-success"):
        judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                    baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
                    skip_ids=[], terminal=lv.WEAK_ALREADY_KNOWS)


def test_judge_weak_already_knows_is_limited_to_safe_rules(tmp_path):
    store = Store(tmp_path / "run")
    seed_baseline(store, (True,) * N_ATTEMPTS)
    store_group(store, [MARKED_A])
    with pytest.raises(CodeBugError, match="strict-family"):
        judge_round(store=store, task_id=TASK_ID, round_=0,
                    rule_name="live_test_pass_at_n",
                    baseline_counts=counts(3, 3), n_attempts=N_ATTEMPTS,
                    skip_ids=[], terminal=lv.WEAK_ALREADY_KNOWS)


def overflow_run(tmp_path, tokens):
    """A dispatched-looking run whose frozen audit row claims ``tokens``."""
    store = Store(tmp_path / "run")
    seed_baseline(store, (False,) * N_ATTEMPTS)
    seed_round(store, 0, (False,) * N_ATTEMPTS)
    skills = store_group(store, [MARKED_A])
    append_jsonl(round_dir(store, 0) / lm.INJECTION_AUDIT_NAME, {
        "schema": "injection-audit-v1", "task_id": TASK_ID, "round": 0,
        "members": [{"skill_id": s.skill_id, "version": 1} for s in skills],
        "injection_block_sha256": "frozen",
        "injection_tokens": tokens,
    })
    return store


def test_judge_injection_overflow_row(tmp_path):
    store = overflow_run(tmp_path / "over", tokens=9999)

    verdict = judge_round(
        store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
        baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
        skip_ids=[], terminal=lv.LIVE_INJECTION_OVERFLOW,
        max_injection_tokens=1000)

    assert verdict["verdict"] == lv.LIVE_INJECTION_OVERFLOW
    assert verdict["injection_tokens"] == 9999
    assert verdict["better"] is None and verdict["live"] is None
    assert verdict["baseline"] is None
    assert verdict["judged_with_shortfall"] is False
    # The evidence requirement: a caller asserting the overflow without
    # a limit, or with the frozen row under the limit, is refused -- the
    # verdict row never claims an overflow the audit does not show.
    with pytest.raises(CodeBugError, match="own evidence"):
        judge_round(store=overflow_run(tmp_path / "nolimit", tokens=9999),
                    task_id=TASK_ID, round_=0, rule_name=RULE,
                    baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
                    skip_ids=[], terminal=lv.LIVE_INJECTION_OVERFLOW)
    with pytest.raises(CodeBugError, match="own evidence"):
        judge_round(store=overflow_run(tmp_path / "under", tokens=10),
                    task_id=TASK_ID, round_=0, rule_name=RULE,
                    baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
                    skip_ids=[], terminal=lv.LIVE_INJECTION_OVERFLOW,
                    max_injection_tokens=1000)


# ---------------------------------------------------------------------------
# judge_round: the aux slot
# ---------------------------------------------------------------------------

def test_judge_aux_slot_records_both_arms(tmp_path):
    store, _ = seeded_run(tmp_path, baseline=(False, True, False),
                          live=(True, False, False))
    _, baseline = load_baseline(store, TASK_ID, N_ATTEMPTS,
                                aux_scorer=lambda t: t.correctness is True)

    verdict = judge_round(
        store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
        baseline_counts=baseline, n_attempts=N_ATTEMPTS, skip_ids=[],
        aux_scorer=lambda t: t.correctness is True)

    assert verdict["aux"] == {
        "baseline": {"valid": 3, "aux_success": 1},
        "live": {"valid": 3, "aux_success": 1},
    }
    # The aux counts never took part in the judgment.
    assert verdict["better"] is False


def test_judge_aux_scorer_without_baseline_counts_is_code_bug(tmp_path):
    store, _ = seeded_run(tmp_path)
    with pytest.raises(CodeBugError, match="same scorer"):
        judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                    baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
                    skip_ids=[], aux_scorer=lambda t: True)


# ---------------------------------------------------------------------------
def test_clip_view_keeps_head_and_tail_under_budget():
    """The clip keeps the head and tail of an over-budget view (the tail
    is where terminal-benchmark failure evidence lives), marks the
    elided middle, and never exceeds the budget."""
    text = "A" * 600 + "B" * 600 + "C" * 600
    clipped = lm._clip_view(text, 700)
    assert len(clipped) <= 700
    assert clipped.startswith("A")
    assert clipped.endswith("C")
    assert "chars elided to fit the attribution view budget" in clipped
    assert lm._clip_view("short view", 700) == "short view"   # no-op


# finalize_group
# ---------------------------------------------------------------------------
# (The no-op guard's shell and core are gone with their stage-4 caller:
# the phase world realizes the same fact as rule 1's cross-phase reuse --
# a revision that produced no new version leaves the block byte-identical
# and the next live phase reuses the last verdict -- pinned by
# tests/unit/stages/test_phase_execute.py's live reuse test.)

def verdicts_path(store):
    return (store.run_dir / "stage4_acceptance" / "live_run"
            / lm.VERDICTS_LEDGER)


def store_verdict(store, row):
    append_jsonl(verdicts_path(store), row)


def judged_row(round_=0, better=False, task_id=TASK_ID):
    row = {"schema": "live-verdict-v1", "task_id": task_id, "round": round_,
           "rule": RULE, "baseline": {"valid": 3, "success": 1},
           "live": {"valid": 3, "success": 1}, "better": better,
           "injection_tokens": 40, "judged_with_shortfall": False,
           "aux": None}
    return row


def test_finalize_round0_better_accepts_all_idempotently(tmp_path):
    store, skills = seeded_run(tmp_path)
    store_verdict(store, judged_row(0, better=True))

    first = finalize_group(store, TASK_ID)
    snapshot = stored_skills(store)
    second = finalize_group(store, TASK_ID)

    assert first == {"outcome": "accepted", "reason": None,
                     "committed": sorted(s.skill_id for s in skills)}
    assert second == {"outcome": "accepted", "reason": None, "committed": []}
    assert stored_skills(store) == snapshot
    assert all(s["status"] == "accepted" for s in snapshot.values())


def test_finalize_round1_miss_discards_survivors(tmp_path):
    skills_ids = group_skill_ids()
    store, skills = seeded_run(tmp_path, contents=(MARKED_A, MARKED_A))
    # The delete already landed during revision; the survivor is the
    # other member, and its round-1 miss keeps the delete's own reason.
    discard_me = store.load("skills", skills_ids[1], Skill)
    discard_skill(store, discard_me, lv.LIVE_REVISION_DELETED)
    store_verdict(store, judged_row(0, better=False))
    store_verdict(store, judged_row(1, better=False))

    result = finalize_group(store, TASK_ID)

    assert result["outcome"] == "discarded"
    assert result["reason"] == lv.LIVE_NO_IMPROVEMENT
    assert result["committed"] == [skills_ids[0]]
    stored = stored_skills(store)
    assert stored[skills_ids[0]]["discard_reason"] == lv.LIVE_NO_IMPROVEMENT
    assert stored[skills_ids[1]]["discard_reason"] == lv.LIVE_REVISION_DELETED


def test_finalize_round1_pass_accepts_survivors(tmp_path):
    skills_ids = group_skill_ids()
    store, skills = seeded_run(tmp_path, contents=(MARKED_A, MARKED_A))
    discard_skill(store, store.load("skills", skills_ids[1], Skill),
                  lv.LIVE_REVISION_DELETED)
    store_verdict(store, judged_row(0, better=False))
    store_verdict(store, judged_row(1, better=True))

    result = finalize_group(store, TASK_ID)

    assert result == {"outcome": "accepted", "reason": None,
                      "committed": [skills_ids[0]]}
    stored = stored_skills(store)
    assert stored[skills_ids[0]]["status"] == "accepted"
    assert stored[skills_ids[1]]["status"] == "discarded"


def test_finalize_zero_execution_row_discards_with_its_reason(tmp_path):
    store, skills = seeded_run(tmp_path)
    row = judged_row(0, better=False)
    row["verdict"] = lv.WEAK_ALREADY_KNOWS
    store_verdict(store, row)

    result = finalize_group(store, TASK_ID)

    assert result["outcome"] == "discarded"
    assert result["reason"] == lv.WEAK_ALREADY_KNOWS
    assert all(s["discard_reason"] == lv.WEAK_ALREADY_KNOWS
               for s in stored_skills(store).values())


def test_finalize_round0_miss_decides_nothing(tmp_path):
    store, skills = seeded_run(tmp_path)
    store_verdict(store, judged_row(0, better=False))

    result = finalize_group(store, TASK_ID)

    assert result == {"outcome": "pending", "reason": None, "committed": []}
    assert all(s["status"] == "pending"
               for s in stored_skills(store).values())


def test_finalize_without_rows_is_pending(tmp_path):
    store, skills = seeded_run(tmp_path)
    assert finalize_group(store, TASK_ID) == {
        "outcome": "pending", "reason": None, "committed": []}
