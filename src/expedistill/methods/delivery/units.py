"""Shared plumbing of the delivery drivers' execution-unit rows.

R5 (docs/decisions/execute_learn_repair_plan_20261016.md) freezes ONE
driver signature, ``resolve(bank, tasks, params) -> ExecutionUnitList``.
The drivers differ in what ``bank`` and ``tasks`` mean (each
module's docstring owns its operands' contract) but build their rows
the same way, so the row plumbing lives here once. The Wave 1 call-form
dispatch is gone: the phase drivers (stages/phase_execute.py) call the
frozen form only.

``unit_id`` is the DELIVERY-side view of the join key. The frozen
construction rule (core/contracts.py) needs phase_label, benchmark and
role -- none of which the frozen signature carries -- so delivery fills
the parts it owns: the task ref plus the first eight hex chars of the
sha256 over the canonical form of ``skills_injected``. The
canonicalization rule itself is NOT defined here: it lives in
``composites/unit_ids.py`` (R1 / P2), and this helper applies it so the
sha8 already matches the one the phase driver rebuilds into the full
frozen key with ``unit_ids.make_unit_id``.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Dict, List, Optional

from expedistill.composites import unit_ids
from expedistill.core.contracts import ExecutionUnitRow
from expedistill.inject import format_playbook_block

__all__ = ["active_bank", "make_row", "task_ref", "unit_id",
           "with_playbook"]


def active_bank(bank: Sequence[Any]) -> List[Any]:
    """The bank's active members, in the given order.

    The phase artifacts carry every member, discarded ones included, but
    the whole-bank and folder drivers deliver the ACTIVE cards only. Bank
    rows (mappings) and Skill objects both work; a row without a
    status is active (the Skill default).
    """
    return [member for member in bank
            if (member.get("status", "active") if isinstance(member, Mapping)
                else member.status) == "active"]


def task_ref(task: Any) -> str:
    """A unit's task reference: the string itself, or the object's id.

    Delivery's tasks are whatever the phase hands over -- ``Task``
    objects or prefixed task ids -- and a row's ``task_ref`` is that
    task's identity as a string.
    """
    if isinstance(task, str):
        return task
    return task.task_id


def unit_id(ref: str, skills_injected: Any) -> str:
    """The delivery-side unit id: ``<task_ref>:<8 hex>``.

    The digest follows the shared unit-id rule
    (``unit_ids.canonical_skills`` -- the single place that rule lives),
    so this sha8 is the one the full frozen key will carry once the phase
    driver rebuilds it.
    """
    canonical = unit_ids.canonical_skills(skills_injected)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
    return f"{ref}:{digest}"


def make_row(ref: str, skills_injected: Any, skills_block: str,
             aux: Optional[Dict[str, Any]] = None) -> ExecutionUnitRow:
    """One execution-unit row, its id derived from (ref, skills)."""
    return ExecutionUnitRow(
        unit_id=unit_id(ref, skills_injected),
        task_ref=ref,
        skills_injected=skills_injected,
        skills_block=skills_block,
        aux={} if aux is None else aux,
    )


def with_playbook(rows: Sequence[ExecutionUnitRow],
                  playbook: str) -> List[ExecutionUnitRow]:
    """The rows with the phase's playbook shown above their skills.

    Every row's block starts with the playbook block; a folder row's
    manifest also carries it as ``playbook_text``, the field the folder
    runners render it from. An empty playbook leaves the rows as they
    are.
    """
    head = format_playbook_block(playbook)
    if not head:
        return list(rows)
    out: List[ExecutionUnitRow] = []
    for row in rows:
        aux = dict(row.aux)
        folder = aux.get("skill_folder")
        if isinstance(folder, Mapping):
            aux["skill_folder"] = {**folder,
                                   "playbook_text": playbook.strip()}
        out.append(replace(row, skills_block=head + row.skills_block,
                           aux=aux))
    return out
