"""TB's skill-read suffix: the frozen text, the playbook frame, and how
every folder unit uploads it for the vendored agent."""

import hashlib

from expedistill.benchmarks.tb.plugin import TB_SKILL_FOLDER_GUIDANCE
from expedistill.benchmarks.tb.skill_read import (
    READ_SUFFIX_FILENAME, render_read_suffix)
from expedistill.methods.delivery.folder_delivery import skill_folder_manifest
from expedistill.methods.delivery.units import make_row, with_playbook
from expedistill.prompts import get as get_prompt
from tests.helpers.tb_fake_remote import (
    HANDLE, ROOT, TRIAL, FakeRemoteLink, make_plugin)
from tests.helpers.tb_folder import (
    BANK, CATALOG, materialized, trial_files_with_cat)
from expedistill.benchmarks.base import Task

PLAYBOOK = "## orientation\nLook before you type.\n"

#: sha256 of the default skill-read suffix the vendored agent shipped with
#: (the cross-system identity: the no-playbook arm must send exactly it).
VENDOR_DEFAULT_SHA256 = (
    "360282525f87cab814311221bc2664628187951109fb006b196a4e5197c10ca9")


def _task(tmp_path, playbook=""):
    # The phase's playbook switch puts the playbook into the manifest.
    row = make_row("tb:train-fasttext", None, "",
                   {"skill_folder": skill_folder_manifest(BANK)})
    manifest = with_playbook([row], playbook)[0].aux["skill_folder"]
    return Task(task_id="train-fasttext", instruction="train-fasttext",
                meta={"task_name": "train-fasttext", "attempt_token": "tok",
                      "skill_folder": manifest,
                      "skill_folder_dir": str(materialized(tmp_path))})


def _run(tmp_path, playbook=""):
    link = FakeRemoteLink(polls=[f"RESULT {TRIAL}/result.json\n"],
                          remote_files=trial_files_with_cat())
    plugin = make_plugin(link)
    plugin.run(_task(tmp_path, playbook), HANDLE, CATALOG, tmp_path / "work")
    return link


def test_without_a_playbook_the_suffix_is_the_vendor_default():
    suffix = render_read_suffix()
    assert suffix == get_prompt("TB_TASK_PROMPT_READ")
    assert hashlib.sha256(suffix.strip().encode()).hexdigest() \
        == VENDOR_DEFAULT_SHA256
    assert render_read_suffix("   ") == suffix


def test_a_playbook_goes_in_front_of_the_skill_read_text():
    suffix = render_read_suffix(PLAYBOOK)
    assert suffix.startswith("Global playbook")
    assert PLAYBOOK.strip() in suffix
    assert suffix.endswith(get_prompt("TB_TASK_PROMPT_READ"))


def test_a_playbook_unit_uploads_the_suffix_file(tmp_path):
    link = _run(tmp_path, PLAYBOOK)
    remote = (f"{ROOT}/skills/ed_train-fasttext_tok/train-fasttext/"
              f"{READ_SUFFIX_FILENAME}")
    assert link.remote_files[remote].decode("utf-8") \
        == render_read_suffix(PLAYBOOK)


def test_a_unit_without_a_playbook_uploads_the_plain_skill_read_text(
        tmp_path):
    link = _run(tmp_path)
    remote = (f"{ROOT}/skills/ed_train-fasttext_tok/train-fasttext/"
              f"{READ_SUFFIX_FILENAME}")
    assert link.remote_files[remote].decode("utf-8") \
        == get_prompt("TB_TASK_PROMPT_READ")


def test_the_playbook_changes_the_launch_conditions(tmp_path):
    plugin = make_plugin(FakeRemoteLink(polls=[], remote_files={}))
    task = _task(tmp_path, PLAYBOOK)
    folder = (task.meta["skill_folder"], tmp_path / "skill_folder")
    args = ("run", "skills", "guidance", folder, 131072)
    plain = plugin._conditions(*args, render_read_suffix())
    with_playbook = plugin._conditions(*args, render_read_suffix(PLAYBOOK))
    other = plugin._conditions(*args, render_read_suffix(PLAYBOOK + "more"))
    assert plain["skill_sha256"] != with_playbook["skill_sha256"]
    assert with_playbook["skill_sha256"] != other["skill_sha256"]


def test_an_empty_bank_still_uploads_the_guidance_and_the_suffix(tmp_path):
    # An empty bank keeps the folder presentation: the task-level
    # SKILL.md is the folder guidance, the read suffix carries the
    # playbook, and no skill file ships.
    row = make_row("tb:train-fasttext", None, "",
                   {"skill_folder": skill_folder_manifest([])})
    manifest = with_playbook([row], PLAYBOOK)[0].aux["skill_folder"]
    folder_dir = tmp_path / "skill_folder"
    folder_dir.mkdir()
    task = Task(task_id="train-fasttext", instruction="train-fasttext",
                meta={"task_name": "train-fasttext", "attempt_token": "tok",
                      "skill_folder": manifest,
                      "skill_folder_dir": str(folder_dir)})
    link = FakeRemoteLink(polls=[f"RESULT {TRIAL}/result.json\n"],
                          remote_files=trial_files_with_cat())

    outcome = make_plugin(link).run(task, HANDLE, "", tmp_path / "work")

    task_dir = f"{ROOT}/skills/ed_train-fasttext_tok/train-fasttext"
    assert link.remote_files[f"{task_dir}/SKILL.md"].decode("utf-8") \
        == TB_SKILL_FOLDER_GUIDANCE
    assert link.remote_files[f"{task_dir}/{READ_SUFFIX_FILENAME}"].decode(
        "utf-8") == render_read_suffix(PLAYBOOK)
    assert not [name for name in link.remote_files
                if "skills_cache" in name]
    assert outcome.meta["prompt_name"] == "TB_TASK_PROMPT_FOLDER"
    assert outcome.meta["skill_folder"]["files"] == []
