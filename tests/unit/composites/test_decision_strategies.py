"""The two P04/P06 decision strategies: per-skill rows from reports/usage."""

from expedistill.composites import decision, decision_strategies
from expedistill.core import ids
from expedistill.core.answer_sheet import UnitSummary
from tests.helpers.skills import make_skill

TASK = ids.make_task_id("tb", "t1")
TRAJ = ids.make_trajectory_id(TASK, "strong", 0)
SKILLS = [make_skill(ids.make_subtask_id(TRAJ, index),
                     summary=f"skill {index}")
          for index in range(2)]
SKILL_IDS = [skill.skill_id for skill in SKILLS]


def _report_row(*, unit_id="p03:live:tb:tb:t1:weak:deadbeef",
                valid=3, success=0, status="normal", cause=""):
    return UnitSummary(
        unit_id=unit_id, phase_label="p03:live", task_ref=TASK,
        role="weak", arm=None, expected_attempts=valid,
        injected_skills=[{"skill_id": skill_id, "version": 1}
                         for skill_id in SKILL_IDS],
        valid_count=valid, success_count=success, status=status,
        cause=cause)


def _baseline_row(*, valid=3, success=0):
    return UnitSummary(
        unit_id="p01:baseline:tb:tb:t1:weak:deadbeef",
        phase_label="p01:baseline", task_ref=TASK, role="weak", arm=None,
        expected_attempts=valid, injected_skills=[], valid_count=valid,
        success_count=success, status="normal", cause="")


def _usage(*, delivery="ok", available=None, read=None, round_=0):
    return {
        "task_id": TASK, "round": round_, "attempt": 0,
        "delivery_status": delivery,
        "available_skill_ids": SKILL_IDS if available is None else available,
        "read_skill_ids": [] if read is None else read,
    }


PENDING = {SKILL_IDS[0]: "pending", SKILL_IDS[1]: "pending"}
ACCEPTED = {SKILL_IDS[0]: "accepted", SKILL_IDS[1]: "accepted"}
GROUP_LOST = {TASK: False}
GROUP_WON = {TASK: True}


def test_task_level_expands_a_group_row_to_every_member():
    rows = decision_strategies.decide(
        name="task_level", current_kind="live",
        current_report=[_report_row()], control_kind="baseline",
        control_report=[_baseline_row()], bank=SKILLS, checks_left=1,
        compare_rule="strict_rate_gain", n_attempts_weak=3,
        task_select_rule="any_weak_failed", delivery="whole_bank")

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
        task_select_rule="any_weak_failed", delivery="folder",
        usage_by_task={TASK: [_usage(read=[SKILL_IDS[0]])]},
        group_better_by_task=GROUP_LOST,
        skill_status_by_id=PENDING)

    by_id = {row.target_id: row for row in rows}
    assert by_id[SKILL_IDS[0]].action == decision.REVISE
    assert by_id[SKILL_IDS[0]].reason == "skill_not_won"
    assert by_id[SKILL_IDS[1]].action == decision.DELETE
    assert by_id[SKILL_IDS[1]].reason == "skill_never_read"


def test_skill_level_keeps_a_group_with_untrusted_delivery():
    rows = decision_strategies.decide(
        name="skill_level", current_kind="live",
        current_report=[_report_row()], control_kind="baseline",
        control_report=[_baseline_row()], bank=SKILLS, checks_left=1,
        compare_rule="strict_rate_gain", n_attempts_weak=3,
        task_select_rule="any_weak_failed", delivery="folder",
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
        task_select_rule="any_weak_failed", delivery="folder",
        usage_by_task={})

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
        task_select_rule="any_weak_failed", delivery="folder",
        usage_by_task={TASK: [_usage(available=[SKILL_IDS[0]],
                                     read=[SKILL_IDS[0]])]},
        group_better_by_task=GROUP_LOST,
        skill_status_by_id=PENDING)

    by_id = {row.target_id: row for row in rows}
    assert by_id[SKILL_IDS[0]].action == decision.REVISE
    assert by_id[SKILL_IDS[0]].reason == "skill_not_won"
    assert by_id[SKILL_IDS[1]].action == decision.KEEP
    assert by_id[SKILL_IDS[1]].reason == "unverified_delivery"


def test_skill_level_keeps_a_read_won_skill():
    rows = decision_strategies.decide(
        name="skill_level", current_kind="live",
        current_report=[_report_row()], control_kind="baseline",
        control_report=[_baseline_row()], bank=SKILLS, checks_left=1,
        compare_rule="strict_rate_gain", n_attempts_weak=3,
        task_select_rule="any_weak_failed", delivery="folder",
        usage_by_task={TASK: [_usage(read=[SKILL_IDS[0]])]},
        group_better_by_task=GROUP_WON,
        skill_status_by_id=ACCEPTED)

    by_id = {row.target_id: row for row in rows}
    assert by_id[SKILL_IDS[0]].action == decision.KEEP
    assert by_id[SKILL_IDS[0]].reason == "already_won"
    # The unread member is still deleted under the default policy.
    assert by_id[SKILL_IDS[1]].action == decision.DELETE


def test_skill_level_deletes_unread_accepted_in_won_group_by_default():
    rows = decision_strategies.decide(
        name="skill_level", current_kind="live",
        current_report=[_report_row()], control_kind="baseline",
        control_report=[_baseline_row()], bank=SKILLS, checks_left=1,
        compare_rule="strict_rate_gain", n_attempts_weak=3,
        task_select_rule="any_weak_failed", delivery="folder",
        usage_by_task={TASK: [_usage()]},
        group_better_by_task=GROUP_WON,
        skill_status_by_id=ACCEPTED,
        unread_in_won_group="delete")

    assert [(row.action, row.reason) for row in rows] == [
        (decision.DELETE, "skill_never_read"),
        (decision.DELETE, "skill_never_read"),
    ]


def test_skill_level_keeps_unread_accepted_in_won_group_when_policy_keep():
    rows = decision_strategies.decide(
        name="skill_level", current_kind="live",
        current_report=[_report_row()], control_kind="baseline",
        control_report=[_baseline_row()], bank=SKILLS, checks_left=1,
        compare_rule="strict_rate_gain", n_attempts_weak=3,
        task_select_rule="any_weak_failed", delivery="folder",
        usage_by_task={TASK: [_usage()]},
        group_better_by_task=GROUP_WON,
        skill_status_by_id=ACCEPTED,
        unread_in_won_group="keep")

    assert [(row.action, row.reason) for row in rows] == [
        (decision.KEEP, "won_group_unread"),
        (decision.KEEP, "won_group_unread"),
    ]


def test_skill_level_keeps_when_group_result_is_missing():
    rows = decision_strategies.decide(
        name="skill_level", current_kind="live",
        current_report=[_report_row()], control_kind="baseline",
        control_report=[_baseline_row()], bank=SKILLS, checks_left=1,
        compare_rule="strict_rate_gain", n_attempts_weak=3,
        task_select_rule="any_weak_failed", delivery="folder",
        usage_by_task={TASK: [_usage(read=[SKILL_IDS[0]])]},
        group_better_by_task={}, skill_status_by_id=PENDING)

    by_id = {row.target_id: row for row in rows}
    assert by_id[SKILL_IDS[0]].action == decision.KEEP
    assert by_id[SKILL_IDS[0]].reason == "unverified_group"


def test_skill_level_keeps_when_status_is_missing():
    rows = decision_strategies.decide(
        name="skill_level", current_kind="live",
        current_report=[_report_row()], control_kind="baseline",
        control_report=[_baseline_row()], bank=SKILLS, checks_left=1,
        compare_rule="strict_rate_gain", n_attempts_weak=3,
        task_select_rule="any_weak_failed", delivery="folder",
        usage_by_task={TASK: [_usage(read=[SKILL_IDS[0]])]},
        group_better_by_task=GROUP_LOST, skill_status_by_id={})

    by_id = {row.target_id: row for row in rows}
    assert by_id[SKILL_IDS[0]].action == decision.KEEP
    assert by_id[SKILL_IDS[0]].reason == "unverified_status"


def test_p04_and_p06_use_the_same_skill_level_rule():
    common = dict(
        name="skill_level", current_kind="live",
        control_kind="baseline", control_report=[_baseline_row()],
        bank=SKILLS, checks_left=1, compare_rule="strict_rate_gain",
        n_attempts_weak=3, task_select_rule="any_weak_failed",
        delivery="folder", usage_by_task={
            TASK: [_usage(read=[SKILL_IDS[0]])]},
        group_better_by_task=GROUP_LOST,
        skill_status_by_id=PENDING)
    p04 = decision_strategies.decide(
        current_report=[
            _report_row(unit_id="p03:live:tb:tb:t1:weak:deadbeef")],
        **common)
    p06 = decision_strategies.decide(
        current_report=[
            _report_row(unit_id="p05:live:tb:tb:t1:weak:deadbeef")],
        **common)

    assert [(row.target_id, row.action, row.reason) for row in p04] == [
        (row.target_id, row.action, row.reason) for row in p06]
