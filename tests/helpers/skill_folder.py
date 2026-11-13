"""A real, materialized skill folder for the local-plugin tests.

Goes through the same delivery manifest and materializer the phase drivers
use, so the folder a plugin test reads is byte-for-byte what a run reads.
"""

from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from expedistill.composites.skill_materialize import materialize_skill_folder
from expedistill.core.ids import make_type_id
from expedistill.core.models import Skill
from expedistill.methods.delivery.folder_delivery import skill_folder_manifest
from tests.helpers.skills import make_skill

__all__ = ["CARD_BODY", "folder_task_meta", "write_skill_folder"]

#: The fields every test card carries.
CARD_FIELDS = {
    "when_to_use": "At task start, when the question names a person.",
    "steps": ["Search the full name first."],
    "not_applicable_when": "The question has no named entity.",
}

#: The rendered card text every test card starts with; tests look for it
#: to tell whether a card's text reached a prompt.
CARD_BODY = "\n".join([
    "[When to use] At task start, when the question names a person.",
    "[Steps]",
    "1. Search the full name first.",
    "[Do not use when] The question has no named entity.",
])


def write_skill_folder(
        target: Path,
        names: Sequence[str] = ("alpha", "beta"),
) -> Tuple[Dict[str, Any], List[Skill], List[str]]:
    """Materialize one card per name under ``target`` (the i-th name is
    the card of subtask type ``type/<i + 1>``).

    Returns ``(manifest, bank, locations)`` where ``locations`` are the
    manifest's ``<directory>/SKILL.md`` paths in the same order as
    ``names``.
    """
    bank = [
        make_skill(make_type_id(index + 1),
                   CARD_FIELDS["when_to_use"],
                   steps=list(CARD_FIELDS["steps"]),
                   not_applicable_when=(
                       f"{CARD_FIELDS['not_applicable_when']}\n(card {name})"),
                   summary=f"Card {name} teaches a lookup order.")
        for index, name in enumerate(names)]
    manifest = skill_folder_manifest(bank, "summary_then_when_to_use")
    materialize_skill_folder(manifest, bank, target)
    locations = [entry["filename"] for entry in manifest["files"]]
    return manifest, bank, locations


def folder_task_meta(manifest: Dict[str, Any], folder_dir: Path
                     ) -> Dict[str, Any]:
    """The Task meta a folder unit carries into ``plugin.run``."""
    return {"skill_folder": manifest, "skill_folder_dir": str(folder_dir)}
