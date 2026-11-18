"""SWE-bench's executor protocol: the template copies against the recorded
harness templates and the recorded first messages, and the skill block each
kind of unit sends."""

import json
import string
from pathlib import Path

import pytest

from expedistill.benchmarks.base import Task
from expedistill.benchmarks.skill_folder import SkillFolder
from expedistill.benchmarks.swebench.protocol import (
    SKILL_MOUNT,
    card_rule,
    render_block,
)
from expedistill.benchmarks.tb.protocol import TB_SKILL_READ
from expedistill.inject.skills import format_playbook_block
from expedistill.prompts import get as get_prompt
from tests.helpers.skill_folder import folder_task_meta, write_skill_folder

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "swebench"
#: ``agent.system_template`` and ``agent.instance_template`` of the
#: harness's ``configs/swebench_bare.yaml``, as loaded from the YAML.
TEMPLATES = json.loads((FIXTURES / "templates.json").read_text(
    encoding="utf-8"))
PLAYBOOK = "Reproduce the issue before editing."
PR_HEAD = "<pr_description>\nConsider the following PR description:\n"


def _fields(template):
    return {name for _, name, _, _ in string.Formatter().parse(template)
            if name}


def _folder(tmp_path, names=("alpha", "beta"), playbook=PLAYBOOK):
    manifest, _, _ = write_skill_folder(tmp_path / "folder", names)
    meta = folder_task_meta(dict(manifest, playbook_text=playbook),
                            tmp_path / "folder")
    return SkillFolder.from_task(Task(task_id="t", instruction="t",
                                      meta=meta))


def test_the_templates_are_byte_copies_of_the_harness_templates():
    assert get_prompt("SWEBENCH_INSTANCE_TEMPLATE").format(
        instruction="{{task}}") == TEMPLATES["instance_template"]
    assert get_prompt("SWEBENCH_SYSTEM_TEMPLATE") \
        == TEMPLATES["system_template"]


def test_the_executor_texts_fill_only_their_own_slots():
    assert _fields(get_prompt("SWEBENCH_INSTANCE_TEMPLATE")) \
        == {"instruction"}
    for name in ("SWEBENCH_SYSTEM_TEMPLATE", "SWEBENCH_SKILL_READ",
                 "SWEBENCH_DISCIPLINE"):
        assert _fields(get_prompt(name)) == set()


def test_the_template_renders_the_recorded_bare_first_messages():
    # The harness renders with Jinja, which drops the template's final
    # newline; everything else is the copy, byte for byte.
    for case in ("resolved", "apply_failed"):
        messages = json.loads((FIXTURES / case / "traj.json").read_text(
            encoding="utf-8"))["messages"]
        first = messages[1]["content"]
        issue = first[len(PR_HEAD):first.index("\n</pr_description>")]
        assert get_prompt("SWEBENCH_INSTANCE_TEMPLATE").format(
            instruction=issue) == first + "\n"
        assert get_prompt("SWEBENCH_SYSTEM_TEMPLATE") \
            == messages[0]["content"] + "\n"


def test_the_card_rule_keeps_the_tb_wording():
    rule = get_prompt("SWEBENCH_SKILL_READ")
    assert rule == TB_SKILL_READ.template.replace(
        "terminal output", "command output")


def test_a_bare_unit_has_no_block():
    assert render_block() == ""
    assert render_block(skills_block="  \n") == ""


def test_a_whole_bank_block_is_followed_by_the_rules():
    assert render_block(skills_block="## Skill rules\n- one\n") \
        == "## Skill rules\n- one\n\n" + get_prompt("SWEBENCH_DISCIPLINE")


def test_a_folder_block_lists_the_cards_at_their_sandbox_locations(
        tmp_path):
    folder = _folder(tmp_path)
    listing = folder.available_skills_block().replace(
        "<location>", f"<location>{SKILL_MOUNT}/")
    assert render_block(folder=folder) == (
        format_playbook_block(PLAYBOOK)
        + "## Skills\n" + listing + "\n\n"
        + get_prompt("SWEBENCH_DISCIPLINE"))
    for card in folder.cards:
        assert f"<location>/skills/{card.location}</location>" in listing


def test_the_card_rule_travels_apart_from_the_block(tmp_path):
    folder = _folder(tmp_path)
    block = render_block(folder=folder)
    assert get_prompt("SWEBENCH_SKILL_READ") not in block
    assert get_prompt("SWEBENCH_SKILL_READ_MANDATORY") not in block
    assert card_rule(folder=folder) == get_prompt("SWEBENCH_SKILL_READ")
    rule = card_rule(folder=folder, mandatory=True)
    assert rule == get_prompt("SWEBENCH_SKILL_READ_MANDATORY")
    assert "You MUST cat at least one skill file" in rule
    assert "/skills/catalog.md" in rule
    # No cards: nothing to read, so no card rule at all.
    assert card_rule(folder=SkillFolder(None, [], ""), mandatory=True) == ""
    assert card_rule() == ""
    with pytest.raises(ValueError, match="needs a folder unit"):
        card_rule(mandatory=True)


def test_a_folder_without_cards_or_playbook_sends_only_the_rules(tmp_path):
    empty = SkillFolder(None, [], "")
    assert render_block(folder=empty) == get_prompt("SWEBENCH_DISCIPLINE")
    assert render_block(folder=SkillFolder(None, [], PLAYBOOK)) == (
        format_playbook_block(PLAYBOOK) + get_prompt("SWEBENCH_DISCIPLINE"))
    without_playbook = render_block(folder=_folder(tmp_path, playbook=""))
    assert without_playbook.startswith("## Skills\n<available_skills>")
