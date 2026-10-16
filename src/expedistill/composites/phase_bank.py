"""Per-phase skill bank artifacts (R7): the layout and the loading side.

Decision doc: docs/decisions/execute_learn_repair_plan_20261016.md, item
R7 (problem G9: the bank used to be produced once by stage 5 and read
implicitly by stage 6 -- a learn phase ended with no artifact of its
own). Every learn phase now ends with an explicit bank artifact on disk,
in the shape the next phase loads directly: the same skill-record rows
``skill_bank.jsonl`` has always carried, full skill dicts, so each
member's status and version travel with the row (delivery receives its
member versions from this artifact, plan item R5).

The five object stores stay run-level and shared (hard constraint 5);
only this artifact is per-phase:

    <run_dir>/phases/<phase_label>/skill_bank.jsonl    the bank rows
    <run_dir>/phases/<phase_label>/bank_summary.json   members/size/digest
    <run_dir>/phases/<phase_label>/curation_log.json   K5 journal (agentbrew)

``phase_label`` is the phase driver's label (plan R9: ``pNN:<name>``).
The colon is the one non-directory character allowed and maps to ``_``
(the R9 phase-directory naming); every other character must already be
directory-safe, so a label can never escape its phase directory.

This module owns the layout and the LOADING side only. The producing
side is the curation drivers (``methods/curation/``): ``agentbrew``
merges with its merge_check regression exams (K3, through
composites.curation's dest mode), ``none`` passes the accepted set
through unchanged, and the sequence-position branch (curation runs only
at the sequence's last learn phase; the intermediate phases produce the
as-is artifact) belongs to the phase driver (Wave 2, plan R9).
"""

from pathlib import Path
from typing import Any, Dict, List, Union

from expedistill.infra.reliability import ConfigError
from expedistill.infra.store import read_json, read_jsonl

__all__ = [
    "BANK_NAME",
    "PHASES_DIR",
    "SUMMARY_NAME",
    "load_phase_bank",
    "load_phase_bank_summary",
    "phase_bank_dir",
    "phase_bank_path",
    "phase_bank_summary_path",
]

#: The per-phase tree inside the run directory (R9's phase layout).
PHASES_DIR = "phases"
#: The bank artifact: same rows, same name as the run-level bank.
BANK_NAME = "skill_bank.jsonl"
#: The small digest file the drivers write beside the artifact.
SUMMARY_NAME = "bank_summary.json"

#: R9's ``pNN:<name>`` colon maps to the directory separator character.
_COLON = str.maketrans({":": "_"})
_SAFE = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def phase_bank_dir(run_dir: Union[str, Path], phase_label: str) -> Path:
    """The phase's directory: ``<run>/phases/<label, colon mapped to _>``.

    A label with characters outside the directory-safe set (or a name a
    path would treat specially, like ``..``) is refused with ConfigError
    rather than silently mangled: a label that cannot name a directory is
    a construction bug in the caller, and a quiet rewrite could collide
    two phases into one directory.
    """
    name = phase_label.translate(_COLON)
    if (not name or name.startswith(".")
            or any(ch not in _SAFE for ch in name)):
        raise ConfigError(
            f"phase label {phase_label!r} is not a directory-safe phase "
            "label; allowed are letters, digits, '.', '_', '-', and the "
            "R9 'pNN:<name>' colon")
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
