"""What the executor of one baseline or practice trial saw and opened.

:func:`sheet_facts` reads one AnswerSheet: the skill folder manifest a
practice sheet carries in ``execution.artifacts["skill_folder"]`` (the
catalog and the playbook the executor saw; a baseline sheet has none), and
the cards the trial opened with the step of each card's first read
(``composites.skill_use.skill_read_events``: ``cat`` and ``ReadSkill[...]``
alike, never the model's own account). The full text of an opened card is
the version the manifest delivered, rebuilt from the stored skill's
rewrite history when the store has moved past it.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional

from expedistill.composites.skill_use import skill_read_events
from expedistill.core.answer_sheet import AnswerSheet
from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError
from expedistill.inject.skill_text import render_card

__all__ = ["SKILL_FOLDER_KEY", "SheetFacts", "sheet_facts",
           "skill_at_version"]

#: The key of the delivered folder manifest in a practice sheet's
#: ``execution.artifacts``.
SKILL_FOLDER_KEY = "skill_folder"


@dataclass(frozen=True)
class SheetFacts:
    """One trial's facts for the learn phase.

    ``opened`` maps each opened skill id to the step of its first read, in
    first-read order; ``opened_cards`` maps the same ids to the card text
    the trial read. ``catalog`` and ``playbook`` are empty for a bare trial.
    """

    sheet_id: str
    unit_id: str
    task_ref: str
    trajectory_id: str
    n_steps: int
    catalog: str = ""
    playbook: str = ""
    opened: Dict[str, int] = field(default_factory=dict)
    opened_cards: Dict[str, str] = field(default_factory=dict)


def skill_at_version(skill: Skill, version: int) -> Skill:
    """``skill`` as it read at ``version``: the stored object with its
    newer rewrites undone (each history entry holds the fields it
    replaced)."""
    if version > skill.version or version < 1:
        raise CodeBugError(
            f"{skill.skill_id!r} is at version {skill.version}; version "
            f"{version} does not exist")
    data = skill.to_dict()
    history = list(data["rewrite_history"])
    for _ in range(skill.version - version):
        entry = history.pop()
        data.update(entry["old"])
    data["rewrite_history"] = history
    data["version"] = version
    return Skill.from_dict(data)


def sheet_facts(sheet: AnswerSheet,
                load_skill: Callable[[str], Optional[Skill]]
                ) -> SheetFacts:
    """The facts of one executed sheet; ``load_skill`` returns the stored
    skill of an id (None when the store does not carry it)."""
    execution = sheet.execution
    if execution is None:
        raise CodeBugError(
            f"sheet {sheet.sheet_id!r} has no execution to read facts from")
    artifacts = execution.artifacts or {}
    trajectory_id = artifacts.get("trajectory_id")
    if not isinstance(trajectory_id, str) or not trajectory_id:
        raise CodeBugError(
            f"sheet {sheet.sheet_id!r} records no trajectory id")
    steps = list(execution.steps or [])
    folder: Optional[Mapping[str, Any]] = artifacts.get(SKILL_FOLDER_KEY)
    if not folder:
        return SheetFacts(sheet_id=sheet.sheet_id,
                          unit_id=sheet.coverage.unit_id,
                          task_ref=execution.task_ref,
                          trajectory_id=trajectory_id, n_steps=len(steps))
    versions = {entry["skill_id"]: entry.get("version", 1)
                for entry in folder.get("files") or []}
    opened: Dict[str, int] = {}
    cards: Dict[str, str] = {}
    for event in skill_read_events(execution, folder):
        skill_id = event["skill_id"]
        opened[skill_id] = event["step_index"]
        stored = load_skill(skill_id)
        cards[skill_id] = (
            render_card(skill_at_version(stored, versions[skill_id]))
            if stored is not None else "(not in the store)")
    return SheetFacts(
        sheet_id=sheet.sheet_id, unit_id=sheet.coverage.unit_id,
        task_ref=execution.task_ref, trajectory_id=trajectory_id,
        n_steps=len(steps),
        catalog=str(folder.get("catalog_text") or ""),
        playbook=str(folder.get("playbook_text") or ""),
        opened=opened, opened_cards=cards)
