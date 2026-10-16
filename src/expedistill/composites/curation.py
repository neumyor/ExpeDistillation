"""K5 bank curation (composite, layer 5): G7 -> G8 -> K3 -> G9 -> D10.

Owns the disk IO, resume and bookkeeping of stage 5 (README section 3): the
curator NEVER re-judges whether a skill is correct -- that was stage 4's job
-- it only maintains the bank: merge near-duplicates, combine conflicting
pairs, re-examine merges against every source skill's exam, assign
capability groups, and keep the injected bank under ``bank_token_budget``.

Parallel compute, ordered commit (the 2026-10-12 redesign)
---------------------------------------------------------
Five independent design reviews converged on this design on 2026-10-12; the
keep-both / batch-derivation variant (compute every unit against the
round-start members, keep both outcomes) was REJECTED. The core principle
that survived every review: **parallel COMPUTE, ordered COMMIT, bit-identical
output to serial** -- serial first-wins semantics are preserved exactly.
``workers=1`` therefore reproduces the old serial behaviour (the only
deliberate semantic changes are listed below).

*Wave partitioning.* Each round's units keep the existing deterministic
order (``_round_units``: dedup groups then conflicts, each
``_ordered_groups``-sorted). Units are grouped into waves by a greedy
assignment that sends each unit to the EARLIEST wave in which it commits
after every earlier unit it overlaps with:

    wave(j) = 0                if no unit i < j shares a group member,
              1 + max(wave(i)) over all i < j sharing a member with j.

This refines the review notes' "earliest wave with no shared member": that
rule alone does NOT preserve first-wins (units [a,b], [b,c], [c,d] would put
[c,d] in wave 0 next to [a,b], so after [a,b] reverts, [c,d] commits before
[b,c] and the bank keeps (c,d) where serial keeps (b,c)). The constrained
greedy still gives disjoint units maximum parallelism (they all land in
wave 0), is a pure function of the G7 output, and guarantees both wave
invariants: (1) units within a wave share no member; (2) overlapping units
commit in unit order, exactly as serial. Waves run sequentially; units
within a wave run in parallel via ``infra.parallel.run_units``.

*Equivalence argument.* A unit's outcome depends only on (a) which of its
own group ids are current members when it resolves and (b) model outputs.
Within a wave the units' group ids are pairwise disjoint, so their
resolved sources are disjoint current members -- and distinct current
members always carry pairwise-disjoint transitive original skills (a kept
merge is the only operation that re-assigns a member's originals, and it
consumes them; merged-away sources never return to "accepted"). Units in
one wave therefore read only shared immutable data (exams, other skills,
G7 output), write only their own merged skill (dispatch serials are unique)
and their own regression attempts (their exams are disjoint), so any
interleaving produces the same per-unit outcomes and the same files; the
main thread commits the wave's outcomes in unit-index order, fixing one
canonical interleaving. Across waves, an overlapping pair i < j commits in
wave(i) < wave(j) -- the same relative order as serial -- and disjoint
pairs cannot observe each other, so the sequence of per-unit outcomes, the
final members, store and log equal the serial run's. Resume equivalence
follows: cross-wave replay is serial by construction, and within-wave
disjointness means a resumed call re-running the undecided units of a
crashed wave reproduces their outcomes identically (reusing their stored
pending merges and judged attempts) whatever interleaving the crash froze.

*Ordered commit.* After each wave, the MAIN thread applies the collected
outcomes in unit-index order: first the merge record (the commit point,
journalled before any effect), then the store effect (kept: sources
"merged", merged skill "accepted"; reverted: merged skill "discarded").
Content within a wave is order-independent (disjoint members); unit order
is applied anyway so the write sequence is deterministic. The journal is
single-writer: only the main thread ever calls ``_write_log`` (workers
never touch it), which makes ``record_merge`` naturally main-thread-only;
``merge_records`` is sorted by (round, unit) before every write.

*Regression exams stay SEQUENTIAL within one unit* (deliberate: the
first-failure early-exit would be lost under parallelism, the stored
attempt footprint is preserved exactly, and at ~2.2 exams per unit inner
parallelism is not worth the complexity).

*Dispatch-time serial pre-allocation.* At the start of each round, BEFORE
any wave runs, serials are assigned to all of the round's units in the
existing deterministic order and the full dispatch map (unit index,
serial, sources, mode per unit) is written into that round's log record.
The map is the single source of truth for in-flight identity; resume
replays it instead of recomputing serials. A unit whose G8 fails, or that
resolves to fewer than two current members, BURNS its serial (semantic
change vs the v2 code, where a G8 failure consumed no serial; recorded
with the serial in its skipped merge record). Merged-skill ids can
therefore be sparse within a round; they are never reused.
``next_serial`` continues from the maximum serial ever allocated in a
dispatch section or stored (whichever is higher), never from the store
alone -- burned serials must not be handed out twice.

*Duplicate units.* G7 may emit the same source set twice; the block's
validation allows it. Duplicates (same mode and same source set as an
earlier unit of the round) are deduplicated at dispatch: the later
duplicate is recorded as skipped (no serial is allocated) and never runs.
Semantic change vs the v2 code, where a duplicate re-merged whenever the
first copy reverted; both changes are recorded here so a reviewer can find
them.

*In-flight identification on resume.* In-flight = the pending merged
skills of a wave that crashed mid-flight (several at once under workers >
1). Each pending merge is matched to its unit BY DISPATCH SERIAL: a merge
whose decision is in the log is finalised from the log; one whose serial
names an undecided unit is reused for that unit (its ``merged_from`` is
checked against the unit's re-resolved sources as a validation -- under
the map a mismatch means the store and journal disagree, i.e. a corrupted
run); one whose serial no dispatch entry claims is an orphan and is
rolled back (``interrupted_merge``). The v2 ">1 undecided in-flight is
corrupted" guard is REPLACED by this map-based identification. For runs
whose journal was lost entirely (a pending merge with NO log) the v2
heuristic survives unchanged: sources already "merged" roll the merge
forward (the premise "a source is merged ⟺ its merge's kept decision was
applied" holds again under ordered commit, since sources are only
marked by the main thread after the decision is journalled); otherwise
the pending merge is offered to the first undecided unit by merged_from
matching and rolled back if nobody claims it. In that recovery corner
the reusing unit's own dispatch serial burns (the reused merge keeps its
stored id), so later merges may sit at higher serials than the lost run
would have produced -- the plan cannot survive the journal's loss.

*K5 marker checks (protocol v2).* Every fresh merge unit runs two
structural gates around G8 (docs/decisions/stage23_protocol_v2_20261013.md
section 3.5). The PRE-CHECK reads every resolved source's
``skill_markers`` before the G8 call: a group that mixes skill kinds -- a
procedure skill with a failure reference, or a marked skill with an
unmarked one -- has no legal merge product, so it is skipped with the
pre-check's own reason and no model call (its record never claims a G8
failure). The POST-CHECK runs between the G8 output and its commit-step-1
save: a merged text whose ``skill_markers`` set differs from the sources'
set is never stored. Both raises sit INSIDE the unit's
ModelOutputError-to-skip try scope -- outside it run_units would treat
them as fatal, stop stage 5, and replay the same journaled G7 output into
the same raise on every resume. A group whose sources are ALL unmarked
(prose; test fixtures and foreign content, unreachable in a pure v2 run)
skips both checks: prose merges into prose. The resume reuse branches
never re-check a pending merge: it passed the post-check before it was
ever written to disk.

*Failure dispositions (unchanged semantics, now under run_units).*
ModelOutputError from G8 or from a merge's regression exam affects only
that merge: recorded by the worker as skipped (G8, serial burned) or
reverted (regression) and curation continues; both are also written to
``failed_units.jsonl`` by the worker. Any exception escaping the worker
(InfrastructureError, code bugs) is fatal by run_units' contract: the
batch stops, in-flight units still complete, and the main thread applies
and journals the completed outcomes BEFORE re-raising, so a stop is clean
and resume continues from the log. G7/G9 see the whole bank; their
ModelOutputError and every InfrastructureError propagate and stop the
stage.

*Crash protocol.* A crash can leave several pending merges of one wave
with no decisions, unjudged attempts, or a half-applied commit; every
case resumes to the same state as an uninterrupted run at the same
worker count: (a) a crash between the dispatch-map write and the first
wave re-derives the same map (it is a pure function of the journaled G7
output and the serial continuation); (b) a crash mid-wave leaves the
completed units committed (the main thread finished their bookkeeping or
the resume finalises them from the log) and the rest re-run with their
pending merges and stored attempts reused, so no model call is repeated;
(c) a crash after outcomes were collected but before they are committed
loses no compute -- the wave re-runs on reuse; (d) a crash inside the
commit apply loop (record durable, effects not) is finalised from the
log by the next call. Sources are only ever marked "merged" by the
commit of their own merge, so "pending with merged sources but no logged
decision" remains a corrupted run (unless the journal was lost).

*Staleness and corruption guards (re-derived).* The accepted set must
equal the journal's replay -- input_skill_ids, minus every kept record's
sources, plus every kept record's skill id -- a formula blind to serials
and waves: burned serials never enter it (no stored skill), never-run
units leave no record, and wave-parallel application order cannot change
a set computed from outcome-blind record effects. Merged skills are
written once at version 1 (a rewrite is corruption), and an accepted
merged skill without a logged keep decision is corruption. A finished
bank must still cover every accepted skill (staleness). The empty-member
early return and the over-budget stop-for-a-human are unchanged.

*Phase-directory mode (R7).* The ``dest`` parameter retargets every
artifact this composite owns: the journal, the bank file
(``<dest>/skill_bank.jsonl``) and the unit-failure records all land
inside ``dest``, and the run-level ``skill_bank.jsonl`` /
``stage5_curation`` journal are never touched -- so a per-phase
curation (every learn phase ends with its own loadable bank, plan item
R7) coexists with the frozen stage-5 behaviour. The resume and
staleness machinery is unchanged -- every check is relative to
``dest``'s own journal -- with one deliberate difference: an empty
accepted set still writes an (empty) bank file, because the artifact's
existence is the point of a phase bank. ``dest=None`` (the default) is
byte-for-byte the frozen stage-5 path.

Resume is unit-level. ``curation_log.json`` (schema ``curation-log-v3``;
v2 journals are refused -- the old run directory stays on the old code)
is the journal: it records each round's G7 output, the round's dispatch
map, every merge decision and each round's G9 output BEFORE their effects
are applied, so a resumed call replays the journal instead of asking the
models again, and finishes whatever effect the crash interrupted. A merge
commits in this order (crash-safe, docs/decisions/curation_resume_20261011.md):

  1. save the merged skill as "pending" (the G8 output is now durable; a
     pending skill is never a bank member);
  2. regression exams -- each Attempt is stored by K3 (the answer first,
     then the verdict); a resumed call reuses the JUDGED attempts already
     stored for this merged skill, and K3 itself completes an unjudged one
     by judging its stored answer;
  3. append the decision (kept / reverted) to the log -- the commit point;
  4. apply it: kept -> each source "merged", then the merged skill
     "accepted"; reverted -> the merged skill "discarded".

The final append loop is healed the same way as before: the log records
``final_member_ids`` first and a resumed call completes a truncated
append. A non-empty bank short-circuits every model call unless the
store holds accepted skills the bank does not cover ("skill bank is
stale", ConfigError: nothing is ever merged into a curation uncurated)
-- with ONE dest-mode exception: the short-circuit first refreshes the
artifact to the store's current state (``_refresh_dest_artifact``), so
a PRE-SEEDED artifact (an assembled run's last learn phase, whose A/B
phases accept members the source run never knew) reconciles instead of
refusing; the newly accepted members land uncurated, exactly as the
assembler's ASSEMBLED_FROM record discloses (the Wave 3 review's B1
ruling, 2026-10-16).

The ``weak_profile`` parameter is accepted for signature uniformity with
the stage entry point but unused here: stage 5 runs no leak audit and no
correctness judgment by design (README stage 5).
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from expedistill.blocks.author import skill_markers
from expedistill.blocks.curate import assign_groups, find_similar, merge_skills
from expedistill.composites.exam_once import exam_once
from expedistill.core import ids
from expedistill.core.models import (
    FAILURE_REFERENCE_MARKERS,
    PROCEDURE_MARKERS,
    Attempt,
    Exam,
    Skill,
    merged_source_subtasks,
)
from expedistill.infra.parallel import UnitSkipped, run_units
from expedistill.infra.reliability import (
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import (
    Store,
    append_jsonl,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
)
from expedistill.inject import count_bank_tokens, format_skills_block
from expedistill.llm.call import ModelHandle

__all__ = ["curate_bank"]

#: Where the curation audit log lives inside the run directory.
LOG_DIR = "stage5_curation"
LOG_NAME = "curation_log.json"
#: The bank file this composite writes: the run-level stage-5 output, or
#: the phase artifact in dest mode (composites.phase_bank's layout).
BANK_NAME = "skill_bank.jsonl"
#: Journal format; a log without it (or with another value) is refused on
#: resume rather than misread. v3 adds the per-round dispatch maps (the
#: in-flight identity of the wave-parallel run); v2 logs stay on the old
#: code -- no read compatibility.
LOG_SCHEMA = "curation-log-v3"
#: Stage label in failed_units.jsonl.
FAILURE_STAGE = "stage5_curation"

_MERGED_ID_RE = re.compile(r"^bank/merge/(\d+)$")


# ---------------------------------------------------------------------------
# Audit log (the journal)
# ---------------------------------------------------------------------------

def _log_path(store: Store, run_dir: Optional[Union[str, Path]],
              dest: Optional[Union[str, Path]]) -> Path:
    # Falls back to the store's run directory: the log is the resume journal,
    # so curating without one is not an option. Dest mode (R7) keeps the
    # journal inside the phase directory instead.
    if dest is not None:
        return Path(dest) / LOG_NAME
    base = Path(run_dir) if run_dir is not None else store.run_dir
    return base / LOG_DIR / LOG_NAME


def _bank_path(store: Store, dest: Optional[Union[str, Path]]) -> Path:
    """The bank file this call reads and writes (skill_bank.jsonl rows)."""
    if dest is not None:
        return Path(dest) / BANK_NAME
    return store.run_dir / BANK_NAME


def _read_bank(store: Store,
               dest: Optional[Union[str, Path]]) -> List[Dict[str, Any]]:
    # Run mode goes through the store's own accessors (not the raw file):
    # tests subclass them to inject crashes, and the frozen behaviour is
    # what those accessors do.
    if dest is None:
        return store.bank()
    # The phase artifact carries the pending members through unchanged
    # (R7's full-membership ruling, 2026-10-16): they are not curation
    # products, so every curation-side check below sees the accepted
    # rows only. The entry (methods/curation/phase_bank.py) re-appends
    # them after this call returns.
    return [row for row in read_jsonl(_bank_path(store, dest))
            if row.get("status") != "pending"]


def _append_bank_row(store: Store, dest: Optional[Union[str, Path]],
                     row: Dict[str, Any]) -> None:
    if dest is None:
        store.append_bank(row)
        return
    append_jsonl(_bank_path(store, dest), row)


def _write_log(log: Dict[str, Any], path: Path) -> None:
    """Rewrite the audit log atomically; deterministic content (no
    timestamps). Only the main thread calls this (single-writer journal)."""
    write_json(path, log, indent=2)


def _fresh_log() -> Dict[str, Any]:
    return {
        "schema": LOG_SCHEMA,
        "rounds_executed": 0,
        "merges": {"attempted": 0, "kept": 0, "reverted": 0, "skipped": 0},
        "merge_records": [],
        "rounds": [],
        "final_member_ids": [],
        "bank_tokens": 0,
    }


def _load_log(path: Path) -> Tuple[Dict[str, Any], bool]:
    """(journal of a previous call, False) or (a fresh journal, True).

    A log that exists but is not a v3 journal is refused: continuing from a
    log we cannot interpret would silently restart curation over a store
    that already carries its effects.
    """
    if not path.exists():
        return _fresh_log(), True
    try:
        log = read_json(path)
    except (OSError, ValueError) as exc:
        raise ConfigError(
            f"curation log {path} is unreadable ({exc}); a human must look "
            "at this run") from exc
    if not isinstance(log, dict) or log.get("schema") != LOG_SCHEMA:
        raise ConfigError(
            f"curation log {path} is not a {LOG_SCHEMA} journal; cannot "
            "resume stage 5 from it -- start a new run")
    if not isinstance(log.get("input_skill_ids"), list):
        raise ConfigError(
            f"curation log {path} records no input_skill_ids; cannot check "
            "the run for skills accepted after curation started -- start a "
            "new run directory")
    return log, False


def _read_final_member_ids(path: Path) -> Optional[List[str]]:
    """The final member ids recorded by a previous (possibly crashed) call.

    None when there is nothing usable to compare against (no log,
    unreadable log) -- the resume path then trusts the bank as-is.
    """
    if not path.exists():
        return None
    try:
        log = read_json(path)
    except (OSError, ValueError):
        return None
    final = log.get("final_member_ids") if isinstance(log, dict) else None
    if not isinstance(final, list):
        return None
    return [str(item) for item in final]


# ---------------------------------------------------------------------------
# Store helpers
# ---------------------------------------------------------------------------

def _accepted_members(store: Store) -> List[Skill]:
    """Stored skills with status "accepted", sorted by skill_id.

    The fixed order is what makes merged-skill serials (and therefore IDs)
    reproducible: the same stored set always yields the same member order.
    """
    members = []
    for skill_id in store.ids("skills"):
        skill = store.load("skills", skill_id, Skill)
        if skill.status == "accepted":
            members.append(skill)
    return sorted(members, key=lambda skill: skill.skill_id)


def _merge_serial(skill_id: str) -> Optional[int]:
    match = _MERGED_ID_RE.match(skill_id)
    return int(match.group(1)) if match else None


def _stored_merges(store: Store) -> List[Tuple[int, str]]:
    """(serial, skill_id) of every stored merged skill, by serial."""
    found = []
    for skill_id in store.ids("skills"):
        serial = _merge_serial(skill_id)
        if serial is not None:
            found.append((serial, skill_id))
    return sorted(found)


def _mark_sources_merged(store: Store, merged: Skill) -> None:
    for source_id in merged.merged_from:
        source = store.load("skills", source_id, Skill)
        if source.status != "merged":
            source.status = "merged"
            store.save(source, "skills")


def _apply_kept(store: Store, merged: Skill) -> None:
    """Commit step 4 for a kept merge: sources first, merged skill last.

    Idempotent, so a resumed call can simply run it again.
    """
    _mark_sources_merged(store, merged)
    if merged.status == "pending":
        merged.status = "accepted"
        store.save(merged, "skills")


def _apply_reverted(store: Store, merged: Skill, discard_reason: str) -> None:
    if merged.status == "pending":
        merged.status = "discarded"
        merged.discard_reason = discard_reason
        store.save(merged, "skills")


def _corrupted(what: str) -> ConfigError:
    return ConfigError(
        f"corrupted run: {what}; only K5 may change a merged skill -- a "
        "human must look at this run (start a new run directory)")


def _check_merged_versions(store: Store) -> None:
    """A merged skill is written once by K5 at version 1 and never
    rewritten; any other version means another stage (e.g. a stage-4
    rewrite) touched it."""
    for _, skill_id in _stored_merges(store):
        merged = store.load("skills", skill_id, Skill)
        if merged.version != 1:
            raise _corrupted(
                f"merged skill {skill_id} has version {merged.version} "
                "(K5 writes merged skills at version 1 only)")


def _recover_pending_merges(store: Store, log: Dict[str, Any],
                            log_lost: bool,
                            ) -> Tuple[Optional[Skill], Dict[Tuple[int, int], Skill]]:
    """Resume rule for merges a crash left half-done (module docstring).

    Returns ``(legacy_in_flight, reuse)``: the single pending merge of a
    run whose journal was lost (reused by merged_from matching, v2
    behaviour) -- always None for a journalled run -- and the pending
    merges keyed by their unit's (round, unit_index) from the dispatch
    map, to be reused by that unit without a second G8 call.

    * a merge whose decision is in the log but whose merged skill is still
      "pending" is finalised from the log (commit step 4 is re-applied);
    * with a journal, a pending merge whose sources are already partly
      "merged" is impossible (sources are only marked after their own
      merge's kept decision is journalled) -> corrupted run;
    * with a journal, a pending merge whose serial no dispatch entry
      claims is an orphan -> rolled back (``interrupted_merge``);
    * without a journal (``log_lost``), the v2 heuristics survive
      unchanged: merged sources roll the merge forward, and at most one
      undecided pending merge may exist (the next unit's in-flight merge).
    """
    decided = {record["skill_id"]: record
               for record in log["merge_records"] if record.get("skill_id")}
    decided_units = {(record.get("round"), record.get("unit"))
                     for record in log["merge_records"]}
    dispatched = _dispatch_serial_index(log)
    reuse: Dict[Tuple[int, int], Skill] = {}
    legacy: List[Skill] = []
    for _, skill_id in _stored_merges(store):
        merged = store.load("skills", skill_id, Skill)
        if merged.status != "pending":
            continue
        record = decided.get(skill_id)
        if record is not None:
            if record["outcome"] == "kept":
                _apply_kept(store, merged)
            else:
                _apply_reverted(store, merged,
                                record.get("discard_reason")
                                or "regression_failed")
            continue
        statuses = [store.load("skills", source_id, Skill).status
                    for source_id in merged.merged_from
                    if store.exists("skills", source_id)]
        if "merged" in statuses:
            if not log_lost:
                raise _corrupted(
                    f"merged skill {skill_id} is pending with merged "
                    "sources but the curation log holds no decision for it")
            _apply_kept(store, merged)
            continue
        if not log_lost:
            unit_key = dispatched.get(_merge_serial(skill_id))
            if unit_key is None:
                # No dispatch entry ever claimed this serial: it predates
                # the journal (or the journal is not this store's) -- an
                # audit record, never a bank member.
                _apply_reverted(store, merged, "interrupted_merge")
                continue
            if unit_key in decided_units:
                raise _corrupted(
                    f"merged skill {skill_id} is pending but unit "
                    f"{unit_key[0]}/{unit_key[1]:03d} already decided "
                    "another merge")
            reuse[unit_key] = merged
            continue
        legacy.append(merged)
    if log_lost and len(legacy) > 1:
        raise ConfigError(
            "more than one undecided merged skill in the store "
            f"({[skill.skill_id for skill in legacy]}); K5 commits one "
            "merge at a time, so this run is inconsistent -- a human must "
            "look at it")
    return (legacy[0] if legacy else None), reuse


# ---------------------------------------------------------------------------
# Round helpers
# ---------------------------------------------------------------------------

def _ordered_groups(groups: Any) -> List[Any]:
    """Deterministic processing order for one family of G7 groups.

    Sorts by the group's member ids so the merge serials (hence merged-skill
    IDs) do not depend on the model's listing order. Malformed entries sort
    first and are recorded as skipped by ``_resolve_group``; a non-list
    ``groups`` value (a broken G7 contract that escaped the block) is
    treated as no groups rather than crashing the stage.
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
    if isinstance(group, (list, tuple)):
        return {str(item) for item in group}
    return {repr(group)}


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


def _build_dispatch(round_index: int, units: Sequence[Tuple[str, Any]],
                    next_serial: int,
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


def _dispatch_serial_index(log: Dict[str, Any]) -> Dict[int, Tuple[int, int]]:
    """serial -> (round, unit_index) over every journaled dispatch entry."""
    index: Dict[int, Tuple[int, int]] = {}
    for round_record in log["rounds"]:
        for entry in round_record.get("dispatch") or ():
            serial = entry.get("serial")
            if serial is not None:
                index[serial] = (round_record["round"], entry["unit"])
    return index


def _initial_next_serial(store: Store, log: Dict[str, Any]) -> int:
    """Serial continuation: past every serial ever allocated in a dispatch
    map or stored, whichever is higher. Burned serials (allocated, no merge
    stored) must not be handed out again -- the journal names them."""
    stored = _stored_merges(store)
    continuation = stored[-1][0] + 1 if stored else 0
    for serial in _dispatch_serial_index(log):
        continuation = max(continuation, serial + 1)
    return continuation


def _resolve_group(group: Any,
                   members: Sequence[Skill]) -> Tuple[List[Skill], Optional[str]]:
    """Map one G7 group (a list of skill ids) onto current members.

    Model output is only a candidate: ids that are not current members are
    dropped (a member merged away earlier this round is no longer current
    either), duplicates collapse, and a group left with fewer than two
    sources cannot be merged. Returns (sources sorted by skill_id, skip
    reason or None).
    """
    if not isinstance(group, (list, tuple)) or not group:
        return [], "malformed group"
    by_id = {skill.skill_id: skill for skill in members}
    sources = []
    seen = set()
    for skill_id in group:
        if not isinstance(skill_id, str) or skill_id in seen:
            continue
        seen.add(skill_id)
        skill = by_id.get(skill_id)
        if skill is not None:
            sources.append(skill)
    if len(sources) < 2:
        unknown = sorted(
            str(item) for item in group
            if not (isinstance(item, str) and item in by_id))
        return [], ("group has fewer than two current members"
                    + (f"; unknown ids {unknown}" if unknown else ""))
    return sorted(sources, key=lambda skill: skill.skill_id), None


def _original_sources(store: Store, sources: Sequence[Skill],
                      ) -> Tuple[List[Skill], Optional[str]]:
    """Resolve merge sources through ``merged_from`` to original skills.

    A merged skill carries only its anchor's ``subtask_id``; the exams it
    must keep passing are those of every original (non-merged) skill it
    stands for. Returns (originals sorted by skill_id, error or None).
    """
    originals: Dict[str, Skill] = {}
    seen = set()
    stack = list(sources)
    while stack:
        skill = stack.pop()
        if skill.skill_id in seen:
            continue
        seen.add(skill.skill_id)
        if not skill.merged_from:
            originals[skill.skill_id] = skill
            continue
        for source_id in skill.merged_from:
            if not store.exists("skills", source_id):
                return [], (f"merged skill {skill.skill_id} names source "
                            f"{source_id}, which is not stored; merge "
                            "unverifiable")
            stack.append(store.load("skills", source_id, Skill))
    return sorted(originals.values(), key=lambda skill: skill.skill_id), None


def _source_exams(store: Store, sources: Sequence[Skill],
                  ) -> Tuple[List[Exam], Optional[str]]:
    """Every exam the merged skill must pass: one per original source.

    Deduplicated by exam id, in original-skill order. A source without a
    stored exam makes the merge unverifiable, so it must be skipped, not
    merged on faith (and the reason logged).
    """
    originals, error = _original_sources(store, sources)
    if error is not None:
        return [], error
    exams: List[Exam] = []
    seen = set()
    for source in originals:
        exam_id = ids.make_exam_id(source.subtask_id)
        if exam_id in seen:
            continue
        seen.add(exam_id)
        if not store.exists("exams", exam_id):
            return [], (f"source skill {source.skill_id} has no stored exam "
                        f"({exam_id}); merge unverifiable")
        exams.append(store.load("exams", exam_id, Exam))
    return exams, None


def _describe_skill_kind(markers: Optional[Tuple[str, ...]]) -> str:
    """One source's kind for a K5 marker message: which of the two stored
    skill structures its content carries, or none."""
    if markers == PROCEDURE_MARKERS:
        return "procedure"
    if markers == FAILURE_REFERENCE_MARKERS:
        return "failure reference"
    return "no section markers"


def _group_marker_state(
        sources: Sequence[Skill],
        ) -> Tuple[Optional[Tuple[str, ...]], Optional[str]]:
    """K5 marker gate for one merge group (protocol v2, decision 3.5).

    Reads every source's ``skill_markers`` once and returns
    ``(expected_markers, conflict)``: all sources unmarked (prose; test
    and foreign content, unreachable in a pure v2 run) -> ``(None, None)``
    -- prose merges into prose and BOTH marker checks are skipped; one
    marker set shared by every source -> ``(set, None)`` -- the merged
    text must reproduce it; otherwise (a procedure skill with a failure
    reference, or a marked skill with an unmarked one) -> ``(None,
    reason)`` -- the group has no legal merge product.
    """
    per_source = {skill.skill_id: skill_markers(skill.content)
                  for skill in sources}
    present = {markers for markers in per_source.values()
               if markers is not None}
    if not present:
        return None, None
    if len(present) == 1 and all(markers is not None
                                 for markers in per_source.values()):
        return present.pop(), None
    described = ", ".join(
        f"{skill_id}={_describe_skill_kind(per_source[skill_id])}"
        for skill_id in sorted(per_source))
    return None, f"merge group mixes skill kinds: {described}"


def _stored_regression_attempts(store: Store, merged: Skill,
                                exams: Sequence[Exam]) -> Dict[str, Attempt]:
    """JUDGED regression attempts already stored for this merged skill.

    Merged-skill ids are never reused and K5 examines a merge on each exam
    once (a resume skips exams that already have a judged attempt), so at
    most one judged attempt exists per merge x exam; should there ever be
    more, the lowest serial wins, deterministically. An unjudged attempt
    (``judge_verdict is None``: K3 stored the answer, then crashed or ran
    out of judge repairs) is NOT a result -- it is left to ``exam_once``,
    which reuses the stored answer and only judges.
    """
    wanted = {exam.exam_id for exam in exams}
    found: Dict[str, Tuple[int, Attempt]] = {}
    for attempt_id in store.ids("attempts"):
        exam_id, serial = ids.parse_attempt_id(attempt_id)
        if exam_id not in wanted:
            continue
        attempt = store.load("attempts", attempt_id, Attempt)
        if (attempt.purpose == "regression"
                and attempt.skill_id == merged.skill_id
                and attempt.skill_version == merged.version
                and attempt.judge_verdict is not None
                and (exam_id not in found or serial < found[exam_id][0])):
            found[exam_id] = (serial, attempt)
    return {exam_id: attempt for exam_id, (_, attempt) in found.items()}


def _regression_passes(merged: Skill, exams: Sequence[Exam], store: Store,
                       answerer_handle: ModelHandle,
                       judge_handle: ModelHandle, usage: Any,
                       run_dir: Optional[Union[str, Path]],
                       ) -> Tuple[bool, str]:
    """Re-examine the merged skill against every source exam (K3 regression).

    The merged skill is injected alone, the same one-skill injection stage 4
    used to accept each source, so "the merge kept the capability" is tested
    under acceptance-time conditions. Attempts stored before a crash are
    reused. Exams run SEQUENTIALLY with the first-failure early exit (see
    the module docstring). Returns (passed, failure reason); K3's
    ModelOutputError and InfrastructureError propagate to the caller.
    """
    skills_block = format_skills_block([merged])
    stored = _stored_regression_attempts(store, merged, exams)
    for exam in exams:
        attempt = stored.get(exam.exam_id)
        if attempt is None:
            attempt = exam_once(
                store=store,
                exam=exam,
                answerer_handle=answerer_handle,
                judge_handle=judge_handle,
                skills_block=skills_block,
                purpose="regression",
                skill_id=merged.skill_id,
                skill_version=merged.version,
                usage=usage,
                run_dir=run_dir,
            )
        if attempt.judge_verdict is not True:
            return False, (f"regression failed on exam {exam.exam_id} "
                           f"(attempt {attempt.attempt_id})")
    return True, ""


def _bank_ids(bank: Sequence[Dict[str, Any]]) -> List[str]:
    return [str(row.get("skill_id")) for row in bank]


def _stale_error(stale: Sequence[str]) -> ConfigError:
    return ConfigError(
        f"skill bank is stale: accepted skills not in the bank: "
        f"{sorted(stale)}; they were accepted after curation started -- "
        "start a new run directory")


def _expected_accepted(log: Dict[str, Any]) -> List[str]:
    """Replay the journal: the input set, minus every kept merge's sources,
    plus the kept merged skill (which a later kept merge may consume)."""
    expected = set(log["input_skill_ids"])
    for record in log["merge_records"]:
        if record.get("outcome") == "kept":
            expected -= set(record["sources"])
            expected.add(record["skill_id"])
    return sorted(expected)


def _check_store_matches_log(store: Store, log: Dict[str, Any]) -> None:
    """The accepted skills in the store must be exactly what the journal
    says (run AFTER finalisation, so no commit is half-applied).

    An extra stage-3 skill was accepted after curation started (a resumed
    stage 3/4 re-offered a failed unit): the curation is stale and must not
    absorb it uncurated. An extra merged skill (accepted without a logged
    keep decision) or a missing one means something outside K5 changed a
    status: the run is corrupted. Either way, ConfigError -- never a silent
    member. The formula (see module docstring) is blind to serials and
    waves: burned serials never enter it and never-run units leave no
    record.
    """
    actual = {skill.skill_id for skill in _accepted_members(store)}
    expected = set(_expected_accepted(log))
    extra = actual - expected
    extra_merged = sorted(i for i in extra if _merge_serial(i) is not None)
    if extra_merged:
        raise _corrupted(
            f"merged skills {extra_merged} are accepted without a logged "
            "keep decision")
    if extra:
        raise _stale_error(extra)
    missing = sorted(expected - actual)
    if missing:
        raise _corrupted(
            f"skills {missing} should be accepted according to the curation "
            "log but are not")


def _refresh_dest_artifact(store: Store,
                           dest: Union[str, Path]) -> None:
    """Bring a finished dest artifact back in step with the store (the
    Wave 3 review's B1 fix, the 2026-10-16 coordinator ruling).

    An assembled run pre-seeds the last learn phase's bank artifact from
    the SOURCE run's curation, then its own A/B phases legitimately
    accept members the artifact does not know yet (a ride-along pending
    member that passes its exam; a re-offered generation-failure unit).
    The run-mode answer to that state is the staleness refusal -- the
    pre-seeded case must not be refused, so the short-circuit refreshes
    the artifact to the store's current state first, program-only:

    * a row whose member the store has since moved pending -> accepted
      is replaced by the member's own on-disk record (the same form the
      final-append loop uses, guaranteed to match what K5 itself would
      have written);
    * an accepted store skill with no row at all is appended, sorted by
      skill_id, in that same on-disk record form -- uncurated, exactly
      as the ASSEMBLED_FROM record discloses.

    The source run's curation products (the merged rows) are kept
    verbatim. Deterministic and idempotent: a repeat call finds nothing
    left to do -- the file is only rewritten when a row actually
    changed, so a pre-seeded artifact the run never touched stays
    byte-identical."""
    path = _bank_path(store, dest)
    rows = list(read_jsonl(path))
    if not rows:
        return  # nothing pre-seeded: the caller's short-circuit never ran
    stored_ids = set(store.ids("skills"))
    records: Dict[str, Dict[str, Any]] = {}

    def record_of(skill_id: str) -> Dict[str, Any]:
        if skill_id not in records:
            records[skill_id] = read_json(
                store.path_for("skills", skill_id))
        return records[skill_id]

    changed = False
    refreshed: List[Dict[str, Any]] = []
    for row in rows:
        skill_id = row.get("skill_id")
        if (row.get("status") == "pending" and skill_id in stored_ids
                and record_of(skill_id).get("status") == "accepted"):
            # The phase's own keep row landed after the artifact was
            # written: the artifact row follows the store's record.
            row = record_of(skill_id)
            changed = True
        refreshed.append(row)
    present = {row.get("skill_id") for row in refreshed}
    missing = sorted(
        skill_id for skill_id in stored_ids
        if skill_id not in present
        and store.load("skills", skill_id, Skill).status == "accepted")
    if missing:
        refreshed.extend(record_of(skill_id) for skill_id in missing)
        changed = True
    if changed:
        write_jsonl(path, refreshed)


def _check_bank_not_stale(store: Store,
                          bank: Sequence[Dict[str, Any]]) -> None:
    """Refuse a finished bank that no longer covers the accepted skills.

    Earlier stages re-offer their failed units on every resume, so a resume
    after the bank was written can accept NEW skills in stage 4. Returning
    the old bank would drop them silently; merging them into a finished
    bank would bypass curation. Only status "accepted" counts: merged
    sources, discarded versions and pending skills (including an in-flight
    merged skill) are never bank candidates. The only remedy offered is a
    new run directory: re-curating in place would leave stage 6's stored
    trajectories carrying the old bank.
    """
    known = set(_bank_ids(bank))
    stale = sorted(
        skill_id for skill_id in store.ids("skills")
        if skill_id not in known
        and store.load("skills", skill_id, Skill).status == "accepted")
    if stale:
        raise _stale_error(stale)


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def curate_bank(
    *,
    store: Store,
    weak_profile: str,
    answerer_handle: ModelHandle,
    judge_handle: ModelHandle,
    curator_handle: ModelHandle,
    bank_token_budget: int,
    curation_rounds: int,
    merge_check: str = "exam",
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
    workers: int = 1,
    dest: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """Curate the accepted skills into the final bank (README stage 5).

    Returns ``store.bank()``: the final member rows in sorted skill_id
    order. Idempotent at the stage level -- a non-empty bank (completed
    from the log first if a crash interrupted the final append) is returned
    without any model call, unless the store holds accepted skills the bank
    does not cover (ConfigError: the bank is stale; in dest mode the
    short-circuit instead REFRESHES the artifact from the store first --
    the pre-seeded/assemble case, see ``_refresh_dest_artifact``) -- and
    resumable at the unit level (see module docstring). Raises ConfigError
    when the bank is still over budget after ``curation_rounds`` rounds;
    nothing is deleted in that case.

    ``merge_check`` (M3, plan section 3's curate family): ``exam`` (the
    default) is the frozen behaviour -- each kept merge re-answers every
    source's exam; ``none`` skips the regression exams (the merge product
    goes pending -> accepted with zero answerer/judge calls). The G8
    marker gates and the budget valve are unchanged either way, and the
    merge journal records the same outcomes.

    ``workers`` bounds the parallelism of one wave's units (B5 run_units);
    any output is bit-identical to the serial run (module docstring).
    ``weak_profile`` is part of the frozen stage signature but unused
    (see module docstring).

    ``dest`` (R7): the phase-directory mode -- when given, the journal,
    the bank file and the unit-failure records all land inside ``dest``
    and the run-level ``skill_bank.jsonl`` is never touched (module
    docstring). An empty accepted set still writes an empty bank file
    there: every learn phase ends with a loadable artifact.
    """
    if curation_rounds < 1:
        raise ConfigError(f"curation_rounds must be >= 1, got {curation_rounds}")
    if merge_check not in ("exam", "none"):
        raise ConfigError(
            f"merge_check must be 'exam' or 'none', got {merge_check!r}")
    if workers < 1:
        raise ConfigError(f"workers must be >= 1, got {workers}")
    log_path = _log_path(store, run_dir, dest)

    # -- resume: a written bank means the stage already finished -----------
    bank = _read_bank(store, dest)
    if bank:
        final_ids = _read_final_member_ids(log_path)
        if final_ids is not None:
            have = set(_bank_ids(bank))
            if not have <= set(final_ids):
                raise ConfigError(
                    "skill_bank.jsonl contains rows that are not in "
                    "curation_log.json final_member_ids "
                    f"({sorted(have - set(final_ids))}); the bank and the "
                    "curation log disagree -- a human must look at this run")
            for skill_id in (i for i in final_ids if i not in have):
                # Crash inside the final append loop: the ledger holds a
                # prefix of the deterministic rows. Complete it from the
                # stored records in their OWN on-disk form (same dicts,
                # same order, no model call): the prefix rows were written
                # from these same records, so the raw record is the only
                # form guaranteed to match them across schema versions --
                # the from_dict roundtrip would migrate a v1 record to v2
                # (a mixed-version bank) and cannot load a v1 merge
                # product at all (from_dict lacks the sibling records).
                _append_bank_row(store, dest,
                                read_json(store.path_for("skills", skill_id)))
        if dest is not None:
            # The assembled-run case (B1 fix): a pre-seeded artifact predates
            # the run's own acceptances. The refresh reconciles it with the
            # store BEFORE the staleness check, so the short-circuit stays a
            # short-circuit. Run mode keeps the refusal: a run-level bank
            # never has a pre-seeding author, and stage 6's stored
            # trajectories would silently diverge from a patched bank.
            _refresh_dest_artifact(store, dest)
        bank = _read_bank(store, dest)
        _check_bank_not_stale(store, bank)
        return bank

    # -- resume: carry the journal forward, finish interrupted commits ------
    log, fresh = _load_log(log_path)
    _check_merged_versions(store)
    legacy_in_flight, reuse = _recover_pending_merges(store, log, log_lost=fresh)
    if fresh:
        # The curation's input set, fixed at first start: every later
        # resume checks the store against it (stale / corrupted run).
        log["input_skill_ids"] = sorted(
            skill.skill_id for skill in _accepted_members(store))
        _write_log(log, log_path)
    else:
        _check_store_matches_log(store, log)
    next_serial = _initial_next_serial(store, log)
    recorder = UnitFailureRecorder(
        Path(dest) if dest is not None
        else (Path(run_dir) if run_dir is not None else store.run_dir))

    members = _accepted_members(store)
    if not members:
        # An empty bank is a valid bank; nothing to curate or append. (An
        # in-flight merge implies accepted sources, so none can be
        # pending.) Dest mode still writes the (empty) artifact: a phase
        # bank's existence is the point (R7).
        _write_log(log, log_path)
        if dest is not None:
            write_jsonl(_bank_path(store, dest), [])
        return []

    def record_merge(record: Dict[str, Any]) -> None:
        # The commit point of a unit: the decision is durable before any of
        # its effects touch the stored skills. Main thread only (the
        # single-writer journal invariant); canonical record order.
        log["merge_records"].append(record)
        log["merge_records"].sort(
            key=lambda item: (item["round"], item["unit"]))
        counts = log["merges"]
        counts["attempted"] += 1
        counts[record["outcome"]] += 1
        _write_log(log, log_path)

    def record_failure(unit_id: str, exc: BaseException) -> None:
        recorder.record(unit_id, FAILURE_STAGE, exc, str(exc))

    def commit(unit_index: int, outcome: Dict[str, Any]) -> None:
        # Ordered commit, main thread, one unit at a time: record first
        # (crash-safe, a resumed call finalises from the record), then the
        # store effect, then the member list.
        nonlocal members
        merged: Optional[Skill] = outcome.get("merged")
        record = dict(outcome["base"], sources=outcome["sources"],
                      skill_id=merged.skill_id if merged is not None else None,
                      outcome=outcome["status"], reason=outcome["reason"])
        if outcome["status"] == "reverted":
            record["discard_reason"] = outcome["discard_reason"]
        record_merge(record)
        if merged is None:
            return
        if outcome["status"] == "kept":
            _apply_kept(store, merged)
            consumed = set(outcome["sources"])
            members = [skill for skill in members
                       if skill.skill_id not in consumed]
            members.append(merged)
            members.sort(key=lambda skill: skill.skill_id)
        else:
            _apply_reverted(store, merged, outcome["discard_reason"])

    rounds_by_index = {record["round"]: record for record in log["rounds"]}
    # The journal-less (log_lost) in-flight merge, offered to the first
    # undecided unit in serial order -- exactly the v2 rule.
    legacy_offer: Dict[Tuple[int, int], Skill] = {}

    for round_index in range(1, curation_rounds + 1):
        log["rounds_executed"] = round_index
        unit_prefix = f"curation/round{round_index:02d}"
        round_record = rounds_by_index.get(round_index)
        if round_record is not None and round_record.get("done"):
            if round_record.get("stop"):
                break
            continue

        if round_record is None:
            try:
                similar_groups, conflicts = find_similar(
                    members, curator_handle, usage=usage, run_dir=run_dir)
            except (InfrastructureError, ModelOutputError) as exc:
                record_failure(f"{unit_prefix}/similarity", exc)
                raise
            round_record = {"round": round_index,
                            "similar_groups": similar_groups,
                            "conflicts": conflicts,
                            "grouping": None, "dispatch": None,
                            "done": False, "stop": False}
            log["rounds"].append(round_record)
            rounds_by_index[round_index] = round_record
            _write_log(log, log_path)

        units = _round_units(round_record)
        if not units and len(members) < 2:
            # No merge candidates and a bank of fewer than two skills:
            # nothing to merge, and grouping a single skill (or nothing) is
            # meaningless, so the round ends without G9 (README stage 5
            # step 4 runs G9 whenever the bank has >= 2 members).
            round_record["done"] = True
            round_record["stop"] = (count_bank_tokens(members)
                                    <= bank_token_budget)
            _write_log(log, log_path)
            if round_record["stop"]:
                break
            continue

        # Dispatch: serials for the whole round, journalled BEFORE any wave
        # runs. The map is the in-flight identity; resume replays it. The
        # duplicate-unit records are written right after the map (and
        # re-derived on replay for any the crash interrupted), because a
        # duplicate is decided at dispatch and never runs.
        if round_record.get("dispatch") is None:
            dispatch, next_serial = _build_dispatch(
                round_index, units, next_serial)
            round_record["dispatch"] = dispatch
            _write_log(log, log_path)
        dispatch = round_record["dispatch"]
        serial_of = {entry["unit"]: entry["serial"] for entry in dispatch}

        decided = {record.get("unit")
                   for record in log["merge_records"]
                   if record.get("round") == round_index}
        for unit_index, first_index in _duplicate_units(units):
            if unit_index not in decided:
                record_merge(_duplicate_record(
                    round_index, unit_index, first_index, *units[unit_index]))
                decided.add(unit_index)
        if legacy_in_flight is not None:
            # Offer the journal-less in-flight merge to the first unit that
            # will actually run (v2 matched it there by merged_from).
            undecided = [index for index in range(len(units))
                        if index not in decided]
            if undecided:
                legacy_offer[(round_index, undecided[0])] = legacy_in_flight
                legacy_in_flight = None

        for wave in _partition_waves(units):
            todo = [index for index in wave if index not in decided]
            if not todo:
                continue
            wave_members = list(members)

            def unit_worker(unit_index: int,
                            _members: List[Skill] = wave_members) -> Dict[str, Any]:
                # One unit: resolve -> G8 (or reuse) -> regression exams
                # (sequential, early-exit). Expected failures become
                # outcomes; anything escaping is fatal by run_units'
                # contract. Only this unit's files are written (the wave's
                # units are member-disjoint, module docstring).
                mode, group = units[unit_index]
                unit_id = f"{unit_prefix}/unit{unit_index:03d}"
                base = {"round": round_index, "unit": unit_index,
                        "mode": mode, "serial": serial_of[unit_index]}
                sources, skip = _resolve_group(group, _members)
                if skip is None:
                    exams, skip = _source_exams(store, sources)
                if skip is not None:
                    return {"status": "skipped", "base": base, "reason": skip,
                            "sources": ([source.skill_id for source in sources]
                                        if sources
                                        else _display_sources(group)),
                            "merged": None}
                source_ids = [source.skill_id for source in sources]
                # Reused in-flight merges skip the K5 marker checks: a
                # pending merge passed the post-check before it was ever
                # written to disk (module docstring).
                merged = reuse.pop((round_index, unit_index), None)
                if merged is not None and merged.merged_from != source_ids:
                    raise _corrupted(
                        f"pending merge {merged.skill_id} was dispatched for "
                        f"round {round_index} unit {unit_index} but its "
                        f"merged_from {merged.merged_from} disagrees with the "
                        "unit's sources " f"{source_ids}")
                legacy = legacy_offer.pop((round_index, unit_index), None)
                if merged is None and legacy is not None:
                    if legacy.merged_from == source_ids:
                        merged = legacy  # the G8 output survived the crash
                    else:
                        # Not this unit's merge: roll it back as an audit
                        # record (its own id is never reused); this unit
                        # merges freshly at its own dispatched serial.
                        _apply_reverted(store, legacy, "interrupted_merge")
                if merged is None:
                    expected_markers, kind_conflict = _group_marker_state(
                        sources)
                    try:
                        if kind_conflict is not None:
                            # K5 pre-check (protocol v2): the raise must
                            # stay inside this ModelOutputError-to-skip try
                            # scope -- outside it run_units would treat it
                            # as fatal, stop stage 5, and replay the same
                            # journaled G7 output into the same raise on
                            # every resume.
                            raise ModelOutputError(kind_conflict)
                        content = merge_skills(
                            sources, curator_handle, mode=mode,
                            usage=usage, run_dir=run_dir).strip()
                        if expected_markers is not None:
                            got = skill_markers(content)
                            if got != expected_markers:
                                # K5 post-check: a structure-losing merge
                                # is rejected before its commit-step-1
                                # save, so nothing lands on disk.
                                raise ModelOutputError(
                                    "merged skill must carry the source "
                                    "skills' four bracket sections "
                                    f"({' / '.join(expected_markers)}), "
                                    f"got {_describe_skill_kind(got)}")
                    except InfrastructureError as exc:
                        record_failure(f"{unit_id}/merge", exc)
                        raise
                    except ModelOutputError as exc:
                        record_failure(f"{unit_id}/merge", exc)
                        # kind_conflict is not None only for the pre-check
                        # raise, which precedes every G8 call: that skip
                        # record carries the pre-check's own reason (G8
                        # never ran); every other ModelOutputError here is
                        # a G8 output failure.
                        reason = (_describe(exc) if kind_conflict is not None
                                  else f"G8 merge failed: {_describe(exc)}")
                        return {"status": "skipped", "base": base,
                                "reason": reason,
                                "sources": source_ids, "merged": None}
                    merged = Skill(
                        skill_id=ids.make_merged_skill_id(base["serial"]),
                        content=content,
                        # A merged skill spans several subtasks; the anchor
                        # is only a stable reference -- its regression exams
                        # come from merged_from, resolved transitively.
                        subtask_id=sources[0].subtask_id,
                        # The union of the sources' own sources, computed
                        # once here (schema v2): readers never re-derive
                        # it, and a v1 record migrated later rebuilds the
                        # same value (models.migrate_skill_record).
                        source_subtasks=merged_source_subtasks(
                            source.source_subtasks for source in sources),
                        version=1,
                        status="pending",
                        merged_from=source_ids,
                    )
                    store.save(merged, "skills")  # commit step 1

                discard_reason = "regression_failed"
                if merge_check == "none":
                    # merge_check: none -- the product goes pending ->
                    # accepted without any regression exam (M3): zero
                    # answerer/judge calls; the marker gates above and the
                    # budget valve below are unchanged.
                    passed, reason = True, ""
                else:
                    try:
                        passed, reason = _regression_passes(
                            merged, exams, store, answerer_handle,
                            judge_handle, usage, run_dir)
                    except InfrastructureError as exc:
                        record_failure(f"{unit_id}/regression", exc)
                        raise
                    except ModelOutputError as exc:
                        record_failure(f"{unit_id}/regression", exc)
                        passed = False
                        reason = f"regression exam unusable: {_describe(exc)}"
                        discard_reason = "regression_error"
                return {"status": "kept" if passed else "reverted",
                        "base": base, "sources": source_ids, "merged": merged,
                        "reason": "" if passed else reason,
                        "discard_reason": discard_reason}

            fatal: Optional[BaseException] = None
            try:
                outcomes = run_units(unit_worker, todo, workers)
            except BaseException as exc:  # noqa: BLE001 - re-raised below
                # run_units' fatal rule: nothing new starts, in-flight
                # units finish. Their outcomes ride the exception; commit
                # them BEFORE re-raising so the stop is clean (a real kill
                # loses them, and the resume re-derives them from the
                # stored pending merges and attempts instead).
                fatal = exc
                outcomes = getattr(exc, "outcomes", None)
            if outcomes is not None:
                for unit_index, outcome in zip(todo, outcomes):
                    if outcome is UnitSkipped:
                        continue
                    commit(unit_index, outcome)
                    decided.add(unit_index)
            if fatal is not None:
                raise fatal

        # G9 grouping over the surviving members (this round's merges
        # included). G9 runs even on a revert-only or zero-merge round
        # (README stage 5 step 4 groups the bank whenever it has >= 2
        # members): the members are the same but their groups are still
        # part of the bank's shape.
        if round_record.get("grouping") is None:
            try:
                group_map = assign_groups(
                    members, curator_handle, usage=usage, run_dir=run_dir)
            except (InfrastructureError, ModelOutputError) as exc:
                record_failure(f"{unit_prefix}/grouping", exc)
                raise
            round_record["grouping"] = group_map
            _write_log(log, log_path)
        group_map = round_record["grouping"]
        for skill in members:
            group = group_map.get(skill.skill_id) \
                if isinstance(group_map, dict) else None
            skill.group = group if isinstance(group, str) and group else None
            store.save(skill, "skills")

        round_record["done"] = True
        round_record["stop"] = count_bank_tokens(members) <= bank_token_budget
        _write_log(log, log_path)
        if round_record["stop"]:
            break

    if legacy_in_flight is not None:
        # The journal ended without reaching this merge's unit: roll back.
        _apply_reverted(store, legacy_in_flight, "interrupted_merge")

    bank_tokens = count_bank_tokens(members)
    log["bank_tokens"] = bank_tokens
    log["final_member_ids"] = [skill.skill_id for skill in members]

    if bank_tokens > bank_token_budget:
        # Over budget after the round cap: the stage stops, nothing is
        # auto-deleted; a human decides (new budget or new merge strategy).
        _write_log(log, log_path)
        raise ConfigError(
            "skill bank exceeds bank_token_budget after "
            f"{curation_rounds} curation rounds: {bank_tokens} > "
            f"{bank_token_budget}")

    # Write the log BEFORE the append loop: it carries final_member_ids, so
    # a crash mid-append can be completed on resume (see the resume path).
    _write_log(log, log_path)
    for skill in members:  # already sorted by skill_id
        _append_bank_row(store, dest, skill.to_dict())
    return _read_bank(store, dest)
