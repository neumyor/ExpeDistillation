"""SearchQA vocabulary for the learning prompts (layer 3).

The two actions and the answer rule named here are the ones the task
prompt in ``plugin.py`` (``_TASK_TEMPLATE``) already uses; nothing is
invented.
"""

from expedistill.prompts.learning_context import (
    LearningContext,
    trigger_timing_slots,
)

__all__ = ["SEARCHQA_CONTEXT"]

#: One text per moment; SearchQA shows it the same way in all three
#: writer and classifier prompts.
_MOMENTS = {
    "at_task_start":
        "the reader has just received the question and has not searched "
        "yet",
    "after_first_environment_check":
        "the reader has seen the passage returned by its first search and "
        "knows what the collection offers",
    "when_command_fails":
        "a search returned nothing useful, or the same query keeps "
        "repeating without progress",
    "before_submitting":
        "the reader has a candidate answer and is about to reply with "
        "Finish[<answer>]",
    "at_any_step":
        "a basic rule that holds at every step, such as keeping the final "
        "answer short. Use this only when none of the four moments above "
        "fits",
}

SEARCHQA_CONTEXT = LearningContext(name="searchqa", slots={
    "agent_kind": "search",
    "benchmark_name": "SearchQA",
    "skill_description": "a SearchQA skill",
    "task_tag": "SearchQA",
    "action_noun": "action",
    "capability_section": (
        "If the segment fits none of the SearchQA capability families "
        "below, use\n"
        '"general search procedure" and write only the meta-rules you can '
        "support.\n"
        "\n"
        "SearchQA capability families:\n"
        "- identifying the entity or fact the question asks for\n"
        "- choosing search queries from the question's key terms\n"
        "- reformulating a query after a weak or repeated result\n"
        "- bridging facts across several passages\n"
        "- answers that are dates, numbers or names\n"
        "- keeping the final answer short and in the form the question "
        "asks for\n"
        "- deciding when to stop searching and answer"
    ),
    "capability_example": '"reformulate a query after a result that does not help"',
    "discipline_examples": (
        "check the passage before relying on it, rewrite the query before "
        "repeating a search, compare the answer with the question before "
        "calling Finish[<answer>]."
    ),
    "executable_rules": (
        "- every task-specific value is a <placeholder> in angle brackets: "
        "entity\n"
        "  names, dates, numbers, query terms, passage text;\n"
        "- an action may appear only in a general form that works after "
        "substituting\n"
        "  the placeholders, for example Search[<query>] or "
        "Finish[<answer>];\n"
        "- use only the two actions the environment accepts, "
        "Search[<query>] and\n"
        "  Finish[<answer>]; never invent an action;"
    ),
    "observable_signs": "question wording, entity types, passage text",
    "segment_label_examples": (
        '"search for the entity the question names", '
        '"answer with a short phrase"'
    ),
    "setup_steps": "Reading the question, one-off query retries, and answer formatting",
    "group_examples": '"query formulation" or "answer formatting"',
    **trigger_timing_slots(
        phrases={
            "at_task_start": "At question start,",
            "after_first_environment_check": "After the first search,",
            "when_command_fails":
                "When a search returns nothing useful or repeats,",
            "before_submitting": "Before answering,",
            "at_any_step": "At any step,",
        },
        moments=_MOMENTS,
        family_meanings=_MOMENTS,
        subtask_meanings=_MOMENTS,
    ),
})
