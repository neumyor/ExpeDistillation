"""Per-phase skill bank artifacts: the layout, the writing and the loading.

Every learn phase ends with an explicit bank artifact on disk, in the shape
the next phase loads directly: full skill dicts, so each member's status
and version travel with the row (delivery hands out the ``active``
members only):

    <run_dir>/phases/<phase_label>/skill_bank.jsonl    the bank rows
    <run_dir>/phases/<phase_label>/bank_summary.json   members/size/digest

The object stores stay run-level and shared; only this artifact is
per-phase. The artifact carries EVERY stored skill, ``discarded`` ones
included, in skill-id order (:func:`write_phase_bank`).

``phase_label`` is the phase driver's label (``pNN:<name>`` -- exactly one
colon, right after the ``pNN`` head). The colon is the one non-directory
character allowed and maps to ``_``; on the frozen label shape that rewrite
is injective, and every other character must already be directory-safe, so
a label can never escape its phase directory nor collide with another
label's.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Union

from expedistill.core.models import Skill
from expedistill.infra.reliability import ConfigError
from expedistill.infra.store import (
    Store, read_json, read_jsonl, write_json, write_jsonl)
from expedistill.inject import bank_reference

__all__ = [
    "BANK_NAME",
    "PHASES_DIR",
    "SUMMARY_NAME",
    "load_phase_bank",
    "load_phase_bank_summary",
    "phase_bank_dir",
    "phase_bank_path",
    "phase_bank_summary_path",
    "write_phase_bank",
]

#: The per-phase tree inside the run directory (R9's phase layout).
PHASES_DIR = "phases"
#: The bank artifact: same rows, same name as the run-level bank.
BANK_NAME = "skill_bank.jsonl"
#: The small digest file the drivers write beside the artifact.
SUMMARY_NAME = "bank_summary.json"

#: R9's ``pNN:<name>`` colon maps to the directory separator character.
_COLON = str.maketrans({":": "_"})

#: The frozen label shape (R9): ``pNN:<name>`` with EXACTLY ONE colon,
#: right after the ``pNN`` head, and a name over the directory-safe
#: alphabet. The colon->underscore rewrite is injective on this shape,
#: so two labels can never collide on one directory (``p1:a`` and the
#: colonless ``p1_a`` are different labels, and only the first is
#: legal -- D3).
_PHASE_LABEL_RE = re.compile(r"p[0-9]+:[A-Za-z0-9._-]+")


def phase_bank_dir(run_dir: Union[str, Path], phase_label: str) -> Path:
    """The phase's directory: ``<run>/phases/<label, colon mapped to _>``.

    A label outside the frozen ``pNN:<name>`` shape (wrong head, no
    colon, more than one colon, or a name with characters outside the
    directory-safe set) is refused with ConfigError rather than silently
    mangled: a label that cannot name a directory is a construction bug
    in the caller, and a quiet rewrite could collide two phases into one
    directory.
    """
    if not _PHASE_LABEL_RE.fullmatch(phase_label):
        raise ConfigError(
            f"phase label {phase_label!r} is not a 'pNN:<name>' phase "
            "label (R9: exactly one colon, right after the pNN head; "
            "allowed name characters are letters, digits, '.', '_', '-')")
    name = phase_label.translate(_COLON)
    return Path(run_dir) / PHASES_DIR / name


def phase_bank_path(run_dir: Union[str, Path],
                    phase_label: str) -> Path:
    """The phase's bank artifact (skill_bank.jsonl rows)."""
    return phase_bank_dir(run_dir, phase_label) / BANK_NAME


def phase_bank_summary_path(run_dir: Union[str, Path],
                            phase_label: str) -> Path:
    """The phase's bank summary (members, size, bank_reference)."""
    return phase_bank_dir(run_dir, phase_label) / SUMMARY_NAME


def load_phase_bank(run_dir: Union[str, Path],
                    phase_label: str) -> List[Dict[str, Any]]:
    """Read a learn phase's bank artifact; a missing one is a ConfigError.

    The rows are the full skill dicts the run-level bank carries, so the
    caller (the next phase's delivery, plan R5) reads the members, their
    statuses and their versions straight from them.
    """
    path = phase_bank_path(run_dir, phase_label)
    if not path.exists():
        raise ConfigError(
            f"learn phase {phase_label!r} has no skill bank artifact at "
            f"{path}; the phase did not finish (or never ran) -- a human "
            "must look at this run")
    return read_jsonl(path)


def load_phase_bank_summary(run_dir: Union[str, Path],
                            phase_label: str) -> Dict[str, Any]:
    """The phase's bank summary (members/size/bank_reference); missing is
    a ConfigError naming the phase (same contract as load_phase_bank)."""
    path = phase_bank_summary_path(run_dir, phase_label)
    if not path.exists():
        raise ConfigError(
            f"learn phase {phase_label!r} has no bank summary at {path}; "
            "the phase did not finish (or never ran) -- a human must look "
            "at this run")
    return read_json(path)


def write_phase_bank(store: Store, run_dir: Union[str, Path],
                     phase_label: str) -> Dict[str, Any]:
    """Write the learn phase's bank artifact and its summary; return the
    summary (``members`` / ``size`` / ``bank_reference``).

    The rows are every stored skill, ``discarded`` included, in skill-id
    order; both files are rewritten whole and atomically, so a repeated
    call over the same store lands on exactly the same bytes.
    """
    skills: List[Skill] = [store.load("skills", skill_id, Skill)
                           for skill_id in store.ids("skills")]
    directory = phase_bank_dir(run_dir, phase_label)
    directory.mkdir(parents=True, exist_ok=True)
    write_jsonl(directory / BANK_NAME, [skill.to_dict() for skill in skills])
    summary = {
        "members": [skill.skill_id for skill in skills],
        "size": len(skills),
        "bank_reference": bank_reference(skills),
    }
    write_json(phase_bank_summary_path(run_dir, phase_label), summary,
               indent=2)
    return summary
