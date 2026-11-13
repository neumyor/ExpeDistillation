"""SearchQA prompt pack (layer 3): every SearchQA-specific prompt input.

The two actions and the answer rule named here are the ones the task
prompt in ``plugin.py`` (``_TASK_TEMPLATE``) already uses; nothing is
invented.
"""

from expedistill.benchmarks.base import PromptPack
from expedistill.prompts.learning_context import LearningContext

__all__ = ["PROMPT_PACK"]

_VOCABULARY = LearningContext(name="searchqa", slots={
    "agent_kind": "search",
    "benchmark_name": "SearchQA",
    "skill_description": "a SearchQA skill",
    "task_tag": "SearchQA",
    "action_noun": "action",
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
})

_PROTOCOL = """\
The reader answers one trivia question in a stepwise search environment.
At every step it sees the question and the transcript so far: each earlier
action with its observation (long observations are shortened). Every reply
is exactly one action:

- Search[<query>]: the observation is the passage of the collection that
  best matches <query>;
- Finish[<answer>]: submits <answer> as the final answer and ends the
  episode.

The episode has a small step budget, and a run that never replies with
Finish[<answer>] is not scored. The final answer is a few words or a short
phrase."""

PROMPT_PACK = PromptPack(
    vocabulary=_VOCABULARY,
    protocol=_PROTOCOL,
    executor_prompts=("SEARCHQA_TASK_PROMPT", "SEARCHQA_TASK_PROMPT_FOLDER"),
    playbook_seed="",
    playbook_facts=None,
    playbook_checks=(),
    overrides={},
)
