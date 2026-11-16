"""Skill-folder M4, the read parse: which skill files a stored
trajectory's actions actually cat (composites/skill_use.py).

The parse is benchmark-blind by design (the M4 ruling): no mount path
constant appears in the module, so these tests pin the coarse edges the
docstring discloses -- a same-named file under another directory
counts, a longer name containing the manifest's name does not, and
only cat counts. The real action shape is the one M3 froze
(tests/helpers/tb_folder.py's CAT_TRAJECTORY): one rendered tool call per
line, ``bash_command({"keystrokes": "cat <path>\\n", ...})``, with the
newline inside the keystrokes JSON-escaped.
"""

import pytest

from expedistill.composites.skill_use import (
    skill_files_read, skill_read_events)
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


_MOUNT = "/opt/openclaw-skills/train-fasttext"
_KEYS = 'bash_command({"keystrokes": "%s\\n"})'

# Each case: the actions of one trajectory and the skills read, in order.
@pytest.mark.parametrize("actions, expected", [
    pytest.param(
        # The real M3 form: the catalog cat is navigation, only the skill
        # file cat is a read.
        [_cat(f"{_MOUNT}/catalog.md"), _cat(f"{_MOUNT}/{_FILE_A}")],
        [_SKILL_A], id="frozen_cat_shape_and_catalog_is_not_a_skill"),
    pytest.param(
        [_cat(f"{_MOUNT}/{_FILE_B}"), _cat(f"{_MOUNT}/{_FILE_A}"),
         _cat(f"{_MOUNT}/{_FILE_B}"), _cat(f"{_MOUNT}/{_FILE_A}")],
        [_SKILL_B, _SKILL_A], id="first_read_order_each_skill_once"),
    pytest.param(
        [_cat(f"{_MOUNT}/{_FILE_B} {_MOUNT}/{_FILE_A}")],
        [_SKILL_A, _SKILL_B], id="several_files_on_one_line_in_manifest_order"),
    pytest.param(
        # One step, two rendered calls: the cat line reads A, the rm line
        # mentions B but is not a read.
        ["\n".join([_cat(f"{_MOUNT}/{_FILE_A}"),
                    _KEYS % f"rm /tmp/{_FILE_B}"])],
        [_SKILL_A], id="only_the_cat_line_of_a_step_counts"),
    pytest.param(
        # The common shell form: the directory and SKILL.md share the line,
        # so the read is attributable.
        [_KEYS % (f"cd {_MOUNT}/{_FILE_A.split('/')[0]} && cat SKILL.md")],
        [_SKILL_A], id="cd_then_cat_skill_md"),
    pytest.param(
        # All skills share the name SKILL.md: without the unique directory
        # the read cannot be attributed.
        [_cat("SKILL.md")], [], id="bare_cat_skill_md_is_ambiguous"),
    pytest.param([], [], id="no_steps"),
    pytest.param([_KEYS % "ls"], [], id="no_read"),
    pytest.param(
        # Only cat counts (the folder guidance's own contract).
        [_KEYS % f"grep pattern /tmp/{_FILE_A}",
         _KEYS % f"head /tmp/{_FILE_A}"], [], id="other_read_commands"),
    pytest.param(
        # Disclosed limitation: the directory is not checked (the mount path
        # is benchmark knowledge).
        [_cat(f"/tmp/notes/{_FILE_A}")], [_SKILL_A],
        id="same_named_file_under_another_directory"),
    pytest.param(
        [_cat(f"{_MOUNT}/x{_FILE_A}"), _cat(f"{_MOUNT}/{_FILE_B}.bak"),
         _cat(f"{_MOUNT}/{_FILE_A}")],
        [_SKILL_A], id="longer_names_containing_the_file_name"),
    pytest.param(
        # B's SKILL.md and A's directory share the line, but the SKILL.md
        # belongs to B's path.
        [_cat(f"{_MOUNT}/{_FILE_B} /tmp/{_FILE_A.split('/')[0]}")],
        [_SKILL_B], id="no_cross_attribution_between_two_paths"),
    pytest.param(["ReadSkill[other-skill/SKILL.md]", "Search[a]", "look"],
                 [], id="readskill_of_an_unlisted_card"),
    pytest.param(
        [f"ReadSkill[{_FILE_B}]", _cat(f"/opt/openclaw-skills/t1/{_FILE_A}"),
         f"ReadSkill[{_FILE_B}]"],
        [_SKILL_B, _SKILL_A], id="readskill_and_cat_share_one_order"),
    *[pytest.param([action], [_SKILL_A], id=f"readskill_form_{i}")
      for i, action in enumerate([
          f"ReadSkill[{_FILE_A}]",
          f"> ReadSkill[{_FILE_A}]",
          "ReadSkill[traj-tb-t1-strong-00-seg00-skill]",
          f"ReadSkill['{_FILE_A}']",
          f"ReadSkill[./{_FILE_A}]"])],
])
def test_skill_files_read(actions, expected):
    assert skill_files_read(_trajectory(*actions), _MANIFEST) == expected


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


# ---------------------------------------------------------------------------
# the local benchmarks' read action
# ---------------------------------------------------------------------------


def test_the_first_readskill_event_keeps_its_step_and_original_command():
    trajectory = _trajectory(
        "look", f"> ReadSkill[{_FILE_A}]", f"ReadSkill[{_FILE_A}]")
    events = skill_read_events(trajectory, _MANIFEST)
    assert [(event["step_index"], event["command"]) for event in events] \
        == [(1, f"> ReadSkill[{_FILE_A}]")]
    assert {event["skill_id"] for event in events} == {_SKILL_A}
