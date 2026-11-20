"""G3: write one card for one subtask type (``CARD_WRITE``).

The card's identity is the type's: ``skill_id = make_skill_id(type_id)``,
version 1, status ``active``, ``subtask_type`` the type id and
``source_subtasks`` every material segment. A reply whose fields fail the
checks (the four fields of ``blocks.author``, 3 to 7 steps) is sent back
with the problem named, at most ``write_retry_limit`` times, then a
ModelOutputError. The model call lives here; persistence belongs to the
learn phase.

``write_card_prefix`` is the prefix-authoring variant: the same prompt asked
twice, the content side kept from the first reply and the trigger side from
the second, whose segments show the steps before each segment instead of the
segment's own steps.
"""

from typing import Any, Tuple

from expedistill import prompts
from expedistill.blocks.author import parse_skill_fields
from expedistill.blocks.subtask_types import segment_card
from expedistill.core.ids import make_skill_id
from expedistill.core.models import TRIGGER_FIELDS, Skill, SubtaskType
from expedistill.infra.reliability import CodeBugError, ModelOutputError
from expedistill.llm.call import call_model
from expedistill.methods.cards import CardAuthoringParams
from expedistill.methods.cards.material import Material
from expedistill.prompts.profile import profile_section

__all__ = ["MAX_STEPS", "MIN_STEPS", "write_card", "write_card_prefix"]

#: A card teaches one subtask: a short procedure.
MIN_STEPS = 3
MAX_STEPS = 7

_PROMPT = "CARD_WRITE"


def _cards(experiences, max_chars: int) -> str:
    return "\n\n".join(
        segment_card(item["subtask"], item["steps_view"], max_chars)
        for item in experiences) or "(none)"


def _fields(parsed: Any) -> dict:
    fields = parse_skill_fields(_PROMPT, parsed)
    if not MIN_STEPS <= len(fields["steps"]) <= MAX_STEPS:
        raise ModelOutputError(
            f"{_PROMPT}: the card has {len(fields['steps'])} steps; it "
            f"needs {MIN_STEPS} to {MAX_STEPS}")
    return fields


def _variables(subtask_type: SubtaskType, material: Material, *,
               profile: str, playbook: str, handle: Any,
               params: CardAuthoringParams) -> dict:
    return {
        "type_name": subtask_type.name,
        "entry_state": subtask_type.entry_state,
        "strong_segments": _cards(material.strong,
                                  params.max_chars_per_segment),
        "weak_segments": _cards(material.weak,
                                params.max_chars_per_segment),
        "playbook": playbook.strip() or "(none)",
        "profile_section": profile_section(
            "skill_reader", profile,
            handle.prompt_defaults.get("agent_kind")),
        "correction_note": "",
    }


def _ask(variables: dict, type_id: str, *, handle: Any,
         params: CardAuthoringParams, usage: Any, run_dir: Any) -> dict:
    """One ``CARD_WRITE`` call whose reply passes the checks; an unusable
    reply is sent back with the problem named."""
    problem = "gave no usable reply"
    for _ in range(params.write_retry_limit + 1):
        try:
            return _fields(call_model(handle, _PROMPT, variables,
                                      output_json=True, usage=usage,
                                      run_dir=run_dir))
        except ModelOutputError as exc:
            problem = str(exc)
            variables["correction_note"] = prompts.get(
                "CARDS_CORRECTION").format(problem=problem)
    raise ModelOutputError(
        f"{_PROMPT}: no usable card for {type_id!r} after "
        f"{params.write_retry_limit} rewrite(s): {problem}")


def _skill(subtask_type: SubtaskType, material: Material,
           fields: dict) -> Skill:
    return Skill(
        skill_id=make_skill_id(subtask_type.type_id),
        subtask_type=subtask_type.type_id,
        source_subtasks=sorted(
            item["subtask"].subtask_id
            for item in material.strong + material.weak),
        version=1, status="active", rewrite_history=[], **fields)


def _require_strong(subtask_type: SubtaskType, material: Material) -> None:
    if not material.strong:
        raise CodeBugError(
            f"type {subtask_type.type_id!r} has no strong material; the "
            "caller receipts it skipped_no_material instead")


def write_card(subtask_type: SubtaskType, material: Material, *,
               profile: str, playbook: str, handle: Any,
               params: CardAuthoringParams, usage: Any = None,
               run_dir: Any = None) -> Skill:
    """ONE card for ``subtask_type`` (model calls only, no persistence)."""
    _require_strong(subtask_type, material)
    variables = _variables(subtask_type, material, profile=profile,
                           playbook=playbook, handle=handle, params=params)
    return _skill(subtask_type, material,
                  _ask(variables, subtask_type.type_id, handle=handle,
                       params=params, usage=usage, run_dir=run_dir))


def write_card_prefix(subtask_type: SubtaskType, material: Material,
                      prefix: Material, *, profile: str, playbook: str,
                      handle: Any, params: CardAuthoringParams,
                      usage: Any = None,
                      run_dir: Any = None) -> Tuple[Skill, bool]:
    """The prefix-authoring card: the content side (``steps``,
    ``not_applicable_when``) from the usual call over ``material``, the
    trigger side (``summary``, ``when_to_use``) from a second call over
    ``prefix`` (the same segments seen from the steps before them).

    Returns ``(card, True)``. When ``prefix`` has no strong segment (every
    one starts at step 0) the second call is not made and the usual
    call's own trigger side is kept: ``(card, False)``."""
    _require_strong(subtask_type, material)
    kwargs = dict(profile=profile, playbook=playbook, handle=handle,
                  params=params)
    fields = _ask(_variables(subtask_type, material, **kwargs),
                  subtask_type.type_id, handle=handle, params=params,
                  usage=usage, run_dir=run_dir)
    if not prefix.strong:
        return _skill(subtask_type, material, fields), False
    seen = _ask(_variables(subtask_type, prefix, **kwargs),
                subtask_type.type_id, handle=handle, params=params,
                usage=usage, run_dir=run_dir)
    fields.update({name: seen[name] for name in TRIGGER_FIELDS})
    return _skill(subtask_type, material, fields), True
