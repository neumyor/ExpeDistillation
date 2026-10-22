"""The folder delivery driver: one Agent Skill directory per skill.

The agent is handed ONE read-only folder of skills. Each accepted skill
is a directory containing ``SKILL.md`` with YAML frontmatter (``name``
and ``description``) and the skill's four-section body. Harbor's
Terminus agent discovers those files itself, renders an
``<available_skills>`` list with each exact location, and appends it to
the initial prompt; the agent cats the skill it needs. The catalog file
is a fallback, not the primary directory.

This module is the delivery side and stays a PURE FUNCTION (zero disk):
``resolve`` renders the catalog text and the folder's manifest, and
materializing them into a real directory is the composite layer's job
(:mod:`expedistill.composites.skill_folder`, called by the M3 execution
path).

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
FolderParams. One row per test task, every row the same fixed
(bank digest, catalog) pair -- the accepted-set bookkeeping mirrors
whole_bank exactly: ``skills_injected`` is the ACCEPTED bank's digest
(:func:`units.accepted_bank`, the pre-phase-artifact meaning of "the
bank"), so the unit ids join the reuse/decision plumbing unchanged.
"""

import hashlib
import re
from collections.abc import Mapping
from typing import Any, Dict, List, Sequence, Union

from expedistill.core.contracts import ExecutionUnitList
from expedistill.core.models import Skill
from expedistill.inject import bank_reference
from expedistill.inject.skill_text import (
    DEFAULT_DESCRIPTION_MODE,
    description_text,
    one_line,
    validate_description_mode,
    when_to_use_line,
)
from expedistill.methods.delivery import FolderParams
from expedistill.methods.delivery import units

__all__ = ["CATALOG_FILENAME", "render_catalog", "resolve",
           "skill_dirname", "skill_filename", "skill_folder_manifest",
           "when_to_use_line"]

#: The catalog file's name inside the folder (one fixed name: every
#: consumer -- the materialization and the M4 cat-audit -- keys on it,
#: so it is a constant, not a parameter).
CATALOG_FILENAME = "catalog.md"

#: The per-skill directory's safe alphabet: lowercase letters, digits
#: and hyphens, the Agent Skills format's name rule. Everything else in
#: a skill id (its benchmark prefix carries ':' and its chain carries
#: '/') maps to '-'; runs of '-' collapse. The mapping is deterministic
#: but not injective, so the manifest builder refuses two skills whose
#: names collide rather than letting one file silently shadow another.
_SAFE_CHARS = re.compile(r"[^a-z0-9]+")

#: The Agent Skills name ceiling. Names longer than this get a short
#: digest suffix; the directory and the frontmatter use the same value.
_SKILL_NAME_MAX_CHARS = 64

#: Words the Agent Skills name rule reserves.
_RESERVED_NAME_WORDS = ("anthropic", "claude")

#: The per-skill file name under its directory.
_SKILL_FILE_NAME = "SKILL.md"

#: The directory line's description budget. The full description lives
#: in SKILL.md; the catalog line stays one readable line.
_LINE_MAX_CHARS = 200
_TRUNCATION_MARK = " [truncated]"

#: The description of a skill with neither a summary nor a [When to
#: use] section (a markerless body): an explicit placeholder, never a
#: silently blank column.
_NO_DESCRIPTION = "(no summary)"

#: The catalog's fixed header: the usage rules the M2 ruling spelled
#: out (read the catalog first, cat only what the step needs, do not
#: open many files at once), then one line per skill in the fixed
#: "<skill id> | <file to cat> | <one-line description>" shape.
_HEADER = "\n".join([
    "## Skill folder catalog (fallback)",
    "",
    "The task prompt normally lists the available skills in an",
    "<available_skills> section, with each exact SKILL.md location. Use",
    "this file only when that list is missing. Every line below lists one skill as",
    '"<skill id> | <file to cat> | <one-line description>".',
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


def skill_dirname(skill_id: str) -> str:
    """One skill's directory name under the Agent Skills rule.

    Lowercase letters, digits and hyphens only, 1-64 characters, no
    reserved word. A long id is truncated and given a short digest
    suffix; a collision between two ids is refused by the manifest
    builder, never silently shadowed.
    """
    if not isinstance(skill_id, str) or not skill_id:
        raise ValueError(
            f"a skill directory needs a non-empty string skill_id, got "
            f"{skill_id!r}")
    name = _SAFE_CHARS.sub("-", skill_id.lower()).strip("-")
    if not name:
        name = "skill-" + hashlib.sha256(
            skill_id.encode("utf-8")).hexdigest()[:8]
    if len(name) > _SKILL_NAME_MAX_CHARS:
        digest = hashlib.sha256(skill_id.encode("utf-8")).hexdigest()[:8]
        name = name[:_SKILL_NAME_MAX_CHARS - len(digest) - 1].rstrip("-")
        name = f"{name}-{digest}"
    for word in _RESERVED_NAME_WORDS:
        if word in name:
            raise ValueError(
                f"skill id {skill_id!r} maps to directory name {name!r}, "
                f"which contains the reserved word {word!r}; rename the "
                "skill before delivering it as an Agent Skill")
    return name


def skill_filename(skill_id: str) -> str:
    """The folder-relative path of one skill: ``<name>/SKILL.md``.

    Deterministic in the skill id alone -- the manifest, the folder on
    disk and the M4 cat-audit all derive the same path from the same
    id, so nothing but the id needs to travel between them.
    """
    return f"{skill_dirname(skill_id)}/{_SKILL_FILE_NAME}"


def _description(record: Union[Skill, Mapping[str, Any]],
                 description_mode: str) -> str:
    """One skill's catalog description under the configured mode, then
    the explicit catalog clip.

    A skill with neither a summary nor a [When to use] section keeps the
    catalog's explicit placeholder; the SKILL.md frontmatter renderer
    uses a non-empty generic fallback instead, because the Agent Skills
    format rejects an empty description.
    """
    summary = one_line(_field(record, "summary", "") or "")
    trigger = when_to_use_line(_field(record, "content", "") or "")
    if not summary and not trigger:
        return _NO_DESCRIPTION
    description = description_text(record, description_mode)
    if len(description) > _LINE_MAX_CHARS:
        return (description[:_LINE_MAX_CHARS].rstrip()
                + _TRUNCATION_MARK)
    return description


def render_catalog(skills: Sequence[Union[Skill, Mapping[str, Any]]],
                   description_mode: str = DEFAULT_DESCRIPTION_MODE) -> str:
    """The catalog file's exact bytes: the fixed usage header, then one
    line per skill, ``<skill id> | <file to cat> | <one-line
    description>``, in the given (bank artifact) order, one trailing
    newline."""
    validate_description_mode(description_mode)
    lines = [_HEADER]
    for record in skills:
        skill_id = _field(record, "skill_id")
        lines.append("{} | {} | {}".format(
            skill_id, skill_filename(skill_id),
            _description(record, description_mode)))
    return "\n".join(lines) + "\n"


def skill_folder_manifest(bank: Sequence[Union[Skill, Mapping[str, Any]]],
                          description_mode: str = DEFAULT_DESCRIPTION_MODE
                          ) -> Dict[str, Any]:
    """The folder manifest a folder unit carries (M3's materialization
    input)::

        {"bank_fingerprint": "bank:<12 hex>",   # the phase artifact's
                                                # bank-summary digest
         "catalog_filename": "catalog.md",
         "catalog_text": "<the catalog file's exact bytes>",
         "description_mode": "summary_then_when_to_use",
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
    A collision (two skill ids mapping to one directory) is refused
    loudly: a silent shadow would be a wrong folder, and an empty
    accepted set renders NO catalog at all (the D9 null-state lesson:
    an empty injection is the empty string, never a header claiming
    experience exists)."""
    validate_description_mode(description_mode)
    accepted = units.accepted_bank(bank)
    files: List[Dict[str, Any]] = []
    owner_of: Dict[str, str] = {}
    for record in accepted:
        skill_id = _field(record, "skill_id")
        filename = skill_filename(skill_id)
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
        "catalog_text": (render_catalog(accepted, description_mode)
                         if files else ""),
        "description_mode": description_mode,
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

    ``skills_block`` receives the catalog text as a fallback. Harbor's
    Terminus agent discovers each ``<skill>/SKILL.md`` itself and
    renders the ``<available_skills>`` list; the catalog file is not
    the primary directory and no benchmark prompt needs to inline it.
    ``params.description_mode`` is carried in the manifest so the
    materializer and the catalog use the same composition rule.
    """
    accepted = units.accepted_bank(bank)
    manifest = skill_folder_manifest(bank, params.description_mode)
    injected = bank_reference(accepted)
    block = manifest["catalog_text"]
    return [units.make_row(units.task_ref(task), injected, block,
                           {"skill_folder": manifest})
            for task in tasks]
