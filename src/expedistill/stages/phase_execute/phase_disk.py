"""What is on disk: the phase directory, earlier phases, the bank.

Every helper here derives a fact from the run directory alone (the
phase's own directory, the earlier phases' records, the previous learn
phase's bank artifact) or persists the phase's units and skill folder.
They are shared by more than one role, which is why they live in their
own module: the roles (baseline / practice / final_test) and the engine
consume them, none owns them.
"""

from dataclasses import asdict
from pathlib import Path
from typing import Any, List, Optional, Sequence

from expedistill.composites import phase_bank
from expedistill.composites.skill_materialize import materialize_skill_folder
from expedistill.core.config import PhaseInstance
from expedistill.core.contracts import ExecutionUnitRow
from expedistill.core.models import Skill
from expedistill.infra.store import write_jsonl
from expedistill.stages.context import StageContext

__all__ = [
    "PHASE_RECORD_NAME",
    "REDISPATCH_NAME",
    "SKILL_FOLDER_DIR",
    "SUMMARY_NAME",
    "UNITS_NAME",
    "arm_dir",
    "bank_skills",
    "execute_phase_dir",
    "folder_manifest",
    "folder_manifest_of",
    "materialize_folder",
    "write_units",
]

#: The execute-phase layout inside ``<run>/phases/<label>/`` (the
#: package docstring). The learn-phase artifacts beside them are
#: phase_bank's.
PHASE_RECORD_NAME = "phase.json"
UNITS_NAME = "units.jsonl"
REDISPATCH_NAME = "redispatch.json"
SUMMARY_NAME = "phase_summary.json"
SKILL_FOLDER_DIR = "skill_folder"


def execute_phase_dir(ctx: StageContext, phase: PhaseInstance,
                      arm: str = "") -> Path:
    """The phase's directory (phase_bank's validated label mapping)."""
    directory = phase_bank.phase_bank_dir(ctx.run_dir, phase.label)
    return directory / arm if arm else directory


def arm_dir(ctx: StageContext, phase: PhaseInstance, arm: str) -> Path:
    """An arm's directory (store + ledgers), created on first use."""
    directory = execute_phase_dir(ctx, phase, arm)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _load_bank(ctx: StageContext, label: str) -> List[Skill]:
    return [Skill.from_dict(row)
            for row in phase_bank.load_phase_bank(ctx.run_dir, label)]


def _run_level_bank(ctx: StageContext) -> List[Skill]:
    return [Skill.from_dict(row) for row in ctx.store.bank()]


def bank_skills(ctx: StageContext, phase: PhaseInstance) -> List[Skill]:
    """The bank delivery receives: the previous learn phase's artifact
    (R7's cross-phase load), else the run-level frozen bank (before the
    first learn phase there is no artifact to load)."""
    labels = [label for label in phase_bank.bank_phase_labels(ctx.run_dir)
              if phase_bank.phase_number(label) <= phase.index]
    if labels:
        return _load_bank(ctx, labels[-1])
    return _run_level_bank(ctx)


def write_units(ctx: StageContext, phase: PhaseInstance,
                units: Sequence[ExecutionUnitRow]) -> None:
    """Persist the resolved unit list (one resolution per phase; the
    rewrite never grows a second copy of a row). Atomic -- temp file +
    rename (B3's pattern), so a crash mid-write leaves the previous file
    intact instead of a truncated one."""
    write_jsonl(execute_phase_dir(ctx, phase) / UNITS_NAME,
                [asdict(unit) for unit in units])


def folder_manifest_of(row: ExecutionUnitRow) -> Optional[Any]:
    """The row's folder manifest, or None when it carries none (the one
    folder-row predicate: the folder delivery attaches ONE phase-level
    manifest to every row, every other delivery's rows carry none, so
    presence alone is the question -- the engine's attachment, the
    materialization and the read-set pass all ask it here). A manifest
    with no ``files`` is the empty bank's state and is still a folder
    row."""
    return row.aux.get("skill_folder")


def folder_manifest(rows: Sequence[ExecutionUnitRow]) -> Optional[Any]:
    """The phase-level folder manifest of the first row carrying one."""
    for row in rows:
        manifest = folder_manifest_of(row)
        if manifest is not None:
            return manifest
    return None


def materialize_folder(ctx: StageContext, phase: PhaseInstance,
                       bank: Sequence[Skill],
                       rows: Sequence[ExecutionUnitRow]) -> Optional[str]:
    """Materialize the rows' skill folder into the phase directory's
    ``skill_folder/``; returns its path, or None for rows that are not
    folder rows. An empty bank still gets the (empty) directory, so every
    folder unit -- with or without cards -- carries one."""
    manifest = folder_manifest(rows)
    if manifest is None:
        return None
    directory = materialize_skill_folder(
        manifest, bank, execute_phase_dir(ctx, phase) / SKILL_FOLDER_DIR)
    return str(directory)
