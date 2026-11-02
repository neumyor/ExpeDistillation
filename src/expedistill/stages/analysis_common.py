"""Shared plumbing of the read-only analyses over a finished run (layer 7).

``judge-skills`` and ``exam-replay`` read a finished run and write their own
results elsewhere. Two rules keep them honest, and both live here so the two
stages cannot drift:

* the output directory is never inside the run it reads (the run directory
  stays byte-for-byte what the experiment left);
* the settings that decide what a stored row means (which run, which phase,
  which model answered or judged) are pinned in ``analysis_config.json`` on
  the first start, and a resume with different settings is refused -- rows
  from two answering models in one file would be a result nobody ran.

The second helper finds which phases left a skill bank artifact: the bank a
phase received is the latest artifact of an earlier phase.
"""

import json
import re
from pathlib import Path
from typing import Any, List, Mapping, Union

from expedistill.composites import phase_bank
from expedistill.infra.reliability import ConfigError
from expedistill.infra.store import read_json, write_json

__all__ = ["PINNED_NAME", "bank_phase_labels", "phase_number",
           "prepare_out_dir"]

PINNED_NAME = "analysis_config.json"

_PHASE_DIR_RE = re.compile(r"p([0-9]+)_(.+)")
_PHASE_LABEL_RE = re.compile(r"p([0-9]+):.+")


def prepare_out_dir(run_dir: Union[str, Path], out_dir: Union[str, Path],
                    pinned: Mapping[str, Any]) -> Path:
    """Create ``out_dir`` and pin ``pinned`` in it; returns the directory.

    ``pinned`` holds JSON-safe values. A directory that already pins other
    values, or one inside ``run_dir``, is a ConfigError.
    """
    run = Path(run_dir).resolve()
    out = Path(out_dir).resolve()
    if out == run or run in out.parents:
        raise ConfigError(
            f"the output directory {out} is inside the run directory "
            f"{run}; an analysis never writes into the run it reads")
    out.mkdir(parents=True, exist_ok=True)
    wanted = json.loads(json.dumps(dict(pinned)))
    path = out / PINNED_NAME
    if path.exists():
        stored = read_json(path)
        if stored != wanted:
            raise ConfigError(
                f"{path} was written for {stored}, but this call asks for "
                f"{wanted}; use a fresh output directory for different "
                "settings")
    else:
        write_json(path, wanted, indent=2)
    return out


def phase_number(phase_label: str) -> int:
    """The ``NN`` of a ``pNN:<name>`` phase label."""
    match = _PHASE_LABEL_RE.fullmatch(phase_label)
    if match is None:
        raise ConfigError(
            f"{phase_label!r} is not a 'pNN:<name>' phase label")
    return int(match.group(1))


def bank_phase_labels(run_dir: Union[str, Path]) -> List[str]:
    """The labels of the phases that left a bank artifact, oldest first.

    A phase counts by the artifact's presence (the same signal the phase
    drivers use to find the bank an execute phase receives).
    """
    phases_dir = Path(run_dir) / phase_bank.PHASES_DIR
    if not phases_dir.is_dir():
        return []
    found = []
    for entry in phases_dir.iterdir():
        match = _PHASE_DIR_RE.fullmatch(entry.name)
        if match is not None and (entry / phase_bank.BANK_NAME).exists():
            found.append((int(match.group(1)),
                          f"p{match.group(1)}:{match.group(2)}"))
    return [label for _, label in sorted(found)]
