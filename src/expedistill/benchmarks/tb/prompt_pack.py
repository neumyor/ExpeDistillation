"""Terminal Bench 2.1 prompt pack (layer 3): every TB-specific prompt input.

The vocabulary is the words the learning prompts carried before the
vocabulary became slots; ``tests/fixtures/prompts/learning_prompts_tb.json``
pins that the prompts render to the same wording (line wrapping aside).
"""

from expedistill.benchmarks.base import PromptPack
from expedistill.benchmarks.tb.playbook import (
    PLAYBOOK_PROMPT_NAME,
    facts_from_sheets,
    playbook_requirements,
)
from expedistill.prompts.learning_context import LearningContext

__all__ = ["PROMPT_PACK"]

_VOCABULARY = LearningContext(name="tb", slots={
    "agent_kind": "terminal",
    "benchmark_name": "Terminal Bench V2.1",
    "skill_description": "a Terminal Bench V2.1 skill",
    "task_tag": "TB21",
    "action_noun": "command",
    "discipline_examples": (
        "check before acting, read the error before retrying, run the "
        "verifier before calling mark_task_complete."
    ),
    "executable_rules": (
        "- every task-specific value is a <placeholder> in angle brackets: "
        "file\n"
        "  names, paths, commands, flags, API names, constants, error "
        "strings,\n"
        "  entities;\n"
        "- a command may appear only in a general form that works after "
        "substituting\n"
        "  the placeholders;\n"
        "- scripts used as tools: write only the invocation pattern, the "
        "inputs, and\n"
        "  the expected output—never include the script body, even if the "
        "trajectory\n"
        "  showed it in full;\n"
        "- code as the deliverable (when the task is to write a program or "
        "module):\n"
        "  describe the structure the code must satisfy and how to verify "
        "it with\n"
        "  the task's tests—do not include the implementation;"
    ),
    "observable_signs": "file types, input structure, error text",
    "segment_label_examples": (
        '"build and verify a source project", '
        '"recover a key from known pairs"'
    ),
    "setup_steps": (
        "Environment setup, one-off commands, and task-specific data "
        "wrangling"
    ),
})

_PROTOCOL = """\
The reader solves one command-line task inside a Linux sandbox through a
terminal: it runs shell commands, reads their output, and iterates until it
declares the task complete. A verifier then checks the sandbox state
against the task's success condition. In a skill-folder trial the skills
are files mounted read-only at /opt/openclaw-skills/<task>/, listed in the
prompt's <available_skills> section with the location of each SKILL.md; the
reader opens a skill by reading that file from the terminal."""

PROMPT_PACK = PromptPack(
    vocabulary=_VOCABULARY,
    protocol=_PROTOCOL,
    executor_prompts=(
        "TB_TASK_PROMPT",
        "TB_TASK_PROMPT_FOLDER",
        "TB_TASK_PROMPT_READ",
        "TB_TASK_PROMPT_READ_PLAYBOOK",
    ),
    playbook_seed="",
    playbook_facts=facts_from_sheets,
    playbook_checks=tuple(playbook_requirements()),
    overrides={"PLAYBOOK_UPDATE": PLAYBOOK_PROMPT_NAME},
)
