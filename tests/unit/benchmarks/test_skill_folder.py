"""Tests for benchmarks/skill_folder.py: the card list and the reader the
local ReAct loop serves ReadSkill actions from."""

import xml.etree.ElementTree as ET

import pytest

from expedistill.benchmarks.base import Task
from expedistill.benchmarks.skill_folder import (
    MAX_READS,
    SkillFolder,
    canonical_location,
    derive_folder_template,
    read_limit_message,
    read_target,
)
from expedistill.infra.reliability import CodeBugError
from tests.helpers.skill_folder import (
    CARD_BODY,
    folder_task_meta,
    write_skill_folder,
)


def _folder(tmp_path, names=("alpha", "beta")):
    manifest, _, locations = write_skill_folder(tmp_path / "folder", names)
    task = Task(task_id="t1", instruction="q",
                meta=folder_task_meta(manifest, tmp_path / "folder"))
    return SkillFolder.from_task(task), locations, manifest


def test_the_folder_exposes_the_global_playbook(tmp_path):
    # 2026-11-04 P0: the manifest's playbook is available to the prompt
    # builder above the card list; the default is empty.
    manifest, _, _ = write_skill_folder(tmp_path / "folder", ("alpha",))
    task = Task(task_id="t1", instruction="q",
                meta=folder_task_meta(manifest, tmp_path / "folder"))
    assert SkillFolder.from_task(task).playbook_text == ""
    with_playbook = {**manifest, "playbook_text": "GLOBAL PLAYBOOK"}
    task = Task(task_id="t1", instruction="q",
                meta=folder_task_meta(with_playbook, tmp_path / "folder"))
    assert SkillFolder.from_task(task).playbook_text == "GLOBAL PLAYBOOK"


# ---------------------------------------------------------------------------
# the read-action grammar
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action, target", [
    ("ReadSkill[a/SKILL.md]", "a/SKILL.md"),
    ("  ReadSkill[a/SKILL.md]  ", "a/SKILL.md"),
    ("> ReadSkill[a/SKILL.md]", "a/SKILL.md"),
    ("ReadSkill[a]", "a"),
    ("ReadSkill[]", ""),
])
def test_read_target_accepts_a_single_read_action(action, target):
    assert read_target(action) == target


@pytest.mark.parametrize("action", [
    "Search[a/SKILL.md]",
    "think: ReadSkill[a/SKILL.md]",
    "ReadSkill[a/SKILL.md]\nSearch[x]",
    "ReadSkill[a/SKILL.md] and more",
    "readskill[a/SKILL.md]",
    "ReadSkill a/SKILL.md",
])
def test_read_target_rejects_anything_else(action):
    assert read_target(action) is None


@pytest.mark.parametrize("target, expected", [
    ("a/SKILL.md", "a/SKILL.md"),
    ("a", "a/SKILL.md"),
    ("a/", "a/SKILL.md"),
    ("./a/SKILL.md", "a/SKILL.md"),
    ("'a/SKILL.md'", "a/SKILL.md"),
    ('"a"', "a/SKILL.md"),
    ("  a  ", "a/SKILL.md"),
])
def test_canonical_location_normalizes_the_ways_a_model_writes_it(
        target, expected):
    assert canonical_location(target) == expected


# ---------------------------------------------------------------------------
# building the folder from a task
# ---------------------------------------------------------------------------

def test_a_task_without_a_manifest_has_no_folder():
    assert SkillFolder.from_task(Task(task_id="t", instruction="q")) is None


def test_a_manifest_without_files_runs_bare(tmp_path):
    manifest = {"files": []}
    task = Task(task_id="t", instruction="q",
                meta={"skill_folder": manifest})
    assert SkillFolder.from_task(task) is None


def test_a_manifest_with_files_but_no_directory_is_a_wiring_bug():
    manifest = {"files": [{"skill_id": "s", "version": 1,
                           "filename": "s/SKILL.md"}]}
    task = Task(task_id="t", instruction="q",
                meta={"skill_folder": manifest})
    with pytest.raises(CodeBugError, match="skill_folder_dir"):
        SkillFolder.from_task(task)


def test_a_non_mapping_manifest_is_a_wiring_bug():
    task = Task(task_id="t", instruction="q",
                meta={"skill_folder": ["not", "a", "mapping"]})
    with pytest.raises(CodeBugError, match="folder manifest mapping"):
        SkillFolder.from_task(task)


@pytest.mark.parametrize("filename", [
    "../escape/SKILL.md", "/abs/SKILL.md", "a/b/SKILL.md", "a/OTHER.md",
    "SKILL.md", None,
])
def test_an_unsafe_manifest_file_name_is_refused(tmp_path, filename):
    manifest = {"files": [{"skill_id": "s", "version": 1,
                           "filename": filename}]}
    task = Task(task_id="t", instruction="q", meta={
        "skill_folder": manifest, "skill_folder_dir": str(tmp_path)})
    with pytest.raises(CodeBugError, match="folder manifest file name"):
        SkillFolder.from_task(task)


def test_a_card_file_without_frontmatter_is_a_wiring_bug(tmp_path):
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "SKILL.md").write_text("no frontmatter", "utf-8")
    manifest = {"files": [{"skill_id": "s", "version": 1,
                           "filename": "s/SKILL.md"}]}
    task = Task(task_id="t", instruction="q", meta={
        "skill_folder": manifest, "skill_folder_dir": str(tmp_path)})
    with pytest.raises(CodeBugError, match="frontmatter"):
        SkillFolder.from_task(task)


# ---------------------------------------------------------------------------
# the card list the prompt carries
# ---------------------------------------------------------------------------

def test_the_card_list_has_names_descriptions_and_locations_only(tmp_path):
    folder, locations, _ = _folder(tmp_path)
    block = folder.available_skills_block()
    root = ET.fromstring(block)
    assert root.tag == "available_skills"
    listed = [(skill.findtext("name"), skill.findtext("location"))
              for skill in root.findall("skill")]
    assert listed == sorted(
        [(location.split("/")[0], location) for location in locations],
        key=lambda pair: pair[1])
    assert all(skill.findtext("description")
               for skill in root.findall("skill"))
    # Progressive disclosure: the list never carries a card's body.
    assert "Search the full name first" not in block
    assert "[Steps]" not in block


def test_the_card_list_escapes_markup_in_descriptions(tmp_path):
    root = tmp_path / "folder"
    (root / "s").mkdir(parents=True)
    (root / "s" / "SKILL.md").write_text(
        "---\nname: s\ndescription: 'Use <b> & more'\n---\n\nbody", "utf-8")
    manifest = {"files": [{"skill_id": "s", "version": 1,
                           "filename": "s/SKILL.md"}]}
    task = Task(task_id="t", instruction="q", meta={
        "skill_folder": manifest, "skill_folder_dir": str(root)})
    block = SkillFolder.from_task(task).available_skills_block()
    assert "&lt;b&gt; &amp; more" in block
    assert ET.fromstring(block).findtext("skill/description") \
        == "Use <b> & more"


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def test_a_read_returns_the_whole_file_like_cat(tmp_path):
    folder, locations, _ = _folder(tmp_path)
    text, found = folder.read(locations[0])
    assert found is True
    assert text.startswith("---\nname: ")
    assert CARD_BODY in text


def test_a_read_accepts_the_bare_directory_name(tmp_path):
    folder, locations, _ = _folder(tmp_path)
    directory = locations[1].split("/")[0]
    assert folder.read(directory) == folder.read(locations[1])


def test_an_unknown_location_is_reported_not_raised(tmp_path):
    folder, _, _ = _folder(tmp_path)
    text, found = folder.read("nowhere/SKILL.md")
    assert found is False
    assert "Unknown skill location 'nowhere/SKILL.md'" in text
    assert "<available_skills>" in text


def test_the_read_limit_message_states_the_cap():
    assert str(MAX_READS) in read_limit_message()


# ---------------------------------------------------------------------------
# deriving a folder prompt from a plain prompt
# ---------------------------------------------------------------------------

def test_derive_inserts_after_the_anchor_once():
    assert derive_folder_template("a ANCHOR z", "ANCHOR", "+X+") \
        == "a ANCHOR+X+ z"


@pytest.mark.parametrize("base", ["no anchor here", "ANCHOR and ANCHOR"])
def test_derive_refuses_a_missing_or_ambiguous_anchor(base):
    with pytest.raises(CodeBugError, match="anchor"):
        derive_folder_template(base, "ANCHOR", "+X+")
