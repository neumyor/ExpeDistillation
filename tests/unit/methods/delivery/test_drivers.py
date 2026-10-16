"""Contract tests for the delivery drivers' LEGACY module seam (the
pre-R5 ``resolve(ctx, params, ...)`` shapes stage 6 and the live driver
still call, none / per_skill / group_by_task here; whole_bank and
retrieval through their callers).

R5 (Wave 1) freezes the new signature ``resolve(bank, tasks, params) ->
ExecutionUnitList`` and keeps the legacy shapes as a compat layer that
delegates to it; these tests pin that delegation byte-for-byte until
Wave 2 replaces the call sites. The frozen signature itself is pinned by
tests/unit/test_w1d_delivery_signature.py.
"""

import pytest

from expedistill.benchmarks.base import Task
from expedistill.composites.live_measurement import survivors_and_block
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
