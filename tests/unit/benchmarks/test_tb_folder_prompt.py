"""Skill-folder M3, the prompt: a folder unit's task prompt is the plain
TB task prompt with its SKILLS slot replaced by the folder guidance.
Harbor's Terminus agent discovers each ``<skill>/SKILL.md`` itself and
appends the ``<available_skills>`` list; the catalog text is never part
of this prompt.

The registered template is the frozen protocol of what the remote agent
effectively receives (the Terminus agent composes its own prompt; the
guidance reaches it as the trial's task-level SKILL.md injection), so
what these tests pin is the text itself: the guidance section embedded
verbatim, no {skills_block} slot for a catalog to leak through, and the
bare / plain-skills prompt untouched by any folder wording.
"""

import string

from expedistill.benchmarks.tb.plugin import (
    TB_SKILL_FOLDER_GUIDANCE,
    TB_TASK_PROMPT,
)
from expedistill.prompts.registry import PROMPTS, digest, get

FOLDER_NAME = "TB_TASK_PROMPT_FOLDER"


def test_the_folder_prompt_is_registered_under_its_own_name():
    assert FOLDER_NAME in PROMPTS
    # A NEW name = a new experiment: it is not the plain task prompt.
    assert get(FOLDER_NAME) != get("TB_TASK_PROMPT")
    assert digest(FOLDER_NAME) != digest("TB_TASK_PROMPT")


def test_the_guidance_section_is_embedded_verbatim():
    # The prompt's SKILL FOLDER section IS the guidance text -- the same
    # bytes the remote skill agent injects as the trial's SKILL.md, so
    # the frozen protocol and the shipped injection cannot drift.
    assert TB_SKILL_FOLDER_GUIDANCE in get(FOLDER_NAME)


def test_the_catalog_cannot_leak_into_the_prompt():
    template = get(FOLDER_NAME)
    # No {skills_block} slot: there is nothing variable to inject, so a
    # catalog (or any other block) can never land in the prompt.
    assert "{skills_block}" not in template
    rendered = template.format(instruction="do the task", history="")
    catalog = ("## Skill folder catalog\n\nUsage rules:\n"
               "- cat only the skill file")
    assert catalog not in rendered
    assert "{instruction}" in template and "{history}" in template


def test_the_plain_task_prompt_carries_no_folder_wording():
    # Bare and plain-skills runs keep the frozen pre-folder prompt,
    # byte for byte: the control unit's prompt has no guidance.
    template = get("TB_TASK_PROMPT")
    assert TB_SKILL_FOLDER_GUIDANCE not in template
    assert "/opt/openclaw-skills" not in template
    assert template == TB_TASK_PROMPT.template
    # The three slots the harness fills: any other variable would be unfilled.
    fields = {name for _, name, _, _ in string.Formatter().parse(template)
              if name}
    assert fields == {"instruction", "skills_block", "history"}
