"""Materialize a folder unit's manifest into a real directory (M2).

The folder DELIVERY (``methods/delivery/folder.py``) is a pure function:
it resolves the catalog text and the per-skill file LIST into the unit
row's ``aux["skill_folder"]``, and touching the file system belongs
here (layer 5 owns disk IO). This module writes that folder out: one
markdown file per skill (the file's body is the skill's four-section
content, verbatim) plus the catalog file (the manifest's catalog text,
verbatim).

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

Wiring: the C-phase driver (``stages/phase_execute/final_test.py``)
materializes the phase's folder once, before dispatch, into the phase
directory's ``skill_folder/``.
"""

import os
import re
import tempfile
from pathlib import Path
from typing import Any, List, Mapping, Sequence, Union

from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError
from expedistill.inject import bank_reference

__all__ = ["materialize_skill_folder"]

#: The safe file-name alphabet of the delivery's derivation
#: (``folder.skill_filename``): a name over these characters cannot
#: name a path outside the folder. Duplicated here on purpose -- the
#: composite layer may not import the methods layer, so the manifest's
#: contract is restated, not shared.
_FILENAME_RE = re.compile(r"[A-Za-z0-9._-]+\.md")

#: The manifest's required per-file fields (delivery's frozen shape,
#: rev-1: a name mapping -- no content).
_FILE_FIELDS = frozenset({"skill_id", "version", "filename"})


def _write_if_changed(path: Path, text: str) -> bool:
    """Atomically write ``text`` to ``path`` unless the bytes already
    match; returns whether anything was written."""
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
    resolved). One file per ``files`` entry under its ``filename``,
    bytes = that skill's content read from ``bank`` at the recorded
    version (a missing skill or a version drift is a CodeBugError);
    the catalog file (``catalog_filename``) carries ``catalog_text``
    when it is non-empty -- an empty accepted bank carries no catalog
    (the null-state lesson), so nothing is written for it. A manifest
    whose file names are unsafe, duplicated, or collide with the
    catalog's own name is a CodeBugError: the manifest is contract,
    and a silently shadowed file would be a wrong folder.
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
    if not isinstance(catalog_text, str):
        raise CodeBugError(
            f"the folder manifest's 'catalog_text' must be a string, got "
            f"{type(catalog_text).__name__}")

    entries: List[Mapping[str, Any]] = list(folder.get("files", []))
    owner_of = {}
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != _FILE_FIELDS:
            raise CodeBugError(
                f"a folder manifest file entry must carry exactly "
                f"{sorted(_FILE_FIELDS)}, got {entry!r}")
        filename = entry["filename"]
        if not isinstance(filename, str) or \
                not _FILENAME_RE.fullmatch(filename):
            raise CodeBugError(
                f"folder file name {filename!r} is not a safe name over "
                "[A-Za-z0-9._-] + '.md': the manifest cannot name a file "
                "the delivery would not derive")
        if filename == catalog_name:
            raise CodeBugError(
                f"folder file name {filename!r} collides with the "
                "catalog's own name: the folder cannot hold both")
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
        content = record.get("content") if isinstance(record, Mapping) \
            else record.content
        _write_if_changed(target / entry["filename"], content)
    if catalog_text:
        _write_if_changed(target / catalog_name, catalog_text)
    return target
