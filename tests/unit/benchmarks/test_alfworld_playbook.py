"""ALFWorld's learned-playbook facts: deterministic, train-only text."""

import pytest

from expedistill.benchmarks.alfworld.playbook import (
    PLAYBOOK_PROMPT_NAME, REQUIRED_LABELS, facts_from_sheets)
from expedistill.core.answer_sheet import (
    AnswerSheet, CoverageRecord, ExecutionRecord, GradingRecord)
from expedistill.core.models import Step
from expedistill.prompts import get as get_prompt


def _sheet(kind, role, attempt, actions, verdict, task="Put a mug in desk"):
    task_ref = ("alfworld:json_2.1.1/train/"
                f"{kind}-Mug-None-Desk-1/trial_T1/game.tw-pddl")
    sheet_id = f"{task_ref}:{role}:{attempt}"
    execution = ExecutionRecord(
        sheet_id=sheet_id, unit_id=f"u:{role}", phase_label="p01:baseline",
        task_ref=task_ref, role=role, arm=role, model_role=role,
        model_name="m", sample_index=attempt,
        steps=[Step(action=a, observation="o") for a in actions],
        metrics={"initial_observation": f"Your task is to: {task}."})
    return AnswerSheet(
        sheet_id=sheet_id, phase_label="p01:baseline",
        coverage=CoverageRecord(unit_id="u", attempt_index=attempt,
                                present=True, status="present"),
        execution=execution,
        grading=GradingRecord(verdict=verdict, basis="b", rule_name="scorer"))


LOOP = ["go to cabinet 1", "go to fridge 1", "go to cabinet 1"] * 8


def test_facts_describe_the_loop_of_a_failing_trial():
    sheets = [
        _sheet("pick_cool_then_place_in_recep", "weak", 0, LOOP, "failure"),
        _sheet("pick_cool_then_place_in_recep", "strong", 0,
               ["go to fridge 1", "take mug 1", "cool mug 1 with fridge 1",
                "put mug 1 in/on desk 1"], "success"),
    ]
    text = facts_from_sheets(sheets)
    assert "[pick_cool_then_place_in_recep] 2 trials" in text
    assert "weak: 0 success / 1 failure of 1" in text
    assert "strong: 1 success / 0 failure of 1" in text
    assert "median distinct places 2" in text
    assert "1 of 1 never took, used or put anything" in text
    assert "most repeated single go-to: 'go to cabinet 1' x16" in text
    assert "example success (Put a mug in desk.)" in text
    assert "cool mug 1 with fridge 1" in text


def test_facts_do_not_depend_on_input_order():
    sheets = [
        _sheet("pick_and_place_simple", "weak", 0, ["go to a 1"], "failure"),
        _sheet("pick_and_place_simple", "weak", 1,
               ["go to b 1", "take x 1"], "success"),
        _sheet("look_at_obj_in_light", "strong", 0, ["look"], "success"),
    ]
    assert facts_from_sheets(sheets) == facts_from_sheets(reversed(sheets))


def test_unscored_and_missing_trials_are_skipped():
    scored = _sheet("pick_and_place_simple", "weak", 0, ["a"], "success")
    no_grade = _sheet("pick_and_place_simple", "weak", 1, ["a"], "success")
    no_grade.grading = None
    missing = _sheet("pick_and_place_simple", "weak", 2, ["a"], "success")
    missing.execution = None
    text = facts_from_sheets([scored, no_grade, missing])
    assert "1 trials" in text
    assert facts_from_sheets([no_grade, missing]) \
        == "(no scored baseline trials)"


# -- the dimensions added for the extraction gap (2026-11-05) -----------------

def _trial(actions, verdict, task, role="weak", attempt=0):
    return _sheet("pick_heat_then_place_in_recep", role, attempt, actions,
                  verdict, task=task)


def test_the_article_never_eats_the_first_letter_of_the_object():
    sheets = [_sheet("look_at_obj_in_light", "weak", 0,
                     ["look"], "failure",
                     task="look at alarmclock under the desklamp")]
    text = facts_from_sheets(sheets)
    assert "->" not in text            # no bogus "larmclock" confusion pair


# -- object identity, name families, destination instance (2026-11-06) --------

from expedistill.benchmarks.alfworld import playbook as _pb


def _scene_sheet(actions, verdict, task, observations, role="weak", attempt=0):
    """A sheet whose steps carry real observations, for scene-level facts."""
    sheet = _sheet("pick_heat_then_place_in_recep", role, attempt, actions,
                   verdict, task=task)
    sheet.execution.steps = [
        Step(action=a, observation=o) for a, o in zip(actions, observations)]
    return sheet


def test_name_families_find_related_names_in_one_scene_without_any_failure():
    obs = ["You arrive at countertop 1. On the countertop 1, you see a knife 1, "
           "a butterknife 2, and a saltshaker 1.",
           "You arrive at shelf 1. On the shelf 1, you see a peppershaker 1 "
           "and a saltshaker 2."]
    sheet = _scene_sheet(["go to countertop 1", "go to shelf 1"], "success",
                         "put a knife in drawer", obs)
    pairs = _pb._name_families([sheet])
    assert ("butterknife", "knife") in pairs
    assert ("peppershaker", "saltshaker") in pairs
    text = facts_from_sheets([sheet])
    assert "[object names that share a scene and share a word]" in text
    assert "butterknife/knife" in text


def test_names_that_merely_share_letters_are_not_a_family():
    obs = ["You arrive at desk 1. On the desk 1, you see a bowl 1 and a "
           "cellphone 1 and a keychain 1."]
    sheet = _scene_sheet(["go to desk 1"], "success", "put a bowl in desk", obs)
    assert _pb._name_families([sheet]) == []


def test_name_families_do_not_cross_scenes():
    a = _scene_sheet(["go to desk 1"], "success", "put a knife in desk",
                     ["On the desk 1, you see a knife 1."])
    b = _sheet("pick_heat_then_place_in_recep", "weak", 1, ["go to desk 1"],
               "success", task="put a knife in desk")
    b.execution.task_ref = "alfworld:json_2.1.1/train/other/trial_T2/game.tw-pddl"
    b.execution.steps = [Step(action="go to desk 1", observation=
                              "On the desk 1, you see a butterknife 1.")]
    assert _pb._name_families([a, b]) == []


def test_requirements_are_the_six_labelled_parts():
    from expedistill.benchmarks.alfworld.playbook import playbook_requirements
    reqs = playbook_requirements()
    assert [r.name for r in reqs] == [
        "format", "skills", "search order", "object identity",
        "delivering", "prohibitions"]
    text = "\n".join(f"{label}\nbody" for _, label, _ in REQUIRED_LABELS)
    assert [r.name for r in reqs if not r.matches(text)] == ["object identity"]


def test_the_identity_requirement_needs_both_directions():
    from expedistill.benchmarks.alfworld.playbook import playbook_requirements
    identity = [r for r in playbook_requirements()
                if r.name == "object identity"][0]
    base = "\n".join(f"{label}\nbody" for _, label, _ in REQUIRED_LABELS)
    one_way = base + "\nIf the task says pot, potato is not it."
    assert not identity.matches(one_way)
    both = one_way + "\nIf the task says potato, pot is not it."
    assert identity.matches(both)


def test_the_update_prompt_fills_the_method_prompts_slots():
    import string

    def slots(name):
        return {field for _, field, _, _ in
                string.Formatter().parse(get_prompt(name))
                if field is not None}

    assert slots(PLAYBOOK_PROMPT_NAME) == slots("PLAYBOOK_UPDATE")



def _trial(actions, verdict, task, role="weak", attempt=0,
           kind="pick_heat_then_place_in_recep"):
    return _sheet(kind, role, attempt, actions, verdict, task=task)


_VISITS = ["go to countertop 3", "go to countertop 1",
           "take pot 1 from countertop 1", "go to sinkbasin 1",
           "clean pot 1 with sinkbasin 1", "go to countertop 3",
           "move pot 1 to countertop 3", "go to countertop 3"]
_CLEAN = "pick_clean_then_place_in_recep"
_TWO = "pick_two_obj_and_place"
_CUP = "put a hot cup in cabinet"
_TWO_NEWS = "find two newspaper and put them in sofa"
_NEWS = ["go to coffeetable 1", "take newspaper 1 from coffeetable 1",
         "go to sofa 1", "move newspaper 1 to sofa 1"]


def _pot_trials(kind, verdict, count):
    return [_sheet(kind, "weak", i, _VISITS, verdict) for i in range(count)]


# Each case: the trials, then the fact lines the text must contain. One
# dimension per case, so a failure names the fact that broke.
@pytest.mark.parametrize("sheets, expected", [
    pytest.param(
        [_trial(["go to countertop 1", "take mug 3 from countertop 1",
                 "go to microwave 1", "heat mug 3 with microwave 1",
                 "go to cabinet 1", "move mug 3 to cabinet 1"],
                "failure", "put a hot cup in cabinet"),
         _trial(["go to countertop 1", "take cup 1 from countertop 1",
                 "go to microwave 1", "heat cup 1 with microwave 1",
                 "go to cabinet 1", "move cup 1 to cabinet 1"],
                "success", "put a hot cup in cabinet", role="strong")],
        ["object identity: 1 of 1 failing trials handled an object whose "
         "name is not the task's (most common: cup->mug x1)"],
        id="object_identity_confusion"),
    pytest.param(
        # move the object in, take it back out, move it in again.
        [_trial(["take cup 1 from countertop 1", "move cup 1 to cabinet 1",
                 "take cup 1 from cabinet 1", "heat cup 1 with microwave 1",
                 "move cup 1 to cabinet 1"], "failure", _CUP)],
        ["re-place loops: 1 of 1 failing trials"], id="re_place_loop"),
    pytest.param(
        [_trial(["go to fridge 1", "open fridge 1",
                 "take cup 1 from fridge 1", "go to microwave 1",
                 "heat cup 1 with microwave 1", "move cup 1 to cabinet 1"],
                "success", _CUP, role="strong"),
         _trial(["take cup 2 from countertop 1", "go to microwave 1",
                 "heat cup 2 with microwave 1", "move cup 2 to cabinet 1"],
                "success", _CUP, role="strong", attempt=1)],
        ["where successful trials first took the object: "
         "countertop 1, fridge 1"], id="success_sources"),
    pytest.param(
        # The destination is one instance; the source is often another
        # instance of the same kind, so a kind-level filter would delete the
        # only useful prior for those tasks.
        [_sheet(_CLEAN, "strong", 0,
                ["go to countertop 3", "take bread 1 from countertop 3",
                 "go to fridge 1", "cool bread 1 with fridge 1",
                 "go to countertop 1", "move bread 1 to countertop 1"],
                "success", task="put a cool bread in countertop")],
        ["where successful trials first took the object: countertop 1"],
        id="take_at_destination_kind_stays_a_source"),
    pytest.param(
        # inventory, look, go to -> three actions between heat and move
        [_trial(["take cup 1 from countertop 1",
                 "heat cup 1 with microwave 1", "inventory", "look",
                 "go to cabinet 1", "move cup 1 to cabinet 1"], "success",
                _CUP, role="strong")],
        ["between the transform and the first delivery", "median 3"],
        id="actions_after_the_transform"),
    pytest.param(
        [_sheet("look_at_obj_in_light", "weak", 0,
                ["take cd 1 from desk 1", "move cd 1 to desk 1",
                 "examine cd 1"], "failure",
                task="examine the cd with the desklamp")],
        ["look tasks: 1 of 1 failing trials never used a lamp",
         "1 moved the object to a receptacle"], id="look_task_handling"),
    pytest.param(
        [_sheet(_CLEAN, "weak", 0,
                ["go to cabinet 1", "open cabinet 1", "go to fridge 1",
                 "go to cabinet 1"] * 4,
                "failure", task="clean a cup and put it in shelf")],
        ["of all go-to actions in failing trials returned to a place",
         "gap between two visits of the same place",
         "last two interactions"], id="search_memory_gap"),
    pytest.param(
        [_sheet(_TWO, "strong", 0,
                _NEWS + ["take newspaper 2 from coffeetable 1",
                         "move newspaper 2 to sofa 1"],
                "success", task=_TWO_NEWS),
         _sheet(_TWO, "strong", 1,
                _NEWS + ["take newspaper 2 from shelf 1",
                         "move newspaper 2 to sofa 1"],
                "success", task=_TWO_NEWS)],
        ["second object: 1 of 2 successful two-object trials took the "
         "second object from the same place kind as the first"],
        id="second_object_share"),
    pytest.param(
        [_trial(["open cabinet 1", "go to cabinet 2", "open cabinet 2",
                 "go to cabinet 1", "open cabinet 1"], "failure", _CUP)],
        ["repeated opening: 1 of 1 failing trials opened the same "
         "container more than once"], id="repeated_container_opening"),
    pytest.param(
        [_trial(["take mug 3 from countertop 1", "heat mug 3 with microwave 1",
                 "move mug 3 to cabinet 1"], "failure", _CUP),
         _trial(["take cup 2 from countertop 3", "heat cup 2 with microwave 1",
                 "move cup 2 to cabinet 1"], "success", _CUP, attempt=1)],
        ["carried the task's own noun: 1 success / 0 failure",
         "carried a different noun: 0 success / 1 failure"],
        id="identity_outcome_by_noun"),
    pytest.param(
        # the LAST delivery is what counts: this trial is a success of the
        # own noun
        [_trial(["take mug 3 from countertop 1", "move mug 3 to cabinet 1",
                 "take mug 3 from cabinet 1", "take cup 2 from countertop 3",
                 "move cup 2 to cabinet 1"], "success", _CUP)],
        ["1 trial(s) first delivered a different noun and then delivered "
         "the right one",
         "carried the task's own noun: 1 success / 0 failure"],
        id="corrected_wrong_delivery"),
    pytest.param(
        # Delivering to cabinet 1 after visiting cabinets 2 and 3 empty-handed
        # (other instances of the destination's kind) is not a visit; going
        # to cabinet 1 itself twice is.
        [_trial(["go to cabinet 2", "go to cabinet 3", "go to cabinet 2",
                 "take cup 1 from cabinet 3", "move cup 1 to cabinet 1"],
                "success", _CUP),
         _trial(["go to cabinet 1", "go to shelf 1", "go to cabinet 1",
                 "take cup 1 from shelf 1", "move cup 1 to cabinet 1"],
                "failure", _CUP, attempt=1)],
        ["two or more times: 0 of 1 successful trials, 1 of 1 failing "
         "trials"], id="destination_instance_not_kind"),
    pytest.param(
        # Equal rates (9/9 and 15/15 visited the destination empty-handed):
        # the fact states the verdict so the writer never compares the two
        # fractions itself.
        _pot_trials(_CLEAN, "success", 9) + _pot_trials(_CLEAN, "failure", 15),
        ["failing rate vs successful rate: similar or lower"],
        id="destination_verdict_similar"),
    pytest.param(
        _pot_trials(_TWO, "success", 20) + _pot_trials(_TWO, "failure", 2),
        ["too few trials on one side to support any rule"],
        id="destination_verdict_too_few"),
])
def test_facts_report_each_dimension(sheets, expected):
    text = facts_from_sheets(sheets)
    for fragment in expected:
        assert fragment in text
