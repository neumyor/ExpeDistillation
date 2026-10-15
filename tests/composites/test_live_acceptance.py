"""K6 live-acceptance phase-function tests (llm.fake + tmp run dir).

Every test drives the composites-level functions directly with explicit
parameters -- the stage driver (stage4_live_run.py) is T4's and is not
imported here. The run directory is hand-built the way the stage would
have left it: stage-1 weak bare trajectories in the main store, the
group's pending candidate skills, the frozen injection_audit rows, and
the round stores' trajectories. Model verdicts and plans are FakeClient
scripts on the author / rewriter handles (AGENTS.md section 8: no
network, deterministic, assertable).
"""

import hashlib
import json

import pytest

from expedistill.composites import live_acceptance as la
from expedistill.composites.acceptance import discard_skill
from expedistill.composites.live_acceptance import (
    apply_revisions,
    attribute_group,
    audit_row,
    expected_units,
    finalize_group,
    judge_round,
    load_baseline,
    no_op_guard,
    pending_groups,
    skip_ids_for,
    survivors_and_block,
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
    ModelOutputError,
)
from expedistill.infra.store import Store, append_jsonl, read_jsonl, write_json
from expedistill.inject.skills import estimate_tokens, format_skills_block
from expedistill.llm import json_script, make_fake_handle

BENCHMARK = "tb"
TASK_ID = make_task_id(BENCHMARK, "t1")
OTHER_TASK_ID = make_task_id(BENCHMARK, "t2")
N_ATTEMPTS = 3
RULE = "strict_rate_gain"
WEAK_PROFILE = "A small model; known to skip intermediate steps."
TASK_INSTRUCTION = "Fix the stuck service on host <host>."

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
#: A revision that drops [Verify]: skill_markers reads None -> rejected.
MARKED_A_DROPPED = "\n".join([
    "[When to use] The service rejects connections and needs a restart.",
    "[Steps]",
    "1. Run the status command on the service.",
    "2. Restart the service with its restart command.",
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
                 subtask_id=subtask_id, version=version)


def store_group(store, contents, task_id=TASK_ID):
    skills = [make_group_skill(task_id, segment, content)
             for segment, content in enumerate(contents)]
    for skill in skills:
        store.save(skill, "skills")
    return skills


def make_traj(attempt, status, task_id=TASK_ID, role="weak", marker="x"):
    return Trajectory(
        trajectory_id=make_trajectory_id(task_id, role, attempt),
        task_id=task_id, benchmark=BENCHMARK, model_role=role,
        model_name="weak-model", attempt_index=attempt,
        steps=[Step(action=f"do {marker} {attempt}",
                    observation=f"obs {marker} {attempt}")],
        status=status,
    )


def seed_baseline(store, statuses, task_id=TASK_ID):
    for attempt, status in enumerate(statuses):
        store.save(make_traj(attempt, status, task_id), "trajectories")


def round_dir(store, round_):
    return store.run_dir / "stage4_acceptance" / "live_run" / f"round{round_:02d}"


def round_store(store, round_):
    return Store(round_dir(store, round_))


def seed_round(store, round_, statuses, task_id=TASK_ID):
    rs = round_store(store, round_)
    for attempt, status in enumerate(statuses):
        rs.save(make_traj(attempt, status, task_id), "trajectories")
    return rs


def freeze_audit(store, round_=0, task_id=TASK_ID, row=None):
    """Freeze the group's audit line. The stage appends once per group;
    the fixture keeps ONE line per task so a test can re-freeze a
    tampered row without stacking copies (readers take the first line
    of the group either way)."""
    if row is None:
        row = audit_row(store, task_id, round_)
    path = round_dir(store, round_) / la.INJECTION_AUDIT_NAME
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


def plan_script(actions, diagnosis="the injected skill did not help"):
    """A G10 response: one (skill_id, action, problem) per group member."""
    return json_script({
        "diagnosis": diagnosis,
        "skill_actions": [
            {"skill_id": skill_id, "action": action, "problem": problem}
            for skill_id, action, problem in actions
        ],
    })


def revision_script(text, category="missing_step",
                    reason="spelled out the verification step"):
    return json_script({"skill_text": text, "failure_category": category,
                        "revision_reason": reason})


def seeded_run(tmp_path, *, baseline=("failure", "failure", "failure"),
               live=("failure", "failure", "failure"),
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
    assert (la.WEAK_ALREADY_KNOWS, la.LIVE_NO_IMPROVEMENT,
            la.LIVE_REVISION_DELETED, la.MODEL_OUTPUT_ERROR,
            la.LIVE_UNDETERMINED, la.LIVE_INJECTION_OVERFLOW) == (
        "weak_already_knows", "live_no_improvement", "live_revision_deleted",
        "model_output_error", "live_undetermined", "live_injection_overflow")


def test_expected_units_is_the_full_set(tmp_path):
    assert expected_units(TASK_ID, N_ATTEMPTS) == {TASK_ID: N_ATTEMPTS}


# ---------------------------------------------------------------------------
# load_baseline
# ---------------------------------------------------------------------------

def test_load_baseline_reads_only_the_weak_arm_by_id(tmp_path):
    store = Store(tmp_path / "run")
    seed_baseline(store, ("failure", "success", "failure"))
    for attempt in range(2):
        store.save(make_traj(attempt, "success", role="strong"),
                   "trajectories")

    trajectories, baseline = load_baseline(store, TASK_ID, N_ATTEMPTS)

    assert [t.trajectory_id for t in trajectories] == [
        make_trajectory_id(TASK_ID, "weak", i) for i in range(N_ATTEMPTS)]
    assert baseline == counts(3, 1)


def test_load_baseline_counts_accepted_holes_as_infra(tmp_path):
    store = Store(tmp_path / "run")
    seed_baseline(store, ("failure", "success"))
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
    seed_baseline(store, ("failure", "failure"))

    with pytest.raises(InfrastructureError, match="stage 1 resumes first"):
        load_baseline(store, TASK_ID, N_ATTEMPTS)


def test_load_baseline_zero_valid_is_config_error(tmp_path):
    store = Store(tmp_path / "run")
    hole0 = make_trajectory_id(TASK_ID, "weak", 0)
    hole1 = make_trajectory_id(TASK_ID, "weak", 1)
    hole2 = make_trajectory_id(TASK_ID, "weak", 2)
    write_stage1_shortfall(store, {hole0, hole1, hole2})

    with pytest.raises(ConfigError, match="no valid attempt"):
        load_baseline(store, TASK_ID, N_ATTEMPTS)


def test_load_baseline_aux_scorer_counts(tmp_path):
    store = Store(tmp_path / "run")
    seed_baseline(store, ("failure", "success", "failure"))

    _, baseline = load_baseline(store, TASK_ID, N_ATTEMPTS,
                                aux_scorer=lambda t: t.status == "success")

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
    gone.discard_reason = la.LIVE_REVISION_DELETED
    other = make_group_skill(OTHER_TASK_ID, 0, MARKED_A)
    merged = Skill(skill_id=make_merged_skill_id(0), content=MARKED_A,
                   subtask_id=keep.subtask_id)
    for skill in (keep, gone, other, merged):
        store.save(skill, "skills")

    groups = pending_groups(store)

    # Terminal members, merged products and unknown-chain ids are not
    # units; every pending candidate group is, keyed by its task id.
    assert groups == {TASK_ID: [keep], OTHER_TASK_ID: [other]}
    assert la._pending_group(store, TASK_ID) == [keep]
    assert la._pending_group(store, make_task_id("tb", "nobody")) == []


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
    gone.discard_reason = la.LIVE_REVISION_DELETED
    elsewhere = make_group_skill(OTHER_TASK_ID, 0, MARKED_A)
    merged = Skill(skill_id=make_merged_skill_id(0), content=MARKED_A,
                   subtask_id=keep.subtask_id)
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
    statuses = ["success"] * ls + ["failure"] * (lv - ls)
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
    store, skills = seeded_run(tmp_path, baseline=("failure", "success",
                                                   "failure"))
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
                      / la.VERDICTS_LEDGER)
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
                      / la.VERDICTS_LEDGER)
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
    seed_round(store, 0, ["failure"] * N_ATTEMPTS + ["success"])
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
    store, _ = seeded_run(tmp_path, live=("success", "failure", "failure"))
    seed_round(store, 0, ("success",) * N_ATTEMPTS, task_id=OTHER_TASK_ID)

    verdict = judge_round(
        store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
        baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS, skip_ids=[])

    assert verdict["live"] == {"valid": 3, "success": 1}


def test_judge_counts_holes_and_flags_shortfall(tmp_path):
    store, _ = seeded_run(tmp_path, live=("success", "success"))
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
    store, _ = seeded_run(tmp_path, live=("failure", "failure"))

    with pytest.raises(CodeBugError, match="incomplete"):
        judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                    baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
                    skip_ids=[])


def test_judge_requires_a_dispatched_round_store(tmp_path):
    store = Store(tmp_path / "run")
    seed_baseline(store, ("failure",) * N_ATTEMPTS)
    store_group(store, [MARKED_A])

    # No audit frozen and no round store: the guard that fires first is
    # the dispatch one (judging reads the round store before the audit).
    with pytest.raises(CodeBugError, match="never dispatched"):
        judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                    baseline_counts=counts(3, 0), n_attempts=N_ATTEMPTS,
                    skip_ids=[])


def test_judge_requires_the_frozen_audit_row(tmp_path):
    store, _ = seeded_run(tmp_path)
    (round_dir(store, 0) / la.INJECTION_AUDIT_NAME).unlink()

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

    assert verdict["verdict"] == la.LIVE_UNDETERMINED
    assert verdict["baseline"] is None and verdict["live"] is None
    assert verdict["better"] is None
    assert verdict["injection_tokens"] == audit["injection_tokens"]
    assert verdict["judged_with_shortfall"] is True
    # The group terminalizes from the row alone.
    result = finalize_group(store, TASK_ID)
    assert result["outcome"] == "discarded"
    assert result["reason"] == la.LIVE_UNDETERMINED
    assert all(s["status"] == "discarded"
               and s["discard_reason"] == la.LIVE_UNDETERMINED
               for s in stored_skills(store).values())


def _unit_list():
    return [make_trajectory_id(TASK_ID, "weak", i)
            for i in range(N_ATTEMPTS)]


def test_judge_stored_infra_trajectory_is_not_undetermined(tmp_path):
    """A round store holding an infra_error trajectory (impossible through
    K1, which never stores infra-exhausted units) is NOT the all-holes
    case: it flows to the rule, whose defensive branch reads "no live
    evidence" as not better instead of crashing."""
    store, _ = seeded_run(tmp_path, live=("infra_error", "failure", "failure"))

    verdict = judge_round(
        store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
        baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS, skip_ids=[])

    assert "verdict" not in verdict
    assert verdict["better"] is False
    assert verdict["live"] == {"valid": 2, "success": 0}


def test_judge_undetermined_cannot_be_asserted_by_caller(tmp_path):
    store, _ = seeded_run(tmp_path)
    with pytest.raises(CodeBugError, match="derived by judge_round"):
        judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                    baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
                    skip_ids=[], terminal=la.LIVE_UNDETERMINED)


def test_judge_weak_already_knows_zero_execution(tmp_path):
    store = Store(tmp_path / "run")
    seed_baseline(store, ("success", "success", "success"))
    store_group(store, [MARKED_A, PROSE])

    verdict = judge_round(
        store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
        baseline_counts=counts(3, 3), n_attempts=N_ATTEMPTS,
        skip_ids=[], terminal=la.WEAK_ALREADY_KNOWS)

    assert verdict["verdict"] == la.WEAK_ALREADY_KNOWS
    assert verdict["baseline"] is None and verdict["live"] is None
    assert verdict["better"] is None
    assert verdict["injection_tokens"] is None   # nothing was ever injected
    assert verdict["judged_with_shortfall"] is False
    # Zero execution means zero dispatch: no round store came to exist.
    assert not round_dir(store, 0).exists()
    finalize_group(store, TASK_ID)
    assert all(s["status"] == "discarded"
               and s["discard_reason"] == la.WEAK_ALREADY_KNOWS
               for s in stored_skills(store).values())


def test_judge_weak_already_knows_requires_all_success(tmp_path):
    store, _ = seeded_run(tmp_path)
    with pytest.raises(CodeBugError, match="not all-success"):
        judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                    baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
                    skip_ids=[], terminal=la.WEAK_ALREADY_KNOWS)


def test_judge_weak_already_knows_is_limited_to_safe_rules(tmp_path):
    store = Store(tmp_path / "run")
    seed_baseline(store, ("success",) * N_ATTEMPTS)
    store_group(store, [MARKED_A])
    with pytest.raises(CodeBugError, match="strict-family"):
        judge_round(store=store, task_id=TASK_ID, round_=0,
                    rule_name="live_test_pass_at_n",
                    baseline_counts=counts(3, 3), n_attempts=N_ATTEMPTS,
                    skip_ids=[], terminal=la.WEAK_ALREADY_KNOWS)


def overflow_run(tmp_path, tokens):
    """A dispatched-looking run whose frozen audit row claims ``tokens``."""
    store = Store(tmp_path / "run")
    seed_baseline(store, ("failure",) * N_ATTEMPTS)
    seed_round(store, 0, ("failure",) * N_ATTEMPTS)
    skills = store_group(store, [MARKED_A])
    append_jsonl(round_dir(store, 0) / la.INJECTION_AUDIT_NAME, {
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
        skip_ids=[], terminal=la.LIVE_INJECTION_OVERFLOW,
        max_injection_tokens=1000)

    assert verdict["verdict"] == la.LIVE_INJECTION_OVERFLOW
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
                    skip_ids=[], terminal=la.LIVE_INJECTION_OVERFLOW)
    with pytest.raises(CodeBugError, match="own evidence"):
        judge_round(store=overflow_run(tmp_path / "under", tokens=10),
                    task_id=TASK_ID, round_=0, rule_name=RULE,
                    baseline_counts=counts(3, 1), n_attempts=N_ATTEMPTS,
                    skip_ids=[], terminal=la.LIVE_INJECTION_OVERFLOW,
                    max_injection_tokens=1000)


# ---------------------------------------------------------------------------
# judge_round: the aux slot
# ---------------------------------------------------------------------------

def test_judge_aux_slot_records_both_arms(tmp_path):
    store, _ = seeded_run(tmp_path, baseline=("failure", "success",
                                              "failure"),
                          live=("success", "failure", "failure"))
    _, baseline = load_baseline(store, TASK_ID, N_ATTEMPTS,
                                aux_scorer=lambda t: t.status == "success")

    verdict = judge_round(
        store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
        baseline_counts=baseline, n_attempts=N_ATTEMPTS, skip_ids=[],
        aux_scorer=lambda t: t.status == "success")

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
# attribute_group
# ---------------------------------------------------------------------------

def test_clip_view_keeps_head_and_tail_under_budget():
    """The clip keeps the head and tail of an over-budget view (the tail
    is where terminal-benchmark failure evidence lives), marks the
    elided middle, and never exceeds the budget."""
    text = "A" * 600 + "B" * 600 + "C" * 600
    clipped = la._clip_view(text, 700)
    assert len(clipped) <= 700
    assert clipped.startswith("A")
    assert clipped.endswith("C")
    assert "chars elided to fit the attribution view budget" in clipped
    assert la._clip_view("short view", 700) == "short view"   # no-op


def test_attribution_views_are_clipped_to_budget(tmp_path, monkeypatch):
    """Three long-trajectory views are clipped per-view against the
    joined budget: the author's user prompt carries the elision marker
    for every one, and the attribution row still lands normally. Without
    the clip a context overflow comes back as a permanent 400/413 that
    neither repair budget nor the stage catches, hard-stopping the run."""
    store, skills = seeded_run(tmp_path)
    rs = round_store(store, 0)
    for attempt in range(N_ATTEMPTS):
        traj = make_traj(attempt, "failure")
        traj.steps = [Step(action=f"cmd {attempt}-{i}",
                           observation="x" * 400)
                      for i in range(40)]
        rs.save(traj, "trajectories")
    monkeypatch.setattr(la, "_LIVE_VIEWS_BUDGET_CHARS", 2_000)
    author = make_fake_handle("author", [
        plan_script([(s.skill_id, "keep", "") for s in skills])])

    row = attribute_group(
        store=store, task_id=TASK_ID, round_=0, n_attempts=N_ATTEMPTS,
        task_instruction=TASK_INSTRUCTION, weak_profile=WEAK_PROFILE,
        author_handle=author, view_rules=None, run_dir=store.run_dir)

    assert row is not None and row["schema"] == "live-attribution-v1"
    prompt = author.client.calls[0]["user"]
    assert (prompt.count("chars elided to fit the attribution view budget")
            == N_ATTEMPTS)


def attribution_run(tmp_path, author_script, *, plan_actions=None,
                    baseline=("failure",) * N_ATTEMPTS,
                    live=("failure",) * N_ATTEMPTS,
                    contents=(MARKED_A, MARKED_A, MARKED_A)):
    store, skills = seeded_run(tmp_path, baseline=baseline, live=live,
                               contents=contents)
    author = make_fake_handle("author", author_script)
    row = attribute_group(
        store=store, task_id=TASK_ID, round_=0, n_attempts=N_ATTEMPTS,
        task_instruction=TASK_INSTRUCTION, weak_profile=WEAK_PROFILE,
        author_handle=author, view_rules=None, run_dir=store.run_dir)
    return store, skills, author, row


def test_attribution_appends_row_with_store_versions(tmp_path):
    store, skills = seeded_run(tmp_path, live=("failure", "success",
                                               "failure"))
    # The model never sees or emits versions; the row's versions are read
    # from the store at append time -- prove it by bumping one first.
    bumped = store.load("skills", skills[0].skill_id, Skill)
    bumped.version = 4
    bumped.content = MARKED_A_REVISED
    store.save(bumped, "skills")
    author = make_fake_handle("author", [
        plan_script([(skills[0].skill_id, "revise", "trigger too vague"),
                     (skills[1].skill_id, "keep", ""),
                     (skills[2].skill_id, "delete", "harmful here")]),
    ])

    row = attribute_group(
        store=store, task_id=TASK_ID, round_=0, n_attempts=N_ATTEMPTS,
        task_instruction=TASK_INSTRUCTION, weak_profile=WEAK_PROFILE,
        author_handle=author, view_rules=None, run_dir=store.run_dir)

    assert row["schema"] == "live-attribution-v1"
    assert row["task_id"] == TASK_ID and row["round"] == 0
    assert row["diagnosis"] == "the injected skill did not help"
    assert row["skill_actions"] == [
        {"skill_id": skills[0].skill_id, "version": 4, "action": "revise",
         "problem": "trigger too vague"},
        {"skill_id": skills[1].skill_id, "version": 1, "action": "keep",
         "problem": ""},
        {"skill_id": skills[2].skill_id, "version": 1, "action": "delete",
         "problem": "harmful here"},
    ]
    frozen = read_jsonl(round_dir(store, 0) / la.INJECTION_AUDIT_NAME)[0]
    # Bumping the skill changed what a re-derivation would say now; the
    # sha on the row is the frozen audit's, read from disk.
    assert row["injection_block_sha256"] == frozen["injection_block_sha256"]
    current_members = [store.load("skills", skill_id, Skill)
                       for skill_id in store.ids("skills")]
    current_block = format_skills_block(current_members)
    assert row["injection_block_sha256"] != la._block_sha(current_block)
    stored = read_jsonl(store.run_dir / "stage4_acceptance" / "live_run"
                       / la.ATTRIBUTIONS_LEDGER)
    assert stored == [row]


def test_attribution_lookup_reuses_first_row_without_reasking(tmp_path):
    store, skills = seeded_run(tmp_path)
    first = {"schema": "live-attribution-v1", "task_id": TASK_ID,
             "round": 0, "diagnosis": "the stored judgment",
             "skill_actions": [
                 {"skill_id": skills[0].skill_id, "version": 1,
                  "action": "keep", "problem": ""}],
             "injection_block_sha256": "frozen"}
    append_jsonl(store.run_dir / "stage4_acceptance" / "live_run"
                 / la.ATTRIBUTIONS_LEDGER, first)
    author = make_fake_handle("author", [])

    row = attribute_group(
        store=store, task_id=TASK_ID, round_=0, n_attempts=N_ATTEMPTS,
        task_instruction=TASK_INSTRUCTION, weak_profile=WEAK_PROFILE,
        author_handle=author, view_rules=None, run_dir=store.run_dir)

    assert row == first
    assert author.client.calls == []


def test_attribution_budget_repairs_then_succeeds(tmp_path):
    store, skills = seeded_run(tmp_path)
    author = make_fake_handle("author", [
        ModelOutputError("LIVE_RUN_ATTRIBUTION: bad coverage"),
        plan_script([(s.skill_id, "keep", "") for s in skills]),
    ])

    row = attribute_group(
        store=store, task_id=TASK_ID, round_=0, n_attempts=N_ATTEMPTS,
        task_instruction=TASK_INSTRUCTION, weak_profile=WEAK_PROFILE,
        author_handle=author, view_rules=None, run_dir=store.run_dir)

    assert row["diagnosis"] == "the injected skill did not help"
    failures = read_jsonl(store.run_dir / "stage4_acceptance" / "live_run"
                          / la.ATTRIBUTION_FAILURES_LEDGER)
    assert [(f["try_index"], f["exhausted"]) for f in failures] == [
        (0, False)]
    assert len(author.client.calls) == 2


def test_attribution_budget_persists_across_crash(tmp_path):
    """One failed try is already ledgered (a crash after its line): the
    resumed call gets the REMAINING budget, not a fresh one."""
    store, skills = seeded_run(tmp_path)
    append_jsonl(store.run_dir / "stage4_acceptance" / "live_run"
                 / la.ATTRIBUTION_FAILURES_LEDGER,
                 {"schema": "live-repair-failure-v1", "task_id": TASK_ID,
                  "round": 0, "skill_id": None, "skill_version": None,
                  "try_index": 0, "exhausted": False, "message": "earlier"})
    author = make_fake_handle("author", [
        plan_script([(s.skill_id, "keep", "") for s in skills])])

    row = attribute_group(
        store=store, task_id=TASK_ID, round_=0, n_attempts=N_ATTEMPTS,
        task_instruction=TASK_INSTRUCTION, weak_profile=WEAK_PROFILE,
        author_handle=author, view_rules=None, run_dir=store.run_dir)

    assert row is not None
    assert len(author.client.calls) == 1   # only the last try was left
    failures = read_jsonl(store.run_dir / "stage4_acceptance" / "live_run"
                          / la.ATTRIBUTION_FAILURES_LEDGER)
    assert len(failures) == 1              # the pre-crash line is the budget


def test_attribution_exhaustion_discards_first_then_ledger_row(tmp_path,
                                                               monkeypatch):
    store, skills = seeded_run(tmp_path)
    author = make_fake_handle("author", [
        ModelOutputError("LIVE_RUN_ATTRIBUTION: bad one"),
        ModelOutputError("LIVE_RUN_ATTRIBUTION: bad two"),
    ])
    # Terminal states first: at the moment the exhausted row lands, every
    # member is already discarded (the discard is the truth, the line is
    # its receipt).
    seen = {}

    def spy(path, record):
        if record.get("exhausted"):
            seen["statuses"] = {
                skill_id: store.load("skills", skill_id, Skill).status
                for skill_id in store.ids("skills")}
        return append_jsonl(path, record)

    monkeypatch.setattr(la, "append_jsonl", spy)
    row = attribute_group(
        store=store, task_id=TASK_ID, round_=0, n_attempts=N_ATTEMPTS,
        task_instruction=TASK_INSTRUCTION, weak_profile=WEAK_PROFILE,
        author_handle=author, view_rules=None, run_dir=store.run_dir)
    monkeypatch.undo()

    assert row is None
    assert len(author.client.calls) == 2
    assert seen["statuses"] == {
        skill.skill_id: "discarded" for skill in skills}
    failures = read_jsonl(store.run_dir / "stage4_acceptance" / "live_run"
                          / la.ATTRIBUTION_FAILURES_LEDGER)
    assert [(f["try_index"], f["exhausted"]) for f in failures] == [
        (0, False), (1, True)]
    assert all(s["status"] == "discarded"
               and s["discard_reason"] == la.MODEL_OUTPUT_ERROR
               for s in stored_skills(store).values())


def test_attribution_infra_propagates_group_stays_pending(tmp_path):
    store, skills = seeded_run(tmp_path)
    author = make_fake_handle("author", [
        InfrastructureError("endpoint unreachable")])

    with pytest.raises(InfrastructureError):
        attribute_group(
            store=store, task_id=TASK_ID, round_=0, n_attempts=N_ATTEMPTS,
            task_instruction=TASK_INSTRUCTION, weak_profile=WEAK_PROFILE,
            author_handle=author, view_rules=None, run_dir=store.run_dir)

    assert all(s["status"] == "pending" for s in stored_skills(store).values())
    assert read_jsonl(store.run_dir / "stage4_acceptance" / "live_run"
                      / la.ATTRIBUTIONS_LEDGER) == []
    assert read_jsonl(store.run_dir / "stage4_acceptance" / "live_run"
                      / la.ATTRIBUTION_FAILURES_LEDGER) == []


def test_attribution_prompt_carries_composed_inputs(tmp_path):
    store, skills = seeded_run(
        tmp_path, baseline=("failure", "success", "failure"),
        live=("failure", "success", "failure"))
    author = make_fake_handle("author", [
        plan_script([(s.skill_id, "keep", "") for s in skills])])

    attribute_group(
        store=store, task_id=TASK_ID, round_=0, n_attempts=N_ATTEMPTS,
        task_instruction=TASK_INSTRUCTION, weak_profile=WEAK_PROFILE,
        author_handle=author, view_rules=None, run_dir=store.run_dir)

    prompt = author.client.calls[0]["user"]
    # The baseline is one status line per attempt, successes included in
    # the live views, and every group member is listed.
    assert "attempt 0: failure" in prompt
    assert "attempt 1: success" in prompt
    assert "do x 1" in prompt          # a live view's step text
    assert TASK_INSTRUCTION in prompt
    assert MARKED_A in prompt
    assert WEAK_PROFILE in prompt


def test_attribution_requires_dispatched_round(tmp_path):
    store = Store(tmp_path / "run")
    seed_baseline(store, ("failure",) * N_ATTEMPTS)
    store_group(store, [MARKED_A])
    with pytest.raises(CodeBugError, match="never dispatched"):
        attribute_group(
            store=store, task_id=TASK_ID, round_=0, n_attempts=N_ATTEMPTS,
            task_instruction=TASK_INSTRUCTION, weak_profile=WEAK_PROFILE,
            author_handle=make_fake_handle("author", []),
            run_dir=store.run_dir)


# ---------------------------------------------------------------------------
# apply_revisions
# ---------------------------------------------------------------------------

def apply_run(tmp_path, plan_actions, rewriter_script, *,
              contents=(MARKED_A, MARKED_A, MARKED_A)):
    store, skills = seeded_run(tmp_path, contents=contents)
    append_jsonl(store.run_dir / "stage4_acceptance" / "live_run"
                 / la.ATTRIBUTIONS_LEDGER,
                 {"schema": "live-attribution-v1", "task_id": TASK_ID,
                  "round": 0, "diagnosis": "the steps were skipped",
                  "skill_actions": [
                      {"skill_id": skill_id, "version": 1, "action": action,
                       "problem": problem}
                      for skill_id, action, problem in plan_actions],
                  "injection_block_sha256": "frozen"})
    rewriter = make_fake_handle("rewriter", rewriter_script)
    outcomes = apply_revisions(
        store=store, task_id=TASK_ID, round_=0, rewriter_handle=rewriter,
        weak_profile=WEAK_PROFILE, view_rules=None, run_dir=store.run_dir)
    return store, skills, rewriter, outcomes


def test_apply_keep_delete_revise(tmp_path):
    skills_ids = group_skill_ids()
    store, skills, rewriter, outcomes = apply_run(
        tmp_path,
        [(skills_ids[0], "keep", ""),
         (skills_ids[1], "delete", "wrong for this group"),
         (skills_ids[2], "revise", "the trigger never fires")],
        [revision_script(MARKED_A_REVISED)])

    assert [o["outcome"] for o in outcomes] == [
        "kept", "deleted", "revised"]
    stored = stored_skills(store)
    assert stored[skills_ids[0]]["status"] == "pending"
    assert stored[skills_ids[1]]["status"] == "discarded"
    assert stored[skills_ids[1]]["discard_reason"] == la.LIVE_REVISION_DELETED
    revised = stored[skills_ids[2]]
    assert revised["version"] == 2
    assert revised["content"] == MARKED_A_REVISED
    assert revised["rewrite_history"] == [{
        "content": MARKED_A, "error_category": "missing_step",
        "reason": "spelled out the verification step"}]
    assert len(rewriter.client.calls) == 1


def test_apply_revise_prompt_carries_report_and_failure_views(tmp_path):
    skills_ids = group_skill_ids()
    store, skills, rewriter, _ = apply_run(
        tmp_path,
        [(skills_ids[0], "revise", "the verify step is missing")],
        [revision_script(MARKED_A_REVISED)],
        contents=(MARKED_A,))

    prompt = rewriter.client.calls[0]["user"]
    assert "the steps were skipped" in prompt      # the group diagnosis
    assert "This skill's problem: the verify step is missing" in prompt
    assert "do x 0" in prompt                      # the failed attempt's view
    assert MARKED_A in prompt
    assert WEAK_PROFILE in prompt


def test_apply_failure_view_is_clipped_to_budget(tmp_path, monkeypatch):
    """The revision call's failure view obeys its own hard budget: the
    rewriter's prompt carries the elision marker and the revision still
    lands (the same permanent-400 overflow the G10 clip guards against)."""
    skills_ids = group_skill_ids()
    store, skills = seeded_run(tmp_path, contents=(MARKED_A,))
    rs = round_store(store, 0)
    for attempt in range(N_ATTEMPTS):
        traj = make_traj(attempt, "failure")
        traj.steps = [Step(action=f"cmd {attempt}-{i}",
                           observation="y" * 400)
                      for i in range(40)]
        rs.save(traj, "trajectories")
    append_jsonl(store.run_dir / "stage4_acceptance" / "live_run"
                 / la.ATTRIBUTIONS_LEDGER,
                 {"schema": "live-attribution-v1", "task_id": TASK_ID,
                  "round": 0, "diagnosis": "the steps were skipped",
                  "skill_actions": [
                      {"skill_id": skills_ids[0], "version": 1,
                       "action": "revise",
                       "problem": "the verify step is missing"}],
                  "injection_block_sha256": "frozen"})
    monkeypatch.setattr(la, "_FAILURE_VIEW_BUDGET_CHARS", 700)
    rewriter = make_fake_handle("rewriter",
                                [revision_script(MARKED_A_REVISED)])

    outcomes = apply_revisions(
        store=store, task_id=TASK_ID, round_=0, rewriter_handle=rewriter,
        weak_profile=WEAK_PROFILE, view_rules=None, run_dir=store.run_dir)

    assert outcomes[0]["outcome"] == "revised"
    prompt = rewriter.client.calls[0]["user"]
    assert (prompt.count("chars elided to fit the attribution view budget")
            == N_ATTEMPTS)


def test_apply_marker_failure_rides_the_budget(tmp_path):
    skills_ids = group_skill_ids()
    store, skills, rewriter, outcomes = apply_run(
        tmp_path,
        [(skills_ids[0], "revise", "trigger too vague")],
        [revision_script(MARKED_A_DROPPED),      # drops [Verify]
         revision_script(MARKED_A_REVISED)],
        contents=(MARKED_A,))

    assert outcomes[0]["outcome"] == "revised"
    stored = stored_skills(store)
    assert stored[skills_ids[0]]["version"] == 2
    assert stored[skills_ids[0]]["content"] == MARKED_A_REVISED
    failures = read_jsonl(store.run_dir / "stage4_acceptance" / "live_run"
                          / la.REVISION_FAILURES_LEDGER)
    assert [(f["try_index"], f["exhausted"]) for f in failures] == [(0, False)]
    assert "bracket sections" in failures[0]["message"]
    assert len(rewriter.client.calls) == 2


def test_apply_budget_exhaustion_discards_only_that_skill(tmp_path):
    skills_ids = group_skill_ids()
    store, skills, rewriter, outcomes = apply_run(
        tmp_path,
        [(skills_ids[0], "keep", ""),
         (skills_ids[1], "revise", "hopeless")],
        [ModelOutputError("LIVE_SKILL_REVISION: bad json"),
         revision_script(MARKED_A_DROPPED)],
        contents=(MARKED_A, MARKED_A))

    assert [o["outcome"] for o in outcomes] == [
        "kept", "discarded_model_output_error"]
    stored = stored_skills(store)
    assert stored[skills_ids[0]]["status"] == "pending"
    assert stored[skills_ids[1]]["status"] == "discarded"
    assert stored[skills_ids[1]]["discard_reason"] == la.MODEL_OUTPUT_ERROR
    assert stored[skills_ids[1]]["version"] == 1
    failures = read_jsonl(store.run_dir / "stage4_acceptance" / "live_run"
                          / la.REVISION_FAILURES_LEDGER)
    assert [(f["try_index"], f["exhausted"]) for f in failures] == [
        (0, False), (1, True)]


def test_apply_replay_guard_three_branches(tmp_path):
    skills_ids = group_skill_ids()
    # Branch 1: version == from_version -> the revision is applied.
    store, skills, rewriter, _ = apply_run(
        tmp_path,
        [(skills_ids[0], "revise", "problem one")],
        [revision_script(MARKED_A_REVISED)],
        contents=(MARKED_A,))
    assert stored_skills(store)[skills_ids[0]]["version"] == 2

    # Branch 2: version == from_version + 1 -> already landed, skipped,
    # and the rewriter is not asked again.
    outcomes = apply_revisions(
        store=store, task_id=TASK_ID, round_=0,
        rewriter_handle=make_fake_handle("rewriter", []),
        weak_profile=WEAK_PROFILE, view_rules=None, run_dir=store.run_dir)
    assert outcomes[0]["outcome"] == "already_revised"

    # Branch 3: any other version is a store that drifted from the ledger.
    drifted = store.load("skills", skills_ids[0], Skill)
    drifted.version = 5
    store.save(drifted, "skills")
    with pytest.raises(CodeBugError, match="drifted from the ledger"):
        apply_revisions(
            store=store, task_id=TASK_ID, round_=0,
            rewriter_handle=make_fake_handle("rewriter", []),
            weak_profile=WEAK_PROFILE, view_rules=None, run_dir=store.run_dir)


def test_apply_delete_replay_skips_terminal(tmp_path):
    skills_ids = group_skill_ids()
    store, skills, rewriter, outcomes = apply_run(
        tmp_path,
        [(skills_ids[0], "delete", "wrong for this group")],
        [],
        contents=(MARKED_A,))
    first = stored_skills(store)[skills_ids[0]]

    outcomes = apply_revisions(
        store=store, task_id=TASK_ID, round_=0,
        rewriter_handle=make_fake_handle("rewriter", []),
        weak_profile=WEAK_PROFILE, view_rules=None, run_dir=store.run_dir)

    assert outcomes[0]["outcome"] == "skipped_terminal"
    assert stored_skills(store)[skills_ids[0]] == first


def test_apply_infra_failure_recorded_and_group_continues(tmp_path):
    skills_ids = group_skill_ids()
    store, skills, rewriter, outcomes = apply_run(
        tmp_path,
        [(skills_ids[0], "revise", "problem one"),
         (skills_ids[1], "revise", "problem two")],
        [InfrastructureError("sandbox died"),
         revision_script(MARKED_A_REVISED)],
        contents=(MARKED_A, MARKED_A))

    assert [o["outcome"] for o in outcomes] == [
        "infra_failed", "revised"]
    stored = stored_skills(store)
    assert stored[skills_ids[0]]["status"] == "pending"   # retried on resume
    assert stored[skills_ids[1]]["version"] == 2
    failures = read_jsonl(store.run_dir / "failed_units.jsonl")
    assert [f["unit_id"] for f in failures] == [skills_ids[0]]
    assert failures[0]["stage"] == la.FAILURE_STAGE
    assert failures[0]["error_class"] == "InfrastructureError"


def test_apply_requires_the_attribution_row(tmp_path):
    store, _ = seeded_run(tmp_path)
    with pytest.raises(CodeBugError, match="attribute the group"):
        apply_revisions(
            store=store, task_id=TASK_ID, round_=0,
            rewriter_handle=make_fake_handle("rewriter", []),
            weak_profile=WEAK_PROFILE, view_rules=None, run_dir=store.run_dir)


def test_apply_of_an_unmarked_original_passes_through(tmp_path):
    """The K4/K5 precedent: prose content (no section markers) is never
    marker-checked, so any rewrite of it lands."""
    skills_ids = group_skill_ids()
    store, skills, rewriter, outcomes = apply_run(
        tmp_path,
        [(skills_ids[0], "revise", "too terse")],
        [revision_script(MARKED_A)],   # gains markers, original had none
        contents=(PROSE,))

    assert outcomes[0]["outcome"] == "revised"
    assert stored_skills(store)[skills_ids[0]]["content"] == MARKED_A


# ---------------------------------------------------------------------------
# no_op_guard
# ---------------------------------------------------------------------------

def test_no_op_guard_fires_on_identical_block(tmp_path):
    store, skills = seeded_run(tmp_path)
    append_jsonl(store.run_dir / "stage4_acceptance" / "live_run"
                 / la.ATTRIBUTIONS_LEDGER,
                 {"schema": "live-attribution-v1", "task_id": TASK_ID,
                  "round": 0, "diagnosis": "all keep",
                  "skill_actions": [
                      {"skill_id": s.skill_id, "version": 1,
                       "action": "keep", "problem": ""} for s in skills],
                  "injection_block_sha256": "frozen"})

    assert no_op_guard(store, TASK_ID, 0) is True

    assert all(s["status"] == "discarded"
               and s["discard_reason"] == la.LIVE_NO_IMPROVEMENT
               for s in stored_skills(store).values())
    # And no round 1 follows (the stage's dispatch set is empty).
    assert survivors_and_block(store, TASK_ID) == ([], "")


def test_no_op_guard_passes_when_the_block_changed(tmp_path):
    skills_ids = group_skill_ids()
    store, skills, rewriter, _ = apply_run(
        tmp_path,
        [(skills_ids[0], "revise", "trigger too vague")],
        [revision_script(MARKED_A_REVISED)])

    assert no_op_guard(store, TASK_ID, 0) is False

    assert all(s["status"] == "pending"
               for s in stored_skills(store).values())


def test_no_op_guard_reads_the_frozen_row_not_a_rederivation(tmp_path):
    """Tamper the frozen line and the guard's answer flips with it: the
    data source is the audit row's sha, never a re-render of round 0's
    pre-revision state (which no longer exists)."""
    store2 = Store(tmp_path / "tampered")
    seed_baseline(store2, ("failure",) * N_ATTEMPTS)
    seed_round(store2, 0, ("failure",) * N_ATTEMPTS)
    store_group(store2, [MARKED_A, MARKED_A])
    _, block = survivors_and_block(store2, TASK_ID)

    # A frozen line that does NOT match the survivors' current block.
    row = audit_row(store2, TASK_ID, 0)
    row["injection_block_sha256"] = "0" * 64
    freeze_audit(store2, 0, row=row)
    assert no_op_guard(store2, TASK_ID, 0) is False

    # The same store, frozen line matching the survivors: it fires even
    # though the row's member list says something else entirely.
    row["injection_block_sha256"] = la._block_sha(block)
    freeze_audit(store2, 0, row=row)
    assert no_op_guard(store2, TASK_ID, 0) is True
    assert all(s["status"] == "discarded"
               and s["discard_reason"] == la.LIVE_NO_IMPROVEMENT
               for s in stored_skills(store2).values())


def test_no_op_guard_empty_survivors_returns_false(tmp_path):
    skills_ids = group_skill_ids()
    store, skills, rewriter, _ = apply_run(
        tmp_path,
        [(skills_ids[0], "delete", "wrong for this group"),
         (skills_ids[1], "delete", "also wrong")],
        [],
        contents=(MARKED_A, MARKED_A))

    assert no_op_guard(store, TASK_ID, 0) is False


# ---------------------------------------------------------------------------
# finalize_group
# ---------------------------------------------------------------------------

def verdicts_path(store):
    return (store.run_dir / "stage4_acceptance" / "live_run"
            / la.VERDICTS_LEDGER)


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
    discard_skill(store, discard_me, la.LIVE_REVISION_DELETED)
    store_verdict(store, judged_row(0, better=False))
    store_verdict(store, judged_row(1, better=False))

    result = finalize_group(store, TASK_ID)

    assert result["outcome"] == "discarded"
    assert result["reason"] == la.LIVE_NO_IMPROVEMENT
    assert result["committed"] == [skills_ids[0]]
    stored = stored_skills(store)
    assert stored[skills_ids[0]]["discard_reason"] == la.LIVE_NO_IMPROVEMENT
    assert stored[skills_ids[1]]["discard_reason"] == la.LIVE_REVISION_DELETED


def test_finalize_round1_pass_accepts_survivors(tmp_path):
    skills_ids = group_skill_ids()
    store, skills = seeded_run(tmp_path, contents=(MARKED_A, MARKED_A))
    discard_skill(store, store.load("skills", skills_ids[1], Skill),
                  la.LIVE_REVISION_DELETED)
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
    row["verdict"] = la.WEAK_ALREADY_KNOWS
    store_verdict(store, row)

    result = finalize_group(store, TASK_ID)

    assert result["outcome"] == "discarded"
    assert result["reason"] == la.WEAK_ALREADY_KNOWS
    assert all(s["discard_reason"] == la.WEAK_ALREADY_KNOWS
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
