"""Import an external method's artifact as the bank of a test-only run.

The paper's baselines (SkillOpt, EvoSkill, Trace2Skill, GEPA, ExpeL, AgentBrew)
train in their own repositories. What this repository evaluates is the
artifact they leave behind, under the same test phase, executor, delivery
machinery and control arm as our own method. This module turns such an
artifact into the one thing a test phase reads: the run's skill bank.

What a test-only run (``sequence: [test]``) reads, worked out from the code:

* ``<run>/skill_bank.jsonl``: with no learn phase on disk, the test phase's
  ``phase_disk.bank_skills`` falls back to ``Store.bank()``, which is this
  file. Every delivery (``folder``, ``whole_bank``) keeps only
  the members whose status is ``active``.
* Nothing else. ``manifest.json`` is written by the first ``expedistill run``
  (the stage-0 freeze), after the importer; the freeze does not look at the
  bank, so the bank's identity is recorded in ``BANK_ORIGIN.md`` only.

The importer also writes each skill into the object store
(``stage3_generation/skills``), so the bank is visible there exactly as a
finished learning run would leave it; no test-phase code reads it.

Source formats (deterministic, no model, no network):

* ``markdown_file``: one document is one skill.
* ``skill_folder``: ``<source>/<name>/SKILL.md`` with YAML front matter
  (``description`` is the summary); one skill per directory, keyed by the
  directory name.
* ``notes_jsonl``: one JSON object per line with ``text`` and optional
  ``id``, ``title``; one skill per line.

A skill's ``verbatim_text`` is the source text (front matter removed,
newlines normalised, outer whitespace stripped): the baseline's words reach
the weak model unchanged, and the folder delivery's YAML ``description`` is
the source's own summary. The structured skill fields stay empty. Imported
skills carry no trajectory and belong to no subtask type, so they use the
``import/<origin>/<key>`` id namespace, ``subtask_type`` is empty and
``source_subtasks`` is empty. The skills are stored ``active``.
"""

import json
import re
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    NamedTuple,
    Optional,
    Tuple,
)

import yaml

from expedistill.core.models import Skill
from expedistill.infra.manifest import MANIFEST_NAME
from expedistill.infra.reliability import ConfigError
from expedistill.infra.runlock import RunLock
from expedistill.infra.store import KIND_DIRS, Store, write_jsonl
from expedistill.inject.skill_text import one_line
from expedistill.methods.delivery.folder_delivery import skill_dirname

__all__ = [
    "BANK_ORIGIN_NAME",
    "SKILL_ID_NAMESPACE",
    "SOURCE_FORMATS",
    "import_external_bank",
]

SOURCE_FORMATS = ("markdown_file", "skill_folder", "notes_jsonl")

#: The provenance record written at the run root.
BANK_ORIGIN_NAME = "BANK_ORIGIN.md"

#: First segment of every imported skill id.
SKILL_ID_NAMESPACE = "import"

#: The bank file the test phase falls back to (``Store.bank``).
_BANK_NAME = "skill_bank.jsonl"

#: Origin names are one Agent Skills style slug.
_ORIGIN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_ORIGIN_MAX_CHARS = 24

#: Longest item key inside a skill id. Longer delivery directory names are
#: shortened with a digest suffix by ``skill_dirname``, so distinct keys stay
#: distinct; this only keeps the object file name under file system limits.
_KEY_MAX_CHARS = 100

#: A summary is one short line; the folder delivery clips at 1024 anyway.
_SUMMARY_MAX_CHARS = 300

_SKILL_FILE_NAME = "SKILL.md"

_HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
_SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)|[。！？]")
_NON_KEY_RE = re.compile(r"[^a-z0-9]+")


class _Item(NamedTuple):
    """One source item before it becomes a Skill."""

    key: str
    text: str
    summary: str


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _split_front_matter(text: str, where: str) -> Tuple[Dict[str, Any], str]:
    """(front matter mapping, body). No leading ``---`` line means no front
    matter; an opening line without a closing one, or a block that is not a
    YAML mapping, is a ConfigError (a half-read file would be a wrong skill)."""
    text = _normalise(text)
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            try:
                data = yaml.safe_load("\n".join(lines[1:index]))
            except yaml.YAMLError as exc:
                raise ConfigError(
                    f"{where}: front matter is not valid YAML: {exc}"
                ) from exc
            if not isinstance(data, dict):
                raise ConfigError(
                    f"{where}: front matter must be a YAML mapping")
            return data, "\n".join(lines[index + 1:]).strip()
    raise ConfigError(f"{where}: front matter is opened but never closed")


def _derive_summary(text: str) -> str:
    """The first heading outside code fences, else the first sentence of the
    first paragraph."""
    in_fence = False
    paragraph: List[str] = []
    first_paragraph: Optional[str] = None
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if match is not None and one_line(match.group(1)):
            return one_line(match.group(1))[:_SUMMARY_MAX_CHARS].rstrip()
        if line.strip():
            if first_paragraph is None:
                paragraph.append(line.strip())
        elif paragraph and first_paragraph is None:
            first_paragraph = one_line(" ".join(paragraph))
    if first_paragraph is None and paragraph:
        first_paragraph = one_line(" ".join(paragraph))
    if not first_paragraph:
        return ""
    end = _SENTENCE_END_RE.search(first_paragraph)
    sentence = first_paragraph[:end.end()] if end else first_paragraph
    return sentence[:_SUMMARY_MAX_CHARS].rstrip()


def _key_of(text: str, where: str) -> str:
    key = _NON_KEY_RE.sub("-", text.lower()).strip("-")
    key = key[:_KEY_MAX_CHARS].rstrip("-")
    if not key:
        raise ConfigError(f"{where}: {text!r} gives an empty skill key")
    return key


# ---------------------------------------------------------------------------
# Readers: one per source format
# ---------------------------------------------------------------------------

def _read_markdown_file(path: Path) -> Tuple[List[_Item], List[str]]:
    if not path.is_file():
        raise ConfigError(f"markdown_file source {path} is not a file")
    meta, body = _split_front_matter(
        path.read_text(encoding="utf-8"), str(path))
    if not body:
        raise ConfigError(f"{path} has no text")
    description = meta.get("description")
    summary = (one_line(description) if isinstance(description, str)
               else "") or _derive_summary(body)
    if not summary:
        raise ConfigError(f"{path}: no heading or sentence to summarise")
    name = meta.get("name")
    key = _key_of(name if isinstance(name, str) and name.strip()
                  else path.stem, str(path))
    return [_Item(key, body, summary)], []


def _read_skill_folder(path: Path) -> Tuple[List[_Item], List[str]]:
    if not path.is_dir():
        raise ConfigError(f"skill_folder source {path} is not a directory")
    items: List[_Item] = []
    ignored: List[str] = []
    for directory in sorted(path.iterdir()):
        if directory.name.startswith(".") or not directory.is_dir():
            continue
        skill_file = directory / _SKILL_FILE_NAME
        if not skill_file.is_file():
            ignored.append(f"{directory.name}/ (no {_SKILL_FILE_NAME})")
            continue
        where = f"{directory.name}/{_SKILL_FILE_NAME}"
        meta, body = _split_front_matter(
            skill_file.read_text(encoding="utf-8"), where)
        description = meta.get("description")
        summary = one_line(description) if isinstance(description, str) \
            else ""
        if not summary:
            raise ConfigError(f"{where}: front matter has no description")
        if not body:
            raise ConfigError(f"{where}: no text after the front matter")
        # The directory name is unique by construction; front matter names
        # are not (Trace2Skill's released skills all say "xlsx").
        items.append(_Item(_key_of(directory.name, where), body, summary))
        ignored.extend(
            f"{directory.name}/{extra.relative_to(directory).as_posix()}"
            for extra in sorted(directory.rglob("*"))
            if extra.is_file() and extra != skill_file)
    if not items:
        raise ConfigError(
            f"{path} holds no <name>/{_SKILL_FILE_NAME} directory")
    return items, ignored


def _read_notes_jsonl(path: Path) -> Tuple[List[_Item], List[str]]:
    if not path.is_file():
        raise ConfigError(f"notes_jsonl source {path} is not a file")
    items: List[_Item] = []
    number = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        number += 1
        where = f"{path.name} note {number}"
        try:
            row = json.loads(raw)
        except ValueError as exc:
            raise ConfigError(f"{where}: not valid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ConfigError(f"{where}: must be a JSON object")
        text = row.get("text")
        if not isinstance(text, str) or not _normalise(text):
            raise ConfigError(f"{where}: needs a non-empty string 'text'")
        text = _normalise(text)
        for field_name in ("title",):
            if field_name in row and not isinstance(row[field_name], str):
                raise ConfigError(f"{where}: '{field_name}' must be a string")
        note_id = row.get("id")
        if isinstance(note_id, bool) or not (
                note_id is None or isinstance(note_id, (str, int))):
            raise ConfigError(f"{where}: 'id' must be a string or integer")
        key = (_key_of(str(note_id), where) if note_id is not None
               else f"note-{number:04d}")
        summary = (one_line(row.get("title") or "")
                   or _derive_summary(text))
        if not summary:
            raise ConfigError(f"{where}: no title or sentence to summarise")
        items.append(_Item(key, text, summary))
    if not items:
        raise ConfigError(f"{path} holds no notes")
    return items, []


_READERS: Dict[str, Callable[[Path], Tuple[List[_Item], List[str]]]] = {
    "markdown_file": _read_markdown_file,
    "skill_folder": _read_skill_folder,
    "notes_jsonl": _read_notes_jsonl,
}


# ---------------------------------------------------------------------------
# Skills, run directory
# ---------------------------------------------------------------------------

def _check_origin(origin: str) -> str:
    if (not isinstance(origin, str) or _ORIGIN_RE.fullmatch(origin) is None
            or len(origin) > _ORIGIN_MAX_CHARS):
        raise ConfigError(
            "origin must be lowercase letters, digits and single hyphens, "
            f"at most {_ORIGIN_MAX_CHARS} characters (e.g. 'evoskill'), "
            f"got {origin!r}")
    return origin


def _build_skills(origin: str, items: List[_Item]) -> List[Skill]:
    """One active Skill per item, in item order; two items that share a
    key or a delivery directory are refused rather than one shadowing the
    other."""
    skills: List[Skill] = []
    owner_of_key: Dict[str, str] = {}
    owner_of_dir: Dict[str, str] = {}
    for item in items:
        skill_id = f"{SKILL_ID_NAMESPACE}/{origin}/{item.key}"
        if item.key in owner_of_key:
            raise ConfigError(
                f"two source items map to the skill key {item.key!r}; "
                "give them distinct names or ids in the source")
        owner_of_key[item.key] = skill_id
        try:
            dirname = skill_dirname(skill_id)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        if dirname in owner_of_dir:
            raise ConfigError(
                f"skills {owner_of_dir[dirname]!r} and {skill_id!r} share "
                f"the delivery directory {dirname!r}")
        owner_of_dir[dirname] = skill_id
        skills.append(Skill(
            skill_id=skill_id, subtask_type="", source_subtasks=[],
            summary=item.summary, when_to_use="", steps=[],
            not_applicable_when="", verbatim_text=item.text, version=1,
            status="active"))
    return skills


def _require_fresh_run_dir(run_dir: Path) -> None:
    """Refuse a directory that already carries a manifest, a bank or skills:
    an import writes the bank once, before the first run freezes anything."""
    taken = [name for name in (MANIFEST_NAME, _BANK_NAME, BANK_ORIGIN_NAME)
             if (run_dir / name).exists()]
    skills_dir = run_dir / KIND_DIRS["skills"]
    if skills_dir.is_dir() and any(skills_dir.glob("*.json")):
        taken.append(KIND_DIRS["skills"])
    if taken:
        raise ConfigError(
            f"{run_dir} already holds {', '.join(taken)}; an import needs a "
            "new run directory")


def _render_origin(*, source: Path, source_format: str, origin: str,
                   skills: List[Skill], ignored: List[str]) -> str:
    lines = [
        "# 外部技能库导入",
        "",
        f"- 来源路径：{source}",
        f"- 来源格式：{source_format}",
        f"- 来源方法（origin）：{origin}",
        f"- 导入条数：{len(skills)}",
        "- 转换：每个来源条目成为一条在用（active）的技能，版本 1，"
        "正文逐字保留，摘要取自来源",
        "- 用途：只跑 test 相位的 run（sequence: [test]），技能库由这些"
        "技能组成",
    ]
    if ignored:
        lines += ["", f"## 未导入的文件（{len(ignored)} 个）", ""]
        lines += [f"- {entry}" for entry in ignored]
    return "\n".join(lines) + "\n"


def import_external_bank(source: Any, source_format: str, run_dir: Any, *,
                         origin: str) -> Dict[str, Any]:
    """Write ``source`` into ``run_dir`` as the bank of a test-only
    run and return a summary (``run_dir``, ``origin``, ``source``,
    ``source_format``, ``n_skills``, ``skill_ids``, ``ignored_files``).

    The source is read and converted before anything is written, so an
    unreadable source leaves no directory behind. ``run_dir`` must not hold
    a manifest, a bank or skills; the run lock is held while writing. The
    files written are the skill objects, ``skill_bank.jsonl`` and
    ``BANK_ORIGIN.md``, in that order, so a crash leaves a directory the
    next import refuses and the operator deletes.
    """
    if source_format not in SOURCE_FORMATS:
        raise ConfigError(
            f"source_format must be one of {SOURCE_FORMATS}, got "
            f"{source_format!r}")
    origin = _check_origin(origin)
    source_path = Path(source).expanduser().resolve()
    items, ignored = _READERS[source_format](source_path)
    skills = _build_skills(origin, items)

    run_path = Path(run_dir)
    with RunLock(run_path):
        _require_fresh_run_dir(run_path)
        store = Store(run_path)
        for skill in skills:
            store.save(skill, "skills")
        write_jsonl(run_path / _BANK_NAME,
                    [skill.to_dict() for skill in skills])
        (run_path / BANK_ORIGIN_NAME).write_text(
            _render_origin(source=source_path, source_format=source_format,
                           origin=origin, skills=skills, ignored=ignored),
            encoding="utf-8")
    return {
        "run_dir": str(run_path),
        "origin": origin,
        "source": str(source_path),
        "source_format": source_format,
        "n_skills": len(skills),
        "skill_ids": [skill.skill_id for skill in skills],
        "ignored_files": ignored,
    }
