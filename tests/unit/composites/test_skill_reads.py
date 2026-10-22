"""Skill-folder M4, the read parse: which skill files a stored
trajectory's actions actually cat (composites/skill_reads.py).

The parse is benchmark-blind by design (the M4 ruling): no mount path
constant appears in the module, so these tests pin the coarse edges the
docstring discloses -- a same-named file under another directory
counts, a longer name containing the manifest's name does not, and
only cat counts. The real action shape is the one M3 froze
(test_tb_folder_sandbox._CAT_TRAJECTORY): one rendered tool call per
line, ``bash_command({"keystrokes": "cat <path>\\n", ...})``, with the
newline inside the keystrokes JSON-escaped.
"""

import pytest

from expedistill.composites.skill_reads import skill_files_read
from expedistill.core.models import Trajectory
from expedistill.infra.reliability import CodeBugError

_FILE_A = "traj-tb-t1-strong-00-seg00-skill/SKILL.md"
_SKILL_A = "traj/tb:t1/strong/00/seg00/skill"
_FILE_B = "bank-merge-0001/SKILL.md"
_SKILL_B = "bank/merge/0001"

#: The delivery's frozen manifest shape (M2 rev-1: a name mapping, no
#: content); the parse consumes files[].{skill_id, filename} only.
_MANIFEST = {
    "bank_fingerprint": "bank:0123456789ab",
    "catalog_filename": "catalog.md",
    "catalog_text": "1 | a | b\n",
    "files": [
        {"skill_id": _SKILL_A, "version": 1, "filename": _FILE_A},
        {"skill_id": _SKILL_B, "version": 3, "filename": _FILE_B},
    ],
}


def _cat(path):
    """One rendered tool call in the frozen TB shape (M3's sample)."""
    return ('bash_command({"duration": 5, "keystrokes": '
            f'"cat {path}\\n"}})')


def _trajectory(*actions):
    return Trajectory(
        trajectory_id="fakebench:t1:weak:0", task_id="fakebench:t1",
        benchmark="fakebench", model_role="weak", model_name="weak-7b",
        attempt_index=0,
        steps=[{"action": action, "observation": "ok"}
               for action in actions])


def test_the_frozen_cat_shape_parses_and_the_catalog_is_not_a_skill():
    # The real M3 form: the catalog cat (navigation) plus one skill file
    # cat -- only the skill file is a read.
    trajectory = _trajectory(
        _cat("/opt/openclaw-skills/train-fasttext/catalog.md"),
        _cat(f"/opt/openclaw-skills/train-fasttext/{_FILE_A}"))
    assert skill_files_read(trajectory, _MANIFEST) == [_SKILL_A]


def test_first_read_order_with_each_skill_once():
    trajectory = _trajectory(
        _cat(f"/opt/openclaw-skills/train-fasttext/{_FILE_B}"),
        _cat(f"/opt/openclaw-skills/train-fasttext/{_FILE_A}"),
        _cat(f"/opt/openclaw-skills/train-fasttext/{_FILE_B}"),
        _cat(f"/opt/openclaw-skills/train-fasttext/{_FILE_A}"))
    assert skill_files_read(trajectory, _MANIFEST) == [_SKILL_B, _SKILL_A]


def test_several_files_on_one_line_read_in_manifest_order():
    line = ('bash_command({"keystrokes": "cat '
            f'/opt/openclaw-skills/train-fasttext/{_FILE_B} '
            f'/opt/openclaw-skills/train-fasttext/{_FILE_A}\\n"}})')
    assert skill_files_read(_trajectory(line), _MANIFEST) \
        == [_SKILL_A, _SKILL_B]


def test_only_the_cat_line_of_a_step_counts():
    # One step, two rendered calls: the cat line reads A, the rm line
    # mentions B but is not a read.
    step = ("\n".join([
        _cat(f"/opt/openclaw-skills/train-fasttext/{_FILE_A}"),
        f"bash_command({{\"keystrokes\": \"rm /tmp/{_FILE_B}\\n\"}})",
    ]))
    assert skill_files_read(_trajectory(step), _MANIFEST) == [_SKILL_A]


def test_a_cd_then_cat_skill_md_line_counts_for_that_directory():
    # The common shell form: the directory and SKILL.md both appear on
    # the same action line, so the read is attributable.
    line = ('bash_command({"keystrokes": "cd '
            '/opt/openclaw-skills/train-fasttext/traj-tb-t1-strong-00-'
            'seg00-skill && cat SKILL.md\\n"}})')
    assert skill_files_read(_trajectory(line), _MANIFEST) == [_SKILL_A]


def test_a_bare_cat_skill_md_without_a_directory_is_ambiguous():
    # All skills share the file name SKILL.md; without the unique
    # directory the read cannot be attributed and is not counted.
    assert skill_files_read(
        _trajectory(_cat("SKILL.md")), _MANIFEST) == []


def test_a_bare_trajectory_reads_nothing():
    assert skill_files_read(_trajectory(), _MANIFEST) == []
    assert skill_files_read(
        _trajectory("bash_command({\"keystrokes\": \"ls\\n\"}})"),
        _MANIFEST) == []


def test_other_read_commands_are_not_cat_reads():
    # The disclosed edge: only cat counts (the folder guidance's own
    # contract), so a grep through the file is not reported.
    trajectory = _trajectory(
        f"bash_command({{\"keystrokes\": \"grep pattern /tmp/{_FILE_A}\\n\"}})",
        f"bash_command({{\"keystrokes\": \"head /tmp/{_FILE_A}\\n\"}})")
    assert skill_files_read(trajectory, _MANIFEST) == []


def test_a_same_named_file_under_another_directory_counts():
    # The disclosed limitation, pinned: the directory is not checked
    # (the mount path is benchmark knowledge), so a cat of a same-named
    # file anywhere counts as a read of the manifest's file.
    trajectory = _trajectory(_cat(f"/tmp/notes/{_FILE_A}"))
    assert skill_files_read(trajectory, _MANIFEST) == [_SKILL_A]


def test_a_longer_name_containing_the_file_name_does_not_count():
    trajectory = _trajectory(
        _cat(f"/opt/openclaw-skills/train-fasttext/x{_FILE_A}"),
        _cat(f"/opt/openclaw-skills/train-fasttext/{_FILE_B}.bak"),
        _cat(f"/opt/openclaw-skills/train-fasttext/{_FILE_A}"))
    assert skill_files_read(trajectory, _MANIFEST) == [_SKILL_A]


def test_an_empty_manifest_has_nothing_to_read():
    # The empty accepted bank's null state: the unit ran bare, the read
    # set is empty.
    empty = {**_MANIFEST, "files": [], "catalog_text": ""}
    assert skill_files_read(_trajectory(_cat("/anywhere/a.md")), empty) \
        == []


def test_a_malformed_manifest_entry_is_a_loud_bug():
    with pytest.raises(CodeBugError, match="skill_id"):
        skill_files_read(_trajectory(),
                         {**_MANIFEST, "files": [{"version": 1}]})
    with pytest.raises(CodeBugError, match="mapping"):
        skill_files_read(_trajectory(), {**_MANIFEST, "files": ["x"]})


def test_a_duplicate_file_name_is_a_loud_bug():
    manifest = {**_MANIFEST, "files": [
        {"skill_id": _SKILL_A, "version": 1, "filename": _FILE_A},
        {"skill_id": _SKILL_B, "version": 3, "filename": _FILE_A}]}
    with pytest.raises(CodeBugError, match="twice"):
        skill_files_read(_trajectory(), manifest)
