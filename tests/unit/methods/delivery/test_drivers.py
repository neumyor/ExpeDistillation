"""Contract tests for the delivery drivers registered in M2 (none /
per_skill / group_by_task).

per_skill has no caller until the exam execution driver lands in M4, so
its contract is pinned here directly; none and group_by_task are also
exercised through their callers (stage 6's bare arm, the live driver),
but a direct test keeps the whole family's resolve shape honest.
"""

import pytest

from expedistill.benchmarks.base import Task
from expedistill.composites.live_acceptance import survivors_and_block
from expedistill.core.ids import (
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
    "[Verify] The status command reports running.",
    "[Do not use when] The status command already reports running.",
])


def make_group_skill(task_id, content=MARKED):
    subtask_id = make_subtask_id(make_trajectory_id(task_id, "strong", 0), 0)
    return Skill(skill_id=make_skill_id(subtask_id), content=content,
                 subtask_id=subtask_id, source_subtasks=[subtask_id])


def test_none_supplies_the_bare_scalar_kwargs():
    # The bank and tasks are the family's operands and are ignored:
    # there is nothing to select from. skills_injected is None (the
    # bare control's record), never [] (retrieval's below-threshold
    # task inside a with-bank arm).
    delivery, params = resolve_fixed("delivery", "none")
    method_kwargs, audit_rows = delivery.resolve(
        None, params, ["bank-row"], ["task"])
    assert method_kwargs == {"skills_injected": None, "skills_block": ""}
    assert audit_rows is None


def test_per_skill_block_matches_the_k4_rendering():
    # The driver must delegate to the same layer-1 renderer K4 uses,
    # so acceptance-time and test-time injection stay one rendering.
    skill = make_group_skill(make_task_id(BENCHMARK, "t1"))
    delivery, params = resolve_fixed("delivery", "per_skill")
    method_kwargs, audit_rows = delivery.resolve(None, params, skill)
    assert method_kwargs == {"skills_block": format_skills_block([skill])}
    assert audit_rows is None


def test_group_by_task_resolves_each_tasks_pending_group(tmp_path):
    task_id = make_task_id(BENCHMARK, "t1")
    store = Store(tmp_path / "run")
    store.save(make_group_skill(task_id), "skills")

    delivery, params = resolve_fixed("delivery", "group_by_task")
    method_kwargs, audit_rows = delivery.resolve(None, params, store,
                                                 BENCHMARK)
    assert audit_rows is None
    references, block = method_kwargs["skills_for_task"](
        Task(task_id="t1", instruction="do t1"))
    members, expected_block = survivors_and_block(store, task_id)
    assert references == [{"skill_id": skill.skill_id, "version": skill.version}
                          for skill in members]
    assert block == expected_block


def test_group_by_task_refuses_a_task_without_a_group(tmp_path):
    # An empty injection would be a bare run under the with-skills
    # label; a dispatched group is never empty.
    store = Store(tmp_path / "run")
    delivery, params = resolve_fixed("delivery", "group_by_task")
    method_kwargs, _ = delivery.resolve(None, params, store, BENCHMARK)
    with pytest.raises(CodeBugError, match="no pending skill group"):
        method_kwargs["skills_for_task"](Task(task_id="t1",
                                              instruction="do t1"))
