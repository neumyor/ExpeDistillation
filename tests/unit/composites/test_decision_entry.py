"""W1-C (R3): the unified decision entry, end to end.

decide_task_list turns (this round's report, the control report, the
bank, the checks left) into one task list. Pinned here: the exam-kind
and live-kind target mappings, the in-phase vs borrowed control, the
first-learn-phase baseline path (empty current report, D report as the
input), the C-phase rejection, the bank's immutability (R4's decision
side) and the loud failures on wiring bugs. The table itself is pinned
cell by cell in tests/unit/composites/test_decision_table.py.

Unit ids follow core/contracts.py's frozen construction rule -- the
report row carries no task_ref field, so its task lives inside the id:
``pNN:<local>:<benchmark>:<task_or_exam_id>:<role>:<sha8>``.
"""

import pytest

from expedistill.composites import decision
from expedistill.composites.decision import (
    AcceptanceReportRow,
    FinalTestReportRejected,
    decide_task_list,
)
from expedistill.infra.reliability import CodeBugError, ConfigError
from tests.helpers.skills import make_skill

_EXAM = "traj/tb:t1/strong/00/seg00/exam"
_SKILL = "traj/tb:t1/strong/00/seg00/skill"
_EXAM_UNIT = f"p01:A:tb:{_EXAM}:weak:11111111"
_BARE_EXAM_UNIT = f"p01:A:tb:{_EXAM}:weak:00000000"


def _skill(skill_id=_SKILL):
    return make_skill(skill_id[: -len("/skill")], "do the thing")


def _row(*, unit_id, role="weak", injected=None, valid=1, success=1,
         status="normal", cause="", phase_label="p01:A"):
    return AcceptanceReportRow(
        unit_id=unit_id, phase_label=phase_label, role=role,
        injected_skills=injected, runs=[], valid_count=valid,
        success_count=success, status=status, cause=cause, artifacts={})


def _bank():
    return [_skill()]


def _exam_rows(*, with_skill_valid=1, with_skill_success=1):
    return [
        _row(unit_id=_EXAM_UNIT,
             injected=[{"skill_id": _SKILL, "version": 2}],
             valid=with_skill_valid, success=with_skill_success),
        # The bare answer of the same exam: an in-phase control unit
        # (section 1.3), never a decision target.
        _row(unit_id=_BARE_EXAM_UNIT, injected=None, valid=1, success=0),
    ]


def _live_rows(*, valid=1, success=1, status="normal", cause=""):
    return [_row(unit_id="p01:B:tb:tb:t1:weak:22222222",
                 injected=[{"skill_id": _SKILL, "version": 1}],
                 valid=valid, success=success, status=status, cause=cause,
                 phase_label="p01:B")]


def _d_report(task_valid=2, task_success=0):
    return [
        _row(unit_id="p00:D:tb:tb:t1:strong:00000000", role="strong",
             injected=None, valid=1, success=1, phase_label="p00:D"),
        _row(unit_id="p00:D:tb:tb:t1:weak:00000000", role="weak",
             injected=None, valid=task_valid, success=task_success,
             phase_label="p00:D"),
    ]


# ---------------------------------------------------------------------------
# Exam kind: one skill per exam, in-phase bare control
# ---------------------------------------------------------------------------

def test_exam_improved_unit_targets_its_skill_with_keep():
    out = decide_task_list(current_kind=decision.PHASE_EXAM,
                           current_report=_exam_rows(), bank=_bank(),
                           checks_left=1)
    assert out == [decision.TaskListRow(
        target_kind="skill", target_id=_SKILL, action=decision.KEEP,
        reason="improved", basis_unit=_EXAM_UNIT)]


def test_exam_no_gain_on_the_last_check_deletes_with_retry_exhausted():
    out = decide_task_list(
        current_kind=decision.PHASE_EXAM,
        current_report=_exam_rows(with_skill_valid=1, with_skill_success=0),
        bank=_bank(), checks_left=0)
    assert out[0].action == decision.DELETE
    assert out[0].reason == decision.RETRY_EXHAUSTED
    assert out[0].target_id == _SKILL


def test_exam_row_without_an_exam_id_is_a_wiring_bug():
    rows = [
        _row(unit_id="p01:A:tb:tb:t1:weak:33333333",
             injected=[{"skill_id": _SKILL, "version": 1}]),
        # An in-phase control so the missing-control guard stays quiet
        # and the exam-id check itself fires.
        _row(unit_id="p01:A:tb:tb:t1:weak:00000000", injected=None),
    ]
    with pytest.raises(CodeBugError, match="not an exam id"):
        decide_task_list(current_kind=decision.PHASE_EXAM,
                         current_report=rows, bank=_bank(), checks_left=1)


# ---------------------------------------------------------------------------
# Live kind: a task's whole group, D-phase control
# ---------------------------------------------------------------------------

def test_live_improved_unit_targets_the_task_group():
    out = decide_task_list(current_kind=decision.PHASE_LIVE,
                           current_report=_live_rows(valid=1, success=1),
                           control_kind=decision.PHASE_BASELINE,
                           control_report=_d_report(), bank=_bank(),
                           checks_left=1)
    assert out == [decision.TaskListRow(
        target_kind="group", target_id="tb:t1", action=decision.KEEP,
        reason="improved",
        basis_unit="p01:B:tb:tb:t1:weak:22222222")]


def test_live_strong_arm_of_the_control_batch_is_ignored():
    # The control is the weak model's bare result; the D report carries
    # the strong arm's rows too and they must not pollute the counts.
    out = decide_task_list(current_kind=decision.PHASE_LIVE,
                           current_report=_live_rows(valid=2, success=1),
                           control_kind=decision.PHASE_BASELINE,
                           control_report=_d_report(task_valid=2,
                                                    task_success=1),
                           bank=_bank(), checks_left=1)
    # Control 1/2 vs live 1/2: not strictly better -> revise.
    assert out[0].action == decision.REVISE


def test_live_skipped_run_consumes_not_run_control_perfect():
    out = decide_task_list(
        current_kind=decision.PHASE_LIVE,
        current_report=_live_rows(status=decision.ROW_NOT_RUN_CONTROL_PERFECT),
        control_kind=decision.PHASE_BASELINE,
        control_report=_d_report(task_valid=2, task_success=2),
        bank=_bank(), checks_left=1)
    assert out[0].action == decision.DELETE
    assert out[0].reason == decision.WEAK_ALREADY_KNOWS


def test_live_zero_execution_row_produces_no_task_row():
    # B1's one-meaning rule: zero_execution is a visibility fact (no
    # completed run to report yet), so the row decides nothing and the
    # entry emits no task-list row for it. The weak_already_knows
    # short-circuit travels only as not_run_control_perfect (pinned
    # above), never as a zero_execution cause.
    out = decide_task_list(
        current_kind=decision.PHASE_LIVE,
        current_report=_live_rows(valid=0, success=0,
                                  status=decision.ROW_NOT_TESTABLE,
                                  cause=decision.CAUSE_ZERO_EXECUTION),
        control_kind=decision.PHASE_BASELINE,
        control_report=_d_report(task_valid=2, task_success=2),
        bank=_bank(), checks_left=1)
    assert out == []


def test_deciding_against_an_in_flight_bare_control_is_a_wiring_bug():
    # B1's danger path, end to end through the entry: the with-skill
    # answer is judged right while the exam's bare control has not been
    # answered yet (zero_execution). Deciding now is the caller's bug
    # -- a CodeBugError, never a weak_already_knows delete of a skill
    # whose measurement just passed.
    rows = [
        _row(unit_id=_EXAM_UNIT,
             injected=[{"skill_id": _SKILL, "version": 2}]),
        _row(unit_id=_BARE_EXAM_UNIT, injected=None, valid=0, success=0,
             status=decision.ROW_NOT_TESTABLE,
             cause=decision.CAUSE_ZERO_EXECUTION),
    ]
    with pytest.raises(CodeBugError, match="in flight"):
        decide_task_list(current_kind=decision.PHASE_EXAM,
                         current_report=rows, bank=_bank(), checks_left=1)


def test_live_leaked_unit_revises_then_deletes_on_the_last_check():
    leaked = _live_rows(valid=0, success=0,
                        status=decision.ROW_NOT_TESTABLE,
                        cause=decision.CAUSE_LEAK)
    out = decide_task_list(current_kind=decision.PHASE_LIVE,
                           current_report=leaked,
                           control_kind=decision.PHASE_BASELINE,
                           control_report=_d_report(), bank=_bank(),
                           checks_left=1)
    assert (out[0].action, out[0].reason) == (decision.REVISE, "leak")
    out = decide_task_list(current_kind=decision.PHASE_LIVE,
                           current_report=leaked,
                           control_kind=decision.PHASE_BASELINE,
                           control_report=_d_report(), bank=_bank(),
                           checks_left=0)
    assert (out[0].action, out[0].reason) == (decision.DELETE,
                                              decision.LEAK_AUDIT)


def test_live_target_without_a_control_is_a_wiring_bug():
    # tb:t2 has no control row in the D report: the phase config names
    # the control source, so a missing one is wiring, not data.
    with pytest.raises(CodeBugError, match="no control row"):
        decide_task_list(
            current_kind=decision.PHASE_LIVE,
            current_report=[_row(
                unit_id="p01:B:tb:tb:t2:weak:44444444",
                injected=[{"skill_id": "s", "version": 1}])],
            control_kind=decision.PHASE_BASELINE,
            control_report=_d_report(), bank=_bank(), checks_left=1)


def test_retrieval_miss_rows_are_controls_never_targets():
    # P1: skills_injected=[] (a retrieval that reached no threshold) is
    # R5's frozen legal value, and it is the same measurement as a bare
    # run (reuse_key's semantics) -- so it joins the in-phase controls
    # instead of raising, produces no action row of its own, and serves
    # as the control for the same task's target row.
    rows = [
        _row(unit_id="p01:B:tb:tb:t1:weak:55555555",
             injected=[{"skill_id": _SKILL, "version": 1}],
             valid=1, success=1),
        _row(unit_id="p01:B:tb:tb:t1:weak:66666666", injected=[],
             valid=2, success=0),
    ]
    out = decide_task_list(current_kind=decision.PHASE_LIVE,
                           current_report=rows, bank=_bank(),
                           checks_left=1)
    # One target row, kept against the [] control's 0/2 -- and nothing
    # for the [] row itself.
    assert out == [decision.TaskListRow(
        target_kind="group", target_id="tb:t1", action=decision.KEEP,
        reason="improved",
        basis_unit="p01:B:tb:tb:t1:weak:55555555")]
    assert decide_task_list(
        current_kind=decision.PHASE_LIVE,
        current_report=[rows[1]], bank=_bank(), checks_left=1) == []


def test_malformed_unit_ids_fail_loudly():
    # The third id is a bare "pNN" phase label (no ":<name>" colon): it
    # eats the benchmark slot, which the parser now refuses instead of
    # silently mis-slicing the task id (D2).
    for bad_id in ("not-a-unit-id", "p01:A:tb:t1:weak:not-hex!",
                   "x01:A:tb:t1:weak:aaaaaaaa",
                   "p01:tb:tb:t1:weak:aaaaaaaa"):
        with pytest.raises(CodeBugError, match="malformed unit id"):
            decide_task_list(
                current_kind=decision.PHASE_LIVE,
                current_report=[_row(unit_id=bad_id,
                                     injected=[{"skill_id": "s",
                                                "version": 1}])],
                control_kind=decision.PHASE_BASELINE,
                control_report=_d_report(), bank=_bank(), checks_left=1)


# ---------------------------------------------------------------------------
# The first learn phase: empty current report, D report as the input
# ---------------------------------------------------------------------------

def _d_weak_row(task, *, valid=2, success=0, status="normal",
                cause=""):
    return _row(unit_id=f"p00:D:tb:{task}:weak:00000000", role="weak",
                injected=None, valid=valid, success=success, status=status,
                cause=cause, phase_label="p00:D")


def test_baseline_path_adds_failed_skillless_tasks():
    out = decide_task_list(
        current_kind=None, current_report=None,
        control_kind=decision.PHASE_BASELINE,
        control_report=[
            _d_weak_row("tb:t1", valid=2, success=0),
            _d_weak_row("tb:t2", valid=2, success=2),
            _d_weak_row("tb:t3", valid=0, success=0,
                        status=decision.ROW_NOT_TESTABLE,
                        cause=decision.CAUSE_ALL_HOLES),
        ],
        bank=[], checks_left=2, n_attempts_weak=2,
        task_select_rule="any_weak_failed")
    # t1 fails and has no skill; t2 the weak model already solves; t3 was
    # never measured (infra holes are not model failures).
    assert out == [decision.TaskListRow(
        target_kind="task", target_id="tb:t1", action=decision.ADD,
        reason=decision.BASELINE_GAP_REASON,
        basis_unit="p00:D:tb:tb:t1:weak:00000000")]


def test_baseline_path_skips_tasks_the_bank_already_covers():
    out = decide_task_list(
        current_kind=None,
        control_kind=decision.PHASE_BASELINE,
        control_report=[_d_weak_row("tb:t1")],
        bank=[_skill()],  # the bank holds a skill distilled from tb:t1
        checks_left=0, n_attempts_weak=2,
        task_select_rule="any_weak_failed")
    assert out == []


def test_baseline_path_uses_the_named_task_select_rule():
    # all_valid_failed requires the FULL valid sample: a partial sample
    # with a real failure is not a gap under it.
    partial = [_d_weak_row("tb:t1", valid=1, success=0)]
    out = decide_task_list(current_kind=None,
                           control_kind=decision.PHASE_BASELINE,
                           control_report=partial, bank=[],
                           checks_left=1, n_attempts_weak=2,
                           task_select_rule="all_valid_failed")
    assert out == []
    out = decide_task_list(current_kind=None,
                           control_kind=decision.PHASE_BASELINE,
                           control_report=partial, bank=[],
                           checks_left=1, n_attempts_weak=2,
                           task_select_rule="any_weak_failed")
    assert [row.target_id for row in out] == ["tb:t1"]


def test_baseline_path_demands_the_d_report():
    with pytest.raises(CodeBugError, match="D-phase report"):
        decide_task_list(current_kind=None, control_kind=None,
                         control_report=None, bank=[], checks_left=1)
    with pytest.raises(CodeBugError, match="D-phase report"):
        decide_task_list(current_kind=None, control_kind=decision.PHASE_EXAM,
                         control_report=[], bank=[], checks_left=1)
    with pytest.raises(CodeBugError, match="without a phase kind"):
        decide_task_list(current_kind=None,
                         current_report=[_row(unit_id=_EXAM_UNIT)],
                         control_kind=decision.PHASE_BASELINE,
                         control_report=[], bank=[], checks_left=1)


# ---------------------------------------------------------------------------
# Section 3 rule 9: the C-phase rejection, and R4's decision side
# ---------------------------------------------------------------------------

def test_final_test_reports_are_rejected_on_both_sides():
    with pytest.raises(FinalTestReportRejected, match="final-test"):
        decide_task_list(current_kind=decision.PHASE_TEST,
                         current_report=[], bank=[], checks_left=0)
    with pytest.raises(FinalTestReportRejected, match="final-test"):
        decide_task_list(current_kind=decision.PHASE_EXAM,
                         current_report=[], control_kind=decision.PHASE_TEST,
                         control_report=[], bank=[], checks_left=0)


def test_the_bank_is_untouched_by_the_decision():
    # R4's decision side: a skill's deletion is a task-list row, never a
    # write. The bank objects pass through byte-identical even when the
    # table deletes everything they cover.
    bank = [_skill(), _skill("traj/tb:t2/strong/00/seg00/skill")]
    before = [skill.to_dict() for skill in bank]
    decide_task_list(
        current_kind=decision.PHASE_LIVE,
        current_report=[_row(
            unit_id="p01:B:tb:tb:t2:weak:66666666",
            injected=[{"skill_id": bank[1].skill_id, "version": 1}],
            valid=1, success=0)],
        control_kind=decision.PHASE_BASELINE,
        control_report=[_row(unit_id="p00:D:tb:tb:t2:weak:00000000",
                             role="weak", injected=None, valid=2,
                             success=1, phase_label="p00:D")],
        bank=bank, checks_left=0)
    assert [skill.to_dict() for skill in bank] == before


def test_entry_validation_fails_loudly():
    with pytest.raises(ConfigError, match="unknown compare rule"):
        decide_task_list(current_kind=decision.PHASE_EXAM,
                         current_report=[], bank=[], checks_left=1,
                         compare_rule="mystery")
    with pytest.raises(CodeBugError, match="unknown current phase kind"):
        decide_task_list(current_kind="mystery", current_report=[],
                         bank=[], checks_left=1)
    with pytest.raises(CodeBugError, match="unknown control phase kind"):
        decide_task_list(current_kind=decision.PHASE_EXAM,
                         current_report=[], control_kind="mystery",
                         bank=[], checks_left=1)
    with pytest.raises(CodeBugError, match="checks_left"):
        decide_task_list(current_kind=decision.PHASE_EXAM,
                         current_report=[], bank=[], checks_left=-1)


def test_an_acceptance_phase_with_no_units_decides_nothing():
    assert decide_task_list(current_kind=decision.PHASE_EXAM,
                            current_report=[], bank=[], checks_left=1) == []
