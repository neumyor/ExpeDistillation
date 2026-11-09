"""A learned playbook on disk (layer 5).

The playbook is a learn-phase artifact: ``playbook.md`` sits in the phase
directory beside the bank, written once by the phase that follows the
baseline. Later execute phases read the newest one that precedes them.
"""

import re
from pathlib import Path
from typing import Optional, Sequence, Union

from expedistill.composites import phase_bank
from expedistill.infra.reliability import ConfigError
from expedistill.infra.store import write_json

__all__ = ["PLAYBOOK_NAME", "PLAYBOOK_RECORD_NAME", "load_learned_playbook",
           "playbook_path", "write_learned_playbook"]

PLAYBOOK_NAME = "playbook.md"
#: Where the playbook came from (phase label, facts size), for the reader of
#: the run directory; nothing reads it back.
PLAYBOOK_RECORD_NAME = "playbook.json"

_PHASE_DIR_RE = re.compile(r"p([0-9]+)_(.+)")


def playbook_path(run_dir: Union[str, Path], phase_label: str) -> Path:
    return phase_bank.phase_bank_dir(run_dir, phase_label) / PLAYBOOK_NAME


def write_learned_playbook(run_dir: Union[str, Path], phase_label: str,
                           text: str, *, facts_chars: int,
                           attempts: Optional[Sequence[dict]] = None) -> Path:
    """Write the phase's playbook; ``playbook.md`` lands last so its
    presence means the record beside it is complete.

    ``attempts`` is the write's check history (one entry per attempt: its
    size and the requirements it missed), so a run directory shows whether
    the playbook came out complete first time or needed repair.
    """
    directory = phase_bank.phase_bank_dir(run_dir, phase_label)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / PLAYBOOK_RECORD_NAME, {
        "phase_label": phase_label,
        "chars": len(text),
        "facts_chars": facts_chars,
        "attempts": list(attempts or ()),
    }, indent=2)
    path = directory / PLAYBOOK_NAME
    temp = path.with_suffix(".md.tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)
    return path


def load_learned_playbook(run_dir: Union[str, Path],
                          before_index: int) -> str:
    """The newest learned playbook written by a phase before ``before_index``.

    A missing playbook is a ConfigError: the config asked a delivery for a
    learned playbook, and no earlier learn phase produced one.
    """
    phases_dir = Path(run_dir) / phase_bank.PHASES_DIR
    found = []
    if phases_dir.exists():
        for entry in phases_dir.iterdir():
            match = _PHASE_DIR_RE.fullmatch(entry.name)
            if match is None or int(match.group(1)) - 1 >= before_index:
                continue
            if (entry / PLAYBOOK_NAME).exists():
                found.append((int(match.group(1)), entry / PLAYBOOK_NAME))
    if not found:
        raise ConfigError(
            "playbook_source 'learned' needs a playbook written by an "
            f"earlier learn phase (before phase index {before_index}), but "
            f"none exists under {phases_dir}")
    return max(found)[1].read_text(encoding="utf-8")
