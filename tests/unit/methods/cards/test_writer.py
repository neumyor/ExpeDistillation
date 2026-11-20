"""G3 (methods.cards): a type's material and the card written from it."""

import pytest

from expedistill.core.ids import make_subtask_id, make_trajectory_id
from expedistill.core.models import Step, Subtask, SubtaskType, Trajectory
from expedistill.infra.reliability import CodeBugError, ModelOutputError
from tests.helpers.fake_llm import json_script, make_fake_handle
from expedistill.methods.cards import CardAuthoringParams
from expedistill.methods.cards.material import (
    Material,
    collect_material,
    prefix_material,
)
from expedistill.methods.cards.writer import (
    MAX_STEPS,
    MIN_STEPS,
    write_card,
    write_card_prefix,
)

LEARN = "p04:learn"
TYPE = SubtaskType(type_id="type/002", name="heat the object",
                   entry_state="The object is held and must be hot.",
                   phase_label="p02:learn")


def _trajectory(task, role, phase):
    return Trajectory(
        trajectory_id=make_trajectory_id(phase, task, role, 0),
        task_id=task, benchmark="tb", model_role=role, model_name="m",
        attempt_index=0, correctness=role == "strong",
        steps=[Step(action=f"{task}-{role}-a{i}", observation="o")
               for i in range(3)])


def _segment(trajectory, index, *, success, phase):
    return Subtask(
        subtask_id=make_subtask_id(trajectory.trajectory_id, index),
        phase_label=phase, task_id=trajectory.task_id,
        trajectory_id=trajectory.trajectory_id,
        source=trajectory.model_role, trajectory_success=success,
        segment_index=index, step_range=(index, index + 1),
        label="heat", description="heat it", success=success)


@pytest.fixture()
def world():
    trajectories = {}
    subtasks = {}
    for task in ("tb:t1", "tb:t2", "tb:t3"):
        strong = _trajectory(task, "strong", "p01:baseline")
        trajectories[strong.trajectory_id] = strong
        for index, success in ((0, True), (1, False), (2, True)):
            sub = _segment(strong, index, success=success, phase="p02:learn")
            subtasks[sub.subtask_id] = sub
    for task, phase in (("tb:t1", "p03:practice"), ("tb:t2", "p01:baseline")):
        weak = _trajectory(task, "weak", phase)
        trajectories[weak.trajectory_id] = weak
        for index in (0, 1):
            sub = _segment(weak, index, success=False,
                           phase=LEARN if phase == "p03:practice"
                           else "p02:learn")
            subtasks[sub.subtask_id] = sub
    assignments = {subtask_id: "type/002" for subtask_id in subtasks}
    return trajectories, subtasks, assignments


def _material(world, **overrides):
    trajectories, subtasks, assignments = world
    arguments = dict(type_id="type/002", subtasks=subtasks,
                     assignments=assignments,
                     rule_name="strong_success_weak_failed",
                     phase_label=LEARN, write_lessons=True,
                     gap_tasks={"tb:t3"}, error_segments=set(),
                     max_segments=8, load_trajectory=trajectories.__getitem__)
    arguments.update(overrides)
    return collect_material(**arguments)


def _ids(experiences):
    return [item["subtask"].subtask_id for item in experiences]


def test_strong_material_puts_the_gap_tasks_first(world):
    material = _material(world)
    strong = _ids(material.strong)
    assert len(strong) == 6   # the rule keeps strong successes only
    assert all("tb:t3" in subtask_id for subtask_id in strong[:2])
    assert strong[2:] == sorted(strong[2:])
    assert "step 2" in material.strong[1]["steps_view"]


def test_strong_material_is_capped(world):
    assert len(_material(world, max_segments=3).strong) == 3


def test_weak_material_is_this_phases_failed_segments(world):
    material = _material(world, error_segments={
        make_subtask_id(make_trajectory_id("p03:practice", "tb:t1", "weak",
                                           0), 1)})
    weak = _ids(material.weak)
    assert len(weak) == 2 and all("p03_practice" in item for item in weak)
    assert weak[0].endswith("seg01")


def test_without_lessons_there_is_no_weak_material(world):
    assert _material(world, write_lessons=False).weak == []


def test_another_type_has_no_material(world):
    material = _material(world, type_id="type/009")
    assert material == Material()


def _card(steps=4, **fields):
    payload = {"summary": "Heat the held object.",
               "when_to_use": "The object is held and must be hot.",
               "steps": [f"Step {index}." for index in range(steps)],
               "not_applicable_when": "The object is already hot."}
    payload.update(fields)
    return json_script(payload)


def test_a_card_is_written_for_the_type(world):
    material = _material(world)
    handle = make_fake_handle(script=[_card()])

    skill = write_card(TYPE, material, profile="", playbook="THE PLAYBOOK",
                       handle=handle, params=CardAuthoringParams())

    assert (skill.skill_id, skill.subtask_type, skill.version,
            skill.status) == ("type/002/skill", "type/002", 1, "active")
    assert skill.source_subtasks == sorted(_ids(material.strong)
                                           + _ids(material.weak))
    prompt = handle.client.calls[0]["user"]
    for text in ("heat the object", "The object is held and must be hot.",
                 "THE PLAYBOOK", "WHERE THE WEAK MODEL FAILED"):
        assert text in prompt


@pytest.mark.parametrize("steps", [MIN_STEPS - 1, MAX_STEPS + 1])
def test_a_card_outside_the_step_bounds_is_rewritten(world, steps):
    handle = make_fake_handle(script=[_card(steps=steps), _card()])

    skill = write_card(TYPE, _material(world), profile="", playbook="",
                       handle=handle, params=CardAuthoringParams())

    assert len(skill.steps) == 4
    assert "rejected" in handle.client.calls[1]["user"]


def test_the_rewrites_are_bounded(world):
    handle = make_fake_handle(script=[_card(summary="")] * 2)
    with pytest.raises(ModelOutputError, match="after 1 rewrite"):
        write_card(TYPE, _material(world), profile="", playbook="",
                   handle=handle,
                   params=CardAuthoringParams(write_retry_limit=1))
    assert len(handle.client.calls) == 2


def test_writing_without_strong_material_is_a_bug():
    with pytest.raises(CodeBugError):
        write_card(TYPE, Material(), profile="", playbook="",
                   handle=make_fake_handle(), params=CardAuthoringParams())


# -- prefix authoring --------------------------------------------------------------

def _prefix(world, material, before_steps=15):
    trajectories, _, _ = world
    return prefix_material(material, before_steps=before_steps,
                           load_trajectory=trajectories.__getitem__)


def test_the_prefix_view_is_the_steps_before_each_segment(world):
    material = _material(world, max_segments=8)
    prefix = _prefix(world, material)
    by_segment = {item["subtask"].subtask_id: item["steps_view"]
                  for item in prefix.strong}
    # The three segments at step 0 have no prefix and are left out; the
    # three at step 2 see steps 0 and 1.
    assert len(_material(world, max_segments=8).strong) == 6
    assert len(by_segment) == 3 and all(
        item["subtask"].step_range[0] == 2 for item in prefix.strong)
    two = next(view for key, view in by_segment.items()
               if key.endswith("seg02") and "tb:t3" in key)
    assert "step 0" in two and "step 1" in two and "step 2" not in two
    assert "a2" not in two


def test_the_prefix_view_keeps_at_most_the_asked_steps_before(world):
    material = _material(world)
    prefix = _prefix(world, material, before_steps=1)
    two = next(item["steps_view"] for item in prefix.strong
               if item["subtask"].step_range[0] == 2)
    assert two.startswith("step 1") and "step 0" not in two


def test_a_material_whose_segments_all_start_at_zero_has_no_prefix(world):
    trajectories, subtasks, _ = world
    first = Material(strong=[
        {"subtask": sub, "steps_view": "x"} for sub in subtasks.values()
        if sub.step_range[0] == 0 and sub.source == "strong"])
    assert first.strong
    assert prefix_material(first, before_steps=15,
                           load_trajectory=trajectories.__getitem__) \
        == Material()


def _tagged(kind):
    return json_script({
        "summary": f"{kind} summary.", "when_to_use": f"{kind} when.",
        "steps": [f"{kind} step {index}." for index in range(3)],
        "not_applicable_when": f"{kind} not applicable."})


def test_the_prefix_card_takes_its_body_from_the_first_call_and_its_trigger_from_the_second(
        world):
    material = _material(world)
    prefix = _prefix(world, material)
    handle = make_fake_handle(script=[_tagged("full"), _tagged("prefix")])

    skill, from_prefix = write_card_prefix(
        TYPE, material, prefix, profile="", playbook="",
        handle=handle, params=CardAuthoringParams())

    assert from_prefix is True
    assert (skill.summary, skill.when_to_use) == (
        "prefix summary.", "prefix when.")
    assert skill.steps == ["full step 0.", "full step 1.", "full step 2."]
    assert skill.not_applicable_when == "full not applicable."
    assert skill.source_subtasks == sorted(_ids(material.strong)
                                           + _ids(material.weak))
    first, second = (call["user"] for call in handle.client.calls)
    assert "a2" in first or "a1" in first
    assert "a2" not in second
    assert second.count("SUBTASK_ID:") < first.count("SUBTASK_ID:")


def test_without_a_usable_prefix_the_first_call_alone_writes_the_card(world):
    material = _material(world)
    handle = make_fake_handle(script=[_tagged("full")])

    skill, from_prefix = write_card_prefix(
        TYPE, material, Material(), profile="", playbook="",
        handle=handle, params=CardAuthoringParams())

    assert from_prefix is False
    assert len(handle.client.calls) == 1
    assert (skill.summary, skill.when_to_use) == (
        "full summary.", "full when.")


def test_an_unusable_second_reply_is_rewritten_like_the_first(world):
    material = _material(world)
    handle = make_fake_handle(script=[
        _tagged("full"), _card(steps=1), _tagged("prefix")])

    skill, _ = write_card_prefix(
        TYPE, material, _prefix(world, material), profile="", playbook="",
        handle=handle, params=CardAuthoringParams())

    assert skill.summary == "prefix summary."
    assert "rejected" in handle.client.calls[2]["user"]


def test_a_prefix_card_without_strong_material_is_a_bug():
    with pytest.raises(CodeBugError):
        write_card_prefix(TYPE, Material(), Material(), profile="",
                          playbook="", handle=make_fake_handle(),
                          params=CardAuthoringParams())
