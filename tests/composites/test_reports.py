"""Report-reader tests: the three readers of composites/reports.py.

Every fixture builds the run directory the way the stage would have
left it -- the existing composite tests' fixture shapes (K4's attempts
and ledgers, K6's round stores and frozen audit lines, stage 1's
trajectories and accepted holes) -- and asserts the row content each
reader derives. The readers are read-only: one test snapshots the run
tree before and after all three and proves not one byte moved.
"""

from pathlib import Path

import pytest

from expedistill.composites import live_acceptance as la
from expedistill.composites import shortfall
from expedistill.composites.acceptance import (
    LEAK_VERDICTS_LEDGER,
    REWRITE_FAILURE_LEDGER,
)
from expedistill.composites.reports import (
    baseline_report,
    exam_report,
    live_report,
)
from expedistill.core.ids import (
    make_attempt_id,
    make_exam_id,
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Attempt, Exam, Skill, Step, Trajectory
from expedistill.infra.reliability import ConfigError
from expedistill.infra.store import Store, append_jsonl

BENCHMARK = "tb"
TASK_ID = make_task_id(BENCHMARK, "t1")
OTHER_TASK_ID = make_task_id(BENCHMARK, "t2")
N_ATTEMPTS = 3
RULE = "strict_rate_gain"

MARKED_A = "\n".join([
    "[When to use] The service rejects connections and needs a restart.",
    "[Steps]",
    "1. Check the service status with the status command.",
    "2. Restart the service with the restart command.",
    "[Verify] The status command reports the service as running.",
    "[Do not use when] The status command already reports running.",
])


# ---------------------------------------------------------------------------
# Fixture helpers (the shapes tests/composites/ already use)
# ---------------------------------------------------------------------------

def make_traj(attempt, status, task_id, role="weak"):
    return Trajectory(
        trajectory_id=make_trajectory_id(task_id, role, attempt),
        task_id=task_id, benchmark=BENCHMARK, model_role=role,
        model_name=f"{role}-model", attempt_index=attempt,
        steps=[Step(action=f"do {attempt}",
                    observation=f"obs {attempt}")],
        status=status,
    )


def seed_task(store, task_id, weak_statuses, strong_statuses=()):
    for attempt, status in enumerate(weak_statuses):
        store.save(make_traj(attempt, status, task_id, "weak"),
                   "trajectories")
    for attempt, status in enumerate(strong_statuses):
        store.save(make_traj(attempt, status, task_id, "strong"),
                   "trajectories")


def accept_holes(store, unit_ids):
    shortfall.write_unit_ids(shortfall.stage1_shortfall_path(store.run_dir),
                             unit_ids)


def make_pair(task_id=BENCHMARK + ":x1"):
    """A (skill, exam) pair hanging on one subtask, stage-3 style."""
    subtask_id = make_subtask_id(
        make_trajectory_id(task_id, "strong", 0), 1)
    skill = Skill(skill_id=make_skill_id(subtask_id), content=MARKED_A,
                  subtask_id=subtask_id, source_subtasks=[subtask_id])
    exam = Exam(exam_id=make_exam_id(subtask_id),
                question_text="What should you do?",
                reference_answer="Restart the service.",
                subtask_id=subtask_id, skill_id=make_skill_id(subtask_id))
    return skill, exam


def make_attempt(exam, serial, *, purpose, skill_id=None, skill_version=None,
                 weak_answer="some answer", judge_verdict=False,
                 judge_reason="the answer skips the restart step",
                 retry_round=0):
    return Attempt(
        attempt_id=make_attempt_id(exam.exam_id, serial),
        exam_id=exam.exam_id,
        skill_id=skill_id,
        skill_version=skill_version,
        purpose=purpose,
        weak_answer=weak_answer,
        judge_verdict=judge_verdict,
        judge_reason=judge_reason,
        retry_round=retry_round,
    )


def group_skill(task_id, segment):
    subtask_id = make_subtask_id(make_trajectory_id(task_id, "strong", 0),
                                 segment)
    return Skill(skill_id=make_skill_id(subtask_id), content=MARKED_A,
                 subtask_id=subtask_id, source_subtasks=[subtask_id])


def store_group(store, task_id, count):
    return [store.save(group_skill(task_id, segment), "skills")
            or group_skill(task_id, segment)
            for segment in range(count)]


def round_dir(store, round_):
    return store.run_dir / "stage4_acceptance" / "live_run" / f"round{round_:02d}"


def freeze_audit(store, task_id, round_=0):
    row = la.audit_row(store, task_id, round_)
    append_jsonl(round_dir(store, round_) / la.INJECTION_AUDIT_NAME, row)
    return row


def seed_round(store, task_id, round_, statuses):
    directory = round_dir(store, round_)
    for attempt, status in enumerate(statuses):
        Store(directory).save(make_traj(attempt, status, task_id),
                              "trajectories")


def _tree(root):
    return sorted((str(path.relative_to(root)), path.read_bytes())
                  for path in Path(root).rglob("*") if path.is_file())


# ---------------------------------------------------------------------------
# baseline_report
# ---------------------------------------------------------------------------

def test_baseline_report_counts_and_f(tmp_path):
    store = Store(tmp_path / "run")
    t1, t2, t3, t4 = (make_task_id(BENCHMARK, name)
                     for name in ("t1", "t2", "t3", "t4"))
    seed_task(store, t1, ("success", "failure", "failure"),
              ("success", "failure"))
    seed_task(store, t2, ("failure", "failure"), ("failure", "failure"))
    seed_task(store, t3, ("success", "success", "success"),
              ("success", "success"))
    # t4: every unit is an accepted hole -- no trajectory, still a row.
    accept_holes(store, [make_trajectory_id(t2, "weak", 2)]
                 + [make_trajectory_id(t4, "weak", attempt)
                    for attempt in range(N_ATTEMPTS)]
                 + [make_trajectory_id(t4, "strong", attempt)
                    for attempt in range(2)])

    report = baseline_report(store, rule_name="any_weak_failed",
                             n_attempts_strong=2, n_attempts_weak=3)

    rows = {row["task_id"]: row for row in report["tasks"]}
    assert sorted(rows) == sorted([t1, t2, t3, t4])
    assert rows[t1]["weak"] == {"success": 1, "total": 3}
    assert rows[t1]["strong"] == {"success": 1, "total": 2}
    # The accepted hole keeps the full denominator and counts as infra.
    assert rows[t2]["weak"] == {"success": 0, "total": 3}
    assert rows[t4]["weak"] == {"success": 0, "total": 3}
    assert rows[t4]["strong"] == {"success": 0, "total": 2}
    # any_weak_failed: t1 (1 of 3), t2 (0 of 2 valid); t3 all-success and
    # t4 all-infra stay out (infra is not a model failure).
    assert report["F"] == [t1, t2]


def test_baseline_report_refuses_unknown_rule_names(tmp_path):
    store = Store(tmp_path / "run")
    with pytest.raises(ConfigError, match="task_select"):
        baseline_report(store, rule_name="no_such_rule",
                        n_attempts_strong=2, n_attempts_weak=3)


# ---------------------------------------------------------------------------
# exam_report
# ---------------------------------------------------------------------------

def test_exam_report_row_over_a_judged_round(tmp_path):
    store = Store(tmp_path / "run")
    skill, exam = make_pair()
    store.save(skill, "skills")
    store.save(exam, "exams")
    bare = make_attempt(exam, 0, purpose="bare", judge_verdict=False)
    failed = make_attempt(exam, 1, purpose="acceptance",
                          skill_id=skill.skill_id, skill_version=1,
                          weak_answer="restart it", judge_verdict=False)
    store.save(bare, "attempts")
    store.save(failed, "attempts")

    report = exam_report(store)

    assert len(report["skills"]) == 1
    row = report["skills"][0]
    assert row["skill_id"] == skill.skill_id
    assert row["version"] == 1
    assert row["status"] == "pending"
    assert row["bare_verdict"] is False
    assert row["leak_verdict"] is None
    # P7b: the pending revision's source is the latest judged with-skill
    # attempt, with the pointer back to it.
    assert row["attempt"] == {"attempt_id": failed.attempt_id,
                              "verdict": False,
                              "judge_reason": failed.judge_reason,
                              "weak_answer": "restart it"}
    assert row["rewrite_exhausted"] == 0


def test_exam_report_leak_row_and_exhausted_rewrites(tmp_path):
    store = Store(tmp_path / "run")
    skill, exam = make_pair()
    store.save(skill, "skills")
    store.save(exam, "exams")
    store.save(make_attempt(exam, 0, purpose="bare", judge_verdict=False),
               "attempts")
    ledger_dir = Path(store.dir_for("attempts")).parent
    append_jsonl(ledger_dir / LEAK_VERDICTS_LEDGER, {
        "skill_id": skill.skill_id, "skill_version": 1,
        "leaked": True, "reason": "the skill quotes the exam's credential",
    })
    append_jsonl(ledger_dir / REWRITE_FAILURE_LEDGER, {
        "schema": "rewrite-failure-v1", "skill_id": skill.skill_id,
        "skill_version": 1, "exam_id": exam.exam_id, "retry_round": 0,
        "try_index": 1, "exhausted": True, "message": "budget gone",
    })

    row = exam_report(store)["skills"][0]

    assert row["leak_verdict"] == {
        "skill_id": skill.skill_id, "skill_version": 1,
        "leaked": True, "reason": "the skill quotes the exam's credential",
    }
    assert row["rewrite_exhausted"] == 1
    # A round-0 leak has no with-skill attempt: the source falls back to
    # the bare attempt (P7b's rule).
    assert row["attempt"]["attempt_id"].endswith("/att00")


def test_exam_report_skips_candidates_without_exams(tmp_path):
    store = Store(tmp_path / "run")
    skill, _ = make_pair()
    store.save(skill, "skills")  # the exam never landed (stage 3 crashed)

    row = exam_report(store)["skills"][0]

    assert row["skill_id"] == skill.skill_id
    assert row["bare_verdict"] is None
    assert row["leak_verdict"] is None
    assert row["attempt"] is None


# ---------------------------------------------------------------------------
# live_report
# ---------------------------------------------------------------------------

def judged_round(tmp_path, *, task_id=TASK_ID,
                 live=("failure", "failure", "failure")):
    """A run directory with one judged round-0 group (no model calls:
    judging is a pure recomputation over the seeded stores)."""
    store = Store(tmp_path / "run")
    seed_task(store, task_id, ("failure",) * N_ATTEMPTS)
    skills = [group_skill(task_id, segment) for segment in range(2)]
    for skill in skills:
        store.save(skill, "skills")
    freeze_audit(store, task_id)
    seed_round(store, task_id, 0, live)
    _, baseline_counts = la.load_baseline(store, task_id, N_ATTEMPTS)
    la.judge_round(store=store, task_id=task_id, round_=0,
                   rule_name=RULE, baseline_counts=baseline_counts,
                   n_attempts=N_ATTEMPTS, skip_ids=[])
    return store, skills, baseline_counts


def test_live_report_row_of_a_judged_round(tmp_path):
    store, skills, _ = judged_round(tmp_path)

    report = live_report(store, round_=0)

    assert report["round"] == 0
    assert len(report["groups"]) == 1
    row = report["groups"][0]
    assert row["task_id"] == TASK_ID
    assert row["members"] == [{"skill_id": skill.skill_id, "version": 1}
                              for skill in skills]
    assert row["baseline"] == {"valid": N_ATTEMPTS, "success": 0}
    assert row["result"] == ["failure", "failure", "failure"]
    assert row["better"] is False
    assert row["verdict"] is None
    assert row["attribution"] is None


def test_live_report_carries_the_landed_attribution_plan(tmp_path):
    store, skills, _ = judged_round(tmp_path)
    plan = {
        "schema": "live-attribution-v1", "task_id": TASK_ID, "round": 0,
        "diagnosis": "the injected skill did not help",
        "skill_actions": [{"skill_id": skills[0].skill_id, "version": 1,
                           "action": "revise",
                           "problem": "missed the verify step"}],
        "injection_block_sha256": "deadbeef",
    }
    append_jsonl(la._ledger_path(store, la.ATTRIBUTIONS_LEDGER), plan)

    row = live_report(store, round_=0)["groups"][0]

    assert row["attribution"] == plan


def test_live_report_zero_execution_row(tmp_path):
    store, _, _ = judged_round(tmp_path)
    seed_task(store, OTHER_TASK_ID, ("success",) * N_ATTEMPTS)
    for segment in range(2):
        store.save(group_skill(OTHER_TASK_ID, segment), "skills")
    _, baseline_counts = la.load_baseline(store, OTHER_TASK_ID, N_ATTEMPTS)
    # The entry guard's zero-execution terminal: no dispatch, no audit
    # row, no round store -- only the verdict row.
    la.judge_round(store=store, task_id=OTHER_TASK_ID, round_=0,
                   rule_name=RULE, baseline_counts=baseline_counts,
                   n_attempts=N_ATTEMPTS, skip_ids=[],
                   terminal=la.WEAK_ALREADY_KNOWS)

    rows = {row["task_id"]: row for row in live_report(store,
                                                       round_=0)["groups"]}
    other = rows[OTHER_TASK_ID]
    assert other["verdict"] == "weak_already_knows"
    assert other["better"] is None
    assert other["baseline"] is None
    assert other["result"] == []
    # Never dispatched: the members fall back to the current pending group.
    assert other["members"] == [
        {"skill_id": group_skill(OTHER_TASK_ID, segment).skill_id,
         "version": 1}
        for segment in range(2)]


# ---------------------------------------------------------------------------
# Read-only by construction
# ---------------------------------------------------------------------------

def test_readers_write_nothing(tmp_path):
    store, _, _ = judged_round(tmp_path)
    # A landed plan row, so live_report reads every ledger it names.
    append_jsonl(la._ledger_path(store, la.ATTRIBUTIONS_LEDGER), {
        "schema": "live-attribution-v1", "task_id": TASK_ID, "round": 0,
        "diagnosis": "d", "skill_actions": [],
        "injection_block_sha256": "deadbeef",
    })

    before = _tree(store.run_dir)
    baseline_report(store, rule_name="any_weak_failed",
                    n_attempts_strong=2, n_attempts_weak=3)
    exam_report(store)
    live_report(store, round_=0)
    live_report(store, round_=1)

    assert _tree(store.run_dir) == before
