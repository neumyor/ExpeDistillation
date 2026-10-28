"""D8 leak-audit judge prompt (blocks/audit.py).

The judge IS the audit: it reads the skill, the exam scene and the
protected reference answer, and decides whether the skill reveals the answer
material. Its ``reason`` is reused as the rewrite (G6) error description when
K4 voids a version, so the prompt forbids restating the reference answer --
the rewriter must never see it.
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["LEAK_AUDIT_JUDGE"]

LEAK_AUDIT_JUDGE = register(
    PromptSpec(
        name="LEAK_AUDIT_JUDGE",
        template="""Judge whether the skill below leaks the answer to the exam scene.

EXAM SCENE:
{scene_text}

REFERENCE ANSWER (protected material; the skill's reader never sees it):
{reference_answer}

SKILL:
{skill_text}

The skill LEAKS when it contains, trivially rephrases, or directly reveals the
protected answer material: the reference answer itself, or the exam's concrete
answer-specific entities -- names, values, file names -- that would let a reader
answer this exam by lookup instead of ability.

The skill does NOT leak when it only gives generic method advice that would
help on ANY similar task, even when that advice also fits this exam.
Protocol words, action names, and environment feedback strings are never
leaks, even when the same words appear in the scene or the reference
answer.

Judge the skill text only; do not judge the exam or its answer.

The reason must NEVER restate, quote, or paraphrase the reference answer or its
concrete entities: the reason is reused as rewrite context whose reader must
not see the answer. Name the KIND of leaked material instead (for example "a
concrete value from the exam situation"), never the material itself.

Output ONLY JSON with exactly these fields:
{{"leaked": <true|false>,
  "reason": "<one to three sentences; never the protected material>"}}
""",
        description=(
            "D8 leak audit: judge whether a skill reveals the exam's "
            "protected answer material."
        ),
    )
)
