"""Progressive skill disclosure for the local ReAct benchmarks (layer 3).

TB hands a folder of ``<skill>/SKILL.md`` files to Harbor's Terminus agent,
which lists each card's name, description and location in an
``<available_skills>`` section and lets the agent ``cat`` a card on demand.
The local benchmarks (ALFWorld, SearchQA) have no shell, so this module
gives their ReAct loop the same contract:

- the prompt carries only the card list (:meth:`SkillFolder.available_skills_block`,
  the same XML shape Terminus renders, read from the materialized files'
  YAML frontmatter);
- a card's text appears only when the model replies with
  ``ReadSkill[<location>]``. The loop answers that action itself, so the
  evaluation environment never sees it;
- reading is capped at :data:`MAX_READS` DISTINCT cards per attempt and
  costs no environment step. A loop of repeat or refused reads is free for
  a fixed allowance, after which each read costs one step, so a model that
  keeps asking cannot stall the episode. The ALFWorld loop also keeps reads
  out of the unified protocol's two-step history and holds every opened card
  in a persistent prompt block.

The folder itself is the delivery's work: the manifest arrives on
``task.meta["skill_folder"]`` and the materialized directory on
``task.meta["skill_folder_dir"]`` (the same channel the TB plugin reads).
The module is benchmark-blind; a plugin only chooses its prompt and where
the card list goes in it.
"""

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Tuple
from xml.etree.ElementTree import Element, SubElement, indent, tostring

import yaml

from expedistill.benchmarks.base import Task
from expedistill.infra.reliability import CodeBugError

__all__ = [
    "MAX_READS",
    "READ_ACTION",
    "SkillCard",
    "SkillFolder",
    "canonical_location",
    "derive_folder_template",
    "read_limit_message",
    "read_target",
]

#: The action word the model uses to open a card.
READ_ACTION = "ReadSkill"

#: Cards one attempt may open. The prompt text states this number, so it is
#: a protocol constant (it freezes with the folder prompt's digest), not a
#: parameter.
MAX_READS = 3

_MANIFEST_KEY = "skill_folder"
_DIR_KEY = "skill_folder_dir"
_SKILL_FILE_NAME = "SKILL.md"

# ``> ReadSkill[x]`` is accepted because the ALFWorld prompt teaches the
# ``> action`` reply syntax.
_READ_RE = re.compile(r"^\s*(?:>\s*)?ReadSkill\[(?P<target>[^\]\n]*)\]\s*$")
#: The unified protocol lowercases the whole reply before projecting the
#: action, so its read requests arrive as ``readskill[...]``; the target
#: itself keeps its (case-sensitive) folder path.
_READ_RE_ANY_CASE = re.compile(
    r"^\s*(?:>\s*)?ReadSkill\[(?P<target>[^\]\n]*)\]\s*$", re.IGNORECASE)
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def read_target(action: str, *, ignore_case: bool = False) -> Optional[str]:
    """The location argument if ``action`` is a read request, else None."""
    pattern = _READ_RE_ANY_CASE if ignore_case else _READ_RE
    found = pattern.match(action)
    return found.group("target") if found is not None else None


def canonical_location(target: str) -> str:
    """A read argument as a folder-relative ``<directory>/SKILL.md``.

    The model may quote the location, prefix ``./`` or give the bare
    directory name; all of them address the same card.
    """
    key = target.strip().strip("'\"`").strip()
    if key.startswith("./"):
        key = key[2:]
    key = key.rstrip("/")
    if not key.endswith(_SKILL_FILE_NAME):
        key = f"{key}/{_SKILL_FILE_NAME}"
    return key


@dataclass(frozen=True)
class SkillCard:
    """One listed card: what the model sees before opening it."""

    name: str
    description: str
    location: str


def read_limit_message() -> str:
    return (f"Skill read limit reached ({MAX_READS} cards already opened). "
            "Continue the task with the other actions.")


def _unknown_card_message(target: str) -> str:
    return (f"Unknown skill location {target!r}. Use a <location> value "
            "from <available_skills>.")


def _parse_card(location: str, text: str) -> SkillCard:
    match = _FRONTMATTER_RE.match(text)
    head = yaml.safe_load(match.group(1)) if match is not None else None
    if not isinstance(head, dict):
        raise CodeBugError(
            f"skill file {location!r} has no YAML frontmatter; the "
            "materialization always writes one")
    name = head.get("name")
    description = head.get("description")
    if not isinstance(name, str) or not name \
            or not isinstance(description, str) or not description:
        raise CodeBugError(
            f"skill file {location!r} frontmatter needs a non-empty string "
            f"'name' and 'description', got {head!r}")
    return SkillCard(name=name, description=description, location=location)


def _check_location(location: Any) -> str:
    path = PurePosixPath(location) if isinstance(location, str) else None
    if path is None or path.is_absolute() or len(path.parts) != 2 \
            or path.parts[1] != _SKILL_FILE_NAME \
            or str(path) != location or ".." in path.parts:
        raise CodeBugError(
            f"folder manifest file name must be '<directory>/"
            f"{_SKILL_FILE_NAME}', got {location!r}")
    return location


class SkillFolder:
    """One folder unit's card list and reader."""

    def __init__(self, folder_dir: Path, cards: List[SkillCard],
                 playbook_text: str = ""):
        self._dir = folder_dir
        self._cards = sorted(cards, key=lambda card: card.location)
        self._by_location: Dict[str, SkillCard] = {
            card.location: card for card in self._cards}
        self._playbook_text = playbook_text

    @classmethod
    def from_task(cls, task: Task) -> Optional["SkillFolder"]:
        """The task's skill folder, or None for a unit without one.

        A manifest with no files is the delivery's empty-bank null state
        and runs bare. A manifest with files but no materialized directory
        is a wiring bug: the phase driver materializes before dispatch.
        """
        meta = task.meta if isinstance(task.meta, dict) else {}
        manifest = meta.get(_MANIFEST_KEY)
        if manifest is None:
            return None
        if not isinstance(manifest, Mapping):
            raise CodeBugError(
                f"task {task.task_id!r} carries {_MANIFEST_KEY!r} but it "
                f"is not the folder manifest mapping, got {manifest!r}")
        entries = manifest.get("files") or []
        if not entries:
            return None
        folder_dir = meta.get(_DIR_KEY)
        if not isinstance(folder_dir, str) or not folder_dir:
            raise CodeBugError(
                f"the folder unit {task.task_id!r} carries a folder "
                f"manifest but no {_DIR_KEY!r}: the phase driver must "
                "materialize the folder before dispatch")
        root = Path(folder_dir)
        cards = []
        for entry in entries:
            location = _check_location(
                entry.get("filename") if isinstance(entry, Mapping) else None)
            cards.append(_parse_card(
                location, (root / location).read_text(encoding="utf-8")))
        playbook_text = str(manifest.get("playbook_text") or "")
        return cls(root, cards, playbook_text)

    @property
    def cards(self) -> Tuple[SkillCard, ...]:
        return tuple(self._cards)

    @property
    def playbook_text(self) -> str:
        """The delivery's global playbook, rendered above the card list."""
        return self._playbook_text

    def available_skills_block(self) -> str:
        """The ``<available_skills>`` list, shaped like Terminus's."""
        root = Element("available_skills")
        for card in self._cards:
            skill = SubElement(root, "skill")
            SubElement(skill, "name").text = card.name
            SubElement(skill, "description").text = card.description
            SubElement(skill, "location").text = card.location
        indent(root, space="  ")
        return tostring(root, encoding="unicode")

    def read(self, target: str) -> Tuple[str, bool]:
        """``(observation, found)`` for one read request.

        The observation is the whole file, as a ``cat`` of it would print.
        """
        card = self._by_location.get(canonical_location(target))
        if card is None:
            return _unknown_card_message(target.strip()), False
        text = (self._dir / card.location).read_text(encoding="utf-8")
        return text, True


def derive_folder_template(base: str, anchor: str, insertion: str) -> str:
    """The folder prompt: ``base`` with ``insertion`` placed after ``anchor``.

    A folder prompt differs from its plain prompt only by the skill-reading
    text, so it is derived rather than copied; an anchor that is missing or
    ambiguous means the plain prompt changed under the derivation.
    """
    if base.count(anchor) != 1:
        raise CodeBugError(
            "the plain task prompt must contain the folder-prompt anchor "
            f"exactly once, found it {base.count(anchor)} times: {anchor!r}")
    return base.replace(anchor, anchor + insertion, 1)
