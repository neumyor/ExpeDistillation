"""Scope notices for a skill rewrite (ablation of where a rewrite may land).

A skill's *trigger* is what the reader sees in the card list before opening
the card: the one-line ``summary`` plus the ``[When to use]`` section. Its
*body* is everything behind it: ``[Steps]`` and ``[Do not use when]``.
A phase configured with ``revise_scope: body`` or ``trigger`` restricts a
rewrite to one of the two; the rewriter is told so here, and
``blocks/revision_scope.py`` enforces it on the model's output regardless.

``REVISION_SCOPE_TRIGGER`` carries one slot, ``{skill_summary}``: the
rewriter edits the summary, and the template shows it only the card text.
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["REVISION_SCOPE_BODY", "REVISION_SCOPE_TRIGGER"]

REVISION_SCOPE_BODY = register(
    PromptSpec(
        name="REVISION_SCOPE_BODY",
        template="""SCOPE OF THIS REWRITE - the body only. The trigger of this card is fixed.
Keep the [When to use] section exactly as in the current version: it is part
of the one line the reader sees in the card list before deciding to open the
card, and this rewrite must not change it. The summary is fixed as well.
Rewrite only [Steps] and [Do not use when].

""",
        description=(
            "G6 rewrite scope notice, body only: the trigger (summary and "
            "[When to use]) stays as it is."
        ),
    )
)

REVISION_SCOPE_TRIGGER = register(
    PromptSpec(
        name="REVISION_SCOPE_TRIGGER",
        template="""SCOPE OF THIS REWRITE - the trigger only. The reader first sees one line per
card in a card list, made of the summary and the [When to use] section, and
decides from that line alone whether to open the card. This rewrite may
change only those two. Keep [Steps] and [Do not use when] exactly as in the
current version.
CURRENT SUMMARY: {skill_summary}
Write a new [When to use] section and a new summary so that a reader in the
situation of the exam recognizes this card and opens it, and a reader in an
unrelated situation does not. The summary says what the card teaches;
[When to use] says when to open it. Do not repeat one in the other.

""",
        description=(
            "G6 rewrite scope notice, trigger only: [Steps] and [Do not use "
            "when] stay as they are; the summary and [When to use] are "
            "rewritten."
        ),
    )
)
