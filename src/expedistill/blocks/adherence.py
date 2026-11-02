"""Skill-use judges (layer 4): card applicability and step adherence.

Two model calls, both after a trial is finished and both over its stored
trajectory (prompts ``SKILL_APPLICABILITY_JUDGE`` / ``SKILL_ADHERENCE_JUDGE``):

* ``judge_applicability`` -- for every card of the delivered list, did the
  situation named by its trigger arise anywhere in the trial?
* ``judge_adherence`` -- for one card the agent opened, which of its numbered
  steps did the agent carry out afterwards?

Validation is strict, like the exam blocks: ``call_model``'s format budget
covers JSON parsing only, so a reply that parses but breaks the contract (a
card id outside the list, a listed card left out, a step number the card does
not have, a status outside the four words) raises ModelOutputError rather
than being repaired by guessing. The blocks return plain dicts and touch no
disk; persistence belongs to the stage that calls them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from expedistill.core.skill_sections import split_sections
from expedistill.infra.reliability import ModelOutputError
from expedistill.llm.call import ModelHandle, call_model

__all__ = [
    "ADHERENCE_STATUSES",
    "card_steps",
    "judge_adherence",
    "judge_applicability",
    "render_card_listing",
]

#: The only statuses SKILL_ADHERENCE_JUDGE may emit for one card step.
ADHERENCE_STATUSES = ("followed", "partial", "skipped", "not_reached")

_STEPS_MARKER = "[Steps]"


def card_steps(card_text: str) -> List[str]:
    """The numbered items of a card's ``[Steps]`` section, in order.

    Items are recognised by their running number (``1.`` / ``1)``): item k
    starts at the first line after item k-1 that begins with ``k.``, so a
    continuation line that happens to start with some other number (a
    heredoc body, say) does not split an item. A card without a ``[Steps]``
    section, or with no numbered item in it, has no steps.
    """
    block = split_sections(card_text).get(_STEPS_MARKER)
    if block is None:
        return []
    items: List[str] = []
    for line in block.split("\n")[1:]:
        match = re.match(rf"{len(items) + 1}[.)]\s+(.*)", line)
        if match:
            items.append(match.group(1))
        elif items:
            items[-1] += "\n" + line
    return items


def _validated_cards(cards: Sequence[Mapping[str, Any]]) -> List[Dict[str, str]]:
    """The card list as ``[{"skill_id", "description"}]``; a malformed list
    is a caller bug (ValueError), not a model failure."""
    if isinstance(cards, (str, bytes)) or not cards:
        raise ValueError("cards must be a non-empty list of mappings")
    out: List[Dict[str, str]] = []
    seen = set()
    for card in cards:
        skill_id = card.get("skill_id") if isinstance(card, Mapping) else None
        description = card.get("description") \
            if isinstance(card, Mapping) else None
        if not isinstance(skill_id, str) or not skill_id.strip() \
                or not isinstance(description, str):
            raise ValueError(
                "every card needs a non-empty string 'skill_id' and a "
                f"string 'description', got {card!r}")
        if skill_id in seen:
            raise ValueError(f"card {skill_id!r} is listed twice")
        seen.add(skill_id)
        out.append({"skill_id": skill_id, "description": description})
    return out


def render_card_listing(cards: Sequence[Mapping[str, Any]]) -> str:
    """The card list as the applicability judge reads it: one entry per
    card, the id on one line and its description (what the agent saw next
    to the card's name) on the next."""
    return "\n".join(
        f"- skill_id: {card['skill_id']}\n  description: "
        f"{' '.join(card['description'].split())}"
        for card in _validated_cards(cards))


def _object(prompt: str, parsed: Any) -> Dict[str, Any]:
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"{prompt} must return a JSON object, got "
            f"{type(parsed).__name__}")
    return parsed


def _entries(prompt: str, parsed: Dict[str, Any], key: str) -> List[Any]:
    entries = parsed.get(key)
    if not isinstance(entries, list):
        raise ModelOutputError(
            f"{prompt}: field {key!r} must be a list, got {entries!r}")
    return entries


def judge_applicability(
    task_instruction: str,
    cards: Sequence[Mapping[str, Any]],
    trajectory_view: str,
    n_steps: int,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Which listed cards' triggers arose in one trial.

    ``cards`` is the list exactly as the agent saw it (``skill_id`` and
    ``description`` per card); ``trajectory_view`` is the rendered trial and
    ``n_steps`` its step count, which bounds ``first_step``. Returns
    ``{skill_id: {"applicable": bool, "first_step": int | None, "reason":
    str}}`` in the order of ``cards``, with EVERY card present. A reply that
    names a card outside the list, names one twice, leaves one out, or
    carries a field of the wrong type raises ModelOutputError. A card that
    does not apply always reports ``first_step`` None.
    """
    cards = _validated_cards(cards)
    known = [card["skill_id"] for card in cards]
    prompt = "SKILL_APPLICABILITY_JUDGE"
    parsed = _object(prompt, call_model(
        handle, prompt,
        {"task_instruction": task_instruction,
         "skill_listing": render_card_listing(cards),
         "trajectory_view": trajectory_view},
        output_json=True, usage=usage, run_dir=run_dir))

    result: Dict[str, Dict[str, Any]] = {}
    for entry in _entries(prompt, parsed, "skills"):
        if not isinstance(entry, dict):
            raise ModelOutputError(
                f"{prompt}: every 'skills' entry must be an object, "
                f"got {entry!r}")
        raw_id = entry.get("skill_id")
        skill_id = raw_id.strip() if isinstance(raw_id, str) else raw_id
        if skill_id not in known:
            raise ModelOutputError(
                f"{prompt}: unknown skill_id {raw_id!r}; the list holds "
                f"{known}")
        if skill_id in result:
            raise ModelOutputError(
                f"{prompt}: skill_id {skill_id!r} appears twice")
        applicable = entry.get("applicable")
        if not isinstance(applicable, bool):
            raise ModelOutputError(
                f"{prompt}: 'applicable' of {skill_id!r} must be true or "
                f"false, got {applicable!r}")
        first_step = entry.get("first_step")
        if first_step is not None and (
                isinstance(first_step, bool)
                or not isinstance(first_step, int)
                or not 0 <= first_step < n_steps):
            raise ModelOutputError(
                f"{prompt}: 'first_step' of {skill_id!r} must be null or a "
                f"step number in [0, {n_steps}), got {first_step!r}")
        reason = entry.get("reason")
        if not isinstance(reason, str):
            raise ModelOutputError(
                f"{prompt}: 'reason' of {skill_id!r} must be a string, "
                f"got {reason!r}")
        result[skill_id] = {
            "applicable": applicable,
            "first_step": first_step if applicable else None,
            "reason": reason.strip(),
        }
    missing = [skill_id for skill_id in known if skill_id not in result]
    if missing:
        raise ModelOutputError(
            f"{prompt}: the reply leaves out listed card(s) {missing}")
    return {skill_id: result[skill_id] for skill_id in known}


def judge_adherence(
    card_text: str,
    trajectory_view: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """How far one opened card's numbered steps were carried out.

    ``card_text`` is the card as the agent read it; ``trajectory_view`` is
    the rendered trial from the step where the card was opened. Returns one
    ``{"number", "status", "evidence"}`` per numbered ``[Steps]`` item, in
    order, ``status`` one of ``ADHERENCE_STATUSES``. A card with no numbered
    step is a caller bug (ValueError: there is nothing to judge); a reply
    that skips, repeats or invents a step number, or uses another status,
    raises ModelOutputError.
    """
    n_steps = len(card_steps(card_text))
    if n_steps == 0:
        raise ValueError("the card has no numbered [Steps] item to judge")
    prompt = "SKILL_ADHERENCE_JUDGE"
    parsed = _object(prompt, call_model(
        handle, prompt,
        {"card_text": card_text, "trajectory_view": trajectory_view,
         "n_steps": str(n_steps)},
        output_json=True, usage=usage, run_dir=run_dir))

    by_number: Dict[int, Dict[str, Any]] = {}
    for entry in _entries(prompt, parsed, "steps"):
        if not isinstance(entry, dict):
            raise ModelOutputError(
                f"{prompt}: every 'steps' entry must be an object, "
                f"got {entry!r}")
        number = entry.get("number")
        if isinstance(number, bool) or not isinstance(number, int) \
                or not 1 <= number <= n_steps:
            raise ModelOutputError(
                f"{prompt}: 'number' must be an integer in 1..{n_steps}, "
                f"got {number!r}")
        if number in by_number:
            raise ModelOutputError(
                f"{prompt}: step {number} appears twice")
        status = entry.get("status")
        if status not in ADHERENCE_STATUSES:
            raise ModelOutputError(
                f"{prompt}: 'status' of step {number} must be one of "
                f"{ADHERENCE_STATUSES}, got {status!r}")
        evidence = entry.get("evidence")
        if not isinstance(evidence, str):
            raise ModelOutputError(
                f"{prompt}: 'evidence' of step {number} must be a string, "
                f"got {evidence!r}")
        by_number[number] = {
            "number": number, "status": status,
            "evidence": " ".join(evidence.split())}
    missing = [number for number in range(1, n_steps + 1)
               if number not in by_number]
    if missing:
        raise ModelOutputError(
            f"{prompt}: the reply leaves out step(s) {missing}")
    return [by_number[number] for number in range(1, n_steps + 1)]
