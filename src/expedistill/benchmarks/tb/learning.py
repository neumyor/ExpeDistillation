"""Terminal Bench 2.1 vocabulary for the learning prompts (layer 3).

These are the words the learning prompts carried before the vocabulary
became slots; ``tests/fixtures/prompts/learning_prompts_tb.json`` pins that
the prompts render to the same wording (line wrapping aside).
"""

from expedistill.prompts.learning_context import (
    LearningContext,
    trigger_timing_slots,
)

__all__ = ["TB_CONTEXT"]

TB_CONTEXT = LearningContext(name="tb", slots={
    "agent_kind": "terminal",
    "benchmark_name": "Terminal Bench V2.1",
    "skill_description": "a Terminal Bench V2.1 skill",
    "task_tag": "TB21",
    "action_noun": "command",
    "capability_section": (
        "If the segment fits none of the TB21 capability families below, "
        "use\n"
        '"general terminal procedure" and write only the meta-rules you '
        "can support.\n"
        "\n"
        "TB21 capability families:\n"
        "- source build, install and verification\n"
        "- runtime crash, hang and resource diagnosis\n"
        "- environment and dependency setup\n"
        "- numerical and scientific computation with result validation\n"
        "- cryptographic analysis and security forensics\n"
        "- sequence, primer and bioinformatics work\n"
        "- distributed PyTorch and parallel execution\n"
        "- data, text and large-file processing\n"
        "- terminal interaction and recovery\n"
        "- final verification and submission"
    ),
    "capability_example": '"recover a key from known plaintext-ciphertext pairs"',
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
    "group_examples": '"file inspection" or "error recovery"',
    **trigger_timing_slots(
        phrases={
            "at_task_start": "At task start,",
            "after_first_environment_check":
                "After the first environment check,",
            "when_command_fails": "When a command fails, hangs, or repeats,",
            "before_submitting": "Before submitting,",
            "at_any_step": "At any step,",
        },
        # The moments next to each phrase in SKILL_DISTILL / SKILL_DIAGNOSE.
        moments={
            "at_task_start":
                "the reader has just received the task and is reading the "
                "task description and the given files",
            "after_first_environment_check":
                "the reader has run its first ls, which, import or network "
                "probe and now knows what is installed and what is "
                "reachable",
            "when_command_fails":
                "a command gave an error, got stuck, or the same action "
                "keeps repeating without progress",
            "before_submitting":
                "the work looks done and the reader is about to run the "
                "final check and submit",
            "at_any_step":
                "a basic rule that holds at every step. Use this only when "
                "none of the four moments above fits",
        },
        # The meanings CLUSTER_TRIGGER_FAMILY lists beside the family keys.
        family_meanings={
            "at_task_start":
                "the reader has just received a new task and is reading the "
                "task description, inputs and initial files",
            "after_first_environment_check":
                "the reader has run its first ls, which, import or network "
                "probe and now knows what is installed and reachable",
            "when_command_fails":
                "a command produced an error, hung, or the same action "
                "keeps repeating without progress",
            "before_submitting":
                "the work looks done and the reader is about to run the "
                "final verifier and submit",
            "at_any_step":
                "a basic rule that applies at every step and fits none of "
                "the four moments above",
        },
        # The meanings CLUSTER_SUBTASKS shows as FAMILY MEANING.
        subtask_meanings={
            "at_task_start":
                "the reader has just received the task and is reading its "
                "description, inputs and initial files",
            "after_first_environment_check":
                "the reader has run its first ls / which / import / network "
                "probe and knows what is installed and reachable",
            "when_command_fails":
                "a command errored, hung, or the same action keeps "
                "repeating without progress",
            "before_submitting":
                "the work looks done and the reader is about to run the "
                "final verifier and submit",
            "at_any_step":
                "a basic rule that applies at every step and fits none of "
                "the four moments above",
        },
    ),
})
