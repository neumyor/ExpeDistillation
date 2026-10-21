"""G7 similarity finding, G8 merging, G9 grouping (layer 4).

Curation maintains the bank without re-judging correctness (README stage 5).
The blocks are pure model-call plumbing per AGENTS.md block rules: assemble
the template variables from the Skill objects, call the model through
``llm.call.call_model`` with a named prompt, and validate the declared
output loudly -- a malformed shape or a group the prompt forbids is a
ModelOutputError. An id that is not in the input is dropped with a stderr
note: at large bank sizes the model fabricates near-miss ids, and failing
the whole call over one of them stalls curation permanently (see
``_filter_unknown``). All bookkeeping (IDs, statuses, exams, disk) belongs
to the composite (K5).

D10 (bank token count) is a pure function and lives in ``expedistill.inject``
(``count_bank_tokens``); K5 imports it from there, so it is not duplicated
here.
"""

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from expedistill.core.models import SKILL_SUMMARY_MAX_CHARS, Skill
from expedistill.infra.reliability import ConfigError, ModelOutputError
from expedistill.llm.call import ModelHandle, call_model

__all__ = ["SIMILARITY_INPUTS", "assign_groups", "find_similar",
           "merge_skills"]

#: What G7 sees of each skill (the curation's ``similarity_input``
#: switch): ``full`` -- the content collapsed to one line (the frozen
#: behaviour); ``summary`` -- the one-line summary only.
SIMILARITY_INPUTS = ("full", "summary")


def _listing(skills: List[Skill], similarity_input: str = "full") -> str:
    """Render the ``{skills_listing}`` variable shared by G7 and G9.

    One line per skill (``- <skill_id>: <content>``). The prompts describe a
    one-entry-per-line listing, so a multi-line skill content would break the
    layout: whitespace runs (newlines included) are collapsed to single
    spaces. The full content is NOT collapsed for G8, whose ``{skills_block}``
    is a headed multi-line block.

    ``similarity_input="summary"`` (G7 only) lists ``- <skill_id>:
    <summary>`` instead. A skill without a summary is a ConfigError raised
    before any model call -- never a silent fall back to the content (the
    2026-10-19 ruling: every skill carries a summary; an old bank gets its
    summaries filled in before it is curated this way).
    """
    if similarity_input not in SIMILARITY_INPUTS:
        raise ValueError(
            f"similarity_input must be one of {SIMILARITY_INPUTS}, got "
            f"{similarity_input!r}")
    if similarity_input == "summary":
        missing = sorted(skill.skill_id for skill in skills
                         if not (skill.summary or "").strip())
        if missing:
            raise ConfigError(
                f"similarity_input 'summary' needs every skill to carry a "
                f"summary; skills without one: {missing}")
        return "\n".join(
            "- {}: {}".format(skill.skill_id, " ".join(skill.summary.split()))
            for skill in skills
        )
    return "\n".join(
        "- {}: {}".format(skill.skill_id, " ".join(skill.content.split()))
        for skill in skills
    )


_STAGE3_ID_SUFFIX = "/skill"


def _filter_unknown(entry: List[str],
                    known: Set[str]) -> Tuple[List[str], List[str]]:
    """(kept known ids, dropped unknown ids) for one model-listed entry.

    Two recoveries, both deterministic: an unknown id that matches exactly
    one known id after appending the stage-3 suffix is that id (the
    mixed-format listing failure mode observed on the TB v3 run, all 68
    suffix-free ids, 2026-10-12); any other unknown id is dropped. The second
    recovery exists for the large-bank failure mode (SearchQA, 2026-10-21):
    with hundreds of long ids in the listing the model invents a near-miss
    id (``.../strong/00/...`` for a listed ``.../strong/01/...``), and
    rejecting the whole call over one fabricated id stalls curation forever.
    The dropped ids are printed to stderr so the loss is visible.
    """
    kept: List[str] = []
    dropped: List[str] = []
    for item in entry:
        if item in known:
            kept.append(item)
            continue
        candidate = item + _STAGE3_ID_SUFFIX
        if candidate in known:
            kept.append(candidate)
        else:
            dropped.append(item)
    return kept, dropped


def _validate_id_entries(
        value: Any, known: Set[str], field: str,
        size_ok: Callable[[int], bool], size_message: str) -> List[List[str]]:
    """Validate one SIMILARITY output field: a list of skill-id lists.

    Unknown ids are dropped per entry with a stderr note (the large-bank
    hallucination recovery in :func:`_filter_unknown`). A malformed entry or
    an entry that misses ``size_ok`` while carrying only known ids raises
    ModelOutputError; an entry whose dropped ids pushed it below the size
    floor is skipped, because the model's usable intent no longer exists.
    """
    if not isinstance(value, list):
        raise ModelOutputError(
            f"SIMILARITY: field {field!r} must be a list of skill-id lists, "
            f"got {type(value).__name__}")
    kept_entries: List[List[str]] = []
    for entry in value:
        if (not isinstance(entry, list) or not entry
                or not all(isinstance(item, str) for item in entry)):
            raise ModelOutputError(
                f"SIMILARITY: {field} entry must be a non-empty list of "
                f"skill ids, got {entry!r}")
        kept, dropped = _filter_unknown(entry, known)
        if dropped:
            print(f"SIMILARITY: dropped unknown skill ids {sorted(dropped)} "
                  f"from field {field!r}", file=sys.stderr)
        if not size_ok(len(kept)):
            if dropped:
                # A fabricated id cost the entry its usable members; skip
                # it instead of failing the whole call.
                continue
            raise ModelOutputError(
                f"SIMILARITY: {field} entry {entry!r} is invalid: "
                f"{size_message}")
        kept_entries.append(kept)
    return kept_entries


def _validate_low_value(value: Any, known: Set[str]) -> List[str]:
    """Validate the SIMILARITY ``low_value`` field: a flat list of known
    skill ids (the same ``/skill`` suffix recovery and unknown-id drop as
    the group fields, with a stderr note). A repeated id collapses to its
    first position."""
    if not isinstance(value, list) or \
            not all(isinstance(item, str) for item in value):
        raise ModelOutputError(
            "SIMILARITY: field 'low_value' must be a list of skill ids, "
            f"got {value!r}")
    kept, dropped = _filter_unknown(list(value), known)
    if dropped:
        print(f"SIMILARITY: dropped unknown low_value skill ids "
              f"{sorted(dropped)}", file=sys.stderr)
    return list(dict.fromkeys(kept))


def find_similar(
    skills: List[Skill],
    handle: ModelHandle,
    *,
    similarity_input: str = "full",
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Tuple[List[List[str]], List[List[str]], List[str]]:
    """Find near-duplicate groups, conflicting pairs and low-value skills
    (G7).

    Returns ``(similar_groups, conflicts, low_value)``: the two group
    fields as the model listed them, ``low_value`` a list of skill ids
    (first-occurrence order, repeats collapsed). An empty input needs no
    model call and returns ``([], [], [])``. ``similarity_input`` picks
    what the model sees of each skill (``_listing``).

    Output contract (SIMILARITY prompt): every group has at least two known
    skill ids; every conflict is a two-element list of known skill ids;
    every low_value entry is a known skill id. Whether the low-value skills
    are dropped is the composite's switch, not this block's.
    """
    if not skills:
        return [], [], []

    parsed = call_model(
        handle, "SIMILARITY",
        {"skills_listing": _listing(skills, similarity_input)},
        output_json=True, usage=usage, run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"SIMILARITY: expected a JSON object, "
            f"got {type(parsed).__name__}")
    for field in ("similar_groups", "conflicts", "low_value"):
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
    low_value = _validate_low_value(parsed["low_value"], known)
    return similar_groups, conflicts, low_value


def _validate_merge_summary(value: Any) -> str:
    """The G8 mirror of blocks/author.py's summary rule (the same-layer
    rule forbids the import): a non-empty string, ONE line, at most
    SKILL_SUMMARY_MAX_CHARS; returns the stripped, newline-normalized
    line the composite stores in ``Skill.summary``."""
    if not isinstance(value, str):
        raise ModelOutputError(
            f"MERGE: field 'summary' must be a string, got {value!r}")
    summary = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not summary:
        raise ModelOutputError(
            f"MERGE: field 'summary' must be a non-empty string, "
            f"got {value!r}")
    if "\n" in summary:
        raise ModelOutputError(
            "MERGE: field 'summary' must be ONE line (the skill's "
            f"directory entry); got a value with an embedded newline: "
            f"{value!r}")
    if len(summary) > SKILL_SUMMARY_MAX_CHARS:
        raise ModelOutputError(
            f"MERGE: field 'summary' must be at most "
            f"{SKILL_SUMMARY_MAX_CHARS} characters, got {len(summary)}")
    return summary


def merge_skills(
    sources: List[Skill],
    handle: ModelHandle,
    *,
    mode: str,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Tuple[str, str]:
    """Merge one group (or one conflicting pair) of skills into one skill
    (G8); returns ``(content, summary)``.

    ``mode`` picks the merge strategy (MERGE prompt): "dedup" for
    near-duplicates, "conflict" for opposite advice. Anything else is a
    caller bug and raises ValueError before any model call; so does a source
    list with fewer than two skills, which has nothing to merge.

    Output contract (MERGE prompt): a JSON object with a non-empty string
    ``content`` (stripped) and a ``summary`` held to the one-line rule.
    Only the format is checked; how general the merged skill is stays the
    model's job (docs/decisions/curation_generalize_20261019.md). The
    section-marker post-check is the composite's.
    """
    if mode not in ("dedup", "conflict"):
        raise ValueError(f'mode must be "dedup" or "conflict", got {mode!r}')
    if len(sources) < 2:
        raise ValueError(
            f"merge_skills needs at least two source skills, got {len(sources)}")

    skills_block = "\n\n".join(
        f"### {skill.skill_id}\n{skill.content}" for skill in sources)
    parsed = call_model(
        handle, "MERGE",
        {"mode": mode, "skills_block": skills_block},
        output_json=True, usage=usage, run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"MERGE: expected a JSON object, got {type(parsed).__name__}")
    for field in ("content", "summary"):
        if field not in parsed:
            raise ModelOutputError(
                f"MERGE: field {field!r} is missing from the response")
    content = parsed["content"]
    if not isinstance(content, str) or not content.strip():
        raise ModelOutputError(
            f"MERGE: field 'content' must be a non-empty string, "
            f"got {content!r}")
    return content.strip(), _validate_merge_summary(parsed["summary"])


def assign_groups(
    skills: List[Skill],
    handle: ModelHandle,
    *,
    grouping_input: str = "full",
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, str]:
    """Assign every skill one capability group name (G9).

    Returns ``{skill_id: group_name}`` with the names stripped. An empty
    input needs no model call and returns ``{}``. Output contract (GROUP
    prompt): every input skill id appears exactly once, mapped to a
    non-empty string; missing, unknown or empty mappings raise
    ModelOutputError listing the offenders.

    ``grouping_input`` picks what G9 sees of each skill: ``full``
    (default, the content) or ``summary`` (the one-line summary; a skill
    without one is refused before the model call). It is independent of
    G7's ``similarity_input``.
    """
    if grouping_input not in SIMILARITY_INPUTS:
        raise ConfigError(
            "GROUP: grouping_input must be one of "
            f"{list(SIMILARITY_INPUTS)}, got {grouping_input!r}")
    if not skills:
        return {}

    parsed = call_model(
        handle, "GROUP",
        {"skills_listing": _listing(skills, grouping_input)},
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
