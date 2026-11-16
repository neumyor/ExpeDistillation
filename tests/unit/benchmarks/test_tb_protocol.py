"""TB's executor protocol: the texts the agent receives, the suffix that
carries them, and what each kind of unit uploads for the vendored agent."""

import string

from expedistill.benchmarks.base import Task
from expedistill.benchmarks.tb.protocol import (
    MOUNT_PLACEHOLDER, READ_SUFFIX_FILENAME, render_suffix)
from expedistill.inject.skills import PLAYBOOK_HEADER
from expedistill.methods.delivery.folder_delivery import skill_folder_manifest
from expedistill.methods.delivery.units import make_row, with_playbook
from expedistill.prompts import get as get_prompt
from tests.helpers.tb_fake_remote import (
    HANDLE, ROOT, TRIAL, FakeRemoteLink, make_plugin)
from tests.helpers.tb_folder import (
    BANK, CATALOG, materialized, trial_files_with_cat)

PLAYBOOK = "## orientation\nLook before you type.\n"
SKILLS = "## Skills\n\n### build a project\nRun the build, then the tests."
TASK_DIR = f"{ROOT}/skills/ed_train-fasttext_tok/train-fasttext"


def _fields(template):
    return {name for _, name, _, _ in string.Formatter().parse(template)
            if name}


def _folder_task(tmp_path, playbook="", bank=BANK):
    row = make_row("tb:train-fasttext", None, "",
                   {"skill_folder": skill_folder_manifest(bank)})
    manifest = with_playbook([row], playbook)[0].aux["skill_folder"]
    folder_dir = materialized(tmp_path) if bank else tmp_path / "empty"
    folder_dir.mkdir(exist_ok=True)
    return Task(task_id="train-fasttext", instruction="train-fasttext",
                meta={"task_name": "train-fasttext", "attempt_token": "tok",
                      "skill_folder": manifest,
                      "skill_folder_dir": str(folder_dir)})


def _plain_task():
    return Task(task_id="train-fasttext", instruction="train-fasttext",
                meta={"task_name": "train-fasttext", "attempt_token": "tok"})


def _run(tmp_path, task, skills_block):
    link = FakeRemoteLink(polls=[f"RESULT {TRIAL}/result.json\n"],
                          remote_files=trial_files_with_cat())
    outcome = make_plugin(link).run(task, HANDLE, skills_block,
                                    tmp_path / "work")
    return link, outcome


def _uploaded(link, name):
    return link.remote_files[f"{TASK_DIR}/{name}"].decode("utf-8")


def test_the_executor_texts_fill_only_their_own_slots():
    assert _fields(get_prompt("TB_TERMINUS_TEMPLATE")) \
        == {"instruction", "terminal_state"}
    assert _fields(get_prompt("TB_SKILL_READ")) == set()
    assert _fields(get_prompt("TB_DISCIPLINE")) == set()


def test_the_suffix_orders_playbook_skills_card_rule_discipline():
    rule, discipline = get_prompt("TB_SKILL_READ"), get_prompt("TB_DISCIPLINE")
    folder = render_suffix(playbook=PLAYBOOK, folder=True)
    assert folder.startswith(PLAYBOOK_HEADER)
    assert folder.index(PLAYBOOK.strip()) < folder.index(rule) \
        < folder.index(discipline)
    assert folder.endswith(discipline)
    whole = render_suffix(skills_block=SKILLS)
    assert whole == SKILLS + "\n\n" + discipline
    assert rule not in whole
    # A blank playbook is no playbook: no header, the card rule first.
    assert render_suffix(playbook="  ", folder=True) \
        == render_suffix(folder=True) == rule + "\n\n" + discipline


def test_a_folder_unit_uploads_the_placeholder_and_the_suffix(tmp_path):
    link, outcome = _run(tmp_path, _folder_task(tmp_path, PLAYBOOK), CATALOG)
    assert _uploaded(link, "SKILL.md") == MOUNT_PLACEHOLDER
    assert _uploaded(link, READ_SUFFIX_FILENAME) \
        == render_suffix(playbook=PLAYBOOK, folder=True)
    assert outcome.meta["prompt_name"] == "TB_SKILL_READ"


def test_an_empty_bank_keeps_the_suffix_and_ships_no_skill(tmp_path):
    link, outcome = _run(tmp_path, _folder_task(tmp_path, PLAYBOOK, []), "")
    assert _uploaded(link, READ_SUFFIX_FILENAME) \
        == render_suffix(playbook=PLAYBOOK, folder=True)
    assert not [name for name in link.remote_files if "skills_cache" in name]
    assert outcome.meta["skill_folder"]["files"] == []


def test_a_whole_bank_unit_carries_its_skills_in_the_suffix(tmp_path):
    link, outcome = _run(tmp_path, _plain_task(), SKILLS)
    assert _uploaded(link, "SKILL.md") == MOUNT_PLACEHOLDER
    assert _uploaded(link, READ_SUFFIX_FILENAME) \
        == render_suffix(skills_block=SKILLS)
    submit = next(cmd for cmd in link.commands if "nohup" in cmd)
    assert "TBENCH_EVAL_MODE=skills" in submit
    assert outcome.meta["prompt_name"] == "TB_TERMINUS_TEMPLATE"


def test_a_bare_unit_uploads_nothing(tmp_path):
    link, outcome = _run(tmp_path, _plain_task(), "")
    assert outcome.meta["eval_mode"] == "bare"
    assert outcome.meta["prompt_name"] == "TB_TERMINUS_TEMPLATE"
    assert not any("skills/" in remote for _, remote in link.puts)


def test_the_suffix_is_part_of_the_launch_conditions(tmp_path):
    plugin = make_plugin(FakeRemoteLink(polls=[], remote_files={}))
    task = _folder_task(tmp_path, PLAYBOOK)
    folder = (task.meta["skill_folder"], tmp_path / "skill_folder")
    args = ("run", "skills", MOUNT_PLACEHOLDER, folder, 131072)
    plain = plugin._conditions(*args, render_suffix(folder=True))
    with_pb = plugin._conditions(
        *args, render_suffix(playbook=PLAYBOOK, folder=True))
    assert plain["skill_sha256"] != with_pb["skill_sha256"]
    whole = ("run", "skills", MOUNT_PLACEHOLDER, None, 131072)
    assert plugin._conditions(*whole, render_suffix(skills_block=SKILLS)) \
        != plugin._conditions(*whole, render_suffix(skills_block=SKILLS + "x"))
