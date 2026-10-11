"""Exam prompts: G3 question generation, G4 answering, G5 judging.

Stage-4 acceptance rules constrain these templates: the judge never reveals the
reference answer, and the rewrite prompt (G6, in author.py) must be able to work
from the judge's error description alone, so JUDGE's error_description below may
only say what is wrong.
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["EXAM_GEN", "ANSWER", "JUDGE"]

EXAM_GEN = register(
    PromptSpec(
        name="EXAM_GEN",
        template="""Write one exam question that tests whether a model has the ability exercised
by the subtask below.

SUBTASK LABEL: {label}
SUBTASK DESCRIPTION: {description}
STEPS OF THE SUBTASK:
{steps_view}

The exam is a TEXT question answering exam: present a situation (for example, an
observation a model might face, or a description of a state), and ask what should
be done and why.

Requirements:
- The question must test the ABILITY this subtask exercised, not the original
  task itself. Change the surface details (a different setting, different
  concrete objects, different names) so that remembering the subtask's steps is
  not enough to answer and the answer cannot be memorized.
- The situation must be self-contained: a reader who has never seen the subtask
  can understand and answer the question.
- Do not mention that this question comes from a trajectory, a subtask, or an
  exam generation process.

Output ONLY JSON with exactly these fields:
{{"question_text": "<the situation and the question, self-contained>",
  "reference_answer": "<what should be done and why; a grading reference that is
  never shown to the model being examined>"}}
""",
        description=(
            "G3 exam generation: a text QA question about the subtask's "
            "ability, distinct from the original task."
        ),
    )
)

ANSWER = register(
    PromptSpec(
        name="ANSWER",
        template="""Answer the question below.

QUESTION:
{question_text}

SKILLS:
{skills_block}

If the skills block above is not empty, first check whether any skill applies to
the situation in the question. Follow the applicable skill exactly; if several
apply, follow the most relevant one. If no skill applies, or the block is empty,
answer from your own knowledge.

Give your reasoning step by step, then state the final answer clearly.
""",
        description=(
            "G4 answering: answer an exam question with optional skill "
            "block (empty block = bare answer, same template)."
        ),
    )
)

JUDGE = register(
    PromptSpec(
        name="JUDGE",
        template="""Grade an exam answer.

QUESTION:
{question_text}

REFERENCE ANSWER (grading standard; never shown to the examined model):
{reference_answer}

ANSWER TO GRADE:
{weak_answer}

The answer to grade is correct when it states the right action for the situation
and a sound reason for it, even if worded differently from the reference answer.

If it is incorrect, the error description must say only WHAT is wrong with the
answer. Never restate, quote, or paraphrase the correct answer in the error
description.

Output ONLY JSON with exactly these fields:
{{"verdict": "<correct|incorrect>",
  "error_description": "<empty string when correct; otherwise only what is wrong,
  never the correct answer>"}}
""",
        description=(
            "G5 judging: verdict correct/incorrect plus an error description "
            "that never reveals the correct answer."
        ),
    )
)
