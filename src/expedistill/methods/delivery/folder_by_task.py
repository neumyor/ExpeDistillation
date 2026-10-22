"""The folder_by_task delivery driver: one folder per live task group.

The live acceptance phase injects a task's whole pending group.  This
driver keeps that unit boundary and changes only the delivery shape:
each group becomes its own Agent Skills folder, so the model sees the
per-skill descriptions and reads only the skills it needs.  The folder
manifest stays a name mapping; the bodies are read from the group by
``composites.skill_folder`` exactly as the test phase's folder does.

Frozen signature (R5): ``resolve(bank, tasks, params)`` with ``bank``
the pending-group mapping ``{prefixed task id: [Skill, ...]}``, ``tasks``
the prefixed task ids, and ``params`` the ``FolderParams``.  One row per
task, every row carrying that task's own manifest in
``aux["skill_folder"]``.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from expedistill.core.contracts import ExecutionUnitList
from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError
from expedistill.methods.delivery import FolderParams
from expedistill.methods.delivery import units
from expedistill.methods.delivery.folder import skill_folder_manifest

__all__ = ["resolve"]


def resolve(bank: Mapping[str, Sequence[Skill]], tasks: Any,
            params: FolderParams) -> ExecutionUnitList:
    """One row per task: its group references and its own folder manifest."""
    rows = []
    for task in tasks:
        ref = units.task_ref(task)
        members = list(bank.get(ref, []))
        if not members:
            raise CodeBugError(
                f"task {ref!r} has no pending skill group to deliver as a "
                "folder; it must not have been dispatched")
        manifest = skill_folder_manifest(
            members, params.description_mode, accepted_only=False)
        references = [units.skill_reference(skill) for skill in members]
        rows.append(units.make_row(
            ref, references, manifest["catalog_text"],
            {"skill_folder": manifest}))
    return rows
