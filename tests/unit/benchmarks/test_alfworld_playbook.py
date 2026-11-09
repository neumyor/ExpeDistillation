"""ALFWorld's learned-playbook facts: deterministic, train-only text."""

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


def test_the_prompt_is_registered_and_names_its_slots():
    template = get_prompt(PLAYBOOK_PROMPT_NAME)
    for slot in ("{facts}", "{profile_section}", "{agent_kind}",
                 "{benchmark_name}", "{correction_note}"):
        assert slot in template


# -- the dimensions added for the extraction gap (2026-11-05) -----------------

def _trial(actions, verdict, task, role="weak", attempt=0):
    return _sheet("pick_heat_then_place_in_recep", role, attempt, actions,
                  verdict, task=task)


def test_object_identity_counts_the_confusion_that_happened():
    sheets = [_trial(["go to countertop 1", "take mug 3 from countertop 1",
                      "go to microwave 1", "heat mug 3 with microwave 1",
                      "go to cabinet 1", "move mug 3 to cabinet 1"],
                     "failure", "put a hot cup in cabinet"),
              _trial(["go to countertop 1", "take cup 1 from countertop 1",
                      "go to microwave 1", "heat cup 1 with microwave 1",
                      "go to cabinet 1", "move cup 1 to cabinet 1"],
                     "success", "put a hot cup in cabinet", role="strong")]
    text = facts_from_sheets(sheets)
    assert "object identity: 1 of 1 failing trials handled an object whose " \
           "name is not the task's (most common: cup->mug x1)" in text


def test_re_place_loop_is_counted():
    # move the object in, take it back out, move it in again.
    sheets = [_trial(["take cup 1 from countertop 1", "move cup 1 to cabinet 1",
                      "take cup 1 from cabinet 1", "heat cup 1 with microwave 1",
                      "move cup 1 to cabinet 1"], "failure",
                     "put a hot cup in cabinet")]
    assert "re-place loops: 1 of 1 failing trials" in facts_from_sheets(sheets)


def test_success_sources_are_the_place_kinds_of_the_first_take():
    sheets = [_trial(["go to fridge 1", "open fridge 1",
                      "take cup 1 from fridge 1", "go to microwave 1",
                      "heat cup 1 with microwave 1", "move cup 1 to cabinet 1"],
                     "success", "put a hot cup in cabinet", role="strong"),
              _trial(["take cup 2 from countertop 1", "go to microwave 1",
                      "heat cup 2 with microwave 1", "move cup 2 to cabinet 1"],
                     "success", "put a hot cup in cabinet", role="strong",
                     attempt=1)]
    text = facts_from_sheets(sheets)
    assert "where successful trials first took the object: " \
           "countertop 1, fridge 1" in text


def test_actions_after_the_transform_are_counted():
    sheets = [_trial(["take cup 1 from countertop 1", "heat cup 1 with microwave 1",
                      "inventory", "look", "go to cabinet 1",
                      "move cup 1 to cabinet 1"], "success",
                     "put a hot cup in cabinet", role="strong")]
    assert "between the transform and the first delivery" in \
        facts_from_sheets(sheets)
    # inventory, look, go to -> three actions between heat and move
    assert "median 3" in facts_from_sheets(sheets)


def test_look_task_handling_is_reported():
    sheets = [_sheet("look_at_obj_in_light", "weak", 0,
                     ["take cd 1 from desk 1", "move cd 1 to desk 1",
                      "examine cd 1"], "failure", task="examine the cd with the desklamp")]
    text = facts_from_sheets(sheets)
    assert "look tasks: 1 of 1 failing trials never used a lamp" in text
    assert "1 moved the object to a receptacle" in text


def test_search_behaviour_reports_the_memory_gap():
    loop = (["go to cabinet 1", "open cabinet 1", "go to fridge 1",
             "go to cabinet 1"] * 4)
    sheets = [_sheet("pick_clean_then_place_in_recep", "weak", 0, loop,
                     "failure", task="clean a cup and put it in shelf")]
    text = facts_from_sheets(sheets)
    assert "of all go-to actions in failing trials returned to a place" in text
    assert "gap between two visits of the same place" in text
    assert "last two interactions" in text


def test_the_article_never_eats_the_first_letter_of_the_object():
    sheets = [_sheet("look_at_obj_in_light", "weak", 0,
                     ["look"], "failure",
                     task="look at alarmclock under the desklamp")]
    text = facts_from_sheets(sheets)
    assert "->" not in text            # no bogus "larmclock" confusion pair


def test_second_object_share_is_reported_for_two_object_tasks():
    base = ["go to coffeetable 1", "take newspaper 1 from coffeetable 1",
            "go to sofa 1", "move newspaper 1 to sofa 1"]
    task = "find two newspaper and put them in sofa"
    same = _sheet("pick_two_obj_and_place", "strong", 0,
                  base + ["take newspaper 2 from coffeetable 1",
                          "move newspaper 2 to sofa 1"], "success", task=task)
    other = _sheet("pick_two_obj_and_place", "strong", 1,
                   base + ["take newspaper 2 from shelf 1",
                           "move newspaper 2 to sofa 1"], "success", task=task)
    text = facts_from_sheets([same, other])
    assert "second object: 1 of 2 successful two-object trials took the " \
           "second object from the same place kind as the first" in text


def test_repeated_container_opening_is_counted():
    sheet = _trial(["open cabinet 1", "go to cabinet 2", "open cabinet 2",
                    "go to cabinet 1", "open cabinet 1"], "failure",
                   "put a hot cup in cabinet")
    assert "repeated opening: 1 of 1 failing trials opened the same " \
           "container more than once" in facts_from_sheets([sheet])


def test_a_take_at_the_destination_kind_stays_in_the_sources():
    # The destination is one instance; the source is often another instance
    # of the same kind (5 of 9 successful cool trials). A kind-level filter
    # would delete the only useful prior for those tasks.
    task = "put a cool bread in countertop"
    sheet = _sheet("pick_cool_then_place_in_recep", "strong", 0,
                   ["go to countertop 3", "take bread 1 from countertop 3",
                    "go to fridge 1", "cool bread 1 with fridge 1",
                    "go to countertop 1", "move bread 1 to countertop 1"],
                   "success", task=task)
    assert "where successful trials first took the object: countertop 1" \
        in facts_from_sheets([sheet])


# -- object identity, name families, destination instance (2026-11-06) --------

from expedistill.benchmarks.alfworld import playbook as _pb


def _scene_sheet(actions, verdict, task, observations, role="weak", attempt=0):
    """A sheet whose steps carry real observations, for scene-level facts."""
    sheet = _sheet("pick_heat_then_place_in_recep", role, attempt, actions,
                   verdict, task=task)
    sheet.execution.steps = [
        Step(action=a, observation=o) for a, o in zip(actions, observations)]
    return sheet


def test_identity_outcome_separates_own_noun_from_a_different_noun():
    task = "put a hot cup in cabinet"
    wrong = _sheet("pick_heat_then_place_in_recep", "weak", 0,
                   ["take mug 3 from countertop 1", "heat mug 3 with microwave 1",
                    "move mug 3 to cabinet 1"], "failure", task=task)
    right = _sheet("pick_heat_then_place_in_recep", "weak", 1,
                   ["take cup 2 from countertop 3", "heat cup 2 with microwave 1",
                    "move cup 2 to cabinet 1"], "success", task=task)
    text = facts_from_sheets([wrong, right])
    assert "carried the task's own noun: 1 success / 0 failure" in text
    assert "carried a different noun: 0 success / 1 failure" in text


def test_a_wrong_delivery_that_was_corrected_is_counted_as_a_correction():
    task = "put a hot cup in cabinet"
    fixed = _sheet("pick_heat_then_place_in_recep", "weak", 0,
                   ["take mug 3 from countertop 1", "move mug 3 to cabinet 1",
                    "take mug 3 from cabinet 1", "take cup 2 from countertop 3",
                    "move cup 2 to cabinet 1"], "success", task=task)
    text = facts_from_sheets([fixed])
    assert "1 trial(s) first delivered a different noun and then delivered " \
           "the right one" in text
    # the LAST delivery is what counts: this trial is a success of the own noun
    assert "carried the task's own noun: 1 success / 0 failure" in text


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


def test_destination_instance_is_counted_per_numbered_place_not_per_kind():
    task = "put a hot cup in cabinet"
    # goes to cabinet 2 and cabinet 3 empty-handed (other instances of the
    # destination's kind), delivers to cabinet 1: must NOT count as a visit.
    kind_only = _sheet(
        "pick_heat_then_place_in_recep", "weak", 0,
        ["go to cabinet 2", "go to cabinet 3", "go to cabinet 2",
         "take cup 1 from cabinet 3", "move cup 1 to cabinet 1"],
        "success", task=task)
    # goes to cabinet 1 twice empty-handed and then delivers there.
    instance = _sheet(
        "pick_heat_then_place_in_recep", "weak", 1,
        ["go to cabinet 1", "go to shelf 1", "go to cabinet 1",
         "take cup 1 from shelf 1", "move cup 1 to cabinet 1"],
        "failure", task=task)
    text = facts_from_sheets([kind_only, instance])
    assert "two or more times: 0 of 1 successful trials, 1 of 1 failing " \
           "trials" in text


def test_the_prompt_asks_for_the_conditional_destination_rule():
    template = get_prompt(PLAYBOOK_PROMPT_NAME)
    assert "applies to the exact numbered place, never to" in template
    # The writer acts on the fact's computed verdict instead of comparing
    # the two rates itself -- comparing them in prose is what produced a
    # spurious exclusion on near-equal rates.
    assert 'verdict is "clearly higher"' in template
    assert 'on "similar or lower" or "too few trials", write no' in \
        " ".join(template.split())
    assert "do not invent a task type to mark" in template


def test_requirements_are_the_six_labelled_parts():
    from expedistill.benchmarks.alfworld.playbook import playbook_requirements
    reqs = playbook_requirements()
    assert [r.name for r in reqs] == [
        "format", "recipes", "search order", "object identity",
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


def test_the_prompt_asks_for_an_instance_sweep_not_alternation():
    # V7: the three-arm comparison showed the v6 playbook's "first listed
    # place + kind alternation" degenerating into a ping-pong between
    # countertop 1 and a cabinet, never reaching countertop 3 where the
    # object was. The prompt now asks for a numbered sweep per kind and
    # class exhaustion, and no longer asks for alternation.
    template = " ".join(get_prompt(PLAYBOOK_PROMPT_NAME).split())
    assert "instance sweep within a kind" in template
    assert "class exhaustion" in template
    assert "Do not alternate between kinds" in template
    assert "kind alternation" not in template.lower()
    assert "keeps naming every place for the whole episode" in template


def test_every_recipe_must_open_a_closed_container():
    template = " ".join(get_prompt(PLAYBOOK_PROMPT_NAME).split())
    assert "arriving at a closed container means opening it" in template
    assert "in the recipe itself and not only in the search-order part" in template


VISITS = ["go to countertop 3", "go to countertop 1",
          "take pot 1 from countertop 1", "go to sinkbasin 1",
          "clean pot 1 with sinkbasin 1", "go to countertop 3",
          "move pot 1 to countertop 3", "go to countertop 3"]


def _trials(kind, verdict, count, offset=0):
    return [_sheet(kind, "weak", offset + i, VISITS, verdict)
            for i in range(count)]


def test_destination_fact_carries_a_computed_verdict():
    # Equal rates: 9/9 successful and 15/15 failing trials visited the
    # destination empty-handed. The fact must say so instead of leaving the
    # writer to compare the two fractions itself.
    text = facts_from_sheets(
        _trials("pick_clean_then_place_in_recep", "success", 9)
        + _trials("pick_clean_then_place_in_recep", "failure", 15))
    assert "failing rate vs successful rate: similar or lower" in text


def test_destination_verdict_reports_too_few_trials():
    text = facts_from_sheets(
        _trials("pick_two_obj_and_place", "success", 20)
        + _trials("pick_two_obj_and_place", "failure", 2))
    assert "too few trials on one side to support any rule" in text
