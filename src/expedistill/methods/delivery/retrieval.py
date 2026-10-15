"""The retrieval delivery driver: per-task top-k selection at test time.

Protocol v2 (docs/decisions/retrieval_delivery_tb_split_20261013.md):
``derive_categories`` runs once over the bank, ``select_skills`` once per
task instruction; K1 then receives the per-task resolver and the
trajectories record the task's ``[{"skill_id", "version"}]`` selection
-- never confused with the bare control's ``None``. The resolution is
audited (one row per test task, task-id order: routed categories, the
full score ranking, the selection, the gate) to
``stage6_test/retrieval_audit.jsonl``, written BEFORE dispatch so the
audit names the injection this dispatch is about to render (decision 15).
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from expedistill.blocks.retrieve import derive_categories, select_skills
from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError
from expedistill.inject import format_skills_block
from expedistill.methods.delivery import RetrievalParams

__all__ = ["RETRIEVAL_AUDIT_NAME", "resolve"]

#: The retrieval audit's file name, inside the stage-6 directory (the
#: directory constant is stage6_test's own layout; duplicated here rather
#: than importing the stage module -- methods sit below stages).
RETRIEVAL_AUDIT_NAME = "retrieval_audit.jsonl"
_STAGE6_DIR = "stage6_test"


def _selected_references(selected: Sequence[Any]) -> List[Dict[str, Any]]:
    """The injection record of each selected skill: id and version.

    The per-task ``skills_injected`` shape (decision 13: a NEW shape -- the
    field is ``Any`` and carried the bank digest until now):
    ``[{"skill_id": ..., "version": ...}, ...]``, in selection order. The
    selected records are bank rows (mappings) or Skill objects; both are
    accepted the way ``blocks.retrieve`` accepts them.
    """
    references: List[Dict[str, Any]] = []
    for record in selected:
        if isinstance(record, Mapping):
            references.append(
                {"skill_id": record["skill_id"],
                 "version": record.get("version", 1)})
        else:
            references.append(
                {"skill_id": record.skill_id, "version": record.version})
    return references


def resolve(ctx: Any, params: RetrievalParams, bank: Sequence[Skill],
            test_tasks: Sequence[Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Resolve every test task's retrieval injection once (decision 3).

    Returns (K1 with-bank kwargs, audit rows). The returned resolver only
    READS the finished mapping, so K1's per-unit lookups (one per task
    and attempt) never re-retrieve. A task below ``min_score`` resolves
    to ``([], "")``: a bare run whose ``skills_injected`` is ``[]``, never
    confused with the control's ``None``.
    """
    categories = derive_categories(bank)
    resolved: Dict[str, Tuple[Any, str]] = {}
    rows: List[Dict[str, Any]] = []
    for task in sorted(test_tasks, key=lambda t: t.task_id):
        result = select_skills(
            task.instruction, bank, categories,
            top_k=params.top_k,
            max_categories=params.max_categories,
            min_score=params.min_score)
        selected = _selected_references(result["selected"])
        resolved[task.task_id] = (
            selected, format_skills_block(result["selected"]))
        rows.append({
            "task_id": task.task_id,
            "routed": result["routed"],
            "scores": result["scores"],
            "selected": selected,
            "threshold_passed": result["threshold_passed"],
            "categories": categories,
        })

    def skills_for_task(task: Any) -> Tuple[Any, str]:
        try:
            return resolved[task.task_id]
        except KeyError:
            raise CodeBugError(
                f"task {task.task_id!r} has no retrieval resolution: the "
                "per-task mapping was built over a different task set"
            ) from None

    return {"skills_for_task": skills_for_task}, rows


def write_audit(run_dir: Path, rows: Sequence[Dict[str, Any]]) -> None:
    """Overwrite the retrieval audit with one canonical JSON line per task.

    A full rewrite on every call (decision 15): the file always mirrors the
    current resolution and can never accumulate a stale row from an
    earlier dispatch. Atomic (temp file + rename, B3's pattern) so a crash
    mid-write leaves the previous file intact instead of a torn ledger.
    """
    import json
    import os
    import tempfile

    path = Path(run_dir) / _STAGE6_DIR / RETRIEVAL_AUDIT_NAME
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
