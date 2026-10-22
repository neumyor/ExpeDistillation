"""The live folder usage ledger: read facts and delivery status."""

from expedistill.composites.skill_usage import build_usage_rows
from expedistill.core.ids import (
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill, Step, Trajectory
from expedistill.methods.delivery.folder import skill_folder_manifest

BENCHMARK = "tb"
TASK_ID = make_task_id(BENCHMARK, "t1")

MARKED = "\n".join([
    "[When to use] The service rejects connections.",
    "[Steps]",
    "1. Check the service status.",
    "[Verify] The status command reports running.",
    "[Do not use when] The status command already reports running.",
])


def _skill(segment):
    subtask_id = make_subtask_id(
        make_trajectory_id(TASK_ID, "strong", 0), segment)
    return Skill(skill_id=make_skill_id(subtask_id), content=MARKED,
                 summary=f"Skill {segment}.", subtask_id=subtask_id,
                 source_subtasks=[subtask_id], status="pending")


def _manifest():
    return skill_folder_manifest([_skill(0), _skill(1)],
                                 "when_to_use", accepted_only=False)


def _trajectory(prompt, actions, correctness=False):
    return Trajectory(
        trajectory_id=make_trajectory_id(TASK_ID, "weak", 0),
        task_id=TASK_ID, benchmark=BENCHMARK, model_role="weak",
        model_name="weak-model", attempt_index=0,
        steps=[Step(action=prompt, observation="")] + [
            Step(action=action, observation="")
            for action in actions],
        correctness=correctness,
    )


def _prompt(names):
    skills = "".join(
        f"<skill><name>{name}</name></skill>" for name in names)
    return f"TASK\n<available_skills>{skills}</available_skills>"


def test_a_cat_of_the_absolute_path_counts_as_a_read():
    manifest = _manifest()
    first = manifest["files"][0]
    trajectory = _trajectory(
        _prompt([entry["filename"].split("/")[0]
                 for entry in manifest["files"]]),
        [f"cat /opt/openclaw-skills/t1/{first['filename']}\n",
         "echo done\n"])

    rows = build_usage_rows(task_id=TASK_ID, round_=0,
                            trajectories=[trajectory], folder=manifest)

    assert len(rows) == 1
    row = rows[0]
    assert row["delivery_status"] == "ok"
    assert row["read_skill_ids"] == [first["skill_id"]]
    assert first["skill_id"] not in row["unread_skill_ids"]
    assert row["first_read_step"] == 1
    assert row["outcome"] == "failure"
    assert row["available_skill_ids"] == [
        entry["skill_id"] for entry in manifest["files"]]
    assert row["available_names"]


def test_a_missing_list_with_no_read_is_unverified():
    manifest = _manifest()
    trajectory = _trajectory("TASK without a list", ["echo hi\n"])
    row = build_usage_rows(task_id=TASK_ID, round_=0,
                           trajectories=[trajectory], folder=manifest)[0]
    assert row["delivery_status"] == "unverified"
    assert row["read_skill_ids"] == []


def test_a_missing_prompt_with_a_read_counts_as_delivered():
    manifest = _manifest()
    first = manifest["files"][0]
    trajectory = _trajectory(
        "TASK without a stored prompt",
        [f"cat /opt/openclaw-skills/t1/{first['filename']}\n"])
    row = build_usage_rows(task_id=TASK_ID, round_=0,
                           trajectories=[trajectory], folder=manifest)[0]
    assert row["delivery_status"] == "ok"
    assert row["read_skill_ids"] == [first["skill_id"]]


def test_a_list_with_the_wrong_names_is_a_manifest_mismatch():
    manifest = _manifest()
    trajectory = _trajectory(_prompt(["wrong-name"]), ["echo hi\n"])
    row = build_usage_rows(task_id=TASK_ID, round_=0,
                           trajectories=[trajectory], folder=manifest)[0]
    assert row["delivery_status"] == "manifest_mismatch"
