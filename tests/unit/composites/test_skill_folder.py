"""The skill-folder M2 materialization tests: the composite layer's
folder writer.

``materialize_skill_folder`` consumes the delivery's manifest (the
unit row's ``aux["skill_folder"]``) -- a NAME MAPPING, never the
skills (the rev-1 ruling: progressive disclosure) -- plus the phase
bank it resolves against, and writes the real directory: one markdown
file per accepted skill plus the catalog. The frozen contract under
test: the manifest's bank_fingerprint gates the bank (a bank that
moved is a loud error), byte-exact replay (idempotent, chosen form:
unchanged content is not rewritten), deterministic file names, the
catalog file byte-identical to the manifest's catalog text, and loud
refusals for a manifest that would shadow a file, escape the
directory, or disagree with the bank.
"""

import pytest
import yaml

from expedistill.composites import skill_reads
from expedistill.composites.skill_folder import materialize_skill_folder
from expedistill.core.ids import (
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill, Trajectory
from expedistill.infra.reliability import CodeBugError
from expedistill.methods.delivery import FolderParams
from expedistill.methods.delivery import folder as folder_driver
from tests.helpers.skills import make_skill

BENCHMARK = "tb"

MARKED = "\n".join([
    "[When to use] The service rejects connections.",
    "[Steps]",
    "1. Check the service status.",
    "[Verify] The status command reports running.",
    "[Do not use when] The status command already reports running.",
])


def _skill(task_raw: str, *, summary: str = "",
           content: str = MARKED) -> Skill:
    task_id = make_task_id(BENCHMARK, task_raw)
    subtask_id = make_subtask_id(make_trajectory_id(task_id, "strong", 0), 0)
    return make_skill(subtask_id, content, summary=summary,
                      status="accepted")


def _manifest(tmp_path):
    """A real manifest off the delivery driver (the M3 input shape),
    its bank, a materialized folder, and the two skills' expected file
    names."""
    bank = [_skill("t1", summary="Restart a stuck service safely."),
            _skill("t2", summary="Check the log before restarting.")]
    rows = folder_driver.resolve(bank, ["task-a"], FolderParams())
    manifest = rows[0].aux["skill_folder"]
    folder = materialize_skill_folder(manifest, bank, tmp_path / "folder")
    return manifest, bank, folder


def _inode(path):
    return path.stat().st_ino


def _split_frontmatter(text):
    """(parsed YAML mapping, body) from one rendered SKILL.md."""
    assert text.startswith("---\n")
    head, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(head), body.lstrip("\n")


def _files(folder):
    return sorted(path for path in folder.rglob("*") if path.is_file())


def test_the_folder_writes_each_skill_directory_and_the_catalog(tmp_path):
    manifest, bank, folder = _manifest(tmp_path)
    assert sorted(entry.name for entry in folder.iterdir()) == \
        ["catalog.md",
         "traj-tb-t1-strong-00-seg00-skill",
         "traj-tb-t2-strong-00-seg00-skill"]
    # The catalog file is the manifest's catalog text verbatim.
    assert (folder / "catalog.md").read_text(encoding="utf-8") == \
        manifest["catalog_text"]
    bodies = {skill.skill_id: skill.content for skill in bank}
    for entry in manifest["files"]:
        text = (folder / entry["filename"]).read_text(encoding="utf-8")
        front, body = _split_frontmatter(text)
        assert front["name"] == entry["filename"].split("/", 1)[0]
        assert front["description"]
        assert body == bodies[entry["skill_id"]]
        # The rev-1 shape: no content travels with the manifest.
        assert "content" not in entry


def test_the_frontmatter_is_a_valid_agent_skill_header(tmp_path):
    manifest, bank, folder = _manifest(tmp_path)
    for entry in manifest["files"]:
        text = (folder / entry["filename"]).read_text(encoding="utf-8")
        front, _body = _split_frontmatter(text)
        name = front["name"]
        assert name == entry["filename"].split("/", 1)[0]
        assert 1 <= len(name) <= 64
        assert name == name.lower()
        assert all(ch.isalnum() or ch == "-" for ch in name)
        assert front["description"].strip()
        assert len(front["description"]) <= 1024
        assert "anthropic" not in name and "claude" not in name


def test_the_three_description_modes_reach_the_frontmatter(tmp_path):
    bank = [_skill("t1", summary="Restart a stuck service safely.")]
    expected = {
        "summary": "Restart a stuck service safely.",
        "when_to_use": "The service rejects connections.",
        "summary_then_when_to_use": (
            "Restart a stuck service safely. Use when: "
            "The service rejects connections."),
    }
    for mode, description in expected.items():
        rows = folder_driver.resolve(
            bank, ["task-a"], FolderParams(description_mode=mode))
        manifest = rows[0].aux["skill_folder"]
        folder = materialize_skill_folder(
            manifest, bank, tmp_path / mode)
        entry = manifest["files"][0]
        text = (folder / entry["filename"]).read_text(encoding="utf-8")
        front, body = _split_frontmatter(text)
        assert front["description"] == description
        assert body == bank[0].content


def test_replaying_the_same_manifest_changes_nothing(tmp_path):
    manifest, bank, folder = _manifest(tmp_path)
    before = {path.relative_to(folder).as_posix(): _inode(path)
              for path in _files(folder)}
    again = materialize_skill_folder(manifest, bank, folder)
    assert again == folder
    # Idempotency, chosen form: unchanged content is NOT rewritten --
    # same inode on every file (a rewrite would be a new temp file's
    # inode under the same name).
    after = {path.relative_to(folder).as_posix(): _inode(path)
             for path in _files(folder)}
    assert after == before
    assert (folder / "catalog.md").read_text(encoding="utf-8") == \
        manifest["catalog_text"]


def test_a_changed_bank_rewrites_only_the_changed_files(tmp_path):
    manifest, bank, folder = _manifest(tmp_path)
    skill_files = sorted(entry["filename"] for entry in manifest["files"])
    stable_name, changed_name = skill_files
    stable_before = _inode(folder / stable_name)
    changed_before = _inode(folder / changed_name)
    materialize_skill_folder(manifest, bank, folder)  # no-op replay
    # A rewrite bumps the skill's version, so the manifest re-resolves
    # with it; only that skill's file changes on the disk.
    changed_id = next(entry["skill_id"] for entry in manifest["files"]
                      if entry["filename"] == changed_name)
    for skill in bank:
        if skill.skill_id == changed_id:
            skill.content = skill.content + "\nOne more step."
            skill.version = 2
    rows = folder_driver.resolve(bank, ["task-a"], FolderParams())
    manifest = rows[0].aux["skill_folder"]
    materialize_skill_folder(manifest, bank, folder)
    assert _inode(folder / stable_name) == stable_before
    assert _inode(folder / changed_name) != changed_before


def test_extra_files_are_left_alone(tmp_path):
    manifest, bank, folder = _manifest(tmp_path)
    stray = folder / "leftover.md"
    stray.write_text("not the manifest's business", encoding="utf-8")
    materialize_skill_folder(manifest, bank, folder)
    assert stray.read_text(encoding="utf-8") == \
        "not the manifest's business"


def test_an_empty_bank_manifest_writes_nothing(tmp_path):
    target = tmp_path / "empty"
    target.mkdir()
    pending = _skill("t1")
    pending.status = "pending"
    rows = folder_driver.resolve([pending], ["task-a"], FolderParams())
    manifest = rows[0].aux["skill_folder"]
    assert manifest["files"] == [] and manifest["catalog_text"] == ""
    materialize_skill_folder(manifest, [pending], target)
    assert list(target.iterdir()) == []


def test_a_bank_that_moved_under_the_manifest_is_refused(tmp_path):
    manifest, bank, _folder = _manifest(tmp_path)
    with pytest.raises(CodeBugError, match="bank moved"):
        materialize_skill_folder(manifest, [], tmp_path / "x")


def test_a_version_drift_between_manifest_and_bank_is_refused(tmp_path):
    manifest, bank, _folder = _manifest(tmp_path)
    bank[0].version = 7
    with pytest.raises(CodeBugError, match="bank moved under the manifest"):
        materialize_skill_folder(manifest, bank, tmp_path / "x")


def test_a_manifest_skill_missing_from_the_bank_is_refused(tmp_path):
    manifest, bank, _folder = _manifest(tmp_path)
    rows = folder_driver.resolve(bank[1:], ["task-a"], FolderParams())
    trimmed = rows[0].aux["skill_folder"]
    trimmed["files"] = manifest["files"]  # names the t1 skill too
    with pytest.raises(CodeBugError, match="does not carry"):
        materialize_skill_folder(trimmed, bank[1:], tmp_path / "y")


def test_an_unsafe_file_name_is_refused(tmp_path):
    manifest, bank, _folder = _manifest(tmp_path)
    manifest["files"][0]["filename"] = "../escape/SKILL.md"
    with pytest.raises(CodeBugError, match="exactly one normalized"):
        materialize_skill_folder(manifest, bank, tmp_path / "x")


def test_a_duplicate_file_name_is_refused(tmp_path):
    manifest, bank, _folder = _manifest(tmp_path)
    manifest["files"][1]["filename"] = manifest["files"][0]["filename"]
    with pytest.raises(CodeBugError, match="twice"):
        materialize_skill_folder(manifest, bank, tmp_path / "x")


def test_a_root_level_file_name_is_refused(tmp_path):
    manifest, bank, _folder = _manifest(tmp_path)
    manifest["files"][0]["filename"] = "SKILL.md"
    with pytest.raises(CodeBugError, match="exactly one normalized"):
        materialize_skill_folder(manifest, bank, tmp_path / "x")


def test_an_entry_with_missing_fields_is_refused(tmp_path):
    manifest, bank, _folder = _manifest(tmp_path)
    manifest["files"][0] = {"skill_id": "s", "filename": "s.md"}
    with pytest.raises(CodeBugError, match="must carry exactly"):
        materialize_skill_folder(manifest, bank, tmp_path / "x")


def test_a_manifest_without_a_catalog_name_is_refused(tmp_path):
    manifest, bank, _folder = _manifest(tmp_path)
    del manifest["catalog_filename"]
    with pytest.raises(CodeBugError, match="non-empty 'catalog_filename'"):
        materialize_skill_folder(manifest, bank, tmp_path / "x")


# ---------------------------------------------------------------------------
# The delivery's name rule and the materializer's restatement stay one
# contract
# ---------------------------------------------------------------------------

def test_the_skill_directory_rule_is_one_contract(tmp_path):
    # The delivery DERIVES the directory names; the composite materializer
    # RESTATES the rule (the layer rule forbids the import). This mirror
    # test binds the two spellings: relaxing either one fails here, at
    # test time, instead of as a mid-run CodeBugError.
    from expedistill.composites.skill_folder import _SKILL_DIR_RE
    for skill_id in ("bank/merge/0001", "tb:t1/a00/seg00/skill",
                     "Mixed Case/ID", "plain"):
        directory = folder_driver.skill_filename(skill_id).split("/", 1)[0]
        assert _SKILL_DIR_RE.fullmatch(directory)
    # One odd id end to end: the delivery derives the name, the
    # materializer accepts it, and the read parser counts a cat of the
    # exact relative path.
    odd = Skill(skill_id="tb:odd/id.name 中", content=MARKED,
                subtask_id="s0", source_subtasks=["s0"],
                summary="Odd id.", status="accepted")
    manifest = folder_driver.skill_folder_manifest([odd])
    filename = manifest["files"][0]["filename"]
    assert filename == "tb-odd-id-name/SKILL.md"
    materialize_skill_folder(manifest, [odd], tmp_path / "folder")
    trajectory = Trajectory(
        trajectory_id="tb:odd:weak:0", task_id="tb:odd", benchmark="tb",
        model_role="weak", model_name="weak-7b", attempt_index=0,
        steps=[{"action": f"cat /opt/openclaw-skills/train/{filename}",
                "observation": "ok"}], correctness=True)
    assert skill_reads.skill_files_read(trajectory, manifest) == \
        [odd.skill_id]
