"""Contract tests for the delivery drivers' frozen signature
(``resolve(bank, tasks, params) -> ExecutionUnitList``, the only form
the phase drivers call).

Also pins the bank-membership filter the whole-bank and retrieval
drivers apply: the phase artifacts carry accepted AND pending members,
but those two drivers inject the accepted set only -- the shape the
run-level bank had before the phase artifacts existed (the 2026-10-16
review ruling pins the filter to that pre-change behaviour).
"""

import pytest

from expedistill.composites.live_measurement import (
    pending_groups,
    survivors_and_block,
)
from expedistill.core.ids import (
    make_exam_id,
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError
from expedistill.infra.store import Store
from expedistill.inject import format_skills_block
from expedistill.methods import resolve_fixed

BENCHMARK = "tb"

MARKED = "\n".join([
    "[When to use] The service rejects connections.",
    "[Steps]",
    "1. Check the service status.",
    "2. Restart the service.",
    "[Do not use when] The status command already reports running.",
])


def make_group_skill(task_id, content=MARKED):
    subtask_id = make_subtask_id(make_trajectory_id(task_id, "strong", 0), 0)
    return Skill(skill_id=make_skill_id(subtask_id), content=content,
                 subtask_id=subtask_id, source_subtasks=[subtask_id])


def _bank():
    """A full-membership phase artifact's worth of skills: one accepted,
    one pending, one discarded."""
    accepted = make_group_skill(make_task_id(BENCHMARK, "t1"))
    accepted.status = "accepted"
    pending = make_group_skill(make_task_id(BENCHMARK, "t2"))
    pending.status = "pending"
    discarded = make_group_skill(make_task_id(BENCHMARK, "t3"))
    discarded.status = "discarded"
    return [accepted, pending, discarded], accepted


def _retrieval_params():
    from expedistill.methods.delivery import RetrievalParams
    return RetrievalParams(top_k=1, max_categories=1, min_score=0.0)


def _task(task_id):
    from expedistill.benchmarks.base import Task
    return Task(task_id=task_id, instruction="restart the stuck service")


def test_whole_bank_injects_the_accepted_members_only():
    bank, accepted = _bank()
    tasks = [make_task_id(BENCHMARK, f"t{i}") for i in (1, 2, 3)]
    delivery, params = resolve_fixed("delivery", "whole_bank")
    rows = delivery.resolve(bank, tasks, params)
    # Every row carries the ACCEPTED bank's digest and its rendered
    # block; the pending member never enters the injection.
    expected_block = format_skills_block([accepted])
    assert len(rows) == 3
    for row in rows:
        assert row.skills_injected.startswith("bank:")
        assert row.skills_block == expected_block


def test_retrieval_ranks_over_the_accepted_members_only():
    bank, accepted = _bank()
    from expedistill.methods.delivery.retrieval import resolve
    rows = resolve(bank, [_task(make_task_id(BENCHMARK, "t1"))],
                   _retrieval_params())
    # The single accepted member is what the selection can reach; the
    # pending member never enters the ranking.
    assert len(rows) == 1
    assert rows[0].skills_injected == [
        {"skill_id": accepted.skill_id, "version": accepted.version}]


def test_a_bank_of_only_pending_members_injects_nothing():
    pending = make_group_skill(make_task_id(BENCHMARK, "t1"))
    pending.status = "pending"
    tasks = [make_task_id(BENCHMARK, "t1")]
    whole, whole_params = resolve_fixed("delivery", "whole_bank")
    rows = whole.resolve([pending], tasks, whole_params)
    assert rows[0].skills_block == format_skills_block([])
    from expedistill.methods.delivery.retrieval import resolve
    rows = resolve([pending], [_task(tasks[0])], _retrieval_params())
    assert rows[0].skills_injected == []    # a bare run under the label


def test_none_supplies_the_bare_rows():
    # The bank and tasks are the family's operands and the bank is
    # ignored: there is nothing to select from. skills_injected is None
    # (the bare control's record), never [] (retrieval's below-threshold
    # task inside a with-bank arm). The Wave 1 legacy arm-level seam is
    # gone (W2-A rewired the call sites); one row per task.
    delivery, params = resolve_fixed("delivery", "none")
    rows = delivery.resolve(None, ["task-a", "task-b"], params)
    assert [(row.task_ref, row.skills_injected, row.skills_block)
            for row in rows] == [
        ("task-a", None, ""), ("task-b", None, "")]


def test_exam_folder_catalog_covers_the_full_bank():
    # The generalization exam shows every current member -- pending
    # included -- so the model can find a not-yet-accepted skill. The
    # block is the same layer-1 catalog the folder delivery renders, and
    # the injected digest is the FULL bank's (never the accepted subset).
    from expedistill.inject import bank_reference
    from expedistill.methods.delivery.folder import render_catalog

    accepted = make_group_skill(make_task_id(BENCHMARK, "t1"))
    accepted.status = "accepted"
    pending = make_group_skill(make_task_id(BENCHMARK, "t2"))
    pending.status = "pending"
    bank = [accepted, pending]
    exam = make_exam_id(accepted.skill_id, accepted.version)

    delivery, params = resolve_fixed("delivery", "exam_folder")
    rows = delivery.resolve(bank, [exam], params)

    assert [row.task_ref for row in rows] == [exam]
    assert rows[0].skills_injected == bank_reference(bank)
    assert rows[0].skills_block == render_catalog(bank)
    assert pending.skill_id in rows[0].skills_block
    assert [entry["skill_id"]
            for entry in rows[0].aux["skill_folder"]["files"]] == [
        accepted.skill_id, pending.skill_id]


def test_group_by_task_resolves_each_tasks_pending_group(tmp_path):
    task_id = make_task_id(BENCHMARK, "t1")
    store = Store(tmp_path / "run")
    store.save(make_group_skill(task_id), "skills")

    delivery, params = resolve_fixed("delivery", "group_by_task")
    rows = delivery.resolve(pending_groups(store), [task_id], params)
    members, expected_block = survivors_and_block(store, task_id)
    assert len(rows) == 1
    assert rows[0].task_ref == task_id
    assert rows[0].skills_injected == [
        {"skill_id": skill.skill_id, "version": skill.version}
        for skill in members]
    assert rows[0].skills_block == expected_block


def test_group_by_task_refuses_a_task_without_a_group(tmp_path):
    # An empty injection would be a bare run under the with-skills
    # label; a dispatched group is never empty.
    store = Store(tmp_path / "run")
    delivery, params = resolve_fixed("delivery", "group_by_task")
    with pytest.raises(CodeBugError, match="no pending skill group"):
        delivery.resolve(pending_groups(store),
                         [make_task_id(BENCHMARK, "t1")], params)
