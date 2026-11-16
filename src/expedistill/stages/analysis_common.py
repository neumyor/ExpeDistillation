"""Shared plumbing of the read-only analyses over a finished run (layer 7).

``judge-skills`` reads a finished run and writes its own results elsewhere.
Two rules keep it honest, and both live here so every analysis that writes
rows applies them the same way:

* the output directory is never inside the run it reads (the run directory
  stays byte-for-byte what the experiment left);
* the settings that decide what a stored row means (which run, which phase,
  which model judged) are pinned in ``analysis_config.json`` on the first
  start, and a resume with different settings is refused -- rows from two
  judge models in one file would be a result nobody ran.

The second helper (used by ``segmentation-metrics`` too) finds which phases
left a skill bank artifact: the bank a phase received is the latest artifact
of an earlier phase.
"""

import json
from pathlib import Path
from typing import Any, Mapping, Union

from expedistill.infra.reliability import ConfigError
from expedistill.infra.store import read_json, write_json

__all__ = ["PINNED_NAME", "prepare_out_dir"]

PINNED_NAME = "analysis_config.json"


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
