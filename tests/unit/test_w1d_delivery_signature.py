"""W1-D (R5): the five delivery drivers' frozen resolve signature.

``resolve(bank, tasks, params) -> ExecutionUnitList`` -- asserted per
driver, positionally and by keyword, for every registered delivery
family entry. The Wave 1 legacy module seam was removed with the Wave 2
phase drivers' call-site rewrite (W2-A); the frozen form is the only
form. (tests/unit/methods/delivery/test_drivers.py pins the drivers'
operands.)

Decision doc: docs/decisions/execute_learn_repair_plan_20261016.md, R5.
"""

import pytest

from expedistill.benchmarks.base import Task
from expedistill.blocks.retrieve import derive_categories, select_skills
from expedistill.composites.live_measurement import pending_groups
from expedistill.core.contracts import ExecutionUnitRow
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
from expedistill.inject import bank_reference, format_skills_block
from expedistill.methods import resolve_fixed
from expedistill.methods.delivery import (
    GroupByTaskParams,
    NoneParams,
    PerSkillParams,
    RetrievalParams,
    WholeBankParams,
)
from expedistill.methods.delivery import retrieval, units

BENCHMARK = "tb"

MARKED = "\n".join([
    "[When to use] The service rejects connections.",
    "[Steps]",
    "1. Check the service status.",
    "2. Restart the service.",
    "[Verify] The status command reports running.",
    "[Do not use when] The status command already reports running.",
])


def _skill(task_id: str, content: str = MARKED, status: str = "pending"
           ) -> Skill:
    subtask_id = make_subtask_id(make_trajectory_id(task_id, "strong", 0), 0)
    return Skill(skill_id=make_skill_id(subtask_id), content=content,
                 subtask_id=subtask_id, source_subtasks=[subtask_id],
                 status=status)


def _driver(name: str):
    """The registered driver module for a fixed name (the registry seam).

    retrieval is imported directly: its params have required fields, so
    resolve_fixed's empty raw mapping cannot build them (the same reason
    the config always spells them out).
    """
    if name == "retrieval":
        return retrieval
    return resolve_fixed("delivery", name)[0]


def _operands(name: str):
    """A minimal (bank, tasks, params) triple the driver resolves."""
    prefixed = make_task_id(BENCHMARK, "t1")
    skill = _skill(prefixed)
    task = Task(task_id="t1", instruction="restart the service")
    if name == "none":
        return None, [task], NoneParams()
    if name == "whole_bank":
        return [skill], [task], WholeBankParams()
    if name == "per_skill":
        return [skill], [make_exam_id(skill.subtask_id)], PerSkillParams()
    if name == "group_by_task":
        return {prefixed: [skill]}, [prefixed], GroupByTaskParams()
    if name == "retrieval":
        return ([skill], [task],
                RetrievalParams(top_k=1, max_categories=2, min_score=0.0))
    raise AssertionError(f"unknown delivery driver {name!r}")


@pytest.mark.parametrize("name", [
    "none", "whole_bank", "per_skill", "group_by_task", "retrieval",
])
def test_frozen_signature_returns_unit_rows(name):
    # The one signature every delivery driver resolves: positional and
    # keyword spellings agree, and every row is a frozen ExecutionUnitRow.
    bank, tasks, params = _operands(name)
    driver = _driver(name)
    rows = driver.resolve(bank, tasks, params)
    assert driver.resolve(bank=bank, tasks=tasks, params=params) == rows
    assert isinstance(rows, list) and rows
    assert all(isinstance(row, ExecutionUnitRow) for row in rows)
    for row in rows:
        assert isinstance(row.unit_id, str) and row.unit_id
        assert isinstance(row.task_ref, str)
        assert isinstance(row.skills_block, str)
        assert isinstance(row.aux, dict)


def test_none_rows_are_control_units():
    # skills_injected=None is the control-unit marker; the block is the
    # empty D9 block. One row per task, input order.
    driver = _driver("none")
    tasks = [Task(task_id="t1", instruction="do t1"),
             Task(task_id="t2", instruction="do t2")]
    rows = driver.resolve(None, tasks, NoneParams())
    assert [(row.task_ref, row.skills_injected, row.skills_block)
            for row in rows] == [("t1", None, ""), ("t2", None, "")]
    assert all(row.unit_id == units.unit_id(row.task_ref, None)
               for row in rows)


def test_whole_bank_rows_carry_the_bank_digest():
    # The fixed injection: the bank digest string (never a reference
    # list -- that would change the trajectory bookkeeping) and the
    # whole-bank D9 block, identical on every row.
    bank = [_skill(make_task_id(BENCHMARK, "t1"), status="accepted"),
            _skill(make_task_id(BENCHMARK, "t2"), status="accepted")]
    driver = _driver("whole_bank")
    rows = driver.resolve(bank, [Task(task_id="t1", instruction="do t1"),
                                 Task(task_id="t2", instruction="do t2")],
                         WholeBankParams())
    assert all(row.skills_injected == bank_reference(bank)
               and row.skills_block == format_skills_block(bank)
               for row in rows)
    assert [row.task_ref for row in rows] == ["t1", "t2"]


def test_per_skill_resolves_exam_refs():
    # The A-phase operand contract: tasks are the deterministic exam
    # identifiers (<subtask_id>/exam); delivery resolves the identifier,
    # it never writes the exam itself (R5).
    skill = _skill(make_task_id(BENCHMARK, "t1"))
    exam = make_exam_id(skill.subtask_id)
    driver = _driver("per_skill")
    rows = driver.resolve([skill], [exam], PerSkillParams())
    assert len(rows) == 1
    row = rows[0]
    assert row.task_ref == exam
    assert row.skills_injected == [
        {"skill_id": skill.skill_id, "version": skill.version}]
    assert row.skills_block == format_skills_block([skill])


def test_per_skill_refuses_unknown_exam_and_malformed_ref():
    skill = _skill(make_task_id(BENCHMARK, "t1"))
    driver = _driver("per_skill")
    params = PerSkillParams()
    with pytest.raises(CodeBugError, match="has no skill in the bank"):
        driver.resolve([skill], [make_exam_id("traj/tb:other/strong/00/seg00")],
                       params)
    with pytest.raises(CodeBugError, match="is not an exam ref"):
        driver.resolve([skill], ["traj/tb:t1/strong/00/seg00"], params)


def test_group_by_task_rows_match_the_pending_group(tmp_path):
    # The group view is the previous learn phase's bank artifact; today
    # pending_groups(store) produces it. The row's references and block
    # are that derivation verbatim.
    prefixed = make_task_id(BENCHMARK, "t1")
    skill = _skill(prefixed)
    store = Store(tmp_path / "run")
    store.save(skill, "skills")
    driver = _driver("group_by_task")
    rows = driver.resolve(pending_groups(store), [prefixed],
                          GroupByTaskParams())
    assert len(rows) == 1
    row = rows[0]
    assert row.task_ref == prefixed
    assert row.skills_injected == [
        {"skill_id": skill.skill_id, "version": skill.version}]
    assert row.skills_block == format_skills_block([skill])


def test_group_by_task_refuses_a_task_without_a_group():
    # An empty injection would be a bare run under the with-skills
    # label; a dispatched group is never empty.
    driver = _driver("group_by_task")
    with pytest.raises(CodeBugError, match="no pending skill group"):
        driver.resolve({}, [make_task_id(BENCHMARK, "t1")],
                       GroupByTaskParams())


def _retrieval_bank_and_tasks():
    # Accepted members: the retrieval driver ranks over the bank's
    # accepted set only (the phase artifacts carry pending members too).
    bank = [_skill(make_task_id(BENCHMARK, "t1"),
                   "[When to use] The service rejects connections.\n"
                   "Always read the log file first.", status="accepted"),
            _skill(make_task_id(BENCHMARK, "t2"),
                   "[When to use] The config check fails.\n"
                   "Restart only after the config check.",
                   status="accepted")]
    tasks = [Task(task_id="t2", instruction="debug the failing service"),
             Task(task_id="t1", instruction="read the log file")]
    return bank, tasks


def test_retrieval_rows_match_the_selection_rule():
    # The rows are exactly select_skills' per-task output (ground truth
    # recomputed here), one row per task in task-id order -- the order
    # the audit freezes.
    bank, tasks = _retrieval_bank_and_tasks()
    params = RetrievalParams(top_k=1, max_categories=2, min_score=0.0)
    driver = _driver("retrieval")
    rows = driver.resolve(bank, tasks, params)
    assert [row.task_ref for row in rows] == ["t1", "t2"]
    categories = derive_categories(bank)
    for row, task in zip(rows, sorted(tasks, key=lambda t: t.task_id)):
        result = select_skills(task.instruction, bank, categories,
                               top_k=1, max_categories=2, min_score=0.0)
        assert row.skills_injected == [
            units.skill_reference(record) for record in result["selected"]]
        assert row.skills_block == format_skills_block(result["selected"])


def test_retrieval_below_threshold_keeps_the_empty_list_semantics():
    # [] is a retrieval that reached no threshold -- a bare run INSIDE
    # the with-bank arm, never the control's None (both markers stay
    # distinct unit ids too).
    bank, tasks = _retrieval_bank_and_tasks()
    params = RetrievalParams(top_k=2, max_categories=2, min_score=0.99)
    rows = _driver("retrieval").resolve(bank, tasks, params)
    assert all(row.skills_injected == [] and row.skills_block == ""
               for row in rows)
    assert units.unit_id("t1", []) != units.unit_id("t1", None)


def test_retrieval_audit_side_channel():
    # The audit is the side channel (R5): audit_rows recomputes exactly
    # what resolve resolved, one canonical row per task, and the write
    # stays write_audit's job. (The Wave 1 legacy delegation seam was
    # removed with the phase drivers' call-site rewrite, W2-A.)
    bank, tasks = _retrieval_bank_and_tasks()
    params = RetrievalParams(top_k=1, max_categories=2, min_score=0.0)
    driver = _driver("retrieval")
    rows = driver.resolve(bank, tasks, params)
    audit = retrieval.audit_rows(bank, tasks, params)
    assert [row["task_id"] for row in audit] == [row.task_ref
                                                for row in rows]
    assert all(set(row) == {"task_id", "routed", "scores", "selected",
                            "threshold_passed", "categories"}
               for row in audit)


def test_unit_id_digest_rules():
    # The delivery-side join key: deterministic in (task_ref,
    # skills_injected), and the two empty-value semantics never collide.
    assert units.unit_id("t1", None) == units.unit_id("t1", None)
    assert units.unit_id("t1", None) != units.unit_id("t2", None)
    assert units.unit_id("t1", None) != units.unit_id("t1", [])
    assert units.unit_id("t1", None) != units.unit_id(
        "t1", [{"skill_id": "s", "version": 1}])


@pytest.mark.parametrize("name", [
    "none", "whole_bank", "per_skill", "group_by_task", "retrieval",
])
def test_malformed_resolve_calls_fail_loudly(name):
    # Neither convention accepts a half-spelled call; the dispatcher
    # refuses instead of guessing (the params position is the only
    # structural tell, and it is checked, never sniffed).
    driver = _driver(name)
    with pytest.raises(TypeError):
        driver.resolve(1, 2)
    with pytest.raises(TypeError):
        driver.resolve(bank=1, tasks=2, params=3, extra=4)
