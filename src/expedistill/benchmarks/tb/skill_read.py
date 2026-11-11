"""TB's skill-read suffix: the text Harbor's Terminus agent appends to the
task description of a skill-folder trial.

The text is the frozen protocol of what a folder unit's agent is told about
the skill list, so it lives here as named prompts and their digests freeze
into the manifest (it used to be a default argument of the vendored agent,
outside every digest). ``TB_TASK_PROMPT_READ`` is that default, byte for
byte. ``TB_TASK_PROMPT_READ_PLAYBOOK`` puts a learned global playbook in
front of it. The plugin uploads the rendered text as the task directory's
``SKILL_READ_SUFFIX.md`` and the vendored agent uses it in place of its
default (``tencent_sandbox_terminus.py``), so a new playbook reaches the
agent without any change outside this file's output.
"""

from expedistill.prompts import registry
from expedistill.prompts.registry import PromptSpec, register

__all__ = [
    "READ_SUFFIX_FILENAME",
    "TB_TASK_PROMPT_READ",
    "TB_TASK_PROMPT_READ_PLAYBOOK",
    "render_read_suffix",
]

#: The file the vendored agent looks for beside the task-level SKILL.md.
READ_SUFFIX_FILENAME = "SKILL_READ_SUFFIX.md"

TB_TASK_PROMPT_READ = register(
    PromptSpec(
        name="TB_TASK_PROMPT_READ",
        template='Before choosing a skill:\n1. Read the task description. Look at the workspace once (for example `ls` and a look at the files the task names) so you know what is there.\n2. Then pick at most two skills from <available_skills> whose "when to use" matches what you actually saw. If none matches, you do not have to open any skill: say so in one line and continue.\n3. Read only the skills you need, one at a time; do not open several at once. If the list is no longer visible, cat catalog.md in the same folder.\nAfter reading a skill, write one line before acting: which numbered step of it applies to this task and what its completion check is.\nShell hygiene, every task:\n- Never nest heredocs. Write a multi-line file with one top-level heredoc, or with `printf`/`python3 - <<\'PY\'`; if the content itself needs a here-doc, write the inner script to a file first.\n- After any multi-line input, run `echo SHELL_OK` and see it before the next step.\n- If the prompt stops echoing: Ctrl-C, then `reset`, then open a new shell (`tmux new-window`). Do not keep typing into a dead terminal; save partial results before switching.\nGraded thresholds:\n- For a required performance ratio, leave 30-50% headroom (target 1.5x when 1.2x is required). Measure several times and use the median, not one lucky run.\n- For a numeric estimate with a tolerance, make the Monte Carlo error much smaller than that tolerance (more samples or chains than needed), rerun once with a different seed, and submit only when both runs fall inside the band.\nBefore you declare the task complete:\n- List the output paths the task names and confirm each exists.\n- Re-run the check that proves the success condition from the task text, and read its output. If that output shows a failure, an error, or no expected result, the task is not complete: keep working instead of declaring it done.\n- If the grader will run your deliverable again (a script, a macro, a Makefile), run it again from a clean copy and compare, not only the result of your last run.',
        description=(
            "E1 TB runner, skill-folder mode: the instruction block the "
            "agent reads after the task description (two-step card choice, "
            "one-line restatement, shell hygiene, graded thresholds, "
            "completion gate). Byte-identical to the vendored agent's "
            "default."
        ),
    )
)

TB_TASK_PROMPT_READ_PLAYBOOK = register(
    PromptSpec(
        name="TB_TASK_PROMPT_READ_PLAYBOOK",
        template=(
            "Global playbook (rules learned from earlier runs in this "
            "environment; they apply to every task):\n"
            "{playbook}\n"
            "\n"
            "{skill_read}"
        ),
        description=(
            "E1 TB runner, skill-folder mode with a learned playbook: the "
            "playbook goes in front of the skill-read suffix."
        ),
    )
)


def render_read_suffix(playbook_text: str = "") -> str:
    """The suffix a folder unit's agent receives: the skill-read text, with
    the playbook in front when there is one. Without a playbook this is
    ``TB_TASK_PROMPT_READ`` itself, so a run that never asks for a
    playbook sends the agent exactly what it always sent."""
    base = registry.get("TB_TASK_PROMPT_READ")
    if not playbook_text or not playbook_text.strip():
        return base
    return registry.get("TB_TASK_PROMPT_READ_PLAYBOOK").format(
        playbook=playbook_text.strip(), skill_read=base)
