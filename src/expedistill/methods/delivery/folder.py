"""The folder delivery driver: a catalog listing, not the skills (M2).

The third delivery play (the 2026-10-17 skill-folder alignment
ruling): the agent is handed ONE folder of skills -- one
markdown file per accepted skill plus a catalog file listing them -- and
the prompt carries only the catalog; the agent cats the files it needs.
This module is the delivery side of that play and stays a PURE FUNCTION
(zero disk): ``resolve`` renders the catalog text and the folder's
manifest, and materializing them into a real directory is the composite
layer's job (:mod:`expedistill.composites.skill_folder`, called by the
M3 execution path).

The manifest rides the unit rows' ``aux["skill_folder"]`` and stays
LIGHTWEIGHT -- the skill contents never travel with it (the M2 rev-1
ruling: progressive disclosure is the whole point of this play, so no
stage may quietly become "inject everything somewhere else"). The
manifest carries the catalog text, the file-name mapping and WHICH
bank it resolves against (``bank_fingerprint`` -- the same digest
``composites.phase_bank``'s bank summary writes, ``bank_reference``
over the phase artifact's full member list, never a new counter); the
skill contents' only source is the phase bank, and the composite
materialization reads them from it (with the fingerprint checked) --
the unit row stays as light as the old top-k one.

Frozen signature (R5): ``resolve(bank, tasks, params)`` with ``bank``
the curated skill bank, ``tasks`` the phase's tasks, ``params`` the
empty FolderParams. One row per test task, every row the same fixed
(bank digest, catalog) pair -- the accepted-set bookkeeping mirrors
whole_bank exactly: ``skills_injected`` is the ACCEPTED bank's digest
(:func:`units.accepted_bank`, the pre-phase-artifact meaning of "the
bank"), so the unit ids join the reuse/decision plumbing unchanged.
"""

import re
from collections.abc import Mapping
from typing import Any, Dict, List, Sequence, Union

from expedistill.core.contracts import ExecutionUnitList
from expedistill.core.models import (
    SKILL_MARKERS,
    Skill,
)
from expedistill.inject import bank_reference
from expedistill.methods.delivery import FolderParams
from expedistill.methods.delivery import units

__all__ = ["CATALOG_FILENAME", "render_catalog", "resolve",
           "skill_filename", "skill_folder_manifest"]

#: The catalog file's name inside the folder (one fixed name: every
#: consumer -- the prompt, the M3 materialization, the M4 cat-audit --
#: keys on it, so it is a constant, not a parameter).
CATALOG_FILENAME = "catalog.md"

#: The per-skill file name's safe alphabet, the same set phase labels
#: are held to (composites.phase_bank): everything else in a skill id
#: (its benchmark prefix carries ':' and its chain carries '/') maps to
#: '_'. The mapping is deterministic but not injective, so the manifest
#: builder refuses two skills whose names collide rather than letting
#: one file silently shadow another.
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")

#: The directory line's description budget. G2 / G6 / G11 summaries
#: are held to SKILL_SUMMARY_MAX_CHARS (500) at write time, but the K5
#: merge product composes its summary from the sources' summaries and
#: is deliberately NOT held to that cap (no model wrote it) -- so the
#: catalog line, whose whole point is to stay one readable line, clips
#: the description here and says so, never silently.
_LINE_MAX_CHARS = 200
_TRUNCATION_MARK = " [truncated]"

#: The section the empty-summary fallback reads ([When to use]); it runs
#: to the next section marker (SKILL_MARKERS) or the end of the content.
_WHEN_TO_USE = SKILL_MARKERS[0]

#: The description of a skill with neither a summary nor a [When to
#: use] section (a markerless body): an explicit placeholder, never a
#: silently blank column.
_NO_DESCRIPTION = "(no summary)"

#: The catalog's fixed header: the usage rules the M2 ruling spelled
#: out (read the catalog first, cat only what the step needs, do not
#: open many files at once), then one line per skill in the fixed
#: "<skill id> | <file to cat> | <one-line description>" shape.
_HEADER = "\n".join([
    "## Skill folder catalog",
    "",
    "Usage rules:",
    '- Read this catalog before acting: every line below lists one '
    'skill of this folder as "<skill id> | <file to cat> | '
    '<one-line description>".',
    "- cat only the skill file the current step needs, one at a time.",
    "- Do not open many skill files at once; come back to this catalog "
    "when a later step needs another one.",
    "",
])


def _field(record: Union[Skill, Mapping[str, Any]], name: str,
           default: Any = None) -> Any:
    """Read one field from a Skill object or a bank row mapping (the
    same both-shapes contract the rest of the delivery family holds)."""
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _one_line(text: str) -> str:
    """Collapse every whitespace run (newlines included) to one space."""
    return re.sub(r"\s+", " ", text).strip()


def skill_filename(skill_id: str) -> str:
    """The folder file name of one skill: the skill id over the safe
    alphabet (everything else mapped to '_') plus ``.md``.

    Deterministic in the skill id alone -- the manifest, the folder on
    disk and the M4 cat-audit all derive the same name from the same
    id, so nothing but the id needs to travel between them."""
    if not isinstance(skill_id, str) or not skill_id:
        raise ValueError(
            f"a skill file name needs a non-empty string skill_id, got "
            f"{skill_id!r}")
    return _SAFE_CHARS.sub("_", skill_id) + ".md"


def when_to_use_line(content: str) -> str:
    """The [When to use] section body as one line, '' when absent.

    The empty-summary fallback the M2 ruling froze (a v2-migrated
    skill has no summary, so its directory line falls back to the
    section that says WHEN the skill applies). Since the 2026-10-19
    ruling every skill carries this section; a body without it (a
    pre-ruling failure reference, a markerless body) falls back to the
    explicit placeholder instead. The section body runs
    from the marker line's remainder (the renderer puts the first
    sentence on the marker line) to the next section marker or the end
    of the content, collapsed to one line the same way the D9 block
    collapses its bullets."""
    body: List[str] = []
    in_section = False
    for line in content.splitlines():
        if in_section and any(line.startswith(section_marker)
                              for section_marker in SKILL_MARKERS):
            break
        if not in_section:
            if line.startswith(_WHEN_TO_USE):
                in_section = True
                body.append(line[len(_WHEN_TO_USE):])
            continue
        body.append(line)
    if not in_section:
        return ""
    return _one_line(" ".join(body))


def _description(record: Union[Skill, Mapping[str, Any]]) -> str:
    """One skill's catalog description: the summary, else the [When to
    use] section, else the
    explicit placeholder -- then one line, then the explicit clip."""
    description = _one_line(_field(record, "summary", "") or "")
    if not description:
        description = when_to_use_line(_field(record, "content", ""))
    if not description:
        return _NO_DESCRIPTION
    if len(description) > _LINE_MAX_CHARS:
        return (description[:_LINE_MAX_CHARS].rstrip()
                + _TRUNCATION_MARK)
    return description


def render_catalog(skills: Sequence[Union[Skill, Mapping[str, Any]]]
                   ) -> str:
    """The catalog file's exact bytes: the fixed usage header, then one
    line per skill, ``<skill id> | <file to cat> | <one-line
    description>``, in the given (bank artifact) order, one trailing
    newline."""
    lines = [_HEADER]
    for record in skills:
        skill_id = _field(record, "skill_id")
        lines.append("{} | {} | {}".format(
            skill_id, skill_filename(skill_id), _description(record)))
    return "\n".join(lines) + "\n"


def skill_folder_manifest(bank: Sequence[Union[Skill, Mapping[str, Any]]]
                          ) -> Dict[str, Any]:
    """The folder manifest a folder unit carries (M3's materialization
    input)::

        {"bank_fingerprint": "bank:<12 hex>",   # the phase artifact's
                                                # bank-summary digest
         "catalog_filename": "catalog.md",
         "catalog_text": "<the catalog file's exact bytes>",
         "files": [{"skill_id", "version", "filename"},
                   ...]}                      # accepted set, bank order

    The file list is the bank's ACCEPTED members only (whole_bank's
    bookkeeping). It is a NAME MAPPING, not the skills: no content
    travels with the manifest (the rev-1 ruling -- progressive
    disclosure must survive every stage; the composite materialization
    reads each skill's body from the phase bank, checking it against
    the fingerprint below). The fingerprint is
    ``bank_reference`` over the FULL bank -- the same digest the
    phase's bank_summary.json writes -- which names which version of
    the bank this folder is without inventing a new version number.
    A collision (two skill ids mapping to one file name, or one mapping
    to the catalog's own name) is refused loudly: a silent shadow would
    be a wrong folder, and an empty accepted set renders NO catalog at
    all (the D9 null-state lesson: an empty injection is the empty
    string, never a header claiming experience exists)."""
    accepted = units.accepted_bank(bank)
    files: List[Dict[str, Any]] = []
    owner_of: Dict[str, str] = {}
    for record in accepted:
        skill_id = _field(record, "skill_id")
        filename = skill_filename(skill_id)
        if filename == CATALOG_FILENAME:
            raise ValueError(
                f"skill {skill_id!r} maps to the catalog's own file name "
                f"{filename!r}: the folder cannot hold both")
        if filename in owner_of:
            raise ValueError(
                f"skills {owner_of[filename]!r} and {skill_id!r} both map "
                f"to file name {filename!r}: one file would silently "
                "shadow the other -- a human must look at this bank")
        owner_of[filename] = skill_id
        files.append({
            "skill_id": skill_id,
            "version": _field(record, "version", 1),
            "filename": filename,
        })
    return {
        "bank_fingerprint": bank_reference(bank),
        "catalog_filename": CATALOG_FILENAME,
        "catalog_text": render_catalog(accepted) if files else "",
        "files": files,
    }


def resolve(bank: Sequence[Union[Skill, Mapping[str, Any]]], tasks: Any,
            params: FolderParams) -> ExecutionUnitList:
    """resolve(bank, tasks, params) -> ExecutionUnitList  [frozen, R5]:
    one row per task, input order (whole_bank's shape), every row the
    same fixed (accepted-bank digest, catalog) pair and the folder
    manifest in ``aux["skill_folder"]`` (shared read-only across the
    phase's rows -- it is phase-level data; a per-unit consumer must
    copy before mutating).

    In both modes ``skills_block`` receives the catalog text and the
    manifest's ``catalog_text`` is populated, so the materialization
    path (``composites.skill_folder``) and the M4 read-stats
    (``composites.skill_reads``) are unaffected by the flag.

    ``params.inline_catalog`` is a signal to the benchmark prompt layer:

    ``False`` (default) -- the catalog text lives in the unit row but
    the folder prompt template is expected to ignore the block slot; the
    agent must ``cat`` the catalog file from the mounted folder to see
    it (the original M2 design).

    ``True`` -- the caller should switch to a prompt template that
    renders the block slot, so the catalog text rides the task prompt
    verbatim.  The agent sees the catalog without having to find and cat
    the catalog file first, which the 2026-10-19 folder-run audit found
    the weak model rarely did (26/84 attempts hit a wrong path and saw
    no catalog lines)."""
    accepted = units.accepted_bank(bank)
    manifest = skill_folder_manifest(bank)
    injected = bank_reference(accepted)
    # inline_catalog=True: the catalog text rides the task prompt directly;
    # inline_catalog=False (default): the block is still the catalog text, but
    # the folder prompt template ignores this slot -- the agent is expected to
    # cat the catalog file from the mounted folder instead.  Either way the
    # manifest carries catalog_text for materialization and read-stats.
    block = manifest["catalog_text"]
    return [units.make_row(units.task_ref(task), injected, block,
                           {"skill_folder": manifest,
                            "inline_catalog": params.inline_catalog})
            for task in tasks]
