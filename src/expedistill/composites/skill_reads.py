"""Post-hoc read statistics for skill-folder units (layer 5, pure).

The folder execution never intercepts a read (M3's ruling: zero hooks,
zero budgets -- the session is recorded whole and the cat commands stay
in the trajectory verbatim), so WHICH skill files a unit actually read
is derived here, AFTER the fact, from the stored trajectory alone (the
alignment receipt's mechanism ruling: the read record is a trajectory
parse, never an interception).

Deliberately benchmark-blind: no mount path constant appears here. A
step counts a read of a manifest file when the step's action text holds
a cat command that references the file's NAME as a delimited token.
Two coarse edges are disclosed rather than hidden:

* a cat of a same-NAMED file under any other directory counts as a
  read of the manifest's file -- the directory is not checked, because
  the mount path is benchmark knowledge and this module must stay
  benchmark-agnostic (the file names are derived from skill ids, so a
  false positive needs a same-named file the agent chose to cat);
* only cat counts -- the folder guidance's own contract -- so a file
  read through another command (grep, head, less) is not reported.

The catalog's own file is not in the manifest's file list and never
appears in a read set: reading the catalog is navigation, not the use
of a skill.

Leak-audit note (M4's survey): the D8 leak judge's only hook is the A
phase's exam pipeline (``composites/acceptance_leak.py``, keyed by
(skill, version) over the skill's content) -- no leak hook exists on
test-phase units, and none is invented here. The read set is a reported
fact for post-hoc manual audit; the existing per-(skill, version)
coverage keeps standing for folder units.
"""

import re
import string
from pathlib import PurePosixPath
from typing import Any, List, Mapping

from expedistill.core.models import Trajectory
from expedistill.infra.reliability import CodeBugError

__all__ = ["skill_files_read"]

#: A cat command's word: "cat" at a word boundary -- ``concat`` or a
#: text fragment like "indicates" never matches.
_CAT_RE = re.compile(r"\bcat\b")

#: The delivery's safe file-name alphabet (``folder.skill_filename``'s
#: character set; restated here, not imported, per the same layer rule
#: ``composites/skill_folder.py`` already follows). A file name
#: matches as a delimited token when the characters around the match
#: sit outside this alphabet (or the text ends), so a manifest name
#: that happens to be a substring of a longer name on the line does
#: not count.
_NAME_ALPHABET = frozenset(string.ascii_letters + string.digits + "._-")


def _name_token_in(line: str, filename: str) -> bool:
    """Whether ``line`` holds ``filename`` as a delimited token
    (module docstring: the match's neighbouring characters are outside
    the file-name alphabet, or the line ends there)."""
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
    """Whether a cat line reads ``filename``.

    A manifest path is ``<skill-directory>/SKILL.md``. The full relative
    path matches an absolute mount path as a substring (the characters
    around it are ``/``, outside the name alphabet). The fallback
    accepts the common ``cd <skill-directory> && cat SKILL.md`` form
    when both the unique directory name and ``SKILL.md`` appear on the
    same line; a bare ``cat SKILL.md`` stays ambiguous and is not
    counted.
    """
    if _name_token_in(line, filename):
        return True
    path = PurePosixPath(filename)
    if len(path.parts) == 2 and path.name == "SKILL.md":
        return (_name_token_in(line, path.parts[0])
                and _name_token_in(line, path.name))
    return False


def skill_files_read(trajectory: Trajectory,
                     folder: Mapping[str, Any]) -> List[str]:
    """The skill ids whose files ``trajectory``'s actions actually cat,
    in first-read order, each skill once.

    ``folder`` is the unit's folder manifest (the delivery's frozen
    shape, ``aux["skill_folder"]``); only ``files[].{skill_id,
    filename}`` is consumed. A manifest with no files (the empty
    accepted bank's null state) has nothing to read: the read set is
    empty. The same file cat more than once counts once; within one
    action line several files match in manifest order. A manifest entry
    without string ``skill_id`` / ``filename``, or two entries naming
    one file, is a CodeBugError -- the delivery refuses both, so a row
    that carries one is a wiring bug, never a statistic.

    Pure and trajectory-readonly: nothing is written, nothing is
    mutated, and the same inputs always yield the same list -- a
    resumed run recomputes the same read sets from the same stored
    trajectories.
    """
    owner_of = {}
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
        if filename in owner_of:
            raise CodeBugError(
                f"the folder manifest lists file name {filename!r} "
                f"twice (skills {owner_of[filename]!r} and "
                f"{skill_id!r}): one cat could not be told apart -- a "
                "human must look at this manifest")
        owner_of[filename] = skill_id
        order.append(filename)
    if not order:
        return []

    read: List[str] = []
    seen = set()
    for step in trajectory.steps:
        for line in step.action.split("\n"):
            if not _CAT_RE.search(line):
                continue
            for filename in order:
                if filename not in seen \
                        and _path_matches(line, filename):
                    seen.add(filename)
                    read.append(owner_of[filename])
    return read
