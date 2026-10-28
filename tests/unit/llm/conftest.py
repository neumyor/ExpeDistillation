"""Test-only prompt fixtures for the generic B1 call tests.

The production exam prompts are now EXAM_SCENE / EXAM_TRIGGER / EXAM_PLAN /
EXAM_JUDGE.  The low-level call tests use small named prompts to exercise
formatting and retries, so register their local names only for the duration
of each test here instead of keeping retired production prompts alive.
"""

import pytest

from expedistill.prompts import PROMPTS
from expedistill.prompts.registry import PromptSpec, register

_PROMPTS = {
    "ANSWER": (
        "Answer this question.\n\nQUESTION:\n{question_text}\n\n"
        "SKILLS:\n{skills_block}",
        "test-only generic answer prompt"),
    "JUDGE": (
        "Judge the answer.\n\nQUESTION:\n{question_text}\n\n"
        "REFERENCE:\n{reference_answer}\n\nANSWER:\n{weak_answer}",
        "test-only generic judge prompt"),
    "EXAM_GEN": (
        "Write an exam.\n\nLABEL:\n{label}\n\nDESCRIPTION:\n{description}"
        "\n\nSTEPS:\n{steps_view}",
        "test-only generic exam-generation prompt"),
}


@pytest.fixture(autouse=True)
def _local_prompts():
    added = []
    for name, (template, description) in _PROMPTS.items():
        if name not in PROMPTS:
            register(PromptSpec(name=name, template=template,
                                description=description))
            added.append(name)
    yield
    for name in added:
        PROMPTS.pop(name, None)
