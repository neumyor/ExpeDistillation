"""Materialize a folder unit's manifest into a real directory (M2).

The folder DELIVERY (``methods/delivery/folder.py``) is a pure function:
it resolves the catalog text and the per-skill file LIST into the unit
row's ``aux["skill_folder"]``, and touching the file system belongs
here (layer 5 owns disk IO). This module writes that folder out: one
directory per skill containing ``SKILL.md`` (YAML frontmatter plus the
skill's three-section content) plus the catalog file (the manifest's
catalog text, verbatim). The frontmatter is what Harbor's Terminus
agent parses to build its ``<available_skills>`` list; a file without
it is silently skipped, so every rendered skill file carries it.

The manifest is a NAME MAPPING, never the skills (the M2 rev-1 ruling:
progressive disclosure is the whole point of this play, so the unit
row stays light and no stage may quietly become "inject everything
somewhere else"). The skill bodies' only source is the BANK the
manifest names: ``materialize_skill_folder`` takes the bank alongside
the manifest, checks it against the manifest's ``bank_fingerprint``
(loudly refusing a bank that moved under the manifest), and reads each
listed skill's content from it at the recorded version. The manifest's
field names (``bank_fingerprint`` / ``catalog_filename`` /
``catalog_text`` / ``files[].{skill_id, version, filename}``) are
frozen by the delivery module and consumed here.

Idempotency (the chosen form: unchanged content is NOT rewritten): one
call walks the manifest and writes a file only when its bytes differ
from what is already on disk, atomically (temp file + rename, B3's
pattern). Replaying the same manifest against the same bank lands on
the same bytes and leaves every file's mtime untouched; a changed
manifest rewrites only the changed files. The function never deletes
anything -- files the manifest does not name are another caller's
business -- and never writes outside ``target_dir``: every file name
is validated against the same safe alphabet the delivery's derivation
uses, so a manifest cannot name a path that escapes the folder. One
disclosed consequence of "never deletes" (the resume review's note,
left on record): if the bank ever changed between two materializations
against the same directory, the previous manifest's files would stay
next to the new ones in the LOCAL directory -- unreachable in the
current wiring (the fingerprint gate makes a mixed version impossible
while resolve and materialize share one bank per call) and never
carried into the run environment (the sandbox upload goes strictly by
the manifest's file names), so a local directory audit should read the
folder against the manifest, not trust the directory alone.

Wiring: the test-phase driver (``stages/phase_execute/final_test.py``)
materializes the phase's folder once, before dispatch, into the phase
directory's ``skill_folder/``.
"""

import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Sequence, Union

import yaml

from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError
from expedistill.inject import bank_reference
from expedistill.inject.skill_text import (
    DEFAULT_DESCRIPTION_MODE,
    description_text,
    validate_description_mode,
)

__all__ = ["materialize_skill_folder"]

#: The Agent Skills name rule: lowercase letters, digits and hyphens.
#: The delivery enforces the same rule; this composite restates it
#: because it may not import the methods layer, and a manifest that
#: names a path outside the folder must be refused here.
_SKILL_DIR_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")

#: The fixed file name inside a skill directory.
_SKILL_FILE_NAME = "SKILL.md"

#: The manifest's required per-file fields (delivery's frozen shape,
#: rev-1: a name mapping -- no content).
_FILE_FIELDS = frozenset({"skill_id", "version", "filename"})


def _write_if_changed(path: Path, text: str) -> bool:
    """Atomically write ``text`` to ``path`` unless the bytes already
    match; returns whether anything was written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    try:
        if path.read_bytes() == data:
            return False
    except FileNotFoundError:
        pass
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent),
                                     prefix=path.name + ".",
                                     suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return True


def _bank_body(bank: Sequence[Union[Skill, Mapping[str, Any]]]
               ) -> Mapping[str, Any]:
    """The bank as a skill_id -> row index (Skill objects or bank row
    mappings, the same both-shapes contract the delivery family holds;
    a duplicated id in the bank is a loud bug -- one id is one skill)."""
    rows: Mapping[str, Any] = {}
    for record in bank:
        skill_id = record.get("skill_id") if isinstance(record, Mapping) \
            else record.skill_id
        if skill_id in rows:
            raise CodeBugError(
                f"the bank carries skill id {skill_id!r} twice: one id "
                "is one skill, the bank itself is inconsistent")
        rows[skill_id] = record
    return rows


def _validate_filename(filename: Any) -> str:
    """One manifest file name: exactly ``<safe-dir>/SKILL.md``.

    The manifest is contract, and this value becomes a path under the
    target directory: an absolute path, a ``..`` segment, a deeper
    path, or any other file name is a CodeBugError.
    """
    if not isinstance(filename, str) or not filename:
        raise CodeBugError(
            f"folder file name must be a non-empty string, got "
            f"{filename!r}")
    path = PurePosixPath(filename)
    if path.is_absolute() or len(path.parts) != 2 \
            or str(path) != filename:
        raise CodeBugError(
            f"folder file name {filename!r} must be exactly one "
            "normalized directory plus SKILL.md")
    directory, leaf = path.parts
    if not _SKILL_DIR_RE.fullmatch(directory):
        raise CodeBugError(
            f"folder directory {directory!r} is not a safe Agent Skills "
            "name over lowercase letters, digits and hyphens")
    if leaf != _SKILL_FILE_NAME:
        raise CodeBugError(
            f"folder file name {filename!r} must end in {_SKILL_FILE_NAME}")
    return filename


def _render_skill_file(record: Union[Skill, Mapping[str, Any]],
                       filename: str, description_mode: str) -> str:
    """The exact bytes of one skill's SKILL.md: YAML frontmatter then
    the skill's three-section content.

    ``name`` is the manifest directory (the delivery derived it from
    the skill id); ``description`` is the configured composition. The
    content is unchanged -- the frontmatter is packaging, not part of
    the stored skill.
    """
    content = record.get("content") if isinstance(record, Mapping) \
        else record.content
    if not isinstance(content, str):
        raise CodeBugError(
            f"skill {filename!r} has non-string content: {content!r}")
    name = PurePosixPath(filename).parts[0]
    description = description_text(record, description_mode)
    if not description:
        raise CodeBugError(
            f"skill {filename!r} has an empty description; the Agent "
            "Skills format requires a non-empty description")
    frontmatter = yaml.safe_dump(
        {"name": name, "description": description},
        allow_unicode=True,
        sort_keys=False,
        width=10**9,
    ).strip()
    return f"---\n{frontmatter}\n---\n\n{content}"


def materialize_skill_folder(folder: Mapping[str, Any],
                             bank: Sequence[Union[Skill, Mapping[str, Any]]],
                             target_dir: Union[str, Path]) -> Path:
    """Write the manifest's folder into ``target_dir``; returns the
    directory.

    ``folder`` is the delivery's manifest (``aux["skill_folder"]``,
    shape frozen in methods/delivery/folder.py); ``bank`` is the phase
    bank it resolves against. The manifest's ``bank_fingerprint`` must
    equal ``bank_reference(bank)`` -- a bank that moved under the
    manifest is a CodeBugError (the manifest names its version; a
    silent mismatch would materialize a folder the delivery never
    resolved). One ``<skill-directory>/SKILL.md`` per ``files`` entry,
    bytes = YAML frontmatter (name, description) plus the skill's
    content read from ``bank`` at the recorded version (a missing
    skill or a version drift is a CodeBugError); the catalog file
    (``catalog_filename``) carries ``catalog_text`` when it is
    non-empty -- an empty accepted bank carries no catalog (the
    null-state lesson), so nothing is written for it. A manifest whose
    file names are unsafe or duplicated is a CodeBugError: the manifest
    is contract, and a silently shadowed file would be a wrong folder.
    """
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)

    fingerprint = folder.get("bank_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise CodeBugError(
            f"the folder manifest needs a non-empty 'bank_fingerprint', "
            f"got {fingerprint!r}")
    if bank_reference(bank) != fingerprint:
        raise CodeBugError(
            f"the folder manifest names bank {fingerprint!r} but the "
            f"bank given is {bank_reference(bank)!r}: the bank moved "
            "under the manifest -- re-resolve the phase before "
            "materializing")

    catalog_name = folder.get("catalog_filename")
    catalog_text = folder.get("catalog_text", "")
    if not isinstance(catalog_name, str) or not catalog_name:
        raise CodeBugError(
            f"the folder manifest needs a non-empty 'catalog_filename', "
            f"got {catalog_name!r}")
    if PurePosixPath(catalog_name).name != catalog_name \
            or catalog_name in (".", ".."):
        raise CodeBugError(
            f"the catalog file name {catalog_name!r} must be one root "
            "file name, never a path")
    if not isinstance(catalog_text, str):
        raise CodeBugError(
            f"the folder manifest's 'catalog_text' must be a string, got "
            f"{type(catalog_text).__name__}")
    description_mode = folder.get("description_mode",
                                  DEFAULT_DESCRIPTION_MODE)
    try:
        validate_description_mode(description_mode)
    except ValueError as exc:
        raise CodeBugError(
            f"the folder manifest carries an invalid description_mode: "
            f"{exc}") from exc

    entries: List[Mapping[str, Any]] = list(folder.get("files", []))
    owner_of: Dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != _FILE_FIELDS:
            raise CodeBugError(
                f"a folder manifest file entry must carry exactly "
                f"{sorted(_FILE_FIELDS)}, got {entry!r}")
        filename = _validate_filename(entry["filename"])
        if filename in owner_of:
            raise CodeBugError(
                f"the folder manifest lists file name {filename!r} "
                f"twice (skills {owner_of[filename]!r} and "
                f"{entry['skill_id']!r}): one file would silently shadow "
                "the other")
        owner_of[filename] = entry["skill_id"]

    rows = _bank_body(bank)
    for entry in entries:
        skill_id = entry["skill_id"]
        if skill_id not in rows:
            raise CodeBugError(
                f"the folder manifest lists skill {skill_id!r} which "
                "the bank does not carry: the manifest and the bank "
                "disagree")
        record = rows[skill_id]
        version = record.get("version") if isinstance(record, Mapping) \
            else record.version
        if version != entry["version"]:
            raise CodeBugError(
                f"skill {skill_id!r} is at version {version} in the "
                f"bank but the manifest recorded version "
                f"{entry['version']}: the bank moved under the manifest")
        _write_if_changed(
            target / entry["filename"],
            _render_skill_file(record, entry["filename"], description_mode))
    if catalog_text:
        _write_if_changed(target / catalog_name, catalog_text)
    return target
