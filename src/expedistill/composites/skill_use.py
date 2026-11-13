"""Which skill cards an attempt read, and at which steps (layer 5, pure).

Read detection is the folder contract: only a ``cat`` of the skill's
``SKILL.md`` counts, and so does the local benchmarks' equivalent, a
``ReadSkill[<location>]`` action (``benchmarks/skill_folder.py``).

The module is benchmark-blind: a manifest file name is matched as a
delimited token, never a mount path.
"""

import re
import string
from pathlib import PurePosixPath
from typing import Any, List, Mapping, Optional

from expedistill.benchmarks.skill_folder import canonical_location, read_target
from expedistill.infra.reliability import CodeBugError

__all__ = [
    "skill_files_read",
    "skill_read_events",
]

_CAT_RE = re.compile(r"\bcat\b")
_SEGMENT_RE = re.compile(r"&&|\|\||[;|]")
_SKILL_FILENAME = "SKILL.md"
_NAME_ALPHABET = frozenset(string.ascii_letters + string.digits + "._-")

def _as_cat_line(line: str) -> str:
    """A ``ReadSkill[x]`` action as the equivalent ``cat`` line."""
    target = read_target(line)
    return line if target is None else f"cat {canonical_location(target)}"


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


def _cat_segments(line: str) -> List[tuple]:
    """The shell segments containing a cat command, each with its prefix.

    ``prefix`` is the line text before the segment. The ``cd DIR && cat
    SKILL.md`` form keeps the directory in the prefix, so a bare
    ``SKILL.md`` argument can still be attributed to it.
    """
    segments: List[tuple] = []
    prefix = ""
    for part in _SEGMENT_RE.split(line):
        if _CAT_RE.search(part):
            segments.append((part, prefix))
        prefix += part + " "
    return segments


def _bare_token_position(line: str, token: str) -> Optional[int]:
    """The first position of ``token`` that is not part of a path."""
    pattern = re.compile(
        rf"(?<![\w./-]){re.escape(token)}(?![\w.-])")
    match = pattern.search(line)
    return match.start() if match is not None else None


def _path_matches(line: str, filename: str) -> bool:
    path = PurePosixPath(filename)
    for segment, prefix in _cat_segments(line):
        if _name_token_in(segment, filename):
            return True
        if len(path.parts) == 2 and path.name == _SKILL_FILENAME:
            position = _bare_token_position(segment, _SKILL_FILENAME)
            if position is None:
                continue
            if _name_token_in(prefix, path.parts[0]) \
                    or _name_token_in(segment[:position], path.parts[0]):
                return True
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


def skill_read_events(trajectory: Any,
                      folder: Mapping[str, Any]) -> List[dict]:
    """The first cat event of each manifest skill in ``trajectory``:
    ``{"skill_id", "filename", "step_index", "command"}``."""
    order = _manifest_files(folder)
    if not order:
        return []
    owner_of = {entry["filename"]: entry["skill_id"]
                for entry in folder["files"]}
    events: List[dict] = []
    seen = set()
    for step_index, step in enumerate(trajectory.steps):
        for line in step.action.split("\n"):
            cat_line = _as_cat_line(line)
            if not _CAT_RE.search(cat_line):
                continue
            for filename in order:
                if filename in seen:
                    continue
                if not _path_matches(cat_line, filename):
                    continue
                seen.add(filename)
                events.append({
                    "skill_id": owner_of[filename],
                    "filename": filename,
                    "step_index": step_index,
                    "command": line.strip(),
                })
    return events


def skill_files_read(trajectory: Any,
                     folder: Mapping[str, Any]) -> List[str]:
    """The skill ids actually cat'd, first-read order, each once."""
    return [event["skill_id"]
            for event in skill_read_events(trajectory, folder)]
