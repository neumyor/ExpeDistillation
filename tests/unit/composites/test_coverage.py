"""Trial-level coverage derivation tests (composites/coverage.py).

Coverage is read-only: it turns the phase's explicit terminal facts plus
the store's expected-vs-stored derivation into one CoverageRecord per
expected trial.  Each phase's rules are pinned against a real
``Store(tmp_path)`` with small saved Trajectory / Attempt records.
"""

from expedistill.composites import coverage, exam_grading
from expedistill.composites import live_measurement as lm
from expedistill.core import ids
from expedistill.core.answer_sheet import UnitSpec
from expedistill.core.models import Attempt, Step, Trajectory
from expedistill.infra.store import Store

TASK = "tb:task-1"
EXAM = ids.make_exam_id(
    ids.make_skill_id(ids.make_subtask_id(
        ids.make_trajectory_id(TASK, "weak", 0), 0)), 1)


def _trajectory(task_ref=TASK, role="weak", attempt=0,
                correctness=True, skills_injected=None):
    return Trajectory(
        trajectory_id=ids.make_trajectory_id(task_ref, role, attempt),
        task_id=task_ref,
        benchmark="tb",
        model_role=role,
        model_name="model-a",
        attempt_index=attempt,
        skills_injected=skills_injected,
        steps=[Step(action="ls", observation="a.txt")],
        correctness=correctness,
    )


def _attempt(serial, **overrides):
    base = dict(
        attempt_id=ids.make_attempt_id(EXAM, serial),
        exam_id=EXAM,
        skill_id=None,
        skill_version=None,
        purpose="bare",
        weak_answer="answer",
        judge_verdict=None,
        judge_reason="",
        retry_round=0,
    )
    base.update(overrides)
    return Attempt(**base)


def _exam_spec(unit_id="unit:exam", injected=None):
    return UnitSpec(
        unit_id=unit_id,
        phase_label="p03:exam",
        task_ref=EXAM,
        role="weak",
        arm=None,
        expected_attempts=1,
        injected_skills=injected,
    )


def _spec(unit_id, *, task_ref=TASK, role="weak", arm=None,
          expected_attempts=2, injected_skills=None):
    return UnitSpec(
        unit_id=unit_id,
        phase_label="p03:live",
        task_ref=task_ref,
        role=role,
        arm=arm,
        expected_attempts=expected_attempts,
        injected_skills=injected_skills,
    )


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------

def test_baseline_coverage_present_missing_and_accepted_hole(tmp_path):
    store = Store(tmp_path)
    store.save(_trajectory(attempt=0), "trajectories")
    spec = _spec("unit:baseline", expected_attempts=3)
    accepted = {ids.make_trajectory_id(TASK, "weak", 1)}

    records = coverage.baseline_coverage(store, [spec], accepted)

    assert [(record.attempt_index, record.present, record.status,
             record.cause) for record in records] == [
        (0, True, "present", ""),
        (1, False, "missing", "accepted_hole"),
        (2, False, "missing", "missing"),
    ]


def test_baseline_coverage_reads_the_named_role_and_task(tmp_path):
    store = Store(tmp_path)
    strong = _trajectory(attempt=0, role="strong")
    store.save(strong, "trajectories")
    spec = _spec("unit:baseline", role="strong", expected_attempts=1)

    records = coverage.baseline_coverage(store, [spec], ())

    assert len(records) == 1
    assert records[0].present is True


# ---------------------------------------------------------------------------
# Exam
# ---------------------------------------------------------------------------

def test_exam_bare_control_is_present_when_judged(tmp_path):
    store = Store(tmp_path)
    store.save(_attempt(0, judge_verdict=True, judge_reason="right"),
               "attempts")
    spec = _exam_spec()

    records = coverage.exam_coverage(
        store, [spec], control_perfect_units=set(),
        leak_versions=set(), generation_failed_units=set())

    assert len(records) == 1
    assert records[0].unit_id == "unit:exam"
    assert records[0].attempt_index == 0
    assert records[0].present is True
    assert records[0].status == "present"
    assert records[0].cause == ""


def test_exam_bare_control_with_no_attempt_is_zero_execution(tmp_path):
    store = Store(tmp_path)
    spec = _exam_spec()

    records = coverage.exam_coverage(
        store, [spec], control_perfect_units=set(),
        leak_versions=set(), generation_failed_units=set())

    assert len(records) == 1
    assert records[0].present is False
    assert records[0].status == "terminal"
    assert records[0].cause == "zero_execution"


def test_exam_bare_control_with_a_spent_judge_budget_is_a_generation_failure(
        tmp_path):
    """The module docstring: a spent judge budget is a terminal generation
    failure, not zero_execution.  The bare arm's detection must find the
    unjudged, budget-exhausted attempt to say so."""
    store = Store(tmp_path)
    assert exam_grading.judge_budget_exhausted(
        _attempt(0, judge_reason="judge_failed_tries=2; bad output"))
    store.save(_attempt(
        0, judge_reason="judge_failed_tries=2; bad output"), "attempts")
    spec = _exam_spec()

    records = coverage.exam_coverage(
        store, [spec], control_perfect_units=set(),
        leak_versions=set(), generation_failed_units=set())

    assert len(records) == 1
    assert records[0].present is False
    assert records[0].cause == "exam_generation_failed"


def test_exam_control_perfect_unit_is_the_skip_cause(tmp_path):
    store = Store(tmp_path)
    spec = _exam_spec("unit:perfect")

    records = coverage.exam_coverage(
        store, [spec], control_perfect_units={"unit:perfect"},
        leak_versions=set(), generation_failed_units=set())

    assert len(records) == 1
    assert records[0].present is False
    assert records[0].status == "terminal"
    assert records[0].cause == "weak_already_knows"


def test_exam_generation_failed_unit_is_terminal(tmp_path):
    store = Store(tmp_path)
    spec = _exam_spec("unit:gen")

    records = coverage.exam_coverage(
        store, [spec], control_perfect_units=set(),
        leak_versions=set(), generation_failed_units={"unit:gen"})

    assert len(records) == 1
    assert records[0].present is False
    assert records[0].status == "terminal"
    assert records[0].cause == "exam_generation_failed"


def test_exam_leak_wins_over_a_judged_attempt(tmp_path):
    store = Store(tmp_path)
    injected = [{"skill_id": "skill-1", "version": 2}]
    store.save(_attempt(
        0, purpose="acceptance", skill_id="skill-1", skill_version=2,
        judge_verdict=True), "attempts")
    spec = _exam_spec("unit:leak", injected)

    records = coverage.exam_coverage(
        store, [spec], control_perfect_units=set(),
        leak_versions={("skill-1", 2)}, generation_failed_units=set())

    assert len(records) == 1
    assert records[0].present is False
    assert records[0].status == "terminal"
    assert records[0].cause == "leak"


def test_exam_judged_skill_attempts_are_present_per_retry_round(tmp_path):
    store = Store(tmp_path)
    injected = [{"skill_id": "skill-1", "version": 2}]
    store.save(_attempt(
        0, purpose="acceptance", skill_id="skill-1", skill_version=2,
        retry_round=0, judge_verdict=True), "attempts")
    store.save(_attempt(
        1, purpose="acceptance", skill_id="skill-1", skill_version=2,
        retry_round=1, judge_verdict=False), "attempts")
    spec = _exam_spec("unit:skill", injected)

    records = coverage.exam_coverage(
        store, [spec], control_perfect_units=set(),
        leak_versions=set(), generation_failed_units=set())

    assert [(record.attempt_index, record.present, record.status)
            for record in records] == [
        (0, True, "present"),
        (1, True, "present"),
    ]
    assert all(record.cause == "" for record in records)


def test_exam_injected_skill_without_an_attempt_is_zero_execution(tmp_path):
    store = Store(tmp_path)
    spec = _exam_spec(
        "unit:skill", [{"skill_id": "skill-1", "version": 2}])

    records = coverage.exam_coverage(
        store, [spec], control_perfect_units=set(),
        leak_versions=set(), generation_failed_units=set())

    assert len(records) == 1
    assert records[0].present is False
    assert records[0].cause == "zero_execution"


def test_exam_injected_skill_with_a_spent_judge_budget_fails_generation(
        tmp_path):
    store = Store(tmp_path)
    store.save(_attempt(
        0, purpose="acceptance", skill_id="skill-1", skill_version=2,
        judge_reason="judge_failed_tries=2; bad output"), "attempts")
    spec = _exam_spec(
        "unit:skill", [{"skill_id": "skill-1", "version": 2}])

    records = coverage.exam_coverage(
        store, [spec], control_perfect_units=set(),
        leak_versions=set(), generation_failed_units=set())

    assert len(records) == 1
    assert records[0].present is False
    assert records[0].status == "terminal"
    assert records[0].cause == "exam_generation_failed"


# ---------------------------------------------------------------------------
# Live
# ---------------------------------------------------------------------------

def test_live_coverage_present_missing_and_accepted_hole(tmp_path):
    store = Store(tmp_path)
    round_store = Store(lm.round_dir(store, 0))
    round_store.save(_trajectory(attempt=0), "trajectories")
    accepted = {ids.make_trajectory_id(TASK, "weak", 1)}
    spec = _spec("unit:live")

    records = coverage.live_coverage(
        store, [spec], round_=0, terminal_overrides={},
        accepted_holes=accepted)

    assert [(record.attempt_index, record.present, record.status,
             record.cause) for record in records] == [
        (0, True, "present", ""),
        (1, False, "missing", "accepted_hole"),
    ]


def test_live_coverage_terminal_override_wins_over_stored_trajectories(
        tmp_path):
    store = Store(tmp_path)
    round_store = Store(lm.round_dir(store, 0))
    round_store.save(_trajectory(attempt=0), "trajectories")
    spec = _spec("unit:overflow", expected_attempts=2)

    records = coverage.live_coverage(
        store, [spec], round_=0,
        terminal_overrides={"unit:overflow": "injection_overflow"},
        zero_execution_units={"unit:overflow"})

    assert [(record.present, record.status, record.cause)
            for record in records] == [
        (True, "present", "injection_overflow"),
        (False, "terminal", "injection_overflow"),
    ]


def test_live_coverage_undispatched_group_is_zero_execution(tmp_path):
    store = Store(tmp_path)
    spec = _spec("unit:zero", expected_attempts=2)

    records = coverage.live_coverage(
        store, [spec], round_=0, terminal_overrides={},
        zero_execution_units={"unit:zero"})

    assert [(record.present, record.status, record.cause)
            for record in records] == [
        (False, "terminal", "zero_execution"),
        (False, "terminal", "zero_execution"),
    ]


def test_live_coverage_unstored_trial_is_a_missing_dispatch(tmp_path):
    store = Store(tmp_path)
    spec = _spec("unit:missing", expected_attempts=1)

    records = coverage.live_coverage(
        store, [spec], round_=0, terminal_overrides={})

    assert len(records) == 1
    assert records[0].present is False
    assert records[0].status == "missing"
    assert records[0].cause == "missing"


# ---------------------------------------------------------------------------
# Test phase (local arms and external rewards)
# ---------------------------------------------------------------------------

def _by_key(records):
    return {(record.unit_id, record.attempt_index): record
            for record in records}


def test_test_coverage_local_arms_and_external_rewards(tmp_path):
    with_bank = Store(tmp_path / "with_bank")
    with_bank.save(_trajectory("tb:t1", attempt=0), "trajectories")
    with_bank.save(_trajectory("tb:t2", attempt=0), "trajectories")
    specs = [
        _spec("unit:present", task_ref="tb:t1", arm="with_bank",
              expected_attempts=1),
        _spec("unit:hole", task_ref="tb:t2", arm="with_bank",
              expected_attempts=1),
        _spec("unit:no-store", task_ref="tb:t3", arm="control",
              expected_attempts=1),
        _spec("unit:external", task_ref="tb:t4", arm="external_control",
              expected_attempts=1),
    ]

    records = coverage.test_coverage(
        {"with_bank": with_bank}, specs,
        accepted_by_arm={
            "with_bank": {ids.make_trajectory_id("tb:t2", "weak", 0)}},
        external_rewards={
            "external_control": {"tb:t4": [1.0, 0.0]}})

    by_key = _by_key(records)
    assert set(by_key) == {
        ("unit:present", 0),
        ("unit:hole", 0),
        ("unit:no-store", 0),
        ("unit:external", 0),
        ("unit:external", 1),
    }
    assert (by_key[("unit:present", 0)].present,
            by_key[("unit:present", 0)].status) == (True, "present")
    assert (by_key[("unit:hole", 0)].present,
            by_key[("unit:hole", 0)].status,
            by_key[("unit:hole", 0)].cause) == (
                False, "missing", "accepted_hole")
    assert (by_key[("unit:no-store", 0)].present,
            by_key[("unit:no-store", 0)].status,
            by_key[("unit:no-store", 0)].cause) == (
                False, "missing", "missing")
    # One present record per supplied external reward, indexed from zero.
    assert (by_key[("unit:external", 0)].present,
            by_key[("unit:external", 1)].present) == (True, True)
    assert all(by_key[("unit:external", index)].cause == ""
               for index in (0, 1))
