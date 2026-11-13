"""G3: write one card for one subtask type (``CARD_WRITE``).

The card's identity is the type's: ``skill_id = make_skill_id(type_id)``,
version 1, status ``active``, ``subtask_type`` the type id and
``source_subtasks`` every material segment. A reply whose fields fail the
checks (the four fields of ``blocks.author``, 3 to 7 steps) is sent back
with the problem named, at most ``write_retry_limit`` times, then a
ModelOutputError. The model call lives here; persistence belongs to the
learn phase.
"""

from typing import Any

from expedistill import prompts
from expedistill.blocks.author import parse_skill_fields
from expedistill.blocks.subtask_types import segment_card
from expedistill.core.ids import make_skill_id
from expedistill.core.models import Skill, SubtaskType
from expedistill.infra.reliability import CodeBugError, ModelOutputError
from expedistill.llm.call import call_model
from expedistill.methods.cards import CardAuthoringParams
from expedistill.methods.cards.material import Material
from expedistill.prompts.profile import profile_section

__all__ = ["MAX_STEPS", "MIN_STEPS", "write_card"]

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


def write_card(subtask_type: SubtaskType, material: Material, *,
               profile: str, playbook: str, handle: Any,
               params: CardAuthoringParams, usage: Any = None,
               run_dir: Any = None) -> Skill:
    """ONE card for ``subtask_type`` (model calls only, no persistence)."""
    if not material.strong:
        raise CodeBugError(
            f"type {subtask_type.type_id!r} has no strong material; the "
            "caller receipts it skipped_no_material instead")
    variables = {
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
    problem = "gave no usable reply"
    for _ in range(params.write_retry_limit + 1):
        try:
            fields = _fields(call_model(handle, _PROMPT, variables,
                                        output_json=True, usage=usage,
                                        run_dir=run_dir))
        except ModelOutputError as exc:
            problem = str(exc)
            variables["correction_note"] = prompts.get(
                "CARDS_CORRECTION").format(problem=problem)
            continue
        return Skill(
            skill_id=make_skill_id(subtask_type.type_id),
            subtask_type=subtask_type.type_id,
            source_subtasks=sorted(
                item["subtask"].subtask_id
                for item in material.strong + material.weak),
            version=1, status="active", rewrite_history=[], **fields)
    raise ModelOutputError(
        f"{_PROMPT}: no usable card for {subtask_type.type_id!r} after "
        f"{params.write_retry_limit} rewrite(s): {problem}")
