"""import_external_bank: the three source formats, the ids it assigns, and
the run-directory contract (what is written, what is refused).

The end-to-end check that a freeze and a test phase deliver exactly the
imported skills is test_import_bank_test_phase.py.
"""

import json
from pathlib import Path

import pytest

from expedistill.core.models import Skill
from expedistill.infra.manifest import MANIFEST_NAME
from expedistill.infra.reliability import ConfigError
from expedistill.infra.runlock import LOCK_NAME
from expedistill.infra.store import Store, read_jsonl
from expedistill.methods.delivery.folder_delivery import skill_dirname
from expedistill.stages.import_bank import (
    BANK_ORIGIN_NAME,
    SKILL_ID_NAMESPACE,
    import_external_bank,
)

NOTE_ONE = "Check the stove before the pan.\nThen turn the knob."
NOTE_TWO = "Prefer exact quotes from the passage."


def _bank_rows(run_dir: Path):
    return read_jsonl(run_dir / "skill_bank.jsonl")


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_skill_folder(root: Path) -> Path:
    _write(root / "search-first" / "SKILL.md",
           "---\nname: search-first\n"
           "description: Search the full name before answering.\n---\n\n"
           "# Search first\n\nSearch the full name.\n")
    _write(root / "search-first" / "references" / "notes.md", "extra\n")
    _write(root / "quote-exactly" / "SKILL.md",
           "---\nname: quote-exactly\n"
           "description: >\n  Quote the passage\n  exactly.\n---\n"
           "Quote it.\n")
    (root / "empty-dir").mkdir()
    return root


# ---------------------------------------------------------------------------
# markdown_file
# ---------------------------------------------------------------------------

def test_a_markdown_file_is_one_skill_summarised_by_its_first_heading(
        tmp_path):
    source = _write(tmp_path / "Playbook.md",
                    "Intro line.\n\n# Search Playbook #\n\nBody text.\n")

    result = import_external_bank(source, "markdown_file", tmp_path / "run",
                                  origin="skillopt")

    (row,) = _bank_rows(tmp_path / "run")
    assert row["skill_id"] == f"{SKILL_ID_NAMESPACE}/skillopt/playbook"
    assert row["summary"] == "Search Playbook"
    assert row["verbatim_text"] == "Intro line.\n\n# Search Playbook #\n\nBody text."
    assert row["status"] == "active" and row["version"] == 1
    assert result["n_skills"] == 1
    assert result["skill_ids"] == [row["skill_id"]]


@pytest.mark.parametrize("text, summary", [
    pytest.param("Always read the task twice. Then act.\nMore.\n",
                 "Always read the task twice.", id="first_sentence"),
    pytest.param("Run it.\n\n```\n# not a heading\n```\n", "Run it.",
                 id="hash_in_a_code_fence"),
])
def test_a_markdown_file_without_a_heading_is_summarised_by_its_first_sentence(
        tmp_path, text, summary):
    source = _write(tmp_path / "doc.md", text)

    import_external_bank(source, "markdown_file", tmp_path / "run",
                         origin="gepa")

    assert _bank_rows(tmp_path / "run")[0]["summary"] == summary


def test_a_markdown_file_with_front_matter_keeps_its_description(tmp_path):
    source = _write(tmp_path / "doc.md",
                    "---\nname: Wise Doc\ndescription: A wise doc.\n---\n"
                    "# Heading\n\nText.\n")

    import_external_bank(source, "markdown_file", tmp_path / "run",
                         origin="trace2skill")

    (row,) = _bank_rows(tmp_path / "run")
    assert row["skill_id"] == "import/trace2skill/wise-doc"
    assert row["summary"] == "A wise doc."
    assert row["verbatim_text"] == "# Heading\n\nText."


@pytest.mark.parametrize("text", ["", "\n\n  \n", "---\nname: x\n---\n"])
def test_an_empty_markdown_file_is_refused(tmp_path, text):
    source = _write(tmp_path / "doc.md", text)

    with pytest.raises(ConfigError):
        import_external_bank(source, "markdown_file", tmp_path / "run",
                             origin="gepa")
    assert not (tmp_path / "run").exists()


def test_an_unclosed_front_matter_is_refused(tmp_path):
    source = _write(tmp_path / "doc.md", "---\nname: x\n# Heading\n")

    with pytest.raises(ConfigError, match="never closed"):
        import_external_bank(source, "markdown_file", tmp_path / "run",
                             origin="gepa")


# ---------------------------------------------------------------------------
# skill_folder
# ---------------------------------------------------------------------------

def test_a_skill_folder_gives_one_skill_per_directory_in_name_order(
        tmp_path):
    source = _write_skill_folder(tmp_path / "skills")

    result = import_external_bank(source, "skill_folder", tmp_path / "run",
                                  origin="evoskill")

    rows = _bank_rows(tmp_path / "run")
    assert [row["skill_id"] for row in rows] == [
        "import/evoskill/quote-exactly", "import/evoskill/search-first"]
    assert [row["summary"] for row in rows] == [
        "Quote the passage exactly.",
        "Search the full name before answering."]
    assert rows[0]["verbatim_text"] == "Quote it."
    assert rows[1]["verbatim_text"] == "# Search first\n\nSearch the full name."
    # What the importer did not carry over is listed, not dropped quietly.
    assert result["ignored_files"] == [
        "empty-dir/ (no SKILL.md)", "search-first/references/notes.md"]
    origin_text = (tmp_path / "run" / BANK_ORIGIN_NAME).read_text(
        encoding="utf-8")
    assert "search-first/references/notes.md" in origin_text


def test_the_directory_name_keys_a_skill_whatever_its_front_matter_name(
        tmp_path):
    # Trace2Skill's released skills carry the same front matter name in
    # different directories.
    root = tmp_path / "skills"
    for directory in ("xlsx-35b", "xlsx-122b"):
        _write(root / directory / "SKILL.md",
               "---\nname: xlsx\ndescription: d\n---\nText.\n")

    result = import_external_bank(root, "skill_folder", tmp_path / "run",
                                  origin="trace2skill")

    assert result["skill_ids"] == ["import/trace2skill/xlsx-122b",
                                   "import/trace2skill/xlsx-35b"]


@pytest.mark.parametrize("skills, match", [
    pytest.param({"a": "---\nname: a\n---\nText.\n"}, "no description",
                 id="no_description"),
    pytest.param({}, "holds no", id="no_skill"),
    pytest.param({"a-b": "---\ndescription: d\n---\nText.\n",
                  "a_b": "---\ndescription: d\n---\nText.\n"},
                 "same key|skill key", id="one_key_twice"),
    pytest.param({"claude-tips": "---\nname: claude-tips\ndescription: d\n"
                                 "---\nText.\n"},
                 "reserved word", id="reserved_word"),
])
def test_a_bad_skill_folder_is_refused(tmp_path, skills, match):
    root = tmp_path / "skills"
    root.mkdir()
    for directory, text in skills.items():
        _write(root / directory / "SKILL.md", text)

    with pytest.raises(ConfigError, match=match):
        import_external_bank(root, "skill_folder", tmp_path / "run",
                             origin="evoskill")


# ---------------------------------------------------------------------------
# notes_jsonl
# ---------------------------------------------------------------------------

def _notes_file(tmp_path: Path) -> Path:
    rows = [
        {"id": "R1", "text": NOTE_ONE, "title": "Stove first",
         "scope": "kitchen"},
        {"text": NOTE_TWO},
        {"id": 7, "text": "Seven."},
    ]
    return _write(tmp_path / "notes.jsonl",
                  "\n".join(json.dumps(row) for row in rows) + "\n\n")


def test_notes_become_one_skill_each(tmp_path):
    import_external_bank(_notes_file(tmp_path), "notes_jsonl",
                         tmp_path / "run", origin="expel")

    rows = _bank_rows(tmp_path / "run")
    assert all(row["when_to_use"] == "" and row["steps"] == []
               and "group" not in row for row in rows)
    assert [row["skill_id"] for row in rows] == [
        "import/expel/r1", "import/expel/note-0002", "import/expel/7"]
    assert [row["summary"] for row in rows] == [
        "Stove first", "Prefer exact quotes from the passage.", "Seven."]
    assert rows[0]["verbatim_text"] == NOTE_ONE


@pytest.mark.parametrize("line", [
    "not json",
    "[1, 2]",
    json.dumps({"title": "no text"}),
    json.dumps({"text": "   "}),
    json.dumps({"text": "x", "id": True}),
])
def test_a_bad_note_is_refused_and_leaves_no_run_directory(tmp_path, line):
    source = _write(tmp_path / "notes.jsonl",
                    json.dumps({"text": "fine"}) + "\n" + line + "\n")

    with pytest.raises(ConfigError, match="note 2"):
        import_external_bank(source, "notes_jsonl", tmp_path / "run",
                             origin="expel")
    assert not (tmp_path / "run").exists()


def test_long_note_ids_that_differ_late_stay_distinct_skills(tmp_path):
    prefix = "a" * 70
    source = _write(tmp_path / "notes.jsonl",
                    json.dumps({"id": prefix + "-x", "text": "x."}) + "\n"
                    + json.dumps({"id": prefix + "-y", "text": "y."}) + "\n")

    result = import_external_bank(source, "notes_jsonl", tmp_path / "run",
                                  origin="expel")

    dirnames = {skill_dirname(skill_id) for skill_id in result["skill_ids"]}
    assert len(dirnames) == 2 and all(len(name) <= 64 for name in dirnames)
    store = Store(tmp_path / "run")
    assert sorted(store.ids("skills")) == sorted(result["skill_ids"])


def test_two_notes_with_one_id_are_refused(tmp_path):
    source = _write(tmp_path / "notes.jsonl",
                    json.dumps({"id": "a", "text": "x"}) + "\n"
                    + json.dumps({"id": "A", "text": "y"}) + "\n")

    with pytest.raises(ConfigError, match="skill key"):
        import_external_bank(source, "notes_jsonl", tmp_path / "run",
                             origin="expel")


# ---------------------------------------------------------------------------
# What an imported skill is
# ---------------------------------------------------------------------------

def test_imported_skills_are_active_members_of_the_bank_and_the_store(
        tmp_path):
    result = import_external_bank(_notes_file(tmp_path), "notes_jsonl",
                                  tmp_path / "run", origin="expel")

    store = Store(tmp_path / "run")
    from_bank = [Skill.from_dict(row) for row in store.bank()]
    from_store = [store.load("skills", skill_id, Skill)
                  for skill_id in result["skill_ids"]]
    assert [skill.skill_id for skill in from_bank] == result["skill_ids"]
    assert sorted(store.ids("skills")) == sorted(result["skill_ids"])
    assert from_store == from_bank
    for skill in from_bank:
        assert skill.status == "active"
        assert skill.discard_reason is None
        assert skill.subtask_type == ""
        assert skill.source_subtasks == []


def test_every_imported_skill_has_its_own_delivery_directory(tmp_path):
    result = import_external_bank(_notes_file(tmp_path), "notes_jsonl",
                                  tmp_path / "run", origin="expel")

    dirnames = [skill_dirname(skill_id) for skill_id in result["skill_ids"]]
    assert len(set(dirnames)) == len(dirnames)
    assert dirnames[0] == "import-expel-r1"


def test_the_same_source_gives_byte_identical_banks(tmp_path):
    source = _notes_file(tmp_path)

    import_external_bank(source, "notes_jsonl", tmp_path / "one",
                         origin="expel")
    import_external_bank(source, "notes_jsonl", tmp_path / "two",
                         origin="expel")

    assert (tmp_path / "one" / "skill_bank.jsonl").read_bytes() == \
        (tmp_path / "two" / "skill_bank.jsonl").read_bytes()


# ---------------------------------------------------------------------------
# The run directory
# ---------------------------------------------------------------------------

def test_an_import_writes_the_bank_the_store_and_the_origin_note_only(
        tmp_path):
    source = _notes_file(tmp_path)
    before = {path for path in tmp_path.rglob("*")}

    result = import_external_bank(source, "notes_jsonl", tmp_path / "run",
                                  origin="expel")

    run_dir = tmp_path / "run"
    written = {path for path in tmp_path.rglob("*")} - before
    assert all(run_dir in path.parents or path == run_dir
               for path in written)
    files = sorted(path.relative_to(run_dir).as_posix()
                   for path in run_dir.rglob("*") if path.is_file())
    assert files == sorted(
        [BANK_ORIGIN_NAME, "skill_bank.jsonl"]
        + [f"stage3_generation/skills/{Store(run_dir).path_for('skills', i).name}"
           for i in result["skill_ids"]])
    # No freeze, no lock left behind: the first `run` writes the manifest.
    assert not (run_dir / MANIFEST_NAME).exists()
    assert not (run_dir / LOCK_NAME).exists()


def test_the_origin_note_names_source_format_origin_and_count(tmp_path):
    source = _notes_file(tmp_path)

    import_external_bank(source, "notes_jsonl", tmp_path / "run",
                         origin="expel")

    text = (tmp_path / "run" / BANK_ORIGIN_NAME).read_text(encoding="utf-8")
    assert str(source.resolve()) in text
    assert "notes_jsonl" in text and "expel" in text
    assert "导入条数：3" in text


def test_a_run_directory_with_a_manifest_is_refused(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / MANIFEST_NAME).write_text("{}", encoding="utf-8")

    with pytest.raises(ConfigError, match=MANIFEST_NAME):
        import_external_bank(_notes_file(tmp_path), "notes_jsonl", run_dir,
                             origin="expel")
    assert not (run_dir / "skill_bank.jsonl").exists()


def test_a_run_directory_with_stored_skills_is_refused(tmp_path):
    run_dir = tmp_path / "run"
    first = import_external_bank(_notes_file(tmp_path), "notes_jsonl",
                                 run_dir, origin="expel")
    (run_dir / "skill_bank.jsonl").unlink()
    (run_dir / BANK_ORIGIN_NAME).unlink()

    with pytest.raises(ConfigError, match="stage3_generation/skills"):
        import_external_bank(_notes_file(tmp_path), "notes_jsonl", run_dir,
                             origin="expel")
    assert len(Store(run_dir).ids("skills")) == len(first["skill_ids"])


def test_a_second_import_into_one_run_directory_is_refused(tmp_path):
    run_dir = tmp_path / "run"
    import_external_bank(_notes_file(tmp_path), "notes_jsonl", run_dir,
                         origin="expel")
    bank_bytes = (run_dir / "skill_bank.jsonl").read_bytes()

    with pytest.raises(ConfigError, match="new run directory"):
        import_external_bank(_notes_file(tmp_path), "notes_jsonl", run_dir,
                             origin="other")
    assert (run_dir / "skill_bank.jsonl").read_bytes() == bank_bytes


@pytest.mark.parametrize("origin", ["", "Expel", "ex pel", "-x", "x-", "a--b",
                                    "x" * 25, None])
def test_a_bad_origin_is_refused(tmp_path, origin):
    with pytest.raises(ConfigError, match="origin"):
        import_external_bank(_notes_file(tmp_path), "notes_jsonl",
                             tmp_path / "run", origin=origin)


def test_an_unknown_format_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="source_format"):
        import_external_bank(_notes_file(tmp_path), "csv", tmp_path / "run",
                             origin="expel")


@pytest.mark.parametrize("source_format", ["markdown_file", "skill_folder",
                                           "notes_jsonl"])
def test_a_missing_source_is_refused(tmp_path, source_format):
    with pytest.raises(ConfigError, match="not a"):
        import_external_bank(tmp_path / "nope", source_format,
                             tmp_path / "run", origin="expel")
    assert not (tmp_path / "run").exists()
