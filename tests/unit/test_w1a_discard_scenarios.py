"""Discard-branch scenario tests (R1): every way a skill can be thrown
away today, pinned as ACCEPTANCE FACTS -- the report rows the decision
module reads -- instead of as golden output.

The golden fixtures are all-accept-zero-discard, so E2's discard branch
is vacuously true on them (plan §4.3 item 11); these scenario tests are
what actually pins it. Each scenario builds the on-disk end state its
discard leaves behind and asserts two things:

1. the one report's row carries the right status/cause/counts (the
   acceptance side);
2. the decision rule that owns the action fires from those facts alone
   (the fact/action split of boundaries doc §3 -- acceptance never
   decides, but its facts must be enough to decide from).
"""

from pathlib import Path

import pytest

from expedistill.composites import (
    live_measurement as lm,
    live_verdicts as lv,
)
from expedistill.blocks.rules import get_acceptance_improve
from expedistill.composites import decision
from expedistill.composites.acceptance_leak import LEAK_VERDICTS_LEDGER
from expedistill.composites.acceptance_skill_status import (
    REWRITE_FAILURE_LEDGER,
)
from expedistill.composites.reports import acceptance_report
from expedistill.core.ids import (
    make_attempt_id,
    make_exam_id,
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Attempt, Exam, Skill, Step, Trajectory
from expedistill.infra.reliability import CodeBugError
from expedistill.infra.store import Store, append_jsonl

BENCHMARK = "tb"
TASK_ID = make_task_id(BENCHMARK, "t1")
N_ATTEMPTS = 3
RULE = "strict_rate_gain"

MARKED_A = "\n".join([
    "[When to use] The service rejects connections and needs a restart.",
    "[Steps]",
    "1. Restart the service with the restart command.",
    "[Verify] The status command reports the service as running.",
])


def make_traj(attempt, correctness, task_id, role="weak"):
    return Trajectory(
        trajectory_id=make_trajectory_id(task_id, role, attempt),
        task_id=task_id, benchmark=BENCHMARK, model_role=role,
        model_name=f"{role}-model", attempt_index=attempt,
        steps=[Step(action=f"do {attempt}",
                    observation=f"obs {attempt}")],
        correctness=correctness,
    )


def make_pair(task_id=BENCHMARK + ":x1"):
    subtask_id = make_subtask_id(
        make_trajectory_id(task_id, "strong", 0), 1)
    skill = Skill(skill_id=make_skill_id(subtask_id), content=MARKED_A,
                  subtask_id=subtask_id, source_subtasks=[subtask_id])
    exam = Exam(exam_id=make_exam_id(subtask_id),
                question_text="What should you do?",
                reference_answer="Restart the service.",
                subtask_id=subtask_id, skill_id=skill.skill_id)
    return skill, exam


def make_attempt(exam, serial, *, purpose, skill_id=None, skill_version=None,
                 judge_verdict=False, judge_reason="wrong",
                 retry_round=0):
    return Attempt(
        attempt_id=make_attempt_id(exam.exam_id, serial),
        exam_id=exam.exam_id, skill_id=skill_id,
        skill_version=skill_version, purpose=purpose,
        weak_answer="an answer", judge_verdict=judge_verdict,
        judge_reason=judge_reason, retry_round=retry_round,
    )


def exam_rows(store):
    return acceptance_report(store, kind="exam", benchmark=BENCHMARK,
                              phase_label="p2:a")


def version_row(rows, version):
    return next(row for row in rows
                if row.injected_skills is not None
                and row.injected_skills[0]["version"] == version)


# ---------------------------------------------------------------------------
# exam-side discards
# ---------------------------------------------------------------------------

def test_scenario_bare_answer_right_is_weak_already_knows(tmp_path):
    """K4's precheck discard (weak_already_knows): the bare control row
    is a success and the version row never ran -- decision fires off the
    two rows alone."""
    store = Store(tmp_path / "run")
    skill, exam = make_pair()
    store.save(skill, "skills")
    store.save(exam, "exams")
    store.save(make_attempt(exam, 0, purpose="bare", judge_verdict=True,
                            judge_reason="matches"),
               "attempts")

    rows = exam_rows(store)
    bare = rows[0]
    version = version_row(rows, 1)

    assert bare.status == "normal" and bare.success_count == 1
    assert version.status == "not_testable"
    assert version.cause == "zero_execution"
    # The decision side fires from the bare row's fact alone.
    action = decision.exam_bare_answer_action(
        bare.success_count == bare.valid_count)
    assert action == decision.Action(decision.DELETE,
                                     decision.WEAK_ALREADY_KNOWS)


def test_scenario_leaked_version_is_not_testable_leak(tmp_path):
    """The leak discard (leak_audit): the version's measurement is void
    and the row says so; on the last round decision deletes with the
    leak_audit reason."""
    store = Store(tmp_path / "run")
    skill, exam = make_pair()
    store.save(skill, "skills")
    store.save(exam, "exams")
    store.save(make_attempt(exam, 0, purpose="bare", judge_verdict=False),
               "attempts")
    ledger_dir = Path(store.dir_for("attempts")).parent
    append_jsonl(ledger_dir / LEAK_VERDICTS_LEDGER, {
        "skill_id": skill.skill_id, "skill_version": 1,
        "leaked": True, "reason": "quotes the reference answer",
    })

    version = version_row(exam_rows(store), 1)

    assert version.status == "not_testable"
    assert version.cause == "leak"
    assert version.runs == []
    action = decision.exam_leak_action(last_round=True)
    assert action == decision.Action(decision.DELETE, decision.LEAK_AUDIT)


def test_scenario_last_round_wrong_answer_is_retry_exhausted(tmp_path):
    """The last-round miss (retry_exhausted): the version row is a
    normal row whose runs are all failures across the consumed rounds;
    decision deletes when no verification round is left."""
    store = Store(tmp_path / "run")
    skill, exam = make_pair()
    store.save(skill, "skills")
    store.save(exam, "exams")
    store.save(make_attempt(exam, 0, purpose="bare", judge_verdict=False),
               "attempts")
    store.save(make_attempt(exam, 1, purpose="acceptance",
                            skill_id=skill.skill_id, skill_version=1,
                            judge_verdict=False, retry_round=0),
               "attempts")
    store.save(make_attempt(exam, 2, purpose="acceptance",
                            skill_id=skill.skill_id, skill_version=1,
                            judge_verdict=False, retry_round=1),
               "attempts")

    version = version_row(exam_rows(store), 1)

    assert version.status == "normal"
    assert version.valid_count == 2 and version.success_count == 0
    action = decision.exam_answer_action(verdict=False, last_round=True)
    assert action == decision.Action(decision.DELETE,
                                     decision.RETRY_EXHAUSTED)


def test_scenario_judge_budget_died_is_exam_generation_failed(tmp_path):
    """The judge's repair budget exhausted (model_output_error on the
    measurement): the attempt stays unjudged with its persistent failure
    marker, so the exam pipeline terminally failed to produce this run's
    measurement -- the version row reports exam_generation_failed (B1:
    a terminal pipeline failure is not the zero_execution visibility
    fact), and decision deletes it as model_output_error."""
    store = Store(tmp_path / "run")
    skill, exam = make_pair()
    store.save(skill, "skills")
    store.save(exam, "exams")
    unjudged = make_attempt(exam, 1, purpose="acceptance",
                            skill_id=skill.skill_id, skill_version=1,
                            judge_verdict=None,
                            judge_reason="judge_failed_tries=3; broken")
    store.save(unjudged, "attempts")

    version = version_row(exam_rows(store), 1)

    assert version.status == "not_testable"
    assert version.cause == "exam_generation_failed"
    assert version.runs == []  # an unjudged attempt is in-flight, not a run
    # The decision side fires model_output_error from the fact alone.
    action = decision.table_action(
        conclusion=decision.CONCLUSION_NOT_TESTABLE, cause=version.cause,
        checks_left=0, phase_kind=decision.PHASE_EXAM)
    assert action == decision.Action(decision.DELETE,
                                     decision.MODEL_OUTPUT_ERROR)


def test_scenario_judge_in_flight_is_zero_execution(tmp_path):
    """The judge's budget merely survived a crash (fewer stored tries
    than the budget): the answer is still in-flight work, so the row
    keeps the zero_execution visibility fact -- and B1's one-meaning
    rule makes the decision entry skip such a row (no action) instead
    of reading weak_already_knows into it."""
    store = Store(tmp_path / "run")
    skill, exam = make_pair()
    store.save(skill, "skills")
    store.save(exam, "exams")
    unjudged = make_attempt(exam, 1, purpose="acceptance",
                            skill_id=skill.skill_id, skill_version=1,
                            judge_verdict=None,
                            judge_reason="judge_failed_tries=1; crash")
    store.save(unjudged, "attempts")
    store.save(make_attempt(exam, 0, purpose="bare", judge_verdict=False),
               "attempts")

    version = version_row(exam_rows(store), 1)

    assert version.status == "not_testable"
    assert version.cause == "zero_execution"
    rows = decision.decide_task_list(
        current_kind=decision.PHASE_EXAM, current_report=exam_rows(store),
        bank=[skill], checks_left=1)
    assert rows == []


def test_scenario_deciding_over_an_in_flight_control_is_a_bug(tmp_path):
    """B1's danger path, end to end from the on-disk state: the
    with-skill answer is judged RIGHT while the exam's bare control has
    not been answered yet (zero_execution, valid=0). Handing that
    report to the decision entry is the caller's bug -- a CodeBugError,
    never the weak_already_knows delete the old cause mapping produced
    for a skill whose measurement just passed."""
    store = Store(tmp_path / "run")
    skill, exam = make_pair()
    store.save(skill, "skills")
    store.save(exam, "exams")
    store.save(make_attempt(exam, 1, purpose="acceptance",
                            skill_id=skill.skill_id, skill_version=1,
                            judge_verdict=True, judge_reason="matches"),
               "attempts")

    rows = exam_rows(store)
    version = version_row(rows, 1)
    bare = rows[0]

    assert version.status == "normal" and version.success_count == 1
    assert bare.status == "not_testable"
    assert bare.cause == "zero_execution" and bare.valid_count == 0
    with pytest.raises(CodeBugError, match="in flight"):
        decision.decide_task_list(
            current_kind=decision.PHASE_EXAM, current_report=rows,
            bank=[skill], checks_left=1)


def test_scenario_rewrite_budget_died_keeps_the_stored_version(tmp_path):
    """The rewrite budget exhausted (model_output_error on G6): the round
    is consumed WITHOUT a new version, so the report keeps describing the
    stored version -- R3's "no new version means no change" is visible
    as the absence of a version-2 row."""
    store = Store(tmp_path / "run")
    skill, exam = make_pair()
    store.save(skill, "skills")
    store.save(exam, "exams")
    store.save(make_attempt(exam, 0, purpose="bare", judge_verdict=False),
               "attempts")
    store.save(make_attempt(exam, 1, purpose="acceptance",
                            skill_id=skill.skill_id, skill_version=1,
                            judge_verdict=False),
               "attempts")
    ledger_dir = Path(store.dir_for("attempts")).parent
    append_jsonl(ledger_dir / REWRITE_FAILURE_LEDGER, {
        "schema": "rewrite-failure-v1", "skill_id": skill.skill_id,
        "skill_version": 1, "exam_id": exam.exam_id, "retry_round": 0,
        "try_index": 3, "exhausted": True, "message": "budget gone",
    })

    rows = exam_rows(store)

    assert {row.injected_skills[0]["version"]
            for row in rows if row.injected_skills is not None} == {1}
    version = version_row(rows, 1)
    assert version.status == "normal" and version.success_count == 0


def test_scenario_exam_generation_died_is_generation_failed(tmp_path):
    """The exam generation budget exhausted (model_output_error before
    any exam existed): the candidate's row is not_testable with the
    generation-failed cause."""
    store = Store(tmp_path / "run")
    skill, _ = make_pair()
    store.save(skill, "skills")  # the exam never landed

    rows = exam_rows(store)

    assert len(rows) == 1
    assert rows[0].status == "not_testable"
    assert rows[0].cause == "exam_generation_failed"


# ---------------------------------------------------------------------------
# baseline-side discards (the D-phase reader's terminal states)
# ---------------------------------------------------------------------------

def test_scenario_redispatch_budget_died_is_invalid_redispatch_exhausted(
        tmp_path):
    """The infra redispatch budget exhausted (P4's pollution source): the
    weak baseline row of a task whose dispatches never completed is
    not_testable with the phase-neutral invalid_redispatch_exhausted
    cause, and the table deletes with the SAME phase-neutral reason in
    an exam phase -- never K6's live_undetermined."""
    store = Store(tmp_path / "run")
    task_id = make_task_id(BENCHMARK, "t9")
    store.save(make_traj(0, True, task_id, role="strong"),
               "trajectories")

    report = acceptance_report(store, kind="baseline", benchmark=BENCHMARK,
                               phase_label="p0:d",
                               n_attempts_strong=N_ATTEMPTS,
                               n_attempts_weak=N_ATTEMPTS)
    weak = next(row for row in report if row.role == "weak")

    assert weak.status == "not_testable"
    assert weak.cause == "invalid_redispatch_exhausted"
    action = decision.table_action(
        conclusion=decision.CONCLUSION_NOT_TESTABLE, cause=weak.cause,
        checks_left=0, phase_kind=decision.PHASE_EXAM)
    assert action == decision.Action(decision.DELETE,
                                     decision.INVALID_REDISPATCH_EXHAUSTED)


# ---------------------------------------------------------------------------
# live-side discards
# ---------------------------------------------------------------------------

def _live_fixture(tmp_path, *, baseline, live, terminal=None,
                   max_injection_tokens=None, skip_units=()):
    """A dispatched round-0 group with a frozen audit line, judged the
    way the driver judges it."""
    store = Store(tmp_path / "run")
    for attempt, correctness in enumerate(baseline):
        store.save(make_traj(attempt, correctness, TASK_ID), "trajectories")
    skills = []
    for segment in range(2):
        subtask_id = make_subtask_id(
            make_trajectory_id(TASK_ID, "strong", 0), segment)
        skill = Skill(skill_id=make_skill_id(subtask_id), content=MARKED_A,
                     subtask_id=subtask_id, source_subtasks=[subtask_id])
        skills.append(skill)
        store.save(skill, "skills")
    directory = (store.run_dir / "stage4_acceptance" / "live_run"
                 / "round00")
    directory.mkdir(parents=True)
    append_jsonl(directory / lm.INJECTION_AUDIT_NAME, lm.audit_row(
        store, TASK_ID, 0))
    for attempt, correctness in enumerate(live):
        Store(directory).save(make_traj(attempt, correctness, TASK_ID),
                              "trajectories")
    _, baseline_counts = lm.load_baseline(store, TASK_ID, N_ATTEMPTS)
    lv.judge_round(store=store, task_id=TASK_ID, round_=0,
                   rule_name=RULE, baseline_counts=baseline_counts,
                   n_attempts=N_ATTEMPTS, skip_ids=list(skip_units),
                   terminal=terminal,
                   max_injection_tokens=max_injection_tokens)
    return store, skills


def _live_row(store):
    report = acceptance_report(store, kind="live", benchmark=BENCHMARK,
                               phase_label="p4:b", round_=0)
    assert len(report) == 1
    return report[0]


def test_scenario_all_success_baseline_short_circuits(tmp_path):
    """The live weak_already_knows zero-execution short-circuit: the
    control was already perfect, the with-skill run never happened, and
    the row says exactly that."""
    store, _ = _live_fixture(tmp_path, baseline=(True,) * N_ATTEMPTS,
                             live=(), terminal=lv.WEAK_ALREADY_KNOWS)

    row = _live_row(store)

    assert row.status == "not_run_control_perfect"
    assert row.cause == "weak_already_knows"
    assert row.runs == []
    # Decision's delete fires off the terminal the row carries.
    action = decision.live_final_action(
        [{"task_id": TASK_ID, "round": 0, "verdict": "weak_already_knows"}],
        [0])
    assert action == decision.Action(decision.DELETE,
                                     decision.WEAK_ALREADY_KNOWS)


def test_scenario_injection_overflow_never_dispatched(tmp_path):
    """The live_injection_overflow terminal: the injection never entered
    execution, the row is not_testable with the overflow cause."""
    store, _ = _live_fixture(
        tmp_path, baseline=(False,) * N_ATTEMPTS, live=(),
        terminal=lv.LIVE_INJECTION_OVERFLOW, max_injection_tokens=0)

    row = _live_row(store)

    assert row.status == "not_testable"
    assert row.cause == "injection_overflow"
    assert row.runs == []
    # The legacy K6 terminal deletes with its own live_* name; the
    # phase-world table deletes with the phase-neutral cause name (P4).
    assert decision.live_final_action(
        [{"task_id": TASK_ID, "round": 0,
          "verdict": lv.LIVE_INJECTION_OVERFLOW}], [0]) == decision.Action(
        decision.DELETE, decision.LIVE_INJECTION_OVERFLOW)
    assert decision.table_action(
        conclusion=decision.CONCLUSION_NOT_TESTABLE, cause=row.cause,
        checks_left=0, phase_kind=decision.PHASE_LIVE) == decision.Action(
        decision.DELETE, decision.INJECTION_OVERFLOW)


def test_scenario_all_accepted_holes_is_undetermined(tmp_path):
    """The live_undetermined terminal (every unit an accepted hole): no
    live denominator, no judgment -- the row is not_testable/all_holes."""
    skip = tuple(make_trajectory_id(TASK_ID, "weak", attempt)
                 for attempt in range(N_ATTEMPTS))
    store, _ = _live_fixture(tmp_path, baseline=(False,) * N_ATTEMPTS,
                             live=(), skip_units=skip)

    row = _live_row(store)

    assert row.status == "not_testable"
    assert row.cause == "all_holes"
    assert row.runs == []
    # Same two-sided pin as the overflow scenario above.
    assert decision.live_final_action(
        [{"task_id": TASK_ID, "round": 0,
          "verdict": lv.LIVE_UNDETERMINED}], [0]) == decision.Action(
        decision.DELETE, decision.LIVE_UNDETERMINED)
    assert decision.table_action(
        conclusion=decision.CONCLUSION_NOT_TESTABLE, cause=row.cause,
        checks_left=0, phase_kind=decision.PHASE_LIVE) == decision.Action(
        decision.DELETE, decision.ALL_HOLES)


def test_scenario_all_unscored_round_is_undetermined(tmp_path):
    """Ruling 1's end-to-end nail: a COMPLETE group whose every stored
    run came back without a scorer verdict (R2b's normal reachable
    state) has zero measurements. It is live_undetermined -- never
    compared against the baseline, never "no improvement" -- the row is
    not_testable/all_holes and the table deletes with the phase-neutral
    all_holes reason."""
    store, _ = _live_fixture(tmp_path, baseline=(False,) * N_ATTEMPTS,
                             live=(None,) * N_ATTEMPTS)

    row = _live_row(store)

    assert row.status == "not_testable"
    assert row.cause == "all_holes"
    # The row carries the terminal: no rule ever ran.
    assert decision.live_final_action(
        [{"task_id": TASK_ID, "round": 0,
          "verdict": lv.LIVE_UNDETERMINED}], [0]) == decision.Action(
        decision.DELETE, decision.LIVE_UNDETERMINED)
    assert decision.table_action(
        conclusion=decision.CONCLUSION_NOT_TESTABLE, cause=row.cause,
        checks_left=0, phase_kind=decision.PHASE_LIVE) == decision.Action(
        decision.DELETE, decision.ALL_HOLES)


def test_scenario_last_round_miss_feeds_the_comparison(tmp_path):
    """The live_no_improvement discard: the group's row is a normal row
    of pure failures and the baseline's weak row is the control -- the
    strict-rate-gain rule answers "not better" from the two rows alone,
    and decision deletes on the last round."""
    store, _ = _live_fixture(tmp_path, baseline=(False,) * N_ATTEMPTS,
                             live=(False,) * N_ATTEMPTS)

    row = _live_row(store)
    baseline_report = acceptance_report(
        store, kind="baseline", benchmark=BENCHMARK, phase_label="p0:d",
        n_attempts_strong=N_ATTEMPTS, n_attempts_weak=N_ATTEMPTS)
    baseline_row = next(r for r in baseline_report
                        if r.role == "weak"
                        and TASK_ID in r.unit_id)

    assert row.status == "normal"
    assert row.valid_count == N_ATTEMPTS and row.success_count == 0
    assert baseline_row.valid_count == N_ATTEMPTS
    assert baseline_row.success_count == 0
    better = get_acceptance_improve(RULE)(
        {"valid": baseline_row.valid_count,
         "success": baseline_row.success_count},
        {"valid": row.valid_count, "success": row.success_count},
        N_ATTEMPTS)
    assert not better
    action = decision.live_final_action(
        [{"task_id": TASK_ID, "round": 0, "better": False}], [0])
    assert action == decision.Action(decision.DELETE,
                                     decision.LIVE_NO_IMPROVEMENT)


def test_scenario_no_op_rewrite_leaves_a_single_version_row(tmp_path):
    """The content-unchanged discard (live_no_improvement via the no-op
    guard): the revisions changed nothing, so the survivors' pending
    group is exactly the injected one and the report keeps describing
    one measured round of the same members."""
    store, skills = _live_fixture(tmp_path,
                                  baseline=(False,) * N_ATTEMPTS,
                                  live=(False,) * N_ATTEMPTS)
    # The no-op guard's comparison inputs, straight from the report:
    # the round's frozen audit line vs the survivors' current block.
    survivors, block = lm.survivors_and_block(store, TASK_ID)
    audit = lm._audit_row_from(
        (store.run_dir / "stage4_acceptance" / "live_run" / "round00")
        / lm.INJECTION_AUDIT_NAME, 0, TASK_ID)
    assert decision.live_no_op_fires(
        rendered_block_sha=lm._block_sha(block),
        frozen_audit_sha=audit["injection_block_sha256"])

    row = _live_row(store)

    assert row.injected_skills == [
        {"skill_id": skill.skill_id, "version": 1} for skill in skills]
    assert row.status == "normal"


# ---------------------------------------------------------------------------
# the discard table is complete
# ---------------------------------------------------------------------------

def test_every_discard_reason_has_a_scenario_pin():
    """The discard-reason vocabulary decision defines is exactly what the
    scenarios above pin; a new reason must consciously add a scenario
    (this is the E2 blind-spot's guardrail). ``live_revision_deleted``
    is the author-side delete inside the rewrite flow (R6, W1-E's
    scenarios pin it), so it is the one reason with no acceptance
    scenario."""
    vocabulary = {
        decision.WEAK_ALREADY_KNOWS,
        decision.LIVE_INJECTION_OVERFLOW,
        decision.LIVE_UNDETERMINED,
        decision.LIVE_NO_IMPROVEMENT,
        decision.LIVE_REVISION_DELETED,
        decision.MODEL_OUTPUT_ERROR,
        decision.LEAK_AUDIT,
        decision.RETRY_EXHAUSTED,
        decision.INJECTION_OVERFLOW,
        decision.INVALID_REDISPATCH_EXHAUSTED,
        decision.ALL_HOLES,
    }
    pinned = {
        # scenario -> the reason its decision rule names
        "bare_answer_right": decision.WEAK_ALREADY_KNOWS,
        "leaked_version": decision.LEAK_AUDIT,
        "last_round_wrong_answer": decision.RETRY_EXHAUSTED,
        "judge_budget_died": decision.MODEL_OUTPUT_ERROR,
        "rewrite_budget_died": decision.MODEL_OUTPUT_ERROR,
        "exam_generation_died": decision.MODEL_OUTPUT_ERROR,
        "baseline_short_circuit": decision.WEAK_ALREADY_KNOWS,
        "redispatch_died": decision.INVALID_REDISPATCH_EXHAUSTED,
        "injection_overflow": decision.INJECTION_OVERFLOW,
        "all_accepted_holes": decision.ALL_HOLES,
        "overflow_terminal": decision.LIVE_INJECTION_OVERFLOW,
        "undetermined_terminal": decision.LIVE_UNDETERMINED,
        "last_round_miss": decision.LIVE_NO_IMPROVEMENT,
        "no_op_rewrite": decision.LIVE_NO_IMPROVEMENT,
    }
    assert vocabulary - {decision.LIVE_REVISION_DELETED} \
        <= set(pinned.values())
