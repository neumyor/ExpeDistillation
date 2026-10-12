"""G7 similarity finding, G8 merging, G9 grouping (layer 4).

Curation maintains the bank without re-judging correctness (README stage 5).
The blocks are pure model-call plumbing per AGENTS.md block rules: assemble
the template variables from the Skill objects, call the model through
``llm.call.call_model`` with a named prompt, and validate the declared
output loudly -- an id that is not in the input or a group the prompt forbids
is a ModelOutputError, never a silently dropped entry. All bookkeeping (IDs,
statuses, exams, disk) belongs to the composite (K5).

D10 (bank token count) is a pure function and lives in ``expedistill.inject``
(``count_bank_tokens``); K5 imports it from there, so it is not duplicated
here.
"""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from expedistill.core.models import Skill
from expedistill.infra.reliability import ModelOutputError
from expedistill.llm.call import ModelHandle, call_model

__all__ = ["assign_groups", "find_similar", "merge_skills"]


def _listing(skills: List[Skill]) -> str:
    """Render the ``{skills_listing}`` variable shared by G7 and G9.

    One line per skill (``- <skill_id>: <content>``). The prompts describe a
    one-entry-per-line listing, so a multi-line skill content would break the
    layout: whitespace runs (newlines included) are collapsed to single
    spaces. The full content is NOT collapsed for G8, whose ``{skills_block}``
    is a headed multi-line block.
    """
    return "\n".join(
        "- {}: {}".format(skill.skill_id, " ".join(skill.content.split()))
        for skill in skills
    )


_STAGE3_ID_SUFFIX = "/skill"


def _normalize_ids(entry: List[str], known: Set[str]) -> Optional[str]:
    """Deterministic recovery for the mixed-format listing failure mode.

    Round-1+ G7/G9 listings mix stage-3 skill ids (``traj/.../segNN/skill``)
    with merge ids (``bank/merge/NNNN``, no suffix); models then tend to
    harmonize to the SHORTER style and drop the ``/skill`` suffix from every
    stage-3 id (observed on the TB v3 run: all 68 output ids suffix-free,
    2026-10-12). An unknown id that matches exactly one known id after
    appending the stage-3 suffix is that id -- the model's grouping intent
    is recovered, not guessed. Returns the id of the first unrecoverable
    entry (for the error message), or None when the whole entry normalizes.
    """
    for position, item in enumerate(entry):
        if item in known:
            continue
        candidate = item + _STAGE3_ID_SUFFIX
        if candidate in known:
            entry[position] = candidate
            continue
        return item
    return None


def _validate_id_entries(
        value: Any, known: Set[str], field: str,
        size_ok: Callable[[int], bool], size_message: str) -> List[List[str]]:
    """Validate one SIMILARITY output field: a list of skill-id lists.

    Every entry must be a list of strings drawn from ``known`` and satisfy
    ``size_ok``. Violations raise ModelOutputError naming the offending
    entry: model output validation is loud (AGENTS.md block rules), the
    composite's lenient re-resolution (dropping stale ids) happens later,
    on our verified output.
    """
    if not isinstance(value, list):
        raise ModelOutputError(
            f"SIMILARITY: field {field!r} must be a list of skill-id lists, "
            f"got {type(value).__name__}")
    for entry in value:
        if (not isinstance(entry, list) or not entry
                or not all(isinstance(item, str) for item in entry)):
            raise ModelOutputError(
                f"SIMILARITY: {field} entry must be a non-empty list of "
                f"skill ids, got {entry!r}")
        unknown = sorted(item for item in entry if item not in known)
        if unknown:
            # The mixed-format recovery runs per entry before the hard error.
            unrecoverable = _normalize_ids(entry, known)
            if unrecoverable is not None:
                raise ModelOutputError(
                    f"SIMILARITY: {field} entry {entry!r} references skill ids "
                    f"that are not in the input: {[unrecoverable]!r}")
        if not size_ok(len(entry)):
            raise ModelOutputError(
                f"SIMILARITY: {field} entry {entry!r} is invalid: "
                f"{size_message}")
    return value


def find_similar(
    skills: List[Skill],
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Tuple[List[List[str]], List[List[str]]]:
    """Find near-duplicate groups and conflicting pairs (G7).

    Returns ``(similar_groups, conflicts)`` verbatim: lists of skill-id
    lists, exactly as the model listed them. An empty input needs no model
    call and returns ``([], [])``.

    Output contract (SIMILARITY prompt): every group has at least two known
    skill ids; every conflict is a two-element list of known skill ids.
    """
    if not skills:
        return [], []

    parsed = call_model(
        handle, "SIMILARITY", {"skills_listing": _listing(skills)},
        output_json=True, usage=usage, run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"SIMILARITY: expected a JSON object, "
            f"got {type(parsed).__name__}")
    for field in ("similar_groups", "conflicts"):
        if field not in parsed:
            raise ModelOutputError(
                f"SIMILARITY: field {field!r} is missing from the response")

    known = {skill.skill_id for skill in skills}
    similar_groups = _validate_id_entries(
        parsed["similar_groups"], known, "similar_groups",
        lambda size: size >= 2,
        "every group needs at least two skill ids")
    conflicts = _validate_id_entries(
        parsed["conflicts"], known, "conflicts",
        lambda size: size == 2,
        "every conflict is a two-element list [skill_id, skill_id]")
    return similar_groups, conflicts


def merge_skills(
    sources: List[Skill],
    handle: ModelHandle,
    *,
    mode: str,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> str:
    """Merge one group (or one conflicting pair) of skills into one text (G8).

    ``mode`` picks the merge strategy (MERGE prompt): "dedup" for
    near-duplicates, "conflict" for opposite advice. Anything else is a
    caller bug and raises ValueError before any model call; so does a source
    list with fewer than two skills, which has nothing to merge.
    """
    if mode not in ("dedup", "conflict"):
        raise ValueError(f'mode must be "dedup" or "conflict", got {mode!r}')
    if len(sources) < 2:
        raise ValueError(
            f"merge_skills needs at least two source skills, got {len(sources)}")

    skills_block = "\n\n".join(
        f"### {skill.skill_id}\n{skill.content}" for skill in sources)
    merged = call_model(
        handle, "MERGE",
        {"mode": mode, "skills_block": skills_block},
        usage=usage, run_dir=run_dir,
    )
    return merged.strip()


def assign_groups(
    skills: List[Skill],
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, str]:
    """Assign every skill one capability group name (G9).

    Returns ``{skill_id: group_name}`` with the names stripped. An empty
    input needs no model call and returns ``{}``. Output contract (GROUP
    prompt): every input skill id appears exactly once, mapped to a
    non-empty string; missing, unknown or empty mappings raise
    ModelOutputError listing the offenders.
    """
    if not skills:
        return {}

    parsed = call_model(
        handle, "GROUP", {"skills_listing": _listing(skills)},
        output_json=True, usage=usage, run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"GROUP: expected a JSON object, got {type(parsed).__name__}")
    groups = parsed.get("groups")
    if not isinstance(groups, dict):
        raise ModelOutputError(
            "GROUP: field 'groups' must be an object mapping skill_id to "
            f"group name, got {groups!r}")

    expected = {skill.skill_id for skill in skills}
    missing = sorted(expected - set(groups))
    unknown = sorted(set(groups) - expected)
    problems = []
    if missing:
        problems.append(f"missing skill ids {missing}")
    if unknown:
        problems.append(f"unknown skill ids {unknown}")
    for skill_id, name in groups.items():
        if not isinstance(name, str) or not name.strip():
            problems.append(
                f"skill {skill_id!r} must map to a non-empty string, "
                f"got {name!r}")
    if problems:
        raise ModelOutputError(
            "GROUP: invalid grouping: " + "; ".join(problems))

    return {skill_id: name.strip() for skill_id, name in groups.items()}
