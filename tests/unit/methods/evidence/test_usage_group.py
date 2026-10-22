"""The usage-aware group evidence driver: read facts become packages."""

from expedistill.composites import live_measurement as lm
from expedistill.composites.skill_usage import SKILL_USAGE_NAME
from expedistill.core.contracts import TaskListRow
from expedistill.core.ids import (
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill, Step, Trajectory
from expedistill.infra.store import Store, write_jsonl
from expedistill.llm import json_script, make_fake_handle
from expedistill.methods.evidence import UsageGroupParams, usage_group
from expedistill.methods.evidence.package import ensure_fact_only

BENCHMARK = "tb"
TASK_ID = make_task_id(BENCHMARK, "t1")
N_ATTEMPTS = 2
WEAK_PROFILE = "A small model."
TASK_INSTRUCTION = "Fix the stuck service."

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


def _traj(attempt):
    return Trajectory(
        trajectory_id=make_trajectory_id(TASK_ID, "weak", attempt),
        task_id=TASK_ID, benchmark=BENCHMARK, model_role="weak",
        model_name="weak-model", attempt_index=attempt,
        steps=[Step(action=f"step {attempt}", observation="")],
        correctness=False,
    )


def _seeded(tmp_path):
    store = Store(tmp_path / "run")
    for attempt in range(N_ATTEMPTS):
        store.save(_traj(attempt), "trajectories")
    round_path = lm.round_dir(store, 0)
    round_store = Store(round_path)
    for attempt in range(N_ATTEMPTS):
        round_store.save(_traj(attempt), "trajectories")
    skills = [_skill(segment) for segment in range(3)]
    for skill in skills:
        store.save(skill, "skills")
    write_jsonl(round_path / SKILL_USAGE_NAME, [{
        "schema": "skill-usage-v1",
        "task_id": TASK_ID,
        "round": 0,
        "attempt": 0,
        "trajectory_id": _traj(0).trajectory_id,
        "delivery": "folder",
        "description_mode": "when_to_use",
        "available_list_present": True,
        "available_names": [],
        "available_skill_ids": [skill.skill_id for skill in skills],
        "delivery_status": "ok",
        "reads": [{"skill_id": skills[0].skill_id,
                   "filename": "a/SKILL.md", "step_index": 1,
                   "command": "cat a/SKILL.md"}],
        "read_skill_ids": [skills[0].skill_id],
        "unread_skill_ids": [skills[1].skill_id, skills[2].skill_id],
        "first_agent_step": 1,
        "first_read_step": 1,
        "outcome": "failure",
    }])
    return store, skills


def _row():
    return TaskListRow(target_kind="group", target_id=TASK_ID,
                       action="revise", reason="no_gain",
                       basis_unit=f"{TASK_ID}:weak:0")


def _script(skills):
    return json_script({
        "diagnosis": "the model read one skill and ignored it",
        "skills": [
            {"skill_id": skill.skill_id,
             "relevance": "relevant",
             "application": "not_applied",
             "evidence_steps": [1],
             "problem": "the trigger did not lead to the steps"}
            for skill in skills],
    })


def test_usage_facts_become_packages(tmp_path):
    store, skills = _seeded(tmp_path)
    packages = usage_group.collect_group_evidence(
        task_row=_row(), store=store, round_=0, n_attempts=N_ATTEMPTS,
        task_instruction=TASK_INSTRUCTION, weak_profile=WEAK_PROFILE,
        author_handle=make_fake_handle("author", _script(skills)),
        params=UsageGroupParams())

    assert len(packages) == len(skills)
    for package in packages:
        ensure_fact_only(package)
        assert package.fact("delivery_status") == "ok"
        assert package.fact("selection_status") == "some_read"
        assert package.fact("skill_application") == "not_applied"
        assert package.fact("skill_read_attempts") in ("0", "1")


def test_a_delivery_failure_skips_the_model_call(tmp_path):
    store, skills = _seeded(tmp_path)
    path = lm.round_dir(store, 0) / SKILL_USAGE_NAME
    rows = []
    for row in [{
            "schema": "skill-usage-v1", "task_id": TASK_ID, "round": 0,
            "attempt": 0, "trajectory_id": _traj(0).trajectory_id,
            "delivery": "folder", "description_mode": "when_to_use",
            "available_list_present": False, "available_names": [],
            "available_skill_ids": [skill.skill_id for skill in skills],
            "delivery_status": "manifest_mismatch", "reads": [],
            "read_skill_ids": [], "unread_skill_ids": [
                skill.skill_id for skill in skills],
            "first_agent_step": 1, "first_read_step": None,
            "outcome": "failure"}]:
        rows.append(row)
    write_jsonl(path, rows)

    packages = usage_group.collect_group_evidence(
        task_row=_row(), store=store, round_=0, n_attempts=N_ATTEMPTS,
        task_instruction=TASK_INSTRUCTION, weak_profile=WEAK_PROFILE,
        author_handle=make_fake_handle("author", ""),
        params=UsageGroupParams())

    # The FakeClient would raise on an unexpected call; reaching here
    # proves the delivery failure skipped the attribution call.
    assert all(package.fact("delivery_status") == "manifest_mismatch"
               for package in packages)
    assert all(package.fact("skill_application") is None
               for package in packages)
