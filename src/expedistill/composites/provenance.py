"""Per-phase provenance ledger: one row per skill version, one write door.

    <run_dir>/phases/<phase_label>/provenance.jsonl

Each row is a :class:`~expedistill.core.trace.Provenance`; its ``sources``
are exact ``TraceRef`` values: the evidence positions behind the version
(a new card's material segment starts and the failing steps of its gap
evidence; a rewrite's failing steps).

* :func:`record_version` -- the only writer. It appends the provenance row
  first and saves the skill object second, so a stored version can never
  exist without a recorded origin; the first row for a
  ``(skill_id, version)`` wins and a re-run only re-saves the skill.
  Every source must resolve to a stored execution before anything is
  written.
* :func:`find_provenance` -- the only reader, scanning phases newest-first.
* :func:`check_provenance` -- the boundary invariant a learn phase checks
  before it publishes its bank: every version has a row, with at least one
  source, and every source resolves.

Writes go through ``infra.store.append_jsonl`` (torn tails repaired).
"""

import re
from pathlib import Path
from typing import Iterable, List, Optional, Union

from expedistill.composites import trace
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
    """Record where this version came from, then save ``skill``.

    The skill and the row must describe the same version, and the row must
    name at least one source. Every source has to resolve to a stored
    execution before the first byte is written; a row that points nowhere
    is refused, so the ledger never holds a pointer a later reader cannot
    follow. The row goes down before the skill: a crash between the two
    leaves a row the replay finds, and the replay saves the skill under it.
    """
    if (provenance.skill_id, provenance.version) != (
            skill.skill_id, skill.version):
        raise CodeBugError(
            f"provenance {provenance.skill_id!r} v{provenance.version} does "
            f"not describe the skill {skill.skill_id!r} v{skill.version}")
    if not provenance.sources:
        raise CodeBugError(
            f"provenance of {skill.skill_id!r} v{skill.version} names no "
            "source: every version comes from evidence")
    for ref in provenance.sources:
        trace.resolve(run_dir, ref)
    phase_dir = Path(phase_dir)
    path = phase_dir / PROVENANCE_NAME
    for row in read_jsonl(path):
        if (row.get("skill_id") == provenance.skill_id
                and row.get("version") == provenance.version):
            store.save(skill, "skills")
            return
    phase_dir.mkdir(parents=True, exist_ok=True)
    append_jsonl(path, provenance.to_dict())
    store.save(skill, "skills")


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
    """The boundary invariant, as a list of violations (empty = clean):
    every skill version has a provenance row with at least one source, and
    every source resolves to a stored execution."""
    problems: List[str] = []
    for skill in skills:
        label = f"{skill.skill_id} v{skill.version}"
        row = find_provenance(run_dir, skill.skill_id, skill.version)
        if row is None:
            problems.append(f"{label}: no provenance row")
            continue
        if not row.sources:
            problems.append(f"{label}: the provenance row names no source")
        for ref in row.sources:
            try:
                trace.resolve(run_dir, ref)
            except (CodeBugError, ModelOutputError) as exc:
                problems.append(f"{label}: {exc}")
    return problems
