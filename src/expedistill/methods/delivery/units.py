"""Shared plumbing of the delivery drivers' execution-unit rows.

R5 (docs/decisions/execute_learn_repair_plan_20261016.md) freezes ONE
driver signature, ``resolve(bank, tasks, params) -> ExecutionUnitList``.
The five drivers differ in what ``bank`` and ``tasks`` mean (each
module's docstring owns its operands' contract) but build their rows
the same way, so the row plumbing and the Wave 1 call-form dispatch
live here once.

``unit_id`` is the DELIVERY-side view of the join key. The frozen
construction rule (core/contracts.py) needs phase_label, benchmark and
role -- none of which the frozen signature carries -- so delivery fills
the parts it owns: the task ref plus the first eight hex chars of the
sha256 over the canonical form of ``skills_injected``. The
canonicalization rule itself is NOT defined here: it is frozen with the
acceptance module (composites.verdicts.canonical_skills, R1 / P2), and
this helper applies it so the sha8 already matches the one the phase
driver (Wave 2) rebuilds into the full frozen key with
``verdicts.make_unit_id``.
"""

import hashlib
from collections.abc import Mapping
from typing import Any, Dict, Optional, Union

from expedistill.composites import verdicts
from expedistill.core.contracts import ExecutionUnitRow
from expedistill.core.models import Skill

__all__ = ["dispatch", "make_row", "skill_reference", "task_ref",
           "unit_id"]


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
    objects, exam refs (``<subtask_id>/exam``), prefixed task ids -- and
    a row's ``task_ref`` is that task's identity as a string.
    """
    if isinstance(task, str):
        return task
    return task.task_id


def unit_id(ref: str, skills_injected: Any) -> str:
    """The delivery-side unit id: ``<task_ref>:<8 hex>``.

    The digest follows the acceptance module's frozen canonicalization
    (``verdicts.canonical_skills`` -- the single place that rule lives,
    core/contracts.py), so this sha8 is the one the full frozen key will
    carry once the phase driver rebuilds it. The two empty-value
    semantics stay distinct keys by construction: ``None`` (a control
    unit) and ``[]`` (a retrieval that reached no threshold)
    canonicalize differently.
    """
    canonical = verdicts.canonical_skills(skills_injected)
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


def dispatch(params_class: Any, args: tuple, kwargs: dict, legacy: Any,
             resolve_units: Any) -> Any:
    """Route one driver ``resolve`` call to the frozen or the legacy form.

    The registry hands every caller the driver MODULE, and the modules
    whose legacy form still has callers (stage 6, the live driver) serve
    both forms through here until Wave 2 rewires the call sites; a driver
    with no legacy caller (per_skill) exposes only the frozen signature.
    The forms are told apart structurally, never by sniffing operand
    values: in every legacy shape the params object sits in the SECOND
    position, in the frozen shape it sits in the THIRD.
    """
    if kwargs:
        if args or set(kwargs) != {"bank", "tasks", "params"}:
            raise TypeError(
                "resolve(bank, tasks, params) takes no positional "
                "argument next to the keyword form")
        return resolve_units(kwargs["bank"], kwargs["tasks"],
                              kwargs["params"])
    if len(args) in (3, 4) and isinstance(args[1], params_class):
        return legacy(*args)
    if len(args) == 3:
        bank, tasks, params = args
        return resolve_units(bank, tasks, params)
    raise TypeError(
        "resolve expects the frozen signature resolve(bank, tasks, "
        "params); the Wave 1 compat shapes are resolve(ctx, params, "
        "bank, tasks) / resolve(ctx, params, <operand>); got "
        f"{len(args)} positional argument(s)")
