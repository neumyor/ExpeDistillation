"""SWE-bench Verified prompt pack (layer 3): every SWE-bench-specific prompt
input.

The environment facts in the protocol are the harness's own settings
(``swebench_bare.yaml``: a fresh subshell per command, a 60 s timeout, output
over 10000 characters cut to its head and tail, the submit command) and the
bridge's skill mount; nothing is invented. The playbook uses the method's
own ``PLAYBOOK_UPDATE``: no baseline facts, no required parts.
"""

from expedistill.benchmarks.base import PromptPack
from expedistill.benchmarks.swebench.diagnose import GRADER_REPORT_PROMPT_NAME
from expedistill.prompts.learning_context import LearningContext

__all__ = ["PROMPT_PACK"]

_VOCABULARY = LearningContext(name="swebench", slots={
    "agent_kind": "coding",
    "benchmark_name": "SWE-bench Verified",
    "skill_description": "a SWE-bench Verified skill",
    "task_tag": "SWE-bench",
    "action_noun": "command",
    "discipline_examples": (
        "reproduce the issue before editing, read the error before "
        "retrying, rerun the reproduction and the relevant existing tests "
        "before writing the patch."
    ),
    "executable_rules": (
        "- every task-specific value is a <placeholder> in angle brackets: "
        "file\n"
        "  names, paths, modules, classes, functions, test names, error "
        "strings,\n"
        "  values;\n"
        "- a command may appear only in a general form that works after "
        "substituting\n"
        "  the placeholders;\n"
        "- scripts used as tools (a reproduction script, a search): write "
        "only the\n"
        "  invocation pattern, the inputs, and the expected output—never "
        "the script\n"
        "  body, even if the trajectory showed it in full;\n"
        "- the fix itself: describe where to look, what kind of change the "
        "code needs\n"
        "  and how to verify it with the repository's own tests—never the "
        "code of a\n"
        "  fix;"
    ),
    "observable_signs": (
        "the issue text, tracebacks and error messages, command output, "
        "test results"
    ),
    "segment_label_examples": (
        '"locate the code an issue points to", '
        '"reproduce a reported bug with a script"'
    ),
    "setup_steps": (
        "Repository orientation, one-off commands, and patch formatting"
    ),
})

_PROTOCOL = """\
The reader fixes one GitHub issue in a Python repository checked out at
/testbed, inside a sandbox without internet access. It works by running
shell commands, one response at a time: each command runs in a fresh
subshell (directory and environment changes do not persist) with a 60 s
timeout, and an output over 10000 characters shows only its head and tail.
It must change only non-test source files. It submits by writing the git
diff of the source files it changed to patch.txt and running the exact
submit command; the run ends there. Hidden FAIL_TO_PASS and PASS_TO_PASS
tests then grade the patch in a fresh sandbox. In a skill-folder trial the
skills are files at /skills/<name>/SKILL.md, listed in the prompt's
<available_skills> section with the location of each; the reader opens a
skill by running cat on that location, which costs one command, and the
card's text then stays in the conversation. Under the optional card rule it
opens at most 3 cards per task, and only when a card's description matches
its current situation; under the mandatory rule it must open the card that
best matches the task before its first task command, and reads further
cards one at a time as it needs them."""

PROMPT_PACK = PromptPack(
    vocabulary=_VOCABULARY,
    protocol=_PROTOCOL,
    executor_prompts=(
        "SWEBENCH_INSTANCE_TEMPLATE",
        "SWEBENCH_SYSTEM_TEMPLATE",
        "SWEBENCH_SKILL_READ",
        "SWEBENCH_DISCIPLINE",
    ),
    playbook_seed="",
    playbook_facts=None,
    playbook_checks=(),
    overrides={"TRIAL_FAILURE_ANALYSIS": "SWEBENCH_TRIAL_FAILURE_ANALYSIS"},
    grader_report_prompt=GRADER_REPORT_PROMPT_NAME,
)
