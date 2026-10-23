"""The two P04/P06 decision strategies: per-skill rows from reports/usage."""

from expedistill.composites import decision, decision_strategies
from expedistill.core import ids
from expedistill.core.contracts import AcceptanceReportRow
from tests.helpers.skills import make_skill

TASK = ids.make_task_id("tb", "t1")
TRAJ = ids.make_trajectory_id(TASK, "strong", 0)
SKILLS = [make_skill(ids.make_subtask_id(TRAJ, index),
                     summary=f"skill {index}")
          for index in range(2)]
SKILL_IDS = [skill.skill_id for skill in SKILLS]


def _report_row(*, unit_id="p03:live:tb:tb:t1:weak:deadbeef",
                valid=3, success=0, status="normal", cause=""):
    return AcceptanceReportRow(
        unit_id=unit_id, phase_label="p03:live", role="weak",
        injected_skills=[{"skill_id": skill_id, "version": 1}
                         for skill_id in SKILL_IDS],
        runs=[], valid_count=valid, success_count=success,
        status=status, cause=cause, artifacts={})


def _baseline_row(*, valid=3, success=0):
    return AcceptanceReportRow(
        unit_id="p01:baseline:tb:tb:t1:weak:deadbeef",
        phase_label="p01:baseline", role="weak", injected_skills=[],
        runs=[], valid_count=valid, success_count=success,
        status="normal", cause="", artifacts={})


def _usage(*, delivery="ok", available=None, read=None):
    return {
        "task_id": TASK, "round": 0, "attempt": 0,
        "delivery_status": delivery,
        "available_skill_ids": SKILL_IDS if available is None else available,
        "read_skill_ids": [] if read is None else read,
    }


def test_task_level_expands_a_group_row_to_every_member():
    rows = decision_strategies.decide(
        name="task_level", current_kind="live",
        current_report=[_report_row()], control_kind="baseline",
        control_report=[_baseline_row()], bank=SKILLS, checks_left=1,
        compare_rule="strict_rate_gain", n_attempts_weak=3,
        task_select_rule="any_weak_failed")

    assert [(row.target_id, row.action, row.reason) for row in rows] == [
        (SKILL_IDS[0], decision.REVISE, "no_gain"),
        (SKILL_IDS[1], decision.REVISE, "no_gain"),
    ]
    assert {row.target_kind for row in rows} == {"skill"}
    assert {row.basis_unit for row in rows} == {
        "p03:live:tb:tb:t1:weak:deadbeef"}


def test_skill_level_revises_read_and_deletes_unread():
    rows = decision_strategies.decide(
        name="skill_level", current_kind="live",
        current_report=[_report_row()], control_kind="baseline",
        control_report=[_baseline_row()], bank=SKILLS, checks_left=1,
        compare_rule="strict_rate_gain", n_attempts_weak=3,
        task_select_rule="any_weak_failed",
        usage_by_task={TASK: [_usage(read=[SKILL_IDS[0]])]})

    by_id = {row.target_id: row for row in rows}
    assert by_id[SKILL_IDS[0]].action == decision.REVISE
    assert by_id[SKILL_IDS[0]].reason == "skill_read"
    assert by_id[SKILL_IDS[1]].action == decision.DELETE
    assert by_id[SKILL_IDS[1]].reason == "skill_never_read"


def test_skill_level_keeps_a_group_with_untrusted_delivery():
    rows = decision_strategies.decide(
        name="skill_level", current_kind="live",
        current_report=[_report_row()], control_kind="baseline",
        control_report=[_baseline_row()], bank=SKILLS, checks_left=1,
        compare_rule="strict_rate_gain", n_attempts_weak=3,
        task_select_rule="any_weak_failed",
        usage_by_task={TASK: [_usage(delivery="manifest_mismatch")]})

    assert [(row.action, row.reason) for row in rows] == [
        (decision.KEEP, "unverified_delivery"),
        (decision.KEEP, "unverified_delivery"),
    ]


def test_skill_level_keeps_when_usage_evidence_is_missing():
    rows = decision_strategies.decide(
        name="skill_level", current_kind="live",
        current_report=[_report_row()], control_kind="baseline",
        control_report=[_baseline_row()], bank=SKILLS, checks_left=1,
        compare_rule="strict_rate_gain", n_attempts_weak=3,
        task_select_rule="any_weak_failed", usage_by_task={})

    assert [(row.action, row.reason) for row in rows] == [
        (decision.KEEP, "unverified_delivery"),
        (decision.KEEP, "unverified_delivery"),
    ]


def test_skill_level_keeps_a_skill_not_in_the_manifest():
    rows = decision_strategies.decide(
        name="skill_level", current_kind="live",
        current_report=[_report_row()], control_kind="baseline",
        control_report=[_baseline_row()], bank=SKILLS, checks_left=1,
        compare_rule="strict_rate_gain", n_attempts_weak=3,
        task_select_rule="any_weak_failed",
        usage_by_task={TASK: [_usage(available=[SKILL_IDS[0]],
                                     read=[SKILL_IDS[0]])]})

    by_id = {row.target_id: row for row in rows}
    assert by_id[SKILL_IDS[0]].action == decision.REVISE
    assert by_id[SKILL_IDS[1]].action == decision.KEEP
    assert by_id[SKILL_IDS[1]].reason == "unverified_delivery"
