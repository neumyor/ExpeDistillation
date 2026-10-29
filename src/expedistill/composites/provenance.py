"""Per-phase provenance ledger: one row per skill version, one write door.

    <run_dir>/phases/<phase_label>/provenance.jsonl

Each row is a :class:`~expedistill.core.trace.Provenance`; its locations
are exact ``TraceRef`` values, so an exam is cut from the very execution a
skill version was written from.

* :func:`record_version` -- the only writer. It saves the skill object and
  appends the provenance row together, so a version can never exist without
  a recorded origin; the first row for a ``(skill_id, version)`` wins and a
  re-run is a no-op. Provenance is validated against the stored sheets
  before anything is written (the source must resolve).
* :func:`find_provenance` -- the only reader, scanning phases newest-first.
* :func:`check_provenance` -- the boundary invariant a phase end and an
  exam start run: every version has a row, its scene resolves and is a real
  execution (never an exam answer), and an ``add`` version's scene is cut
  from its own anchor trajectory.

Writes go through ``infra.store.append_jsonl`` (torn tails repaired).
"""

import re
from pathlib import Path
from typing import Iterable, List, Optional, Union

from expedistill.composites import trace
from expedistill.core import ids
from expedistill.core.models import Skill
from expedistill.core.trace import Provenance
from expedistill.infra.reliability import CodeBugError, ModelOutputError
from expedistill.infra.store import Store, append_jsonl, read_jsonl

__all__ = [
    "PHASES_DIR",
    "PROVENANCE_NAME",
    "check_provenance",
    "find_provenance",
    "record_version",
]

PHASES_DIR = "phases"
PROVENANCE_NAME = "provenance.jsonl"

_PHASE_DIR_NUMBER_RE = re.compile(r"^p([0-9]+)_")


def _phase_dir_number(path: Path) -> int:
    match = _PHASE_DIR_NUMBER_RE.match(path.parent.name)
    return int(match.group(1)) if match else -1


def record_version(store: Store, phase_dir: Union[str, Path], run_dir:
                   Union[str, Path], skill: Skill,
                   provenance: Provenance) -> None:
    """Save ``skill`` and record where this version came from.

    The skill and the row must describe the same version. Every location in
    the provenance has to resolve to a stored real execution before the
    first byte is written; a row that points nowhere is refused, so the
    ledger never holds a pointer a later phase cannot follow. A version
    already recorded in this phase is left as it was (replay-safe).
    """
    if (provenance.skill_id, provenance.version) != (
            skill.skill_id, skill.version):
        raise CodeBugError(
            f"provenance {provenance.skill_id!r} v{provenance.version} does "
            f"not describe the skill {skill.skill_id!r} v{skill.version}")
    for ref in ([provenance.scene] if provenance.scene else []) \
            + list(provenance.sources):
        trace.resolve(run_dir, ref)
    phase_dir = Path(phase_dir)
    path = phase_dir / PROVENANCE_NAME
    for row in read_jsonl(path):
        if (row.get("skill_id") == provenance.skill_id
                and row.get("version") == provenance.version):
            store.save(skill, "skills")
            return
    store.save(skill, "skills")
    phase_dir.mkdir(parents=True, exist_ok=True)
    append_jsonl(path, provenance.to_dict())


def find_provenance(run_dir: Union[str, Path], skill_id: str,
                    version: int) -> Optional[Provenance]:
    """The recorded origin of ``(skill_id, version)``, newest phase first."""
    paths = sorted(
        (Path(run_dir) / PHASES_DIR).glob(f"*/{PROVENANCE_NAME}"),
        key=_phase_dir_number, reverse=True)
    for path in paths:
        for row in read_jsonl(path):
            if (row.get("skill_id") == skill_id
                    and row.get("version") == version):
                return Provenance.from_dict(row)
    return None


def check_provenance(run_dir: Union[str, Path],
                     skills: Iterable[Skill]) -> List[str]:
    """The boundary invariant, as a list of violations (empty = clean).

    For every non-merge skill: a provenance row exists; each location in it
    resolves; the scene is a real execution; and an ``add`` version's scene
    sheet ran the skill's own anchor trajectory. Merge products are exempt
    (they are not examined).
    """
    problems: List[str] = []
    for skill in skills:
        if skill.skill_id.startswith("bank/merge/"):
            continue
        label = f"{skill.skill_id} v{skill.version}"
        row = find_provenance(run_dir, skill.skill_id, skill.version)
        if row is None:
            problems.append(f"{label}: no provenance row")
            continue
        for ref in ([row.scene] if row.scene else []) + list(row.sources):
            try:
                trace.resolve(run_dir, ref)
            except (CodeBugError, ModelOutputError) as exc:
                problems.append(f"{label}: {exc}")
        if row.cause == "add" and row.scene is not None:
            try:
                sheet = trace.load_sheet(run_dir, row.scene)
                got = (sheet.execution.artifacts or {}).get("trajectory_id")
                want = ids.parse_subtask_id(skill.subtask_id)[0]
            except (ModelOutputError, ValueError) as exc:
                problems.append(f"{label}: {exc}")
                continue
            if got != want:
                problems.append(
                    f"{label}: scene cut from {got!r}, but the skill's "
                    f"anchor trajectory is {want!r}")
    return problems
