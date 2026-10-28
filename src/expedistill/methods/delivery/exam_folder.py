"""The exam_folder delivery driver: the exam's read-only skill catalog.

The generalization exam (docs/decisions/generalization_exam_20261027.md)
shows the weak model a CATALOG of the whole current bank and asks it to
find and open the right skills itself. This driver is ``folder``'s exam
twin and differs from it in exactly two ways:

* the catalog covers EVERY member of the bank, pending included
  (``accepted_only=False``): the exam asks whether the model can find a
  skill, not whether curation has accepted it yet;
* nothing is materialized. The exam is text-only, so the rows carry the
  catalog text and the manifest as DATA and no ``SKILL.md`` directory is
  ever written. The exam execution reads a skill's body from the phase
  bank when the model asks to open it (bounded by
  ``execution.exam.max_skill_reads``); the folder play's materializer
  (``composites.skill_folder``) is deliberately not involved.

The manifest therefore stays the same lightweight name mapping
``folder.skill_folder_manifest`` builds: no skill content travels with
it, and its ``bank_fingerprint`` is ``bank_reference`` over the full
bank -- the same digest the phase's bank summary writes. ``resolve`` is a
PURE FUNCTION (zero disk), like every delivery driver.

Frozen signature (R5): ``resolve(bank, tasks, params)`` with ``bank``
the phase's full member list (accepted and pending), ``tasks`` the
phase's exam refs (``<skill_id>/vNN/exam``, resolved opaquely through
``units.task_ref``) and ``params`` the ExamFolderParams. One row per
exam ref, every row the same fixed (full-bank digest, catalog) pair.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Union

from expedistill.core.contracts import ExecutionUnitList
from expedistill.core.models import Skill
from expedistill.inject import bank_reference
from expedistill.inject.skill_text import DEFAULT_DESCRIPTION_MODE
from expedistill.methods.delivery import folder, units

__all__ = ["ExamFolderParams", "resolve"]


@dataclass(frozen=True)
class ExamFolderParams:
    """The exam catalog's parameters.

    ``description_mode`` chooses what the catalog's one-line column is
    built from -- the same three modes the ``folder`` delivery offers
    (``summary`` / ``when_to_use`` / ``summary_then_when_to_use``), so
    the exam's catalog and the test/live folder's catalog describe a
    skill with the same words. The validation is the folder catalog's
    own (``folder.validate_description_mode``), never a second copy of
    the allowed set.
    """

    description_mode: str = DEFAULT_DESCRIPTION_MODE

    def __post_init__(self) -> None:
        folder.validate_description_mode(self.description_mode)


def resolve(bank: Sequence[Union[Skill, Mapping[str, Any]]], tasks: Any,
            params: ExamFolderParams) -> ExecutionUnitList:
    """resolve(bank, tasks, params) -> ExecutionUnitList  [frozen, R5]:
    one row per exam ref, input order, every row the same fixed
    (full-bank digest, catalog) pair and the folder manifest in
    ``aux["skill_folder"]`` (phase-level data, shared read-only across
    the rows -- a per-unit consumer must copy before mutating).

    ``skills_injected`` is ``bank_reference(bank)`` over the FULL bank:
    the exam's universe is every member the bank holds, so a pending
    candidate is visible to the model exactly like an accepted one. The
    catalog text is ``skill_folder_manifest(..., accepted_only=False)``'s
    -- one line per member, in bank order, rendered by the same layer-1
    catalog the folder delivery uses.
    """
    manifest = folder.skill_folder_manifest(
        bank, params.description_mode, accepted_only=False)
    injected = bank_reference(bank)
    block = manifest["catalog_text"]
    return [units.make_row(units.task_ref(task), injected, block,
                           {"skill_folder": manifest})
            for task in tasks]
