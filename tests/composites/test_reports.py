"""The acceptance report reader's tests: composites/reports.py's ONE
reader and its ONE row shape (R1/G6 -- the three former readers and
their three shapes are gone).

Every fixture builds the run directory the way the stage would have
left it -- the existing composite tests' fixture shapes (K4's attempts
and ledgers, K6's round stores and frozen audit lines, stage 1's
trajectories and accepted holes) -- and asserts the row content the
reader derives. The reader is read-only: one test snapshots the run
tree before and after every kind and proves not one byte moved.
"""

from pathlib import Path

import pytest

from expedistill.composites import (
    live_measurement as lm,
    live_verdicts as lv,
)
from expedistill.composites import shortfall
from expedistill.composites import verdicts
from expedistill.composites.acceptance_leak import LEAK_VERDICTS_LEDGER
from expedistill.composites.reports import acceptance_report
from expedistill.core.contracts import AcceptanceReportRow
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
PHASE = "p0:baseline"

MARKED_A = "\n".join([
    "[When to use] The service rejects connections and needs a restart.",
    "[Steps]",
    "1. Check the service status with the status command.",
    "2. Restart the service with the restart command.",
    "[Do not use when] The status command already reports running.",
])


# ---------------------------------------------------------------------------
# Fixture helpers (the shapes tests/composites/ already use)
# ---------------------------------------------------------------------------

def make_traj(attempt, correctness, task_id, role="weak"):
    return Trajectory(
        trajectory_id=make_trajectory_id(task_id, role, attempt),
        task_id=task_id, benchmark=BENCHMARK, model_role=role,
        model_name=f"{role}-model", attempt_index=attempt,
        steps=[Step(action=f"do {attempt}",
                    observation=f"obs {attempt}")],
        correctness=correctness,
    )


def seed_task(store, task_id, weak_statuses, strong_statuses=()):
    for attempt, correctness in enumerate(weak_statuses):
        store.save(make_traj(attempt, correctness, task_id, "weak"),
                   "trajectories")
    for attempt, correctness in enumerate(strong_statuses):
        store.save(make_traj(attempt, correctness, task_id, "strong"),
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
    row = lm.audit_row(store, task_id, round_)
    append_jsonl(round_dir(store, round_) / lm.INJECTION_AUDIT_NAME, row)
    return row


def seed_round(store, task_id, round_, statuses):
    directory = round_dir(store, round_)
    for attempt, correctness in enumerate(statuses):
        Store(directory).save(
            make_traj(attempt, correctness, task_id), "trajectories")


def _tree(root):
    return sorted((str(path.relative_to(root)), path.read_bytes())
                  for path in Path(root).rglob("*") if path.is_file())


def _rows_by_unit(report):
    return {row.unit_id: row for row in report}


# ---------------------------------------------------------------------------
# The one shape
# ---------------------------------------------------------------------------

def test_every_row_is_the_one_shape(tmp_path):
    store = Store(tmp_path / "run")
    seed_task(store, TASK_ID, (False,) * N_ATTEMPTS)

    for report in (
            acceptance_report(store, kind="baseline", benchmark=BENCHMARK,
                              phase_label=PHASE, n_attempts_strong=2,
                              n_attempts_weak=N_ATTEMPTS),
            acceptance_report(store, kind="exam", benchmark=BENCHMARK,
                              phase_label="p2:exam"),
            acceptance_report(store, kind="live", benchmark=BENCHMARK,
                              phase_label="p4:live", round_=0),
    ):
        assert isinstance(report, list)
        for row in report:
            assert isinstance(row, AcceptanceReportRow)
            assert row.status in verdicts.UNIT_STATUSES
            if row.status == verdicts.STATUS_NORMAL:
                assert row.cause == "" and row.valid_count > 0
            else:
                assert row.cause


def test_unknown_kind_and_missing_kind_inputs_are_config_errors(tmp_path):
    store = Store(tmp_path / "run")
    with pytest.raises(ConfigError, match="unknown report kind"):
        acceptance_report(store, kind="no_such_kind",
                          benchmark=BENCHMARK, phase_label=PHASE)
    with pytest.raises(ConfigError, match="n_attempts_strong"):
        acceptance_report(store, kind="baseline", benchmark=BENCHMARK,
                          phase_label=PHASE)
    with pytest.raises(ConfigError, match="round_"):
        acceptance_report(store, kind="live", benchmark=BENCHMARK,
                          phase_label=PHASE)


# ---------------------------------------------------------------------------
# baseline kind
# ---------------------------------------------------------------------------

def test_baseline_rows_split_by_role_and_count(tmp_path):
    store = Store(tmp_path / "run")
    t1, t2, t3, t4 = (make_task_id(BENCHMARK, name)
                     for name in ("t1", "t2", "t3", "t4"))
    seed_task(store, t1, (True, False, False),
              (True, False))
    seed_task(store, t2, (False, False), (False, False))
    seed_task(store, t3, (True, True, True),
              (True, True))
    # t4: every unit is an accepted hole -- no trajectory, still rows.
    accept_holes(store, [make_trajectory_id(t2, "weak", 2)]
                 + [make_trajectory_id(t4, "weak", attempt)
                    for attempt in range(N_ATTEMPTS)]
                 + [make_trajectory_id(t4, "strong", attempt)
                    for attempt in range(2)])

    report = acceptance_report(store, kind="baseline", benchmark=BENCHMARK,
                                phase_label=PHASE, n_attempts_strong=2,
                                n_attempts_weak=N_ATTEMPTS)

    assert len(report) == 8  # four tasks x two roles
    assert len(_rows_by_unit(report)) == 8  # unit ids are unique
    assert all(row.phase_label == PHASE for row in report)
    t1_weak = next(row for row in report
                   if row.unit_id == verdicts.make_unit_id(
                       phase_label=PHASE, benchmark=BENCHMARK,
                       task_ref=t1, role="weak", skills_injected=None))
    assert (t1_weak.valid_count, t1_weak.success_count) == (3, 1)
    assert t1_weak.status == verdicts.STATUS_NORMAL
    t1_strong = next(row for row in report
                     if row.unit_id == verdicts.make_unit_id(
                         phase_label=PHASE, benchmark=BENCHMARK,
                         task_ref=t1, role="strong", skills_injected=None))
    assert (t1_strong.valid_count, t1_strong.success_count) == (2, 1)
    # t2's accepted hole is no run at all: 2 valid of 2 stored.
    t2_weak = next(row for row in report
                   if row.unit_id == verdicts.make_unit_id(
                       phase_label=PHASE, benchmark=BENCHMARK,
                       task_ref=t2, role="weak", skills_injected=None))
    assert t2_weak.valid_count == 2
    # t4: every unit an accepted hole -> nothing executed -> all_holes.
    for role in ("weak", "strong"):
        row = next(row for row in report
                   if row.unit_id == verdicts.make_unit_id(
                       phase_label=PHASE, benchmark=BENCHMARK,
                       task_ref=t4, role=role,
                       skills_injected=None))
        assert row.status == verdicts.STATUS_NOT_TESTABLE
        assert row.cause == verdicts.CAUSE_ALL_HOLES
        assert row.runs == []


def test_baseline_missing_units_are_invalid_runs(tmp_path):
    """A unit whose infra retries were exhausted is an invalid run, not
    a silently shrunken denominator; zero valid runs of that kind is
    invalid_redispatch_exhausted."""
    store = Store(tmp_path / "run")
    t1, t2 = make_task_id(BENCHMARK, "t1"), make_task_id(BENCHMARK, "t2")
    # t1: one stored failure, two units that never came back.
    seed_task(store, t1, (False,))
    # t2: one accepted hole (so the task is in the universe), the rest
    # never came back -- the dispatch budget died.
    accept_holes(store, [make_trajectory_id(t2, "weak", 0)])

    report = acceptance_report(store, kind="baseline", benchmark=BENCHMARK,
                               phase_label=PHASE, n_attempts_strong=2,
                               n_attempts_weak=N_ATTEMPTS)

    t1_weak = next(row for row in report if row.role == "weak"
                   and row.unit_id == verdicts.make_unit_id(
                       phase_label=PHASE, benchmark=BENCHMARK,
                       task_ref=t1, role="weak", skills_injected=None))
    assert [run["verdict"] for run in t1_weak.runs] == [
        "failure", "invalid", "invalid"]
    assert t1_weak.valid_count == 1
    assert t1_weak.status == verdicts.STATUS_NORMAL
    t2_weak = next(row for row in report if row.role == "weak"
                   and row.unit_id == verdicts.make_unit_id(
                       phase_label=PHASE, benchmark=BENCHMARK,
                       task_ref=t2, role="weak", skills_injected=None))
    assert t2_weak.status == verdicts.STATUS_NOT_TESTABLE
    assert t2_weak.cause == verdicts.CAUSE_INVALID_REDISPATCH_EXHAUSTED
    assert t2_weak.valid_count == 0
    # The strong arms of tasks with no strong runs report the same.
    t1_strong = next(row for row in report if row.role == "strong"
                     and row.unit_id == verdicts.make_unit_id(
                         phase_label=PHASE, benchmark=BENCHMARK,
                         task_ref=t1, role="strong",
                         skills_injected=None))
    assert t1_strong.cause == verdicts.CAUSE_INVALID_REDISPATCH_EXHAUSTED


def test_baseline_all_stored_unscored_is_all_holes(tmp_path):
    """Ruling 1's reader unification: a unit whose every run came back
    STORED but unscored (correctness None, R2b's normal state) measured
    nothing -- the baseline reader reports the same zero-measurement
    cause (all_holes) the live reader reports through the
    live_undetermined terminal, never invalid_redispatch_exhausted
    (whose fact is units that never came back)."""
    store = Store(tmp_path / "run")
    t1 = make_task_id(BENCHMARK, "t1")
    seed_task(store, t1, (None, None, None))

    report = acceptance_report(store, kind="baseline", benchmark=BENCHMARK,
                               phase_label=PHASE, n_attempts_strong=2,
                               n_attempts_weak=N_ATTEMPTS)

    t1_weak = next(row for row in report if row.role == "weak"
                   and row.unit_id == verdicts.make_unit_id(
                       phase_label=PHASE, benchmark=BENCHMARK,
                       task_ref=t1, role="weak", skills_injected=None))
    assert t1_weak.status == verdicts.STATUS_NOT_TESTABLE
    assert t1_weak.cause == verdicts.CAUSE_ALL_HOLES
    assert [run["verdict"] for run in t1_weak.runs] == ["invalid"] * 3


def test_baseline_stored_unscored_beside_a_missing_unit_keeps_the_dispatch_cause(
        tmp_path):
    # A unit that mixes stored-unscored runs with one that never came
    # back still has a missing unit whose dispatch budget died: the
    # zero-measurement cause is only for units that all came back.
    store = Store(tmp_path / "run")
    t1 = make_task_id(BENCHMARK, "t1")
    seed_task(store, t1, (None,))
    accept_holes(store, [make_trajectory_id(t1, "weak", 2)])

    report = acceptance_report(store, kind="baseline", benchmark=BENCHMARK,
                               phase_label=PHASE, n_attempts_strong=2,
                               n_attempts_weak=N_ATTEMPTS)

    t1_weak = next(row for row in report if row.role == "weak"
                   and row.unit_id == verdicts.make_unit_id(
                       phase_label=PHASE, benchmark=BENCHMARK,
                       task_ref=t1, role="weak", skills_injected=None))
    assert t1_weak.status == verdicts.STATUS_NOT_TESTABLE
    assert t1_weak.cause == verdicts.CAUSE_INVALID_REDISPATCH_EXHAUSTED
    assert [run["verdict"] for run in t1_weak.runs] == [
        "invalid", "invalid"]


# ---------------------------------------------------------------------------
# exam kind
# ---------------------------------------------------------------------------

def test_exam_rows_bare_control_plus_judged_version(tmp_path):
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

    report = acceptance_report(store, kind="exam", benchmark=BENCHMARK,
                               phase_label="p2:exam")

    assert len(report) == 2
    bare_row = report[0]
    assert bare_row.injected_skills is None
    assert bare_row.status == verdicts.STATUS_NORMAL
    assert bare_row.valid_count == 1 and bare_row.success_count == 0
    assert bare_row.runs[0]["run"] == bare.attempt_id
    version_row = report[1]
    assert version_row.injected_skills == [
        {"skill_id": skill.skill_id, "version": 1}]
    assert version_row.status == verdicts.STATUS_NORMAL
    assert version_row.valid_count == 1 and version_row.success_count == 0
    # The run's key is the attempt id (the evidence side's pointer) and
    # the basis carries the judge reason.
    assert version_row.runs[0]["run"] == failed.attempt_id
    assert failed.judge_reason in version_row.runs[0]["basis"]
    assert bare_row.unit_id != version_row.unit_id


def test_exam_leaked_version_voids_the_measurement(tmp_path):
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

    report = acceptance_report(store, kind="exam", benchmark=BENCHMARK,
                               phase_label="p2:exam")

    version_row = report[1]
    assert version_row.status == verdicts.STATUS_NOT_TESTABLE
    assert version_row.cause == verdicts.CAUSE_LEAK
    assert version_row.runs == []
    # The bare control is untouched by the leak.
    assert report[0].status == verdicts.STATUS_NORMAL


def test_exam_rows_name_the_version_they_answered(tmp_path):
    """Review C-1's state: the stored skill is v2, the only judged
    attempt is v1's failure -- the v1 row carries the failure, the v2 row
    says "not answered yet" instead of inheriting v1's verdict."""
    store = Store(tmp_path / "run")
    skill, exam = make_pair()
    skill.version = 2
    store.save(skill, "skills")
    store.save(exam, "exams")
    store.save(make_attempt(exam, 0, purpose="bare", judge_verdict=False),
               "attempts")
    v1_failed = make_attempt(exam, 1, purpose="acceptance",
                             skill_id=skill.skill_id, skill_version=1,
                             judge_verdict=False)
    store.save(v1_failed, "attempts")

    report = acceptance_report(store, kind="exam", benchmark=BENCHMARK,
                               phase_label="p2:exam")

    assert len(report) == 3  # bare + v1 + v2
    assert report[1].injected_skills == [
        {"skill_id": skill.skill_id, "version": 1}]
    assert report[1].valid_count == 1 and report[1].success_count == 0
    assert report[2].injected_skills == [
        {"skill_id": skill.skill_id, "version": 2}]
    assert report[2].status == verdicts.STATUS_NOT_TESTABLE
    assert report[2].cause == verdicts.CAUSE_ZERO_EXECUTION

    # Once v2's answer lands and passes, its row says so.
    store.save(make_attempt(exam, 2, purpose="acceptance",
                            skill_id=skill.skill_id, skill_version=2,
                            judge_verdict=True), "attempts")
    report = acceptance_report(store, kind="exam", benchmark=BENCHMARK,
                               phase_label="p2:exam")
    assert report[2].status == verdicts.STATUS_NORMAL
    assert report[2].success_count == 1


def test_exam_candidate_without_exam_is_generation_failed(tmp_path):
    store = Store(tmp_path / "run")
    skill, _ = make_pair()
    store.save(skill, "skills")  # the exam never landed (stage 3 crashed)

    report = acceptance_report(store, kind="exam", benchmark=BENCHMARK,
                               phase_label="p2:exam")

    assert len(report) == 1
    row = report[0]
    assert row.status == verdicts.STATUS_NOT_TESTABLE
    assert row.cause == verdicts.CAUSE_EXAM_GENERATION_FAILED
    assert row.runs == []


# ---------------------------------------------------------------------------
# live kind
# ---------------------------------------------------------------------------

def judged_round(tmp_path, *, task_id=TASK_ID,
                 live=(False, False, False)):
    """A run directory with one judged round-0 group (no model calls:
    judging is a pure recomputation over the seeded stores)."""
    store = Store(tmp_path / "run")
    seed_task(store, task_id, (False,) * N_ATTEMPTS)
    skills = [group_skill(task_id, segment) for segment in range(2)]
    for skill in skills:
        store.save(skill, "skills")
    freeze_audit(store, task_id)
    seed_round(store, task_id, 0, live)
    _, baseline_counts = lm.load_baseline(store, task_id, N_ATTEMPTS)
    lv.judge_round(store=store, task_id=task_id, round_=0,
                   rule_name=RULE, baseline_counts=baseline_counts,
                   n_attempts=N_ATTEMPTS, skip_ids=[])
    return store, skills, baseline_counts


def test_live_row_of_a_judged_round(tmp_path):
    store, skills, _ = judged_round(tmp_path)

    report = acceptance_report(store, kind="live", benchmark=BENCHMARK,
                               phase_label="p4:live", round_=0)

    assert len(report) == 1
    row = report[0]
    assert row.role == "weak"
    assert row.injected_skills == [{"skill_id": skill.skill_id,
                                    "version": 1}
                                   for skill in skills]
    assert [run["verdict"] for run in row.runs] == [
        "failure", "failure", "failure"]
    assert row.valid_count == N_ATTEMPTS
    assert row.success_count == 0
    assert row.status == verdicts.STATUS_NORMAL
    assert row.cause == ""
    assert row.artifacts["round_dir"] == str(round_dir(store, 0))


def test_live_zero_execution_weak_already_knows_row(tmp_path):
    store, _, _ = judged_round(tmp_path)
    seed_task(store, OTHER_TASK_ID, (True,) * N_ATTEMPTS)
    for segment in range(2):
        store.save(group_skill(OTHER_TASK_ID, segment), "skills")
    _, baseline_counts = lm.load_baseline(store, OTHER_TASK_ID, N_ATTEMPTS)
    # The entry guard's zero-execution terminal: no dispatch, no audit
    # row, no round store -- only the verdict row.
    lv.judge_round(store=store, task_id=OTHER_TASK_ID, round_=0,
                   rule_name=RULE, baseline_counts=baseline_counts,
                   n_attempts=N_ATTEMPTS, skip_ids=[],
                   terminal=lv.WEAK_ALREADY_KNOWS)

    report = acceptance_report(store, kind="live", benchmark=BENCHMARK,
                               phase_label="p4:live", round_=0)
    other = next(row for row in report
                 if OTHER_TASK_ID in row.unit_id)
    assert other.status == verdicts.STATUS_NOT_RUN_CONTROL_PERFECT
    assert other.cause == verdicts.CAUSE_WEAK_ALREADY_KNOWS
    assert other.runs == []
    assert other.valid_count == 0 and other.success_count == 0
    # Never dispatched: the members fall back to the current pending group.
    assert other.injected_skills == [
        {"skill_id": group_skill(OTHER_TASK_ID, segment).skill_id,
         "version": 1}
        for segment in range(2)]


def _dispatched_group(tmp_path, *, round_statuses):
    """A dispatched round-0 group: frozen audit row, stored round
    trajectories as given, unjudged (the caller judges)."""
    store = Store(tmp_path / "run")
    seed_task(store, TASK_ID, (False,) * N_ATTEMPTS)
    skills = [group_skill(TASK_ID, segment) for segment in range(2)]
    for skill in skills:
        store.save(skill, "skills")
    freeze_audit(store, TASK_ID)
    # The round store exists even when every unit was an accepted hole.
    round_dir(store, 0).mkdir(parents=True, exist_ok=True)
    seed_round(store, TASK_ID, 0, round_statuses)
    return store, skills


def test_live_injection_overflow_row(tmp_path):
    store, _ = _dispatched_group(tmp_path, round_statuses=(False,) * 3)
    lv.judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                   baseline_counts={"valid": N_ATTEMPTS, "success": 1},
                   n_attempts=N_ATTEMPTS, skip_ids=[],
                   terminal=lv.LIVE_INJECTION_OVERFLOW,
                   max_injection_tokens=0)

    report = acceptance_report(store, kind="live", benchmark=BENCHMARK,
                               phase_label="p4:live", round_=0)

    row = report[0]
    assert row.status == verdicts.STATUS_NOT_TESTABLE
    assert row.cause == verdicts.CAUSE_INJECTION_OVERFLOW
    assert row.runs == []


def test_live_all_holes_row(tmp_path):
    store, _ = _dispatched_group(tmp_path, round_statuses=())
    skip = [make_trajectory_id(TASK_ID, "weak", attempt)
            for attempt in range(N_ATTEMPTS)]
    lv.judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                   baseline_counts={"valid": N_ATTEMPTS, "success": 1},
                   n_attempts=N_ATTEMPTS, skip_ids=skip)

    report = acceptance_report(store, kind="live", benchmark=BENCHMARK,
                               phase_label="p4:live", round_=0)

    row = report[0]
    assert row.status == verdicts.STATUS_NOT_TESTABLE
    assert row.cause == verdicts.CAUSE_ALL_HOLES
    assert row.runs == []


# ---------------------------------------------------------------------------
# Read-only by construction
# ---------------------------------------------------------------------------

def test_the_reader_writes_nothing(tmp_path):
    store, _, _ = judged_round(tmp_path)
    skill, exam = make_pair()
    store.save(skill, "skills")
    store.save(exam, "exams")
    store.save(make_attempt(exam, 0, purpose="bare", judge_verdict=False),
               "attempts")

    before = _tree(store.run_dir)
    acceptance_report(store, kind="baseline", benchmark=BENCHMARK,
                      phase_label=PHASE, n_attempts_strong=2,
                      n_attempts_weak=N_ATTEMPTS)
    acceptance_report(store, kind="exam", benchmark=BENCHMARK,
                      phase_label="p2:exam")
    acceptance_report(store, kind="live", benchmark=BENCHMARK,
                      phase_label="p4:live", round_=0)
    acceptance_report(store, kind="live", benchmark=BENCHMARK,
                      phase_label="p4:live", round_=1)

    assert _tree(store.run_dir) == before
