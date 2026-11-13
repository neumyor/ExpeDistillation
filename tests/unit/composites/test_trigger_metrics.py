"""composites/trigger_metrics.py: the pre-registered definitions, checked
against hand-computed small cases, and the refusal of contradictory
records."""

import pytest

from expedistill.composites.trigger_metrics import (
    GROUPS,
    card_use_metrics,
    trigger_metrics,
)
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
    GradingRecord,
)
from expedistill.core.models import Step

CARDS = ["a", "b", "c"]


def _trial(applicable, opened, adherence=None, correct=True):
    return {
        "task": "t", "attempt": 0, "delivered": CARDS,
        "applicable": {card: card in applicable for card in CARDS},
        "opened": opened, "adherence": adherence or {}, "correct": correct}


#: Five trials, small enough to count by hand.
TRIALS = [
    # a applies and is opened; its steps: followed, partial, not_reached.
    _trial({"a"}, ["a"], {"a": ["followed", "partial", "not_reached"]}, True),
    # a and b apply; a and the unrelated c are opened.
    _trial({"a", "b"}, ["a", "c"],
           {"a": ["skipped", "skipped"], "c": ["followed"]}, False),
    # a applies and is never opened.
    _trial({"a"}, [], {}, False),
    # nothing applies; b is opened anyway and no step of it is reached.
    _trial(set(), ["b"], {"b": ["not_reached", "not_reached"]}, True),
    # nothing applies, nothing opened, outcome unknown.
    _trial(set(), [], {}, None),
]


def test_trigger_recall_counts_applicable_pairs_that_were_opened():
    recall = trigger_metrics(TRIALS)["trigger_recall"]

    # applicable pairs: 1 + 2 + 1 = 4; opened among them: a, a = 2.
    assert recall == {"value": 0.5, "opened_applicable": 2,
                      "applicable": 4, "trials": 3}


def test_misfire_rate_counts_opened_pairs_that_did_not_apply():
    misfire = trigger_metrics(TRIALS)["misfire_rate"]

    # opened pairs: a | a, c | b = 4; not applicable: c and b = 2.
    assert misfire == {"value": 0.5, "opened_not_applicable": 2,
                       "opened": 4, "trials": 3}


def test_open_rate_is_the_share_of_trials_that_opened_any_card():
    assert trigger_metrics(TRIALS)["open_rate"] == {
        "value": 0.6, "trials_opened": 3, "trials": 5}


def test_adherence_averages_card_scores_and_leaves_not_reached_out():
    adherence = trigger_metrics(TRIALS)["adherence_rate"]

    # card scores: a in trial 1 = (1 + .5) / 2; a in trial 2 = 0;
    # c in trial 2 = 1; b in trial 4 has no countable step.
    assert adherence["value"] == pytest.approx((0.75 + 0.0 + 1.0) / 3)
    assert adherence["cards_opened"] == 4
    assert adherence["cards_scored"] == 3
    assert adherence["steps"] == {
        "followed": 2, "partial": 1, "skipped": 2, "not_reached": 3}


def test_success_is_split_into_three_disjoint_groups():
    groups = trigger_metrics(TRIALS)["success_by_group"]

    assert tuple(groups) == GROUPS
    assert groups["applicable_opened"] == {
        "n": 2, "n_known": 2, "successes": 1, "rate": 0.5}
    assert groups["applicable_not_opened"] == {
        "n": 1, "n_known": 1, "successes": 0, "rate": 0.0}
    # the unknown outcome counts in n but not in the rate.
    assert groups["nothing_applicable"] == {
        "n": 2, "n_known": 1, "successes": 1, "rate": 1.0}
    assert sum(group["n"] for group in groups.values()) == len(TRIALS)


def test_an_applicable_card_opened_puts_the_trial_in_applicable_opened():
    # b applies and is not opened, but a applies and is: the trial counts
    # as "applicable and opened".
    groups = trigger_metrics(
        [_trial({"a", "b"}, ["a"], {"a": ["followed"]})])["success_by_group"]

    assert groups["applicable_opened"]["n"] == 1
    assert groups["applicable_not_opened"]["n"] == 0


def test_a_ratio_with_nothing_to_divide_is_none_not_zero():
    metrics = trigger_metrics([_trial(set(), [], correct=False)])

    assert metrics["trigger_recall"]["value"] is None
    assert metrics["misfire_rate"]["value"] is None
    assert metrics["adherence_rate"]["value"] is None
    assert metrics["open_rate"]["value"] == 0.0
    assert metrics["success_by_group"]["applicable_opened"]["rate"] is None


def test_no_trials_gives_a_dict_of_nones():
    metrics = trigger_metrics([])

    assert metrics["n_trials"] == 0
    assert metrics["open_rate"]["value"] is None
    assert metrics["trigger_recall"]["value"] is None


def test_extra_record_keys_are_ignored():
    record = dict(TRIALS[0], applicability={"a": {"reason": "x"}})

    assert trigger_metrics([record])["trigger_recall"]["value"] == 1.0


@pytest.mark.parametrize("change", [
    {"opened": ["zzz"]},
    {"opened": ["a", "a"]},
    {"applicable": {"a": True}},
    {"applicable": {"a": 1, "b": False, "c": False}},
    {"adherence": {"b": ["followed"]}},
    {"adherence": {"a": ["done"]}, "opened": ["a"]},
    {"correct": "yes"},
    {"delivered": None},
])
def test_a_contradictory_record_is_refused(change):
    record = dict(_trial({"a"}, ["a"], {"a": ["followed"]}), **change)

    with pytest.raises(ValueError):
        trigger_metrics([record])


# ---------------------------------------------------------------------------
# card_use_metrics (a practice phase's mechanism metrics)
# ---------------------------------------------------------------------------

_FOLDER = {"files": [{"skill_id": "type/001/skill", "version": 1,
                      "filename": "type-001-skill/SKILL.md"},
                     {"skill_id": "type/002/skill", "version": 1,
                      "filename": "type-002-skill/SKILL.md"}]}


def _sheet(actions, verdict):
    """A graded practice sheet whose steps are ``actions``; ``verdict``
    None is a gap sheet (no execution)."""
    coverage = CoverageRecord(unit_id="u", attempt_index=0,
                              present=verdict is not None,
                              status="present" if verdict else "missing")
    if verdict is None:
        return AnswerSheet(sheet_id="gap", phase_label="p03:practice",
                           coverage=coverage, execution=None)
    execution = ExecutionRecord(
        sheet_id="s", unit_id="u", phase_label="p03:practice",
        task_ref="tb:t1", role="weak", arm="weak", model_role="weak",
        model_name="m", sample_index=0,
        steps=[Step(action=action, observation="") for action in actions],
        artifacts={"trajectory_id": "traj", "skill_folder": _FOLDER})
    return AnswerSheet(
        sheet_id="s", phase_label="p03:practice", coverage=coverage,
        execution=execution,
        grading=GradingRecord(verdict=verdict, basis="", rule_name="scorer"))


def test_card_use_metrics_count_valid_trials_per_card():
    read_first = "ReadSkill[type-001-skill/SKILL.md]"
    sheets = [
        _sheet([read_first, "go"], "success"),
        _sheet([read_first, "cat type-002-skill/SKILL.md"], "failure"),
        _sheet(["go"], "failure"),
        # Invalid and missing trials stay out of every count but trials.
        _sheet([read_first], "invalid"),
        _sheet([], None),
    ]

    metrics = card_use_metrics(sheets)

    assert metrics["trials"] == 4
    assert (metrics["valid"], metrics["success"]) == (3, 1)
    assert metrics["success_rate"] == pytest.approx(1 / 3)
    assert (metrics["opened_any"], metrics["opened_any_success"]) == (2, 1)
    assert metrics["cards"] == {
        "type/001/skill": {"opened": 2, "opened_success": 1},
        "type/002/skill": {"opened": 1, "opened_success": 0}}


def test_card_use_metrics_without_trials_report_none_rates():
    metrics = card_use_metrics([])
    assert metrics["success_rate"] is None
    assert metrics["cards"] == {}
