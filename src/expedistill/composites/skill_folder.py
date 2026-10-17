"""Materialize a folder unit's manifest into a real directory (M2).

The folder DELIVERY (``methods/delivery/folder.py``) is a pure function:
it resolves the catalog text and the per-skill file list into the unit
row's ``aux["skill_folder"]``, and touching the file system belongs
here (layer 5 owns disk IO). This module writes that manifest out as
the folder the M3 execution path will drop into the run's environment:
one markdown file per skill (the file's body is the skill's four-section
content, verbatim) plus the catalog file (the manifest's catalog text,
verbatim). The manifest is the WHOLE interface -- the skill rows
themselves never travel here -- so the manifest's field names
(``catalog_filename`` / ``catalog_text`` / ``files[].filename`` /
``files[].content``) are frozen by the delivery module and consumed
here.

Idempotency (the chosen form: unchanged content is NOT rewritten): one
call walks the manifest and writes a file only when its bytes differ
from what is already on disk, atomically (temp file + rename, B3's
pattern). Replaying the same manifest lands on the same bytes and
leaves every file's mtime untouched; a changed manifest rewrites only
the changed files. The function never deletes anything -- files the
manifest does not name are another caller's business -- and never
writes outside ``target_dir``: every file name is validated against
the same safe alphabet the delivery's derivation uses, so a manifest
cannot name a path that escapes the folder.

Not wired to any execution path yet: M2 ships the function and its
tests; the phase driver calls it in M3, once the ``real_folder``
execution version exists.
"""

import os
import re
import tempfile
from pathlib import Path
from typing import Any, List, Mapping, Union

from expedistill.infra.reliability import CodeBugError

__all__ = ["materialize_skill_folder"]

#: The safe file-name alphabet of the delivery's derivation
#: (``folder.skill_filename``): a name over these characters cannot
#: name a path outside the folder. Duplicated here on purpose -- the
#: composite layer may not import the methods layer, so the manifest's
#: contract is restated, not shared.
_FILENAME_RE = re.compile(r"[A-Za-z0-9._-]+\.md")

#: The manifest's required per-file fields (delivery's frozen shape).
_FILE_FIELDS = frozenset({"skill_id", "version", "filename", "content"})


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


def materialize_skill_folder(folder: Mapping[str, Any],
                             target_dir: Union[str, Path]) -> Path:
    """Write the manifest's folder into ``target_dir``; returns the
    directory.

    ``folder`` is the delivery's manifest (``aux["skill_folder"]``,
    shape frozen in methods/delivery/folder.py). One file per
    ``files`` entry under its ``filename``, bytes = its ``content``;
    the catalog file (``catalog_filename``) carries ``catalog_text``
    when it is non-empty -- an empty accepted bank carries no catalog
    (the null-state lesson), so nothing is written for it. A manifest
    whose file names are unsafe, duplicated, or collide with the
    catalog's own name is a CodeBugError: the manifest is contract,
    and a silently shadowed file would be a wrong folder.
    """
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)

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

    for entry in entries:
        _write_if_changed(target / entry["filename"], entry["content"])
    if catalog_text:
        _write_if_changed(target / catalog_name, catalog_text)
    return target
