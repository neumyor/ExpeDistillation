"""Shared plumbing of the delivery drivers' execution-unit rows.

R5 (docs/decisions/execute_learn_repair_plan_20261016.md) freezes ONE
driver signature, ``resolve(bank, tasks, params) -> ExecutionUnitList``.
The five drivers differ in what ``bank`` and ``tasks`` mean (each
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
from typing import Any, Dict, List, Optional, Union

from expedistill.composites import unit_ids
from expedistill.core.contracts import ExecutionUnitRow
from expedistill.core.models import Skill

__all__ = ["accepted_bank", "make_row", "skill_reference", "task_ref",
           "unit_id"]


def accepted_bank(bank: Sequence[Any]) -> List[Any]:
    """The bank's accepted members, in the given order.

    The phase artifacts carry every member (accepted and pending), but
    the whole-bank and retrieval drivers inject the ACCEPTED set only:
    that is what the run-level bank held before the phase artifacts
    existed, and the 2026-10-16 review ruling pins the filter to that
    pre-change behaviour. Pending members ride along in the artifact for
    the A and live phases that test them -- never into a whole-bank or
    retrieval injection. Bank rows (mappings) and Skill objects are
    accepted the way ``blocks.retrieve`` accepts them.
    """
    return [member for member in bank
            if (member.get("status", "pending") if isinstance(member, Mapping)
                else member.status) == "accepted"]


def skill_reference(record: Union[Skill, Mapping[str, Any]]) -> Dict[str, Any]:
    """The injection record of one skill: ``{"skill_id", "version"}``.

    The per-task ``skills_injected`` shape (retrieval's decision 13; the
    same record whole-group delivery lists). Bank rows (mappings) and
    Skill objects are accepted the way ``blocks.retrieve`` accepts them.
    """
    if isinstance(record, Mapping):
        return {"skill_id": record["skill_id"],
                "version": record.get("version", 1)}
    return {"skill_id": record.skill_id, "version": record.version}


def task_ref(task: Any) -> str:
    """A unit's task reference: the string itself, or the object's id.

    Delivery's tasks are whatever the phase hands over -- ``Task``
    objects, exam refs (``<skill_id>/vNN/exam``), prefixed task ids -- and
    a row's ``task_ref`` is that task's identity as a string.
    """
    if isinstance(task, str):
        return task
    return task.task_id


def unit_id(ref: str, skills_injected: Any) -> str:
    """The delivery-side unit id: ``<task_ref>:<8 hex>``.

    The digest follows the shared unit-id rule
    (``unit_ids.canonical_skills`` -- the single place that rule lives),
    so this sha8 is the one the full frozen key will carry once the phase
    driver rebuilds it. The two empty-value semantics stay distinct keys
    by construction: ``None`` (a control unit) and ``[]`` (a retrieval
    that reached no threshold) canonicalize differently.
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
