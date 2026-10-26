"""Where a skill was used in an attempt (layer 5, pure).

This is the one place that answers three questions:

* was a skill used in this attempt?
* at which step(s) was it read or injected?
* which trajectory steps form the context around each use?

Read detection is the folder contract: only a ``cat`` of the skill's
``SKILL.md`` counts. Group/whole-bank deliveries have no per-skill read
event; an injected skill is treated as used for that attempt (the
decision's definition).

The module is benchmark-blind: a manifest file name is matched as a
delimited token, never a mount path.
"""

import re
import string
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, List, Mapping, Optional, Sequence

from expedistill.core.models import Trajectory
from expedistill.infra.reliability import CodeBugError

__all__ = [
    "Invocation",
    "SkillUse",
    "all_skill_read_events",
    "skill_files_read",
    "skill_read_events",
    "skill_used_in_attempt",
    "window_bounds",
]

_CAT_RE = re.compile(r"\bcat\b")
_NAME_ALPHABET = frozenset(string.ascii_letters + string.digits + "._-")

FOLDER_DELIVERIES = ("folder", "folder_by_task")


@dataclass(frozen=True)
class Invocation:
    """One actual (folder) or implied (injected) skill use."""

    skill_id: str
    step_index: Optional[int]
    command: str = ""
    filename: str = ""


@dataclass(frozen=True)
class SkillUse:
    """One skill's use fact in one attempt.

    ``used`` is None when the evidence cannot establish either way
    (folder delivery without a usage row, or an untrusted delivery).
    ``invocations`` is empty for non-folder deliveries: their trigger is
    the attempt itself, not a cat event.
    """

    used: Optional[bool]
    reason: str
    invocations: List[Invocation] = field(default_factory=list)
    injected: bool = False
    delivery: str = ""


def _name_token_in(line: str, filename: str) -> bool:
    start = 0
    while True:
        found = line.find(filename, start)
        if found < 0:
            return False
        end = found + len(filename)
        if (found == 0 or line[found - 1] not in _NAME_ALPHABET) \
                and (end == len(line)
                     or line[end] not in _NAME_ALPHABET):
            return True
        start = found + 1


def _path_matches(line: str, filename: str) -> bool:
    if _name_token_in(line, filename):
        return True
    path = PurePosixPath(filename)
    if len(path.parts) == 2 and path.name == "SKILL.md":
        return (_name_token_in(line, path.parts[0])
                and _name_token_in(line, path.name))
    return False


def _manifest_files(folder: Mapping[str, Any]) -> List[str]:
    seen = set()
    order: List[str] = []
    for entry in folder.get("files") or []:
        if not isinstance(entry, Mapping):
            raise CodeBugError(
                f"a folder manifest file entry must be a mapping, got "
                f"{entry!r}")
        skill_id = entry.get("skill_id")
        filename = entry.get("filename")
        if not isinstance(skill_id, str) or not skill_id \
                or not isinstance(filename, str) or not filename:
            raise CodeBugError(
                f"a folder manifest file entry needs string 'skill_id' "
                f"and 'filename', got {entry!r}")
        if filename in seen:
            raise CodeBugError(
                f"the folder manifest lists file name {filename!r} twice "
                f"(skills {skill_id!r} and another): one cat could not "
                "be told apart -- a human must look at this manifest")
        seen.add(filename)
        order.append(filename)
    return order


def skill_read_events(trajectory: Trajectory,
                      folder: Mapping[str, Any],
                      *, first_only: bool = True) -> List[dict]:
    """The cat events of the manifest's skills in ``trajectory``.

    ``first_only=True`` is the legacy P03 usage shape (one event per
    skill); ``False`` returns every cat, which the P04 invocation
    windows need. Each event is ``{"skill_id", "filename",
    "step_index", "command"}``.
    """
    order = _manifest_files(folder)
    if not order:
        return []
    owner_of = {entry["filename"]: entry["skill_id"]
                for entry in folder["files"]}
    events: List[dict] = []
    seen = set()
    for step_index, step in enumerate(trajectory.steps):
        for line in step.action.split("\n"):
            if not _CAT_RE.search(line):
                continue
            for filename in order:
                if first_only and filename in seen:
                    continue
                if not _path_matches(line, filename):
                    continue
                seen.add(filename)
                events.append({
                    "skill_id": owner_of[filename],
                    "filename": filename,
                    "step_index": step_index,
                    "command": line.strip(),
                })
    return events


def all_skill_read_events(trajectory: Trajectory,
                          folder: Mapping[str, Any]) -> List[dict]:
    """Every cat event, including repeated reads of one skill."""
    return skill_read_events(trajectory, folder, first_only=False)


def skill_files_read(trajectory: Trajectory,
                     folder: Mapping[str, Any]) -> List[str]:
    """The skill ids actually cat'd, first-read order, each once."""
    return [event["skill_id"]
            for event in skill_read_events(trajectory, folder)]


def skill_used_in_attempt(
    *,
    skill_id: str,
    attempt: int,
    delivery: str,
    usage_row: Optional[Mapping[str, Any]] = None,
    injected_skill_ids: Sequence[str] = (),
    trajectory: Optional[Trajectory] = None,
    folder: Optional[Mapping[str, Any]] = None,
) -> SkillUse:
    """Was ``skill_id`` used in ``attempt``? (module docstring)

    Folder deliveries answer from the usage row and, when a trajectory
    and folder are supplied, record every actual cat step. Other
    deliveries answer from the injected set: an injected skill is treated
    as triggered for that attempt.
    """
    if delivery in FOLDER_DELIVERIES:
        if usage_row is None:
            return SkillUse(None, "no_usage", [], False, delivery)
        status = usage_row.get("delivery_status")
        if status != "ok":
            return SkillUse(None, f"untrusted_delivery:{status}", [],
                            False, delivery)
        read_ids = list(usage_row.get("read_skill_ids") or [])
        used = skill_id in read_ids
        invocations: List[Invocation] = []
        if used and trajectory is not None and folder is not None:
            invocations = [
                Invocation(event["skill_id"], event["step_index"],
                           event["command"], event["filename"])
                for event in all_skill_read_events(trajectory, folder)
                if event["skill_id"] == skill_id]
        elif used and usage_row is not None:
            invocations = [
                Invocation(skill_id, event.get("step_index"),
                           event.get("command", ""), event.get("filename", ""))
                for event in usage_row.get("reads") or []
                if event.get("skill_id") == skill_id]
        return SkillUse(used, "skill_read" if used else "skill_never_read",
                        invocations, False, delivery)

    injected = skill_id in set(injected_skill_ids)
    return SkillUse(
        injected, "injected" if injected else "not_injected", [],
        injected, delivery)


def window_bounds(step_index: int, n_steps: int, *,
                  before: int, after: int) -> tuple:
    """The step window around one invocation, clipped to the attempt."""
    if step_index < 0 or step_index >= n_steps:
        raise CodeBugError(
            f"invocation step {step_index} is outside a {n_steps}-step "
            "trajectory")
    return max(0, step_index - before), min(n_steps, step_index + after + 1)
