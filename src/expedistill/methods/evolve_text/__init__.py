"""Learn step 4: evolve one global text from the phase's notes (layer 6).

The playbook's ``facts`` come with every learn phase: the benchmark's
baseline facts in the first one, the trigger-evidence counts afterwards
(``composites.evidence.trigger_facts``).

One shell serves both texts::

    evolve_text(kind, old_text, notes, facts, handle) -> new_text

============ ===================== ====================================
kind         prompt                output check
============ ===================== ====================================
``profile``  ``PROFILE_UPDATE``    a non-empty ``profile_text``
``playbook`` ``PLAYBOOK_UPDATE``   the tagged playbook within the length
                                   bounds, passing the benchmark's checks
============ ===================== ====================================

With no note and no facts there is nothing to learn from: the old text is
returned unchanged and no model is called. A reply that fails its check is
sent back with the problem named (the missing playbook parts by their
description), at most ``REPAIRS`` times, then a ModelOutputError. Nothing
is written to disk here.

Playbook reply contract (the prompt ends with the same shape): the reply
ends with the playbook between ``<playbook>`` and ``</playbook>``. A
reasoning model's visible reply holds drafts before its answer, so the
answer is read from the END: the span from the last opening tag to the
closing tag that ends the reply.
"""

from typing import Any, List, Sequence

from expedistill import prompts
from expedistill.infra.reliability import CodeBugError, ModelOutputError
from expedistill.llm.call import call_model
from expedistill.prompts.profile import profile_section

__all__ = [
    "MAX_PLAYBOOK_CHARS",
    "MIN_PLAYBOOK_CHARS",
    "REPAIRS",
    "evolve_text",
    "extract_playbook",
    "missing_requirements",
]

#: The agent reads the playbook at every step, so its size is bounded by
#: contract (the prompt states the limit).
MAX_PLAYBOOK_CHARS = 20000
#: Below this size the span is a placeholder, not a playbook.
MIN_PLAYBOOK_CHARS = 300
#: Corrections after the first ask.
REPAIRS = 2

_OPEN = "<playbook>"
_CLOSE = "</playbook>"

_PROMPTS = {"profile": "PROFILE_UPDATE", "playbook": "PLAYBOOK_UPDATE"}


def extract_playbook(reply: Any) -> str:
    """The playbook of one reply, or ``ValueError`` naming what is wrong."""
    text = str(reply).rstrip()
    if not text.endswith(_CLOSE):
        raise ValueError(
            f"the reply does not end with the closing {_CLOSE} tag; "
            "nothing may follow it")
    start = text.rfind(_OPEN)
    if start < 0:
        raise ValueError(f"the reply has no opening {_OPEN} tag")
    body = text[start + len(_OPEN):-len(_CLOSE)]
    if _CLOSE in body:
        raise ValueError(
            f"a {_CLOSE} tag appears between the last opening tag and the "
            "end of the reply")
    body = body.strip()
    if len(body) < MIN_PLAYBOOK_CHARS:
        raise ValueError(
            f"the playbook has {len(body)} characters; it needs at least "
            f"{MIN_PLAYBOOK_CHARS}")
    if len(body) > MAX_PLAYBOOK_CHARS:
        raise ValueError(
            f"the playbook has {len(body)} characters; the limit is "
            f"{MAX_PLAYBOOK_CHARS}. Write a shorter playbook")
    return body


def _extract_profile(reply: Any) -> str:
    if not isinstance(reply, dict):
        raise ValueError(
            f"expected a JSON object, got {type(reply).__name__}")
    text = reply.get("profile_text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("profile_text must be a non-empty string")
    return text.strip()


def missing_requirements(text: str, requirements: Sequence[Any]) -> List[str]:
    """The names of the requirements the text does not meet."""
    return [item.name for item in requirements if not item.matches(text)]


def evolve_text(
    kind: str,
    old_text: str,
    notes: Sequence[str],
    facts: str,
    handle: Any,
    *,
    profile: str = "",
    checks: Sequence[Any] = (),
    usage: Any = None,
    run_dir: Any = None,
) -> str:
    """The next version of the ``kind`` text.

    ``profile`` is the phase's evolved weak-model profile (the playbook's
    reader); ``checks`` are the playbook requirements of the benchmark's
    prompt pack (``PlaybookRequirement``).
    """
    if kind not in _PROMPTS:
        raise CodeBugError(
            f"unknown evolving text kind {kind!r}; known: {sorted(_PROMPTS)}")
    cleaned = [note.strip() for note in notes if note and note.strip()]
    if not cleaned and not facts.strip():
        return old_text
    prompt = _PROMPTS[kind]
    variables = {
        f"old_{kind}": old_text.strip() or "(empty)",
        f"{kind}_notes": "\n\n".join(cleaned) or "(none)",
        "facts": facts.strip() or "(none)",
        "profile_section": profile_section(
            "playbook_reader", profile,
            handle.prompt_defaults.get("agent_kind")),
        "correction_note": "",
    }
    problem = "no acceptable reply"
    for _ in range(REPAIRS + 1):
        reply = call_model(handle, prompt, variables,
                           output_json=kind == "profile",
                           usage=usage, run_dir=run_dir)
        try:
            text = (_extract_profile(reply) if kind == "profile"
                    else extract_playbook(reply))
        except ValueError as exc:
            problem = str(exc)
            variables["correction_note"] = prompts.get(
                "EVOLVE_CORRECTION").format(problem=problem)
            continue
        missing = missing_requirements(text, checks)
        if not missing:
            return text
        problem = "the playbook is missing: " + ", ".join(missing)
        variables["correction_note"] = prompts.get(
            "PLAYBOOK_PARTS_CORRECTION").format(
                missing=", ".join(missing),
                missing_parts="\n".join(
                    f"- {item.name}: {item.describe}"
                    for item in checks if item.name in missing))
    raise ModelOutputError(f"{prompt}: {problem}")
