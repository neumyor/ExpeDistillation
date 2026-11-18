"""SWE-bench Verified executor protocol (layer 3): every text the agent receives.

A SWE-bench unit runs mini-SWE-agent through the outer swebench-eval
harness (``plugin.py`` drives it through the harness's bridge script). The
agent's two first messages come from the harness's ``swebench_bare.yaml``:

1. the system message, ``SWEBENCH_SYSTEM_TEMPLATE`` (a byte copy);
2. the first user message, the instance template with the issue text in it
   (``SWEBENCH_INSTANCE_TEMPLATE``, a byte copy with ``{{task}}`` as
   ``{instruction}`` and the literal braces doubled).

The harness places a skill block in front of ``<pr_description>`` under its
own ``## Skill Knowledge`` header, the same slot the harness's skill methods
use. :func:`render_block` builds that block's text: the global playbook
(``inject.skills.format_playbook_block``), the ``<available_skills>`` list of
a folder unit (or a whole-bank skills block), and the working rules
``SWEBENCH_DISCIPLINE``. A bare unit sends no block, so its prompt is the
harness's bare prompt.

A folder unit's card-reading rule (:func:`card_rule`: ``SWEBENCH_SKILL_READ``,
or ``SWEBENCH_SKILL_READ_MANDATORY`` under ``read_rule: mandatory``) goes to
the end of the message instead, after ``</instructions>`` in the harness's
``<skill_rule>`` slot: placed in the skill block, Qwen opened no card in 7
val units under the mandatory rule; at the end, 5 of 6 opened one at the
first step (docs/decisions/swebench_plugin_20261118.md).
"""

from typing import Optional
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from expedistill.benchmarks.skill_folder import SkillFolder
from expedistill.inject.skills import format_playbook_block
from expedistill.prompts import registry
from expedistill.prompts.registry import PromptSpec, register

__all__ = [
    "SKILL_MOUNT",
    "SWEBENCH_DISCIPLINE",
    "SWEBENCH_INSTANCE_TEMPLATE",
    "SWEBENCH_SKILL_READ",
    "SWEBENCH_SKILL_READ_MANDATORY",
    "SWEBENCH_SYSTEM_TEMPLATE",
    "card_rule",
    "render_block",
]

#: Where the bridge writes a folder unit's files in the agent sandbox,
#: outside /testbed so no card can end up in the patch.
SKILL_MOUNT = "/skills"

SWEBENCH_SYSTEM_TEMPLATE = register(
    PromptSpec(
        name="SWEBENCH_SYSTEM_TEMPLATE",
        template="""You are a helpful assistant that can interact with a computer shell to solve programming tasks.
""",
        description=(
            "SWE-bench executor: mini-SWE-agent's system message "
            "(swebench-eval configs/swebench_bare.yaml agent.system_template, "
            "byte for byte). The harness sends it; this copy freezes it "
            "into the manifest."
        ),
    )
)

SWEBENCH_INSTANCE_TEMPLATE = register(
    PromptSpec(
        name="SWEBENCH_INSTANCE_TEMPLATE",
        template="""<pr_description>
Consider the following PR description:
{instruction}
</pr_description>

<instructions>
# Task Instructions

## Overview

You're a software engineer interacting continuously with a computer by submitting commands.
You'll be helping implement necessary changes to meet requirements in the PR description.
Your task is specifically to make changes to non-test files in the current directory in order to fix the issue described in the PR description in a way that is general and consistent with the codebase.
<IMPORTANT>This is an interactive process where you will think and issue AT LEAST ONE command, see the result, then think and issue your next command(s).</important>

For each response:

1. Include a THOUGHT section explaining your reasoning and what you're trying to accomplish
2. Provide one or more bash tool calls to execute

## Important Boundaries

- MODIFY: Regular source code files in /testbed (this is the working directory for all your subsequent commands)
- DO NOT MODIFY: Tests, configuration files (pyproject.toml, setup.cfg, etc.)

## Recommended Workflow

1. Analyze the codebase by finding and reading relevant files
2. Create a script to reproduce the issue
3. Edit the source code to resolve the issue
4. Verify your fix works by running your script again
5. Test edge cases to ensure your fix is robust

## Command Execution Rules

You are operating in an environment where

1. You issue at least one command
2. The system executes the command(s) in a subshell
3. You see the result(s)
4. You write your next command(s)

Each response should include:

1. **Reasoning text** where you explain your analysis and plan
2. At least one tool call with your command

**CRITICAL REQUIREMENTS:**

- Your response SHOULD include reasoning text explaining what you're doing
- Your response MUST include AT LEAST ONE bash tool call. You can make MULTIPLE tool calls in a single response when the commands are independent (e.g., searching multiple files, reading different parts of the codebase).
- Directory or environment variable changes are not persistent. Every action is executed in a new subshell.
- However, you can prefix any action with `MY_ENV_VAR=MY_VALUE cd /path/to/working/dir && ...` or write/load environment variables from files

Example of a CORRECT response:
<example_response>
I need to understand the Builder-related code. Let me find relevant files and check the project structure.

[Makes multiple bash tool calls: {{"command": "ls -la"}}, {{"command": "find src -name '*.java' | grep -i builder"}}, {{"command": "cat README.md | head -50"}}]
</example_response>

## Environment Details

- You have a full Linux shell environment
- Always use non-interactive flags (-y, -f) for commands
- Avoid interactive tools like vi, nano, or any that require user input
- You can use bash commands or invoke any tool that is available in the environment
- You can also create new tools or scripts to help you with the task
- If a tool isn't available, you can also install it

## Submission

When you've completed your work, you MUST submit your changes as a git patch.
Follow these steps IN ORDER, with SEPARATE commands:

Step 1: Create the patch file
Run `git diff -- path/to/file1 path/to/file2 > patch.txt` listing only the source files you modified.
Do NOT commit your changes.

<IMPORTANT>
The patch must only contain changes to the specific source files you modified to fix the issue.
Do not submit file creations or changes to any of the following files:

- test and reproduction files
- helper scripts, tests, or tools that you created
- installation, build, packaging, configuration, or setup scripts unless they are directly part of the issue you were fixing (you can assume that the environment is already set up for your client)
- binary or compiled files
</IMPORTANT>

Step 2: Verify your patch
Inspect patch.txt to confirm it only contains your intended changes and headers show `--- a/` and `+++ b/` paths.

Step 3: Submit (EXACT command required)
You MUST use this EXACT command to submit:

```bash
echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt
```

If the command fails (nonzero exit status), it will not submit.

<CRITICAL>
- Creating/viewing the patch and submitting it MUST be separate commands (not combined with &&).
- If you modify patch.txt after verifying, you SHOULD verify again before submitting.
- You CANNOT continue working (reading, editing, testing) in any way on this task after submitting.
</CRITICAL>
</instructions>
""",
        description=(
            "SWE-bench executor: mini-SWE-agent's first user message "
            "(swebench-eval configs/swebench_bare.yaml "
            "agent.instance_template, byte for byte, with {{task}} as "
            "{instruction}). The harness fills it; this copy freezes the "
            "executor protocol into the manifest and renders the task text "
            "the learn phase shows its diagnoses."
        ),
    )
)

SWEBENCH_SKILL_READ = register(
    PromptSpec(
        name="SWEBENCH_SKILL_READ",
        template=(
            "Skill cards are optional. When this prompt lists "
            "<available_skills>, you may open one card by running\n"
            "`cat <location>` with a location from that list. The card's "
            "full text then appears in the command output and stays in "
            "this conversation until the task ends.\n"
            "Open at most 3 cards per task.\n"
            "Open a card only when its description matches the current "
            "situation; if none matches, open none."
        ),
        description=(
            "SWE-bench executor, skill-folder units: the card-reading rule "
            "after the <available_skills> list, TB_SKILL_READ's wording."
        ),
    )
)

SWEBENCH_SKILL_READ_MANDATORY = register(
    PromptSpec(
        name="SWEBENCH_SKILL_READ_MANDATORY",
        template="""Before running any task command:
1. Read the PR description and decide what the fix needs.
2. Read the skill list in <available_skills>. Each entry says what the skill teaches and when to use it.
3. Choose the skill whose description best matches the task, and cat its SKILL.md at the <location> the list gives. You MUST cat at least one skill file this way before running any task command, even if the task looks simple.
Read only the skills you need, one at a time; do not open several at once. If the list is no longer visible, cat /skills/catalog.md (its paths are relative to /skills).""",
        description=(
            "SWE-bench executor, skill-folder units under read_rule "
            "mandatory: the card-reading rule of TB_SKILL_READ_MANDATORY "
            "(a card read before the first task command), in place of "
            "SWEBENCH_SKILL_READ."
        ),
    )
)

SWEBENCH_DISCIPLINE = register(
    PromptSpec(
        name="SWEBENCH_DISCIPLINE",
        template="""Working rules, every task:
- Keep command output short: read code with `grep -n` and `sed -n '<start>,<end>p'`, and end long commands with `| head -n 80` or `| tail -n 80`. Run tests quietly (`pytest -q`) and show only the tail. Long outputs fill the context, and when it is full the task ends without a submission.
- Before you edit, follow the call path from the code in the PR description's example to the place where the behavior is decided, and fix it there; the line that raises the error is not always that place.
- Find the existing tests of the code you change (`grep -rn "<function or class name>"` in the repository's test directories), run them before and after your edit, and read how they call the code. Do not edit them.
- Change only what the PR description needs, in the style of the surrounding code.
Before you submit:
- Run your reproduction script and the existing tests of every file you changed once more, and read the output.
- Write the patch with `git diff -- <the source files you changed> > patch.txt`, then run `cat patch.txt` as its own command and check that it starts with `diff --git`, has `--- a/` and `+++ b/` lines, and contains every change you made. If it is empty or shows anything else, fix it first.
- Submit with exactly `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt` as its own command. Never submit by printing a source file or any other text.""",
        description=(
            "SWE-bench executor, every unit with a skill block: the "
            "hand-written working rules, last in the block."
        ),
    )
)


def _available_skills(folder: SkillFolder) -> str:
    """The folder's ``<available_skills>`` list in the shape of
    ``SkillFolder.available_skills_block``, each location the card's path
    in the agent sandbox."""
    root = Element("available_skills")
    for card in folder.cards:
        skill = SubElement(root, "skill")
        SubElement(skill, "name").text = card.name
        SubElement(skill, "description").text = card.description
        SubElement(skill, "location").text = f"{SKILL_MOUNT}/{card.location}"
    indent(root, space="  ")
    return tostring(root, encoding="unicode")


def render_block(*, folder: Optional[SkillFolder] = None,
                 skills_block: str = "") -> str:
    """The skill block of one unit, "" for a bare unit.

    A folder unit: the playbook, then (when the folder has cards) the
    ``## Skills`` list, then the working rules. Any other unit with a
    non-empty ``skills_block`` (a whole bank, or a playbook alone): that
    block, then the working rules."""
    if folder is not None:
        parts = [format_playbook_block(folder.playbook_text).strip()]
        if folder.cards:
            parts.append(f"## Skills\n{_available_skills(folder)}")
    elif (skills_block or "").strip():
        parts = [skills_block.strip()]
    else:
        return ""
    parts.append(registry.get("SWEBENCH_DISCIPLINE"))
    return "\n\n".join(part for part in parts if part)


def card_rule(*, folder: Optional[SkillFolder] = None,
              mandatory: bool = False) -> str:
    """The card-reading rule of one unit, "" when it lists no card (no
    folder, or a folder without cards): ``SWEBENCH_SKILL_READ_MANDATORY``
    when ``mandatory``, else ``SWEBENCH_SKILL_READ``."""
    if mandatory and folder is None:
        raise ValueError("a mandatory card read needs a folder unit")
    if folder is None or not folder.cards:
        return ""
    return registry.get("SWEBENCH_SKILL_READ_MANDATORY" if mandatory
                        else "SWEBENCH_SKILL_READ")
