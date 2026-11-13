"""The two evolving texts on disk: one version per learn phase (layer 5).

    <run_dir>/phases/<learn phase label>/profile.json
    <run_dir>/phases/<learn phase label>/playbook.json

Each file is one :class:`~expedistill.core.models.EvolvingText`. Every
learn phase writes both before its commit point, so a reader takes the
version of the learn phase immediately before it -- never an older one --
and a missing file there is a broken run, not an empty text.

``check_playbook_capability`` is the one rule for where an execute phase
can show the playbook (stage 0 and the phase drivers both call it).
"""

from pathlib import Path
from typing import Mapping, Optional, Sequence, Union

from expedistill.benchmarks.registry import resolve_plugin
from expedistill.composites.phase_bank import phase_bank_dir
from expedistill.core.config import (
    FOLDER_DELIVERY_NAME,
    PLAYBOOK_EVOLVED,
    ExecutePhaseSpec,
    LearnPhaseSpec,
    phase_label,
)
from expedistill.core.models import EVOLVING_TEXT_KINDS, EvolvingText
from expedistill.infra.reliability import CodeBugError, ConfigError
from expedistill.infra.store import read_json, write_json

__all__ = [
    "PLAYBOOK",
    "PROFILE",
    "check_playbook_capability",
    "load_previous",
    "previous_learn_label",
    "read_text",
    "text_path",
    "write_text",
]

PROFILE = "profile"
PLAYBOOK = "playbook"


def text_path(run_dir: Union[str, Path], label: str, kind: str) -> Path:
    """Where the learn phase ``label`` keeps its ``kind`` text."""
    if kind not in EVOLVING_TEXT_KINDS:
        raise CodeBugError(
            f"unknown evolving text kind {kind!r}; known: "
            f"{list(EVOLVING_TEXT_KINDS)}")
    return phase_bank_dir(run_dir, label) / f"{kind}.json"


def write_text(run_dir: Union[str, Path], item: EvolvingText) -> None:
    """Write one version (atomic)."""
    path = text_path(run_dir, item.phase_label, item.kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, item.to_dict(), indent=2)


def read_text(run_dir: Union[str, Path], label: str,
              kind: str) -> Optional[EvolvingText]:
    """The version the learn phase ``label`` wrote, or None."""
    path = text_path(run_dir, label, kind)
    if not path.exists():
        return None
    item = EvolvingText.from_dict(read_json(path))
    if item.kind != kind or item.phase_label != label:
        raise CodeBugError(
            f"{path} holds a {item.kind!r} text of phase "
            f"{item.phase_label!r}, not {kind!r} of {label!r}")
    return item


def check_playbook_capability(spec: ExecutePhaseSpec,
                              benchmark: str) -> None:
    """An execute phase shows its playbook through a skill folder's
    manifest or at the top of its skills block; a benchmark whose skills
    block is not plain prompt text cannot carry it there
    (``BenchmarkPlugin.playbook_in_skills_block``)."""
    if spec.playbook != PLAYBOOK_EVOLVED:
        return
    if spec.delivery.get("name") == FOLDER_DELIVERY_NAME:
        return
    if not resolve_plugin(benchmark).playbook_in_skills_block:
        raise ConfigError(
            f"benchmark {benchmark!r} cannot show a playbook with delivery "
            f"{spec.delivery.get('name')!r}: its skills block is not "
            "prompt text; use the folder delivery or playbook 'off'")


def previous_learn_label(names: Sequence[str], specs: Mapping[str, object],
                         index: int) -> Optional[str]:
    """The label of the last learn phase before sequence position
    ``index``, or None when no learn phase precedes it."""
    for position in range(index - 1, -1, -1):
        if isinstance(specs[names[position]], LearnPhaseSpec):
            return phase_label(position, names[position])
    return None


def load_previous(run_dir: Union[str, Path], names: Sequence[str],
                  specs: Mapping[str, object], index: int,
                  kind: str) -> Optional[EvolvingText]:
    """The ``kind`` text of the learn phase immediately before ``index``.

    None when no learn phase precedes ``index``; a preceding learn phase
    that has no such file is a CodeBugError (every finished learn phase
    writes both texts).
    """
    label = previous_learn_label(names, specs, index)
    if label is None:
        return None
    item = read_text(run_dir, label, kind)
    if item is None:
        raise CodeBugError(
            f"learn phase {label!r} has no {kind} text "
            f"({text_path(run_dir, label, kind)}); every learn phase writes "
            "its profile and playbook before it finishes")
    return item
