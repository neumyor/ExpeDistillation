"""Evidence routing (composites.evidence): every cell of the table, the
"opened before" boundary, the delete rule and the playbook's facts."""

import pytest

from expedistill.composites import evidence as routing
from expedistill.core.models import Subtask
from expedistill.infra.reliability import CodeBugError
from tests.helpers.skills import make_skill

CARD = "type/001/skill"


def _route(*, scope="subtask", card=CARD, opened=None, failing_step=5):
    return routing.route(sheet_id="s1", failing_step=failing_step,
                         scope=scope, subtask_id="seg", type_id="type/001",
                         card=card, opened=opened or {})


def _evidence(category, *, sheet="s1", skill=CARD, type_id="type/001"):
    return routing.Evidence(sheet_id=sheet, failing_step=3,
                            subtask_id=f"{sheet}/seg", type_id=type_id,
                            skill_id=skill,
                            opened_before=category == routing.CONTENT,
                            opened_step=1 if category == routing.CONTENT
                            else None, category=category)


# -- the routing table --------------------------------------------------------

@pytest.mark.parametrize("card, opened", [
    (None, {}), (CARD, {}), (CARD, {CARD: 1}), (None, {CARD: 1})])
def test_a_general_failure_is_no_card_evidence(card, opened):
    row = _route(scope="general", card=card, opened=opened)
    assert row.category == routing.NONE


def test_a_type_without_an_active_card_is_a_gap():
    row = _route(card=None, opened={"type/009/skill": 0})
    assert (row.category, row.skill_id, row.opened_before,
            row.opened_step) == (routing.GAP, None, False, None)


def test_a_card_never_opened_is_trigger_evidence():
    row = _route(opened={"type/002/skill": 0})
    assert (row.category, row.opened_before, row.opened_step) == (
        routing.TRIGGER, False, None)


def test_a_card_opened_before_the_failing_step_is_content_evidence():
    row = _route(opened={CARD: 4}, failing_step=5)
    assert (row.category, row.opened_before, row.opened_step) == (
        routing.CONTENT, True, 4)


@pytest.mark.parametrize("read_step", [5, 6])
def test_a_card_first_read_at_or_after_the_failing_step_is_not_before(
        read_step):
    row = _route(opened={CARD: read_step}, failing_step=5)
    assert (row.category, row.opened_before, row.opened_step) == (
        routing.TRIGGER, False, read_step)


def test_an_unknown_scope_is_a_bug():
    with pytest.raises(CodeBugError):
        _route(scope="local")


def test_evidence_round_trips_and_checks_its_category():
    row = _route(opened={CARD: 4})
    assert routing.Evidence.from_dict(row.to_dict()) == row
    with pytest.raises(CodeBugError):
        routing.Evidence(**{**row.to_dict(), "category": "model"})


def test_the_error_segment_holds_the_failing_step():
    segments = [Subtask(subtask_id=f"t/seg0{index}", phase_label="p02:learn",
                        task_id="tb:t1", trajectory_id="t", source="weak",
                        trajectory_success=False, segment_index=index,
                        step_range=span, label="l", description="d",
                        success=False)
                for index, span in enumerate([(0, 2), (2, 5)])]
    assert routing.error_segment(segments, 1).subtask_id == "t/seg00"
    assert routing.error_segment(segments, 2).subtask_id == "t/seg01"
    assert routing.error_segment(segments, 5) is None


def test_the_counts_list_every_category():
    counts = routing.count_categories([_evidence(routing.GAP, skill=None),
                                       _evidence(routing.GAP, skill=None)])
    assert counts == {"gap": 2, "trigger": 0, "content": 0, "none": 0}


# -- the delete rule and the card plans -----------------------------------------

def test_content_evidence_in_both_phases_deletes_the_card():
    now = [_evidence(routing.CONTENT, sheet="s2")]
    before = [_evidence(routing.CONTENT, sheet="s1")]
    plan, = routing.plan_cards(now, before)
    assert plan.delete and plan.skill_id == CARD
    assert routing.content_failed_twice(CARD, before)


@pytest.mark.parametrize("before", [
    [], [_evidence(routing.TRIGGER)],
    [_evidence(routing.CONTENT, skill="type/002/skill",
               type_id="type/002")]])
def test_content_evidence_once_revises_instead(before):
    plan, = routing.plan_cards([_evidence(routing.CONTENT, sheet="s2")],
                               before)
    assert not plan.delete and len(plan.content) == 1


def test_trigger_evidence_alone_never_deletes():
    before = [_evidence(routing.CONTENT, sheet="s1")]
    plan, = routing.plan_cards([_evidence(routing.TRIGGER, sheet="s2")],
                               before)
    assert not plan.delete and len(plan.trigger) == 1


def test_plans_come_one_per_type_in_type_order():
    rows = [_evidence(routing.TRIGGER, sheet="s3", skill="type/002/skill",
                      type_id="type/002"),
            _evidence(routing.GAP, sheet="s1", skill=None,
                      type_id="type/003"),
            _evidence(routing.CONTENT, sheet="s2"),
            _evidence(routing.TRIGGER, sheet="s4"),
            _evidence(routing.NONE, sheet="s5", type_id="type/004")]
    plans = routing.plan_cards(rows, [])
    assert [(plan.type_id, plan.skill_id) for plan in plans] == [
        ("type/001", CARD), ("type/002", "type/002/skill"),
        ("type/003", None)]
    first = plans[0]
    assert ([row.sheet_id for row in first.content],
            [row.sheet_id for row in first.trigger]) == (["s2"], ["s4"])


def test_two_cards_for_one_type_are_a_bug():
    rows = [_evidence(routing.GAP, sheet="s1", skill=None),
            _evidence(routing.TRIGGER, sheet="s2")]
    with pytest.raises(CodeBugError):
        routing.plan_cards(rows, [])


def test_discarded_types_are_the_types_of_discarded_cards():
    bank = [make_skill("type/001", "a", status="discarded"),
            make_skill("type/002", "b")]
    assert routing.discarded_types(bank) == {"type/001"}


# -- the playbook writer's facts -------------------------------------------------

def test_trigger_facts_count_the_unopened_cards_per_type():
    rows = [_evidence(routing.TRIGGER, sheet="s1"),
            _evidence(routing.TRIGGER, sheet="s2"),
            _evidence(routing.CONTENT, sheet="s3"),
            _evidence(routing.TRIGGER, sheet="s4", skill="type/002/skill",
                      type_id="type/002")]
    text = routing.trigger_facts(rows, {"type/001": "find the object",
                                        "type/002": "heat the object"})
    assert '"find the object": 2 failed trial(s)' in text
    assert '"heat the object": 1 failed trial(s)' in text
    assert text.index("find") < text.index("heat")


def test_trigger_facts_are_empty_without_trigger_evidence():
    assert routing.trigger_facts([_evidence(routing.CONTENT)], {}) == ""


# -- the revise scope -------------------------------------------------------------

def _mixed():
    return [_evidence(routing.GAP, sheet="s1", skill=None,
                      type_id="type/009"),
            _evidence(routing.CONTENT, sheet="s2"),
            _evidence(routing.TRIGGER, sheet="s3", skill="type/002/skill",
                      type_id="type/002"),
            _evidence(routing.NONE, sheet="s4", type_id="type/004")]


def test_the_default_scope_sets_nothing_aside():
    rows = _mixed()
    kept, aside = routing.split_by_scope(rows, routing.REVISE_BOTH)
    assert kept == rows and aside == []


@pytest.mark.parametrize("scope, aside_category", [
    (routing.REVISE_TRIGGER_ONLY, routing.CONTENT),
    (routing.REVISE_CONTENT_ONLY, routing.TRIGGER)])
def test_a_scope_sets_aside_the_other_sides_evidence_only(scope,
                                                          aside_category):
    kept, aside = routing.split_by_scope(_mixed(), scope)
    assert [row.category for row in aside] == [aside_category]
    assert aside_category not in {row.category for row in kept}
    # Gap evidence and the none rows are untouched by every scope.
    assert {routing.GAP, routing.NONE} <= {row.category for row in kept}


def test_set_aside_content_evidence_does_not_count_toward_the_delete_rule():
    before = [_evidence(routing.CONTENT, sheet="s1")]
    now = [_evidence(routing.CONTENT, sheet="s2")]
    kept, _ = routing.split_by_scope(now, routing.REVISE_TRIGGER_ONLY)
    assert routing.plan_cards(kept, before) == []
    kept, _ = routing.split_by_scope(now, routing.REVISE_CONTENT_ONLY)
    plan, = routing.plan_cards(kept, before)
    assert plan.delete
