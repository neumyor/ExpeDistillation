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

import string

import pytest

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


def test_the_folder_writes_every_file_and_the_catalog(tmp_path):
    manifest, bank, folder = _manifest(tmp_path)
    assert sorted(entry.name for entry in folder.iterdir()) == \
        ["catalog.md",
         "traj_tb_t1_strong_00_seg00_skill.md",
         "traj_tb_t2_strong_00_seg00_skill.md"]
    # The catalog file is the manifest's catalog text verbatim, and
    # every skill file's body is the skill's four-section content
    # READ FROM THE BANK (the manifest carries names, not bodies).
    assert (folder / "catalog.md").read_text(encoding="utf-8") == \
        manifest["catalog_text"]
    bodies = {skill.skill_id: skill.content for skill in bank}
    for entry in manifest["files"]:
        assert (folder / entry["filename"]).read_text(
            encoding="utf-8") == bodies[entry["skill_id"]]
        # The rev-1 shape: no content travels with the manifest.
        assert "content" not in entry


def test_replaying_the_same_manifest_changes_nothing(tmp_path):
    manifest, bank, folder = _manifest(tmp_path)
    before = {entry.name: _inode(entry) for entry in folder.iterdir()}
    again = materialize_skill_folder(manifest, bank, folder)
    assert again == folder
    # Idempotency, chosen form: unchanged content is NOT rewritten --
    # same inode on every file (a rewrite would be a new temp file's
    # inode under the same name).
    after = {entry.name: _inode(entry) for entry in folder.iterdir()}
    assert after == before
    assert (folder / "catalog.md").read_text(encoding="utf-8") == \
        manifest["catalog_text"]


def test_a_changed_bank_rewrites_only_the_changed_files(tmp_path):
    manifest, bank, folder = _manifest(tmp_path)
    skill_files = sorted(entry.name for entry in folder.iterdir()
                         if entry.name != "catalog.md")
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
    manifest["files"][0]["filename"] = "../escape.md"
    with pytest.raises(CodeBugError, match="not a safe name"):
        materialize_skill_folder(manifest, bank, tmp_path / "x")


def test_a_duplicate_file_name_is_refused(tmp_path):
    manifest, bank, _folder = _manifest(tmp_path)
    manifest["files"][1]["filename"] = manifest["files"][0]["filename"]
    with pytest.raises(CodeBugError, match="twice"):
        materialize_skill_folder(manifest, bank, tmp_path / "x")


def test_a_file_named_like_the_catalog_is_refused(tmp_path):
    manifest, bank, _folder = _manifest(tmp_path)
    manifest["files"][0]["filename"] = manifest["catalog_filename"]
    with pytest.raises(CodeBugError, match="collides with the catalog"):
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
# The safe alphabet's three spellings stay one contract
# ---------------------------------------------------------------------------

def test_the_safe_alphabet_is_one_contract_across_its_three_spellings(
        tmp_path):
    # The delivery DERIVES the file names over the alphabet; the
    # composite materializer and the read parser each RESTATE it (the
    # layer rule forbids the import, and each restatement says so).
    # This mirror test binds the three spellings (the FAILURE_CATEGORIES
    # precedent): relaxing any one of them fails here, at test time,
    # instead of as a mid-run CodeBugError.
    from expedistill.composites.skill_folder import _FILENAME_RE
    alphabet = string.ascii_letters + string.digits + "._-"
    boundary = alphabet + ":/ \t中"
    for ch in boundary:
        inside = ch in alphabet
        # The delivery's derivation keeps exactly the alphabet...
        assert (folder_driver.skill_filename("s" + ch + "t")
                == "s" + ch + "t.md") is inside
        # ...the materializer's restated regex accepts exactly it...
        assert bool(_FILENAME_RE.fullmatch("s" + ch + "t.md")) is inside
        # ...and the read parser's delimited match treats the same
        # characters as name characters.
        assert (ch in skill_reads._NAME_ALPHABET) is inside
    # The same contract end to end, through one odd skill id (colon,
    # slash, dot and a non-ASCII character in one id): the delivery
    # derives the name, the materializer accepts it, and the read
    # parser counts a cat of exactly that name -- never a longer name
    # that merely contains it.
    odd = Skill(skill_id="tb:odd/id.name 中", content=MARKED,
                subtask_id="s0", source_subtasks=["s0"],
                summary="Odd id.", status="accepted")
    manifest = folder_driver.skill_folder_manifest([odd])
    filename = manifest["files"][0]["filename"]
    assert filename == "tb_odd_id.name__.md"
    materialize_skill_folder(manifest, [odd], tmp_path / "folder")
    trajectory = Trajectory(
        trajectory_id="tb:odd:weak:0", task_id="tb:odd", benchmark="tb",
        model_role="weak", model_name="weak-7b", attempt_index=0,
        steps=[{"action": f"cat /opt/openclaw-skills/train/{filename}",
                "observation": "ok"}], correctness=True)
    assert skill_reads.skill_files_read(trajectory, manifest) == \
        [odd.skill_id]
