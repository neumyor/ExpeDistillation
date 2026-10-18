"""K5's per-round unit planning: pure functions over a round's journaled
G7 output, no store and no IO.

A round's units are its dedup groups then its conflicts, each sorted by
member ids (``_round_units``); ``_partition_waves`` assigns every unit
the earliest wave in which it commits after every earlier unit it
overlaps with (module docstring of ``composites.curation``); duplicate
units are decided at dispatch and never run; ``_build_dispatch`` hands
out the merge serials in unit order. ``composites.curation`` imports
every name it uses from here.
"""

from typing import Any, Dict, List, Sequence, Set, Tuple


def _ordered_groups(groups: Any) -> List[Any]:
    """Deterministic processing order for one family of G7 groups.

    Sorts by the group's member ids so the merge serials (hence merged-skill
    IDs) do not depend on the model's listing order. Malformed entries sort
    first and are recorded as skipped by ``_resolve_group``; a non-list
    ``groups`` value (a broken G7 contract that escaped the block) is
    treated as no groups rather than crashing the curation.
    """
    if not isinstance(groups, (list, tuple)):
        return []

    def key(group: Any) -> Tuple[str, ...]:
        if isinstance(group, (list, tuple)):
            return tuple(sorted(str(item) for item in group))
        return ()

    return sorted(groups, key=key)


def _round_units(round_record: Dict[str, Any]) -> List[Tuple[str, Any]]:
    """The round's merge units in processing order: (mode, group)."""
    units = []
    for mode, field in (("dedup", "similar_groups"),
                        ("conflict", "conflicts")):
        for group in _ordered_groups(round_record.get(field)):
            units.append((mode, group))
    return units


def _display_sources(group: Any) -> List[str]:
    """A group rendered for the journal (skip records, dispatch entries)."""
    if isinstance(group, (list, tuple)):
        return [str(item) for item in group]
    return [repr(group)]


def _unit_member_ids(group: Any) -> Set[str]:
    """The member ids one unit's group names (its wave-conflict set)."""
    return set(_display_sources(group))


def _partition_waves(units: Sequence[Tuple[str, Any]]) -> List[List[int]]:
    """Greedy wave assignment that preserves serial first-wins.

    Unit j joins the earliest wave that commits it after every EARLIER unit
    it shares a member with (0 when it overlaps nothing earlier); waves are
    filled in unit order, so a wave only ever holds member-disjoint units
    and overlapping pairs keep their serial order. Pure function of the
    round's units (hence of the journaled G7 output); deterministic. See
    the module docstring for why the unconstrained "earliest wave with no
    shared member" rule is not enough.
    """
    member_sets = [_unit_member_ids(group) for _, group in units]
    waves: List[List[int]] = []
    wave_of: List[int] = []
    for index, member_set in enumerate(member_sets):
        target = 0
        for prior in range(index):
            if member_set & member_sets[prior]:
                target = max(target, wave_of[prior] + 1)
        while len(waves) <= target:
            waves.append([])
        waves[target].append(index)
        wave_of.append(target)
    return waves


def _dedup_key(mode: str, group: Any) -> Tuple[str, Any]:
    """Duplicate units = same mode and same source set (module docstring)."""
    if isinstance(group, (list, tuple)):
        return (mode, frozenset(str(item) for item in group))
    return (mode, repr(group))


def _duplicate_units(units: Sequence[Tuple[str, Any]],
                     ) -> List[Tuple[int, int]]:
    """(unit index, first unit with the same (mode, source set)) pairs.

    Pure function of the round's units (the block's validation allows G7 to
    emit one pair twice); duplicates never run (module docstring).
    """
    first_of: Dict[Tuple[str, Any], int] = {}
    duplicates: List[Tuple[int, int]] = []
    for unit_index, (mode, group) in enumerate(units):
        key = _dedup_key(mode, group)
        if key in first_of:
            duplicates.append((unit_index, first_of[key]))
        else:
            first_of[key] = unit_index
    return duplicates


def _duplicate_record(round_index: int, unit_index: int, first_index: int,
                      mode: str, group: Any) -> Dict[str, Any]:
    return {
        "round": round_index, "unit": unit_index, "mode": mode,
        "serial": None, "sources": _display_sources(group),
        "skill_id": None, "outcome": "skipped",
        "reason": f"duplicate source set of unit {first_index:03d}",
    }


def _build_dispatch(units: Sequence[Tuple[str, Any]], next_serial: int,
                    ) -> Tuple[List[Dict[str, Any]], int]:
    """The round's dispatch plan: serials for every unit, in unit order.

    Returns (dispatch entries, the next unallocated serial). Entries carry
    the unit index, mode, raw source ids and the allocated serial (None for
    duplicates, which never run). Pure function of the units and the serial
    continuation, so a crash between the G7 journal write and this write
    re-derives the same map.
    """
    dispatch: List[Dict[str, Any]] = []
    duplicates = {unit for unit, _ in _duplicate_units(units)}
    for unit_index, (mode, group) in enumerate(units):
        dispatch.append({
            "unit": unit_index, "mode": mode,
            "sources": _display_sources(group),
            "serial": None if unit_index in duplicates else next_serial,
        })
        if unit_index not in duplicates:
            next_serial += 1
    return dispatch, next_serial
