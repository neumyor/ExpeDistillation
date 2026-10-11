"""B4: write-once freezing of the run's inputs and protocol (manifest.json).

The manifest records the frozen identity of the experiment: task data,
split, model role table, weak-model profile, sampling counts, named rules
and all prompt digests (README section 3, stage 0). It is written once when
the run starts; every later start of the same run directory must match it
field by field or the run refuses to continue, because mixing protocols
silently would produce numbers that answer no question (outer AGENTS.md
section 2, the fixed-metric incident).

Adapted from SkillExpand's persistence/io.py ``freeze``: same write-once
semantics, with the differing keys listed in the error so the mismatch can
be diagnosed from the traceback alone. Here it also lives in the run
directory layout (manifest.json at the run root, README section 2).
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from expedistill.infra.reliability import ConfigError
from expedistill.infra.store import read_json, write_json

MANIFEST_NAME = "manifest.json"


class FrozenProtocolChanged(ConfigError):
    """A frozen manifest field differs on resume.

    Continuing would mix two protocols in one run directory; start a new
    run directory instead (AGENTS.md section 7, "修改 prompt": changing one
    word is a new experiment).
    """


def _differing_keys(stored: Any, incoming: Any, prefix: str = "") -> List[str]:
    """Dotted key paths where ``stored`` and ``incoming`` disagree."""
    differing: List[str] = []
    if isinstance(stored, dict) and isinstance(incoming, dict):
        for key in sorted(set(stored) | set(incoming), key=str):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in stored:
                differing.append(f"{path} (not in frozen manifest)")
            elif key not in incoming:
                differing.append(f"{path} (missing from new mapping)")
            else:
                differing.extend(
                    _differing_keys(stored[key], incoming[key], path))
        return differing
    if stored != incoming:
        differing.append(prefix or "(top-level value)")
    return differing


def freeze(run_dir: Path, mapping: Dict[str, Any]) -> None:
    """Write ``mapping`` to manifest.json if absent; otherwise it must match.

    Field-by-field deep comparison, so a one-character prompt digest change
    is caught and named, not absorbed by an aggregate hash.
    """
    path = Path(run_dir) / MANIFEST_NAME
    if not path.exists():
        write_json(path, mapping, indent=2)
        return
    stored = read_json(path)
    if stored != mapping:
        keys = ", ".join(_differing_keys(stored, mapping))
        raise FrozenProtocolChanged(
            f"{path} was frozen with different values; changed keys: "
            f"[{keys}]; use a new run directory for a changed protocol")


def load(run_dir: Path) -> Dict[str, Any]:
    """The frozen manifest of ``run_dir`` (must have been frozen first)."""
    path = Path(run_dir) / MANIFEST_NAME
    if not path.exists():
        raise FileNotFoundError(f"no {MANIFEST_NAME} in {run_dir}")
    return read_json(path)


#: Alias kept for callers that prefer the explicit name.
load_manifest = load


def load_or_none(run_dir: Path) -> Optional[Dict[str, Any]]:
    """The manifest, or None when the run has not been frozen yet."""
    path = Path(run_dir) / MANIFEST_NAME
    if not path.exists():
        return None
    return read_json(path)
