"""The author driver: task list + evidence packages -> skill content (R6).

The acceptance-relevant behaviors pinned here: dispatch by evidence
package (an exam answer sheet goes to the exam rewrite block, failure
views to the live revision block -- the caller never names a report
kind); the revision-flow delete owned by the author (discard reason
live_revision_deleted); and the rewrite-budget disposition per the C8
ruling -- exhaustion and byte-identical output are NO CHANGE (the
skill keeps its version, nothing is discarded), not a discard.
"""

import pytest

from expedistill.core.contracts import TaskListRow
from expedistill.core.ids import (
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill, Subtask
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    ModelOutputError,
)
from expedistill.llm import json_script, make_fake_handle
from expedistill.methods.author.write import (
    RevisionOutcome,
    default_decide,
    revise_group,
    write_new,
)
from expedistill.methods.evidence.package import EvidencePackage

WEAK_PROFILE = "A small model; known to skip intermediate steps."
DIAGNOSIS = "the steps were skipped"

MARKED_A = "\n".join([
    "[When to use] The service rejects connections and needs a restart.",
    "[Steps]",
    "1. Check the service status with the status command.",
    "2. Restart the service with the restart command.",
    "[Verify] The status command reports the service as running.",
    "[Do not use when] The status command already reports running.",
])
MARKED_A_DROPPED = "\n".join([
    "[When to use] The service rejects connections and needs a restart.",
    "[Steps]",
    "1. Run the status command on the service.",
    "2. Restart the service with its restart command.",
    "[Do not use when] The status command already reports running.",
])
MARKED_A_REVISED = "\n".join([
    "[When to use] The service rejects connections and a restart is due.",
    "[Steps]",
    "1. Run the status command to see the service state.",
    "2. Restart the service with its restart command.",
    "3. Re-run the status command to confirm.",
    "[Verify] The second status command reports the service as running.",
    "[Do not use when] The status command already reports running.",
])


def make_skill(segment=0, content=MARKED_A):
    subtask_id = make_subtask_id(
        make_trajectory_id(make_task_id("tb", "t1"), "strong", 0), segment)
    return Skill(skill_id=make_skill_id(subtask_id), content=content,
                 subtask_id=subtask_id, source_subtasks=[subtask_id])


def live_package(skill, *, problem="the verify step is missing",
                 view="do x 0\naction: restart"):
    return EvidencePackage(
        target=skill.skill_id,
        facts=[{"kind": "group_diagnosis", "text": DIAGNOSIS},
               {"kind": "skill_problem", "text": problem},
               {"kind": "failure_view", "text": view}],
        old_skill=skill)


def exam_package(skill):
    return EvidencePackage(
        target=skill.skill_id,
        facts=[{"kind": "exam_question", "text": "How do you restart?"},
               {"kind": "weak_answer", "text": "I would restart it."},
               {"kind": "judge_reason",
                "text": "the answer omits the verification step"},
               {"kind": "skill_problem",
                "text": "the answer omits the verification step"}],
        old_skill=skill)


def revise_row():
    return TaskListRow(target_kind="group", target_id="tb:t1",
                       action="revise", reason="no improvement",
                       basis_unit="u")


def add_row():
    return TaskListRow(target_kind="task", target_id="tb:t1",
                       action="add", reason="weak fails, no skill yet",
                       basis_unit="u")


def revision_script(text, category="missing_step",
                    reason="spelled out the verification step",
                    summary="a revised one-liner"):
    return json_script({"skill_text": text, "failure_category": category,
                        "revision_reason": reason, "summary": summary})


def rewrite_script(text, category="missing_step",
                   reason="spelled out the verification step",
                   summary="a rewritten one-liner"):
    return json_script({"skill_text": text, "failure_category": category,
                        "rewrite_reason": reason, "summary": summary})


def run_revise(packages, script, *, decide=None, revision_attempts=2):
    skill = packages[0].old_skill
    handle = make_fake_handle("rewriter", script)
    outcomes = revise_group(
        task_row=revise_row(), packages=packages, rewriter_handle=handle,
        weak_profile=WEAK_PROFILE, decide=decide,
        revision_attempts=revision_attempts)
    return handle, outcomes, skill


# ---------------------------------------------------------------------------
# decide / keep
# ---------------------------------------------------------------------------

def test_default_policy_revises_on_problem_keeps_without_one():
    with_problem = live_package(make_skill(), problem="trigger vague")
    assert default_decide(with_problem) == "revise"
    no_problem = live_package(make_skill(), problem="")
    no_problem.facts = [f for f in no_problem.facts
                        if f["kind"] != "skill_problem"]
    assert default_decide(no_problem) == "keep"


def test_keep_needs_no_model_call():
    skill = make_skill()
    package = live_package(skill, problem="")
    package.facts = [f for f in package.facts
                     if f["kind"] != "skill_problem"]
    handle, outcomes, _ = run_revise([package], [])

    assert outcomes == [RevisionOutcome(skill_id=skill.skill_id,
                                        decision="keep", outcome="kept")]
    assert handle.client.calls == []


# ---------------------------------------------------------------------------
# revise: dispatch by the evidence package
# ---------------------------------------------------------------------------

def test_live_shaped_package_goes_to_the_live_revision_block():
    skill = make_skill()
    handle, outcomes, _ = run_revise(
        [live_package(skill)], [revision_script(MARKED_A_REVISED)])

    assert outcomes[0].outcome == "revised"
    assert outcomes[0].content == MARKED_A_REVISED
    assert outcomes[0].failure_category == "missing_step"
    assert outcomes[0].revision_reason == \
        "spelled out the verification step"
    prompt = handle.client.calls[0]["user"]
    # The same composed inputs the legacy K6 path renders.
    assert DIAGNOSIS in prompt
    assert "This skill's problem: the verify step is missing" in prompt
    assert "do x 0" in prompt
    assert MARKED_A in prompt
    assert WEAK_PROFILE in prompt


def test_exam_shaped_package_goes_to_the_exam_rewrite_block():
    """The same driver, the other package shape: an exam answer sheet
    feeds the G6 rewrite block. No parameter names the report kind --
    the package alone decides."""
    skill = make_skill()
    handle, outcomes, _ = run_revise(
        [exam_package(skill)], [rewrite_script(MARKED_A_REVISED)])

    assert outcomes[0].outcome == "revised"
    prompt = handle.client.calls[0]["user"]
    assert "How do you restart?" in prompt
    assert "I would restart it." in prompt
    assert "the answer omits the verification step" in prompt
    assert MARKED_A in prompt


def test_a_revision_without_failure_material_is_a_caller_bug():
    skill = make_skill()
    package = EvidencePackage(
        target=skill.skill_id,
        facts=[{"kind": "skill_problem", "text": "trigger vague"},
               {"kind": "group_diagnosis", "text": DIAGNOSIS}],
        old_skill=skill)
    with pytest.raises(CodeBugError, match="no failure material"):
        revise_group(task_row=revise_row(), packages=[package],
                     rewriter_handle=make_fake_handle("rewriter", []),
                     weak_profile=WEAK_PROFILE,
                     decide=lambda p: "revise")


def test_a_package_without_an_old_skill_is_a_caller_bug():
    package = EvidencePackage(target="s", facts=[])
    with pytest.raises(CodeBugError, match="no old_skill"):
        revise_group(task_row=revise_row(), packages=[package],
                     rewriter_handle=make_fake_handle("rewriter", []),
                     weak_profile=WEAK_PROFILE)


def test_refuses_a_task_row_that_is_not_a_revision():
    skill = make_skill()
    row = TaskListRow(target_kind="group", target_id="tb:t1",
                      action="delete", reason="r", basis_unit="u")
    with pytest.raises(ValueError, match="action 'revise'"):
        revise_group(task_row=row, packages=[live_package(skill)],
                     rewriter_handle=make_fake_handle("rewriter", []),
                     weak_profile=WEAK_PROFILE)


def test_refuses_a_nonpositive_revision_budget():
    skill = make_skill()
    with pytest.raises(ValueError, match="revision_attempts"):
        revise_group(task_row=revise_row(),
                     packages=[live_package(skill)],
                     rewriter_handle=make_fake_handle("rewriter", []),
                     weak_profile=WEAK_PROFILE, revision_attempts=0)


# ---------------------------------------------------------------------------
# revise: the C8 budget disposition -- no change, never a discard
# ---------------------------------------------------------------------------

def test_budget_exhaustion_is_no_change_not_a_discard():
    """R6's acceptance scenario: the rewrite budget runs out and the
    skill is NOT discarded -- no new version came out, so the skill is
    unchanged (boundaries 4.3 rule 2; the C8 ruling overrides the old
    failure-disposition row), retried next learning round."""
    skill = make_skill()
    handle, outcomes, _ = run_revise([live_package(skill)], [
        ModelOutputError("LIVE_SKILL_REVISION: bad json"),
        ModelOutputError("LIVE_SKILL_REVISION: worse"),
    ])

    assert outcomes[0].outcome == "unchanged"
    assert outcomes[0].content is None
    assert outcomes[0].discard_reason is None
    assert len(handle.client.calls) == 2


def test_a_marker_failure_rides_the_budget_then_revises():
    skill = make_skill()
    handle, outcomes, _ = run_revise([live_package(skill)], [
        revision_script(MARKED_A_DROPPED),      # drops [Verify]
        revision_script(MARKED_A_REVISED),
    ])

    assert outcomes[0].outcome == "revised"
    assert outcomes[0].content == MARKED_A_REVISED
    assert len(handle.client.calls) == 2


def test_a_marker_failure_exhausting_the_budget_is_no_change():
    skill = make_skill()
    handle, outcomes, _ = run_revise([live_package(skill)], [
        revision_script(MARKED_A_DROPPED),
        revision_script(MARKED_A_DROPPED),
    ])

    assert outcomes[0].outcome == "unchanged"
    assert len(handle.client.calls) == 2


def test_identical_output_is_no_change_not_a_new_version():
    """Boundaries 4.3 rule 1: a revision that lands byte-identical to
    the original counts as no change -- no new version."""
    skill = make_skill()
    handle, outcomes, _ = run_revise(
        [live_package(skill)], [revision_script(MARKED_A)])

    assert outcomes[0].outcome == "unchanged"
    assert outcomes[0].content is None
    assert len(handle.client.calls) == 1


def test_infra_failure_is_not_a_budget_item():
    skill = make_skill()
    handle, outcomes, _ = run_revise(
        [live_package(skill)], [InfrastructureError("sandbox died")])

    assert outcomes[0].outcome == "infra_failed"
    assert len(handle.client.calls) == 1


# ---------------------------------------------------------------------------
# the revision-flow delete (R6 item 3: owned by the author)
# ---------------------------------------------------------------------------

def test_delete_decision_carries_live_revision_deleted():
    """The revision-flow delete is the author's call: a policy that
    decides delete comes back with discard reason live_revision_deleted
    for the task list applier, and no model call is spent."""
    skill = make_skill()
    handle, outcomes, _ = run_revise(
        [live_package(skill, problem="harmful in this group")], [],
        decide=lambda package: "delete")

    assert outcomes[0] == RevisionOutcome(
        skill_id=skill.skill_id, decision="delete", outcome="deleted",
        discard_reason="live_revision_deleted")
    assert handle.client.calls == []


def test_an_unknown_decision_is_rejected():
    skill = make_skill()
    with pytest.raises(CodeBugError, match="revision policy"):
        revise_group(task_row=revise_row(), packages=[live_package(skill)],
                     rewriter_handle=make_fake_handle("rewriter", []),
                     weak_profile=WEAK_PROFILE, decide=lambda p: "add")


# ---------------------------------------------------------------------------
# write_new: the add path
# ---------------------------------------------------------------------------

def make_subtask(segment):
    trajectory_id = make_trajectory_id(make_task_id("tb", "t1"), "strong", 0)
    return Subtask(
        subtask_id=make_subtask_id(trajectory_id, segment),
        task_id=make_task_id("tb", "t1"), trajectory_id=trajectory_id,
        source="strong", trajectory_success=True, segment_index=segment,
        step_range=(0, 2), label="restart the service",
        description="restart the stuck service and verify", success=True)


def add_package(*subtasks):
    return EvidencePackage(
        target="tb:t1",
        facts=[{"kind": "add_target_task", "text": "tb:t1"}],
        source_experiences=[
            {"subtask": sub, "steps_view": "do s0 0", "mode": "distill"}
            for sub in subtasks])


def distill_script():
    return json_script({
        "when_to_use": "When the service rejects connections.",
        "steps": ["Run the status command on the service.",
                  "Restart the service with its restart command."],
        "verify": "The status command reports the service as running.",
        "not_applicable_when": "The service already reports running.",
        "summary": "restore a service that rejects connections",
    })


def test_write_new_distills_one_skill_per_experience():
    subtasks = [make_subtask(0), make_subtask(1)]
    author = make_fake_handle("author", [distill_script(), distill_script()])

    skills = write_new(task_row=add_row(), package=add_package(*subtasks),
                       author_handle=author, weak_profile=WEAK_PROFILE)

    assert [s.skill_id for s in skills] == \
        [make_skill_id(sub.subtask_id) for sub in subtasks]
    assert all("[When to use]" in s.content for s in skills)
    assert all(s.status == "pending" and s.version == 1 for s in skills)
    prompt = author.client.calls[0]["user"]
    assert "restart the service" in prompt   # the subtask description
    assert "do s0 0" in prompt               # the rendered steps view
    assert WEAK_PROFILE in prompt


def test_write_new_propagates_a_model_output_error():
    author = make_fake_handle(
        "author", [ModelOutputError("SKILL_DISTILL: bad json")])
    with pytest.raises(ModelOutputError):
        write_new(task_row=add_row(), package=add_package(make_subtask(0)),
                  author_handle=author, weak_profile=WEAK_PROFILE)


def test_write_new_refuses_a_task_row_that_is_not_an_add():
    row = TaskListRow(target_kind="skill", target_id="s", action="revise",
                      reason="r", basis_unit="u")
    with pytest.raises(ValueError, match="action 'add'"):
        write_new(task_row=row, package=add_package(make_subtask(0)),
                  author_handle=make_fake_handle("author", []),
                  weak_profile=WEAK_PROFILE)


def test_write_new_refuses_a_package_without_source_experiences():
    with pytest.raises(CodeBugError, match="no source_experiences"):
        write_new(task_row=add_row(), package=EvidencePackage(target="tb:t1"),
                  author_handle=make_fake_handle("author", []),
                  weak_profile=WEAK_PROFILE)
