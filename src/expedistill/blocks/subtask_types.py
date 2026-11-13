"""G2: sort segments into the run's subtask type table (layer 4).

* :func:`segment_card` -- one segment's compact card (identity, label,
  description, outcome, a clipped steps view) for the typing and the card
  writing prompts;
* :func:`render_type_table` -- the existing table as the prompt shows it;
* :func:`type_segments` -- one ``SUBTASK_TYPING`` call over one chunk of
  segments, validated and turned into new ``SubtaskType`` rows plus one
  type id per segment.

The reply names types by the table's ids or by keys it declares itself;
the program assigns the new types their ``type/NNN`` ids, in the order the
segments (sorted by subtask id) first use them, so the numbering does not
depend on the order of the reply. A reply that leaves a segment out,
repeats or invents one, or names a type that is neither in the table nor
declared is a ModelOutputError. Nothing is written to disk here.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from expedistill.core.ids import make_type_id, parse_type_id
from expedistill.core.models import Subtask, SubtaskType
from expedistill.infra.reliability import ModelOutputError
from expedistill.llm.call import call_model

__all__ = [
    "TypingResult",
    "parse_typing_reply",
    "render_type_table",
    "segment_card",
    "type_segments",
]

_PROMPT = "SUBTASK_TYPING"
_ELIDED = "\n[... steps elided to fit the card budget ...]"


def segment_card(subtask: Subtask, steps_view: str, max_chars: int) -> str:
    """One segment's card; the steps view is clipped to ``max_chars``
    (head and tail kept, the middle marked)."""
    steps = str(steps_view or "")
    if len(steps) > max_chars:
        head = max(1, int(max_chars * 0.6))
        tail = max(0, max_chars - head - len(_ELIDED))
        steps = steps[:head] + _ELIDED + (steps[-tail:] if tail else "")
    lines = [
        f"SUBTASK_ID: {subtask.subtask_id}",
        f"TASK: {subtask.task_id}",
        f"SOURCE: {subtask.source}",
        f"SUBTASK_SUCCESS: {subtask.success}",
        f"LABEL: {subtask.label}",
        f"DESCRIPTION: {subtask.description}",
    ]
    context = (subtask.context_summary or "").strip()
    if context:
        lines.append(f"CONTEXT_BEFORE_SEGMENT: {context}")
    lines.append("STEPS:")
    lines.append(steps)
    return "\n".join(lines)


def render_type_table(table: Sequence[SubtaskType]) -> str:
    """``type_id | name | entry state`` per row; "(none)" when empty."""
    if not table:
        return "(none)"
    return "\n".join(f"{row.type_id} | {row.name} | {row.entry_state}"
                     for row in table)


@dataclass(frozen=True)
class TypingResult:
    """One chunk's outcome: the new table rows (ids assigned) and the type
    id of every segment of the chunk."""

    new_types: List[SubtaskType]
    assignments: Dict[str, str]


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelOutputError(f"{_PROMPT}: {where} must be a non-empty "
                               f"string, got {value!r}")
    return " ".join(value.split())


def parse_typing_reply(parsed: Any, *, expected: Sequence[str],
                       table: Sequence[SubtaskType],
                       phase_label: str) -> TypingResult:
    """Validate one reply against the chunk's segment ids and the table;
    assign the new types their ids after the table's last serial."""
    if not isinstance(parsed, dict) \
            or not isinstance(parsed.get("assignments"), list) \
            or not isinstance(parsed.get("new_types", []), list):
        raise ModelOutputError(
            f"{_PROMPT}: expected a JSON object with an 'assignments' list "
            "and a 'new_types' list")
    declared: Dict[str, Tuple[str, str]] = {}
    for index, item in enumerate(parsed.get("new_types") or []):
        if not isinstance(item, dict):
            raise ModelOutputError(
                f"{_PROMPT}: new_types[{index}] is not an object")
        key = _text(item.get("key"), f"new_types[{index}].key")
        if key in declared:
            raise ModelOutputError(
                f"{_PROMPT}: new type key {key!r} is declared twice")
        declared[key] = (_text(item.get("name"), f"new type {key!r} name"),
                         _text(item.get("entry_state"),
                               f"new type {key!r} entry_state"))
    known = {row.type_id for row in table}
    clash = sorted(set(declared) & known)
    if clash:
        raise ModelOutputError(
            f"{_PROMPT}: new type key(s) {clash} reuse existing type ids")
    wanted = set(expected)
    refs: Dict[str, str] = {}
    for index, item in enumerate(parsed["assignments"]):
        if not isinstance(item, dict):
            raise ModelOutputError(
                f"{_PROMPT}: assignments[{index}] is not an object")
        subtask_id = item.get("subtask_id")
        if not isinstance(subtask_id, str) or subtask_id not in wanted:
            raise ModelOutputError(
                f"{_PROMPT}: assignments[{index}] names subtask "
                f"{subtask_id!r}, which is not one of the segments to sort")
        if subtask_id in refs:
            raise ModelOutputError(
                f"{_PROMPT}: subtask {subtask_id!r} is assigned twice")
        ref = item.get("type")
        if not isinstance(ref, str) or (ref not in known
                                        and ref not in declared):
            raise ModelOutputError(
                f"{_PROMPT}: subtask {subtask_id!r} names type {ref!r}, "
                "which is neither in the table nor declared as a new type")
        refs[subtask_id] = ref
    missing = sorted(wanted - set(refs))
    if missing:
        raise ModelOutputError(
            f"{_PROMPT}: segment(s) left unassigned: {missing[:10]}")

    serial = max((parse_type_id(row.type_id) for row in table), default=0)
    ids_of: Dict[str, str] = {}
    new_types: List[SubtaskType] = []
    assignments: Dict[str, str] = {}
    for subtask_id in sorted(refs):
        ref = refs[subtask_id]
        if ref in declared and ref not in ids_of:
            serial += 1
            ids_of[ref] = make_type_id(serial)
            name, entry_state = declared[ref]
            new_types.append(SubtaskType(
                type_id=ids_of[ref], name=name, entry_state=entry_state,
                phase_label=phase_label))
        assignments[subtask_id] = ids_of.get(ref, ref)
    return TypingResult(new_types=new_types, assignments=assignments)


def type_segments(cards: Mapping[str, str], table: Sequence[SubtaskType], *,
                  phase_label: str, handle: Any, correction_note: str = "",
                  usage: Any = None, run_dir: Any = None) -> TypingResult:
    """Type one chunk: ``cards`` maps each segment id to its card (sent in
    subtask-id order); ``table`` is the whole current type table."""
    expected = sorted(cards)
    parsed = call_model(
        handle, _PROMPT,
        {"type_table": render_type_table(table),
         "cards": "\n\n".join(cards[subtask_id] for subtask_id in expected),
         "correction_note": correction_note},
        output_json=True, usage=usage, run_dir=run_dir)
    return parse_typing_reply(parsed, expected=expected, table=table,
                              phase_label=phase_label)
