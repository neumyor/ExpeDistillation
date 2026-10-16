"""The live-group evidence driver (G10 as facts) -- R6 acceptance tests.

The driver collects one group's failed-round evidence as fact-only
packages. The load-bearing acceptance criterion is the first one: the
underlying G10 model prompt still emits keep/revise/delete (its output
contract is frozen this wave), and the driver must DROP that vocabulary
at the evidence boundary -- no package, fact or segment carries an
action.
"""

import pytest

from expedistill.composites import live_measurement as lm
from expedistill.core.contracts import TaskListRow
from expedistill.core.ids import (
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill, Step, Trajectory
from expedistill.infra.reliability import ModelOutputError
from expedistill.infra.store import Store, append_jsonl
from expedistill.llm import json_script, make_fake_handle
from expedistill.methods.evidence.package import ensure_fact_only

from expedistill.methods.evidence.live_group import collect_group_evidence

BENCHMARK = "tb"
TASK_ID = make_task_id(BENCHMARK, "t1")
N_ATTEMPTS = 2
WEAK_PROFILE = "A small model; known to skip intermediate steps."
TASK_INSTRUCTION = "Fix the stuck service on host <host>."

MARKED_A = "\n".join([
    "[When to use] The service rejects connections and needs a restart.",
    "[Steps]",
    "1. Check the service status with the status command.",
    "2. Restart the service with the restart command.",
    "[Verify] The status command reports the service as running.",
    "[Do not use when] The status command already reports running.",
])


def make_group_skill(segment, content=MARKED_A):
    subtask_id = make_subtask_id(
        make_trajectory_id(TASK_ID, "strong", 0), segment)
    return Skill(skill_id=make_skill_id(subtask_id), content=content,
                 subtask_id=subtask_id, source_subtasks=[subtask_id])


def make_traj(attempt, correctness, role="weak", marker="x"):
    return Trajectory(
        trajectory_id=make_trajectory_id(TASK_ID, role, attempt),
        task_id=TASK_ID, benchmark=BENCHMARK, model_role=role,
        model_name="weak-model", attempt_index=attempt,
        steps=[Step(action=f"do {marker} {attempt}",
                    observation=f"obs {marker} {attempt}")],
        correctness=correctness,
    )


def seeded_store(tmp_path):
    """A run directory the stage would hand to a round-0 attribution:
    a weak baseline, a failed round store and one frozen audit line."""
    store = Store(tmp_path / "run")
    for attempt in range(N_ATTEMPTS):
        store.save(make_traj(attempt, False), "trajectories")
    round_dir = store.run_dir / "stage4_acceptance" / "live_run" / "round00"
    round_store = Store(round_dir)
    for attempt in range(N_ATTEMPTS):
        round_store.save(make_traj(attempt, False), "trajectories")
    skills = [make_group_skill(segment) for segment in range(3)]
    for skill in skills:
        store.save(skill, "skills")
    members = [{"skill_id": skill.skill_id, "version": 1}
               for skill in skills]
    _, block = lm.survivors_and_block(store, TASK_ID)
    append_jsonl(round_dir / lm.INJECTION_AUDIT_NAME, {
        "schema": "injection-audit-v1", "task_id": TASK_ID, "round": 0,
        "members": members,
        "injection_block_sha256": lm._block_sha(block),
        "injection_tokens": 40,
    })
    return store, skills


def revise_row():
    return TaskListRow(target_kind="group", target_id=TASK_ID,
                       action="revise", reason="no improvement over the "
                       "bare baseline", basis_unit=f"{TASK_ID}:weak:0")


def plan_script(actions, diagnosis="the injected skill did not help"):
    return json_script({
        "diagnosis": diagnosis,
        "skill_actions": [
            {"skill_id": skill_id, "action": action, "problem": problem}
            for skill_id, action, problem in actions],
    })


def collect(tmp_path, plan_builder):
    """Seed a run, build the G10 model script from its skills, collect."""
    store, skills = seeded_store(tmp_path)
    author = make_fake_handle("author", plan_script(plan_builder(skills)))
    packages = collect_group_evidence(
        task_row=revise_row(), store=store, round_=0,
        n_attempts=N_ATTEMPTS, task_instruction=TASK_INSTRUCTION,
        weak_profile=WEAK_PROFILE, author_handle=author,
        run_dir=store.run_dir)
    return store, skills, author, packages


MIXED_PLAN = lambda skills: [   # noqa: E731
    (skills[0].skill_id, "revise", "trigger too vague"),
    (skills[1].skill_id, "keep", ""),
    (skills[2].skill_id, "delete", "harmful in this group"),
]
KEEP_PLAN = lambda skills: [    # noqa: E731
    (skill.skill_id, "keep", "") for skill in skills
]


def test_g10_output_carries_no_actions(tmp_path):
    """R6's acceptance assertion: the model script emits all three
    actions, and not one survives the evidence boundary -- structurally
    (no action key anywhere in a serialized package), by vocabulary
    (no fact kind or text is an action word) and by content (the plan
    list itself is gone)."""
    _, skills, _, packages = collect(tmp_path, MIXED_PLAN)

    assert len(packages) == 3
    for package in packages:
        ensure_fact_only(package)   # no raise: no action key anywhere
        for entry in package.facts:
            assert entry["kind"] not in ("keep", "revise", "delete", "add")
            assert entry["text"] not in ("keep", "revise", "delete", "add")
        assert "skill_actions" not in str(package.facts)
        assert "skill_actions" not in str(package.segments)


def test_packages_carry_the_facts_and_the_old_skills(tmp_path):
    _, skills, _, packages = collect(tmp_path, MIXED_PLAN)

    assert [p.target for p in packages] == [s.skill_id for s in skills]
    assert [p.old_skill for p in packages] == skills
    for package in packages:
        # Measurement numbers, one fact per attempt of each arm.
        assert package.fact("baseline_run") == "attempt 0: failure"
        assert package.fact("live_run") == "attempt 0: failure"
        assert len([f for f in package.facts
                    if f["kind"] == "baseline_run"]) == N_ATTEMPTS
        assert package.fact("group_diagnosis") == \
            "the injected skill did not help"
        # The failure view is the round's rendered trajectory.
        assert "do x 0" in package.fact("failure_view")
        # The frozen audit line's sha, so revisions stay traceable.
        assert package.fact("injection_block_sha256")
    # A problem is a fact about the skill; no problem means no fact.
    assert packages[0].fact("skill_problem") == "trigger too vague"
    assert packages[2].fact("skill_problem") == "harmful in this group"
    assert packages[1].fact("skill_problem") is None


def test_segments_pin_the_rounds_trajectories(tmp_path):
    _, _, _, packages = collect(tmp_path, KEEP_PLAN)

    assert packages[0].segments == [
        {"trajectory_id": make_trajectory_id(TASK_ID, "weak", attempt),
         "step_range": [0, 1], "correctness": False}
        for attempt in range(N_ATTEMPTS)]


def test_prompt_carries_the_composed_inputs(tmp_path):
    _, _, author, _ = collect(tmp_path, KEEP_PLAN)

    prompt = author.client.calls[0]["user"]
    assert "attempt 0: failure" in prompt        # the baseline summary
    assert "do x 0" in prompt                     # a live view's step text
    assert TASK_INSTRUCTION in prompt
    assert MARKED_A in prompt                     # the group listing
    assert WEAK_PROFILE in prompt


def test_model_output_error_propagates_no_discard(tmp_path):
    """A failed G10 call is the caller's problem (the budget is composite
    machinery, the group-abandon on exhaustion is the decision module's
    call): the driver neither retries nor touches the store."""
    store, skills = seeded_store(tmp_path)
    author = make_fake_handle(
        "author", [ModelOutputError("LIVE_RUN_ATTRIBUTION: bad json")])

    with pytest.raises(ModelOutputError):
        collect_group_evidence(
            task_row=revise_row(), store=store, round_=0,
            n_attempts=N_ATTEMPTS, task_instruction=TASK_INSTRUCTION,
            weak_profile=WEAK_PROFILE, author_handle=author,
            run_dir=store.run_dir)

    assert all(store.load("skills", s.skill_id, Skill).status == "pending"
               for s in skills)


def test_refuses_a_task_row_that_is_not_a_revision(tmp_path):
    store, _ = seeded_store(tmp_path)
    row = TaskListRow(target_kind="group", target_id=TASK_ID,
                      action="keep", reason="improved", basis_unit="u")
    with pytest.raises(ValueError, match="action 'revise'"):
        collect_group_evidence(
            task_row=row, store=store, round_=0, n_attempts=N_ATTEMPTS,
            task_instruction=TASK_INSTRUCTION, weak_profile=WEAK_PROFILE,
            author_handle=make_fake_handle("author", []),
            run_dir=store.run_dir)
