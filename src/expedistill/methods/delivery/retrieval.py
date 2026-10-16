"""The retrieval delivery driver: per-task top-k selection at test time.

Protocol v2 (docs/decisions/retrieval_delivery_tb_split_20261013.md):
``derive_categories`` runs once over the bank, ``select_skills`` once per
task instruction; the with-bank trajectories record the task's
``[{"skill_id", "version"}]`` selection -- never confused with the bare
control's ``None``. The resolution is audited (one row per test task,
task-id order: routed categories, the full score ranking, the selection,
the gate) to ``stage6_test/retrieval_audit.jsonl``, written BEFORE
dispatch so the audit names the injection this dispatch is about to
render (decision 15).

Frozen signature (R5): ``resolve(bank, tasks, params)`` with ``bank`` the
curated skill bank, ``tasks`` the phase's ``Task`` objects (the
instruction is what the retrieval ranks against), and ``params`` the
RetrievalParams. One row per task, in task-id order; a task below
``min_score`` resolves to a row whose ``skills_injected`` is ``[]`` and
whose block is empty -- a bare run inside the with-bank arm, never
confused with the control's ``None``. The audit stays a SIDE CHANNEL
(R5): ``audit_rows`` recomputes it deterministically, and
``write_audit`` keeps writing it.
"""

from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from expedistill.blocks.retrieve import derive_categories, select_skills
from expedistill.core.contracts import ExecutionUnitList, ExecutionUnitRow
from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError
from expedistill.inject import format_skills_block
from expedistill.methods.delivery import RetrievalParams
from expedistill.methods.delivery import units

__all__ = ["RETRIEVAL_AUDIT_NAME", "audit_rows", "resolve", "write_audit"]

#: The retrieval audit's file name, inside the stage-6 directory (the
#: directory constant is stage6_test's own layout; duplicated here rather
#: than importing the stage module -- methods sit below stages).
RETRIEVAL_AUDIT_NAME = "retrieval_audit.jsonl"
_STAGE6_DIR = "stage6_test"


def _resolve_all(bank: Sequence[Skill], tasks: Sequence[Any],
                 params: RetrievalParams
                 ) -> Tuple[ExecutionUnitList, List[Dict[str, Any]]]:
    """(rows, audit rows): the one retrieval pass both outputs share."""
    categories = derive_categories(bank)
    rows: ExecutionUnitList = []
    audit: List[Dict[str, Any]] = []
    for task in sorted(tasks, key=units.task_ref):
        ref = units.task_ref(task)
        result = select_skills(
            task.instruction, bank, categories,
            top_k=params.top_k,
            max_categories=params.max_categories,
            min_score=params.min_score)
        selected = [units.skill_reference(record)
                   for record in result["selected"]]
        audit.append({
            "task_id": ref,
            "routed": result["routed"],
            "scores": result["scores"],
            "selected": selected,
            "threshold_passed": result["threshold_passed"],
            "categories": categories,
        })
        rows.append(units.make_row(
            ref, selected, format_skills_block(result["selected"])))
    return rows, audit


def _resolve(bank: Sequence[Skill], tasks: Sequence[Any],
             params: RetrievalParams) -> ExecutionUnitList:
    """One row per task, task-id order (the audit's order)."""
    return _resolve_all(bank, tasks, params)[0]


def audit_rows(bank: Sequence[Skill], tasks: Sequence[Any],
               params: RetrievalParams) -> List[Dict[str, Any]]:
    """The retrieval audit: one canonical row per task, task-id order.

    Deterministic recomputation of what ``resolve`` resolved -- the side
    channel R5 keeps: the unit list alone never carries the audit, and
    the write stays ``write_audit``'s job.
    """
    return _resolve_all(bank, tasks, params)[1]


def _legacy_resolve(ctx: Any, params: RetrievalParams, bank: Sequence[Skill],
                    test_tasks: Sequence[Any]
                    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(K1 with-bank kwargs, audit rows): the per-task resolver.

    The returned resolver only READS the finished rows, so K1's
    per-unit lookups (one per task and attempt) never re-retrieve.
    """
    rows, audit = _resolve_all(bank, test_tasks, params)
    by_ref = {row.task_ref: row for row in rows}

    def skills_for_task(task: Any) -> Tuple[Any, str]:
        try:
            row: ExecutionUnitRow = by_ref[task.task_id]
        except KeyError:
            raise CodeBugError(
                f"task {task.task_id!r} has no retrieval resolution: the "
                "per-task mapping was built over a different task set"
            ) from None
        return row.skills_injected, row.skills_block

    return {"skills_for_task": skills_for_task}, audit


def resolve(*args: Any, **kwargs: Any) -> ExecutionUnitList:
    """resolve(bank, tasks, params) -> ExecutionUnitList  [frozen, R5].

    Wave 1 compat (removed in Wave 2, when the phase drivers replace the
    call sites): the module seam also accepts stage 6's legacy
    ``resolve(ctx, params, bank, test_tasks)`` and returns the K1
    per-task resolver plus the audit rows.
    """
    return units.dispatch(RetrievalParams, args, kwargs,
                          _legacy_resolve, _resolve)


def write_audit(run_dir: Path, rows: Sequence[Dict[str, Any]]) -> None:
    """Overwrite the retrieval audit with one canonical JSON line per task.

    A full rewrite on every call (decision 15): the file always mirrors the
    current resolution and can never accumulate a stale row from an
    earlier dispatch. Atomic (temp file + rename, B3's pattern) so a crash
    mid-write leaves the previous file intact instead of a torn ledger.
    """
    from expedistill.infra.store import write_jsonl

    write_jsonl(Path(run_dir) / _STAGE6_DIR / RETRIEVAL_AUDIT_NAME, rows)
