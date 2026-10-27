"""Per-phase accumulated weak-model profile artifact."""

from pathlib import Path
from typing import Sequence, Tuple

from expedistill.composites.phase_bank import phase_bank_dir
from expedistill.core.config import LearnPhaseSpec, phase_label
from expedistill.infra.store import read_json, write_json

__all__ = ["PROFILE_NAME", "load_previous_profile", "write_profile"]

PROFILE_NAME = "model_profile.json"


def load_previous_profile(
    run_dir: Path,
    names: Sequence[str],
    specs: dict,
    index: int,
) -> Tuple[str, int]:
    """The latest earlier learn phase's profile; empty text when none."""
    previous = [
        position for position in range(index)
        if isinstance(specs[names[position]], LearnPhaseSpec)
    ]
    if not previous:
        return "", 0
    position = previous[-1]
    path = phase_bank_dir(
        run_dir, phase_label(position, names[position])) / PROFILE_NAME
    if not path.exists():
        return "", 0
    payload = read_json(path)
    text = payload.get("profile_text", "")
    version = payload.get("version", 0)
    if not isinstance(text, str):
        raise ValueError(f"model profile {path} has a non-string profile_text")
    if not isinstance(version, int) or isinstance(version, bool):
        version = 0
    return text, version


def write_profile(
    phase_dir: Path,
    phase_label_value: str,
    profile_text: str,
    source_sheet_ids: Sequence[str],
    version: int,
) -> None:
    """Write the current phase's profile artifact."""
    phase_dir.mkdir(parents=True, exist_ok=True)
    write_json(phase_dir / PROFILE_NAME, {
        "phase_label": phase_label_value,
        "profile_text": profile_text,
        "source_sheet_ids": list(source_sheet_ids),
        "version": version,
    }, indent=2)
