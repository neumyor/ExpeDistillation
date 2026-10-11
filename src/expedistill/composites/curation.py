"""K5 bank curation (composite, layer 5): G7 -> G8 -> K3 -> G9 -> D10.

Owns the disk IO, resume and bookkeeping of stage 5 (README section 3): the
curator NEVER re-judges whether a skill is correct -- that was stage 4's job
-- it only maintains the bank: merge near-duplicates, combine conflicting
pairs, re-examine merges against every source skill's exam, assign
capability groups, and keep the injected bank under ``bank_token_budget``.

Invariants:

* a merged skill must pass the exam of EVERY skill it stands for before it
  may replace its sources (K3, purpose "regression"). Sources are resolved
  transitively through ``merged_from`` down to the original stage-3 skills,
  so a merge of a merge is re-examined on all the original exams, not only
  its anchor's. One failure reverts the whole merge -- the sources keep
  status "accepted", the merged skill is kept only as an audit record
  (status "discarded", reason "regression_failed");
* the bank is never silently shrunk: if curation cannot get under budget
  within ``curation_rounds`` rounds, the stage STOPS with ConfigError and a
  human decides. Nothing is auto-deleted;
* a merged-skill id is never reused: serials continue after the highest
  ``bank/merge/NNNN`` already stored, so a resumed call can never overwrite
  a merge an earlier call kept or reverted.

Resume is unit-level. ``curation_log.json`` is the journal: it records each
round's G7 output, every merge decision and each round's G9 output BEFORE
their effects are applied to the stored skills, so a resumed call replays
the journal instead of asking the models again, and finishes whatever effect
the crash interrupted. A merge commits in this order (crash-safe, see
docs/decisions/curation_resume_20261011.md):

  1. save the merged skill as "pending" (the G8 output is now durable; a
     pending skill is never a bank member);
  2. regression exams -- each Attempt is stored by K3 (the answer first,
     then the verdict); a resumed call reuses the JUDGED attempts already
     stored for this merged skill, and K3 itself completes an unjudged one
     by judging its stored answer;
  3. append the decision (kept / reverted) to the log -- the commit point;
  4. apply it: kept -> each source "merged", then the merged skill
     "accepted"; reverted -> the merged skill "discarded".

On resume, a logged decision whose merged skill is still "pending" is
re-applied (finalised); a pending merged skill with no logged decision is
the in-flight merge of the next unit and is reused (no second G8 call) if
its ``merged_from`` matches that unit's sources, otherwise rolled back.

Failure disposition per unit: InfrastructureError always propagates (the
stage stops; resume continues from disk -- skipping a merge because the
network blinked would make the bank depend on luck). ModelOutputError from
G8 or from a merge's regression exam affects only that merge: it is logged
as skipped (G8) or reverted (regression) and curation continues. G7 and G9
see the whole bank, so their ModelOutputError propagates. Every such
failure is also written to ``failed_units.jsonl`` (stage "stage5_curation").

The final append loop is healed the same way as before: the log records
``final_member_ids`` first and a resumed call completes a truncated append.
Staleness and corruption guards (cross-stage): earlier stages re-offer
failed units on every resume, so a later resume can accept NEW skills. The
log records the curation's input set (``input_skill_ids``, the accepted
skills at first start); every resume replays the journal and requires the
store's accepted skills to equal it (input minus kept merges' sources, plus
the kept merges). A new accepted stage-3 skill -> "skill bank is stale"; a
merged skill accepted without a logged keep, rewritten (version > 1), or a
logged member gone -> "corrupted run". A finished bank is likewise refused
when an accepted skill is not a member. Both are ConfigError: nothing is
ever merged into a curation uncurated. An in-flight merged skill is
"pending", never "accepted", so it never trips the guard.

The ``weak_profile`` parameter is accepted for signature uniformity with
the stage entry point but unused here: stage 5 runs no leak audit and no
correctness judgment by design (README stage 5).
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from expedistill.blocks.curate import assign_groups, find_similar, merge_skills
from expedistill.composites.exam_once import exam_once
from expedistill.core import ids
from expedistill.core.models import Attempt, Exam, Skill
from expedistill.infra.reliability import (
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import Store, read_json, write_json
from expedistill.inject import count_bank_tokens, format_skills_block
from expedistill.llm.call import ModelHandle

__all__ = ["curate_bank"]

#: Where the curation audit log lives inside the run directory.
LOG_DIR = "stage5_curation"
LOG_NAME = "curation_log.json"
#: Journal format; a log without it (or with another value) is refused on
#: resume rather than misread.
LOG_SCHEMA = "curation-log-v2"
#: Stage label in failed_units.jsonl.
FAILURE_STAGE = "stage5_curation"

_MERGED_ID_RE = re.compile(r"^bank/merge/(\d+)$")


# ---------------------------------------------------------------------------
# Audit log (the journal)
# ---------------------------------------------------------------------------

def _log_path(store: Store, run_dir: Optional[Union[str, Path]]) -> Path:
    # Falls back to the store's run directory: the log is the resume journal,
    # so curating without one is not an option.
    base = Path(run_dir) if run_dir is not None else store.run_dir
    return base / LOG_DIR / LOG_NAME


def _write_log(log: Dict[str, Any], path: Path) -> None:
    """Rewrite the audit log atomically; deterministic content (no
    timestamps)."""
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

    A log that exists but is not a v2 journal is refused: continuing from a
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


def _finalise_and_find_in_flight(store: Store, log: Dict[str, Any],
                                 log_lost: bool) -> Optional[Skill]:
    """Resume rule for merges a crash left half-done (README stage 5).

    * a merge whose decision is in the log but whose merged skill is still
      "pending" is finalised from the log (step 4 is re-applied);
    * a pending merged skill without a logged decision whose sources are
      already partly "merged": sources are marked only after a kept
      decision is logged, so with no log at all (``log_lost``) it is rolled
      FORWARD; with a log it cannot happen legitimately -> ConfigError;
    * the remaining pending merged skill (at most one) is the in-flight
      merge of the next unit; it is returned for reuse.
    """
    decided = {record["skill_id"]: record
               for record in log["merge_records"] if record.get("skill_id")}
    in_flight = []
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
        in_flight.append(merged)
    if len(in_flight) > 1:
        raise ConfigError(
            "more than one undecided merged skill in the store "
            f"({[skill.skill_id for skill in in_flight]}); K5 commits one "
            "merge at a time, so this run is inconsistent -- a human must "
            "look at it")
    return in_flight[0] if in_flight else None


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
    reused. Returns (passed, failure reason); K3's ModelOutputError and
    InfrastructureError propagate to the caller.
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
    member.
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
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """Curate the accepted skills into the final bank (README stage 5).

    Returns ``store.bank()``: the final member rows in sorted skill_id
    order. Idempotent at the stage level -- a non-empty bank (completed
    from the log first if a crash interrupted the final append) is returned
    without any model call, unless the store holds accepted skills the bank
    does not cover (ConfigError: the bank is stale) -- and resumable at the
    unit level (see module docstring). Raises ConfigError when the bank is still over budget after
    ``curation_rounds`` rounds; nothing is deleted in that case.

    ``weak_profile`` is part of the frozen stage signature but unused
    (see module docstring).
    """
    if curation_rounds < 1:
        raise ConfigError(f"curation_rounds must be >= 1, got {curation_rounds}")
    log_path = _log_path(store, run_dir)

    # -- resume: a written bank means the stage already finished -----------
    bank = store.bank()
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
                # stored skills (same dicts, same order, no model call).
                store.append_bank(
                    store.load("skills", skill_id, Skill).to_dict())
        bank = store.bank()
        _check_bank_not_stale(store, bank)
        return bank

    # -- resume: carry the journal forward, finish interrupted commits ------
    log, fresh = _load_log(log_path)
    _check_merged_versions(store)
    in_flight = _finalise_and_find_in_flight(store, log, log_lost=fresh)
    if fresh:
        # The curation's input set, fixed at first start: every later
        # resume checks the store against it (stale / corrupted run).
        log["input_skill_ids"] = sorted(
            skill.skill_id for skill in _accepted_members(store))
        _write_log(log, log_path)
    else:
        _check_store_matches_log(store, log)
    stored_merges = _stored_merges(store)
    next_serial = stored_merges[-1][0] + 1 if stored_merges else 0
    recorder = UnitFailureRecorder(
        Path(run_dir) if run_dir is not None else store.run_dir)

    members = _accepted_members(store)
    if not members:
        # An empty bank is a valid bank; nothing to curate or append. (An
        # in-flight merge implies accepted sources, so none can be pending.)
        _write_log(log, log_path)
        return []

    def record_merge(record: Dict[str, Any]) -> None:
        # The commit point of a unit: the decision is durable before any of
        # its effects touch the stored skills.
        log["merge_records"].append(record)
        counts = log["merges"]
        counts["attempted"] += 1
        counts[record["outcome"]] += 1
        _write_log(log, log_path)

    def record_failure(unit_id: str, exc: BaseException) -> None:
        recorder.record(unit_id, FAILURE_STAGE, exc, str(exc))

    rounds_by_index = {record["round"]: record for record in log["rounds"]}

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
                            "grouping": None, "done": False, "stop": False}
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

        decided_units = {record.get("unit")
                         for record in log["merge_records"]
                         if record.get("round") == round_index}
        for unit_index, (mode, group) in enumerate(units):
            if unit_index in decided_units:
                continue  # decided (and finalised) by an earlier call
            unit_id = f"{unit_prefix}/unit{unit_index:03d}"
            base = {"round": round_index, "unit": unit_index, "mode": mode}

            sources, skip = _resolve_group(group, members)
            if skip is None:
                exams, skip = _source_exams(store, sources)
            if skip is not None:
                record_merge(dict(base, **{
                    "sources": [s.skill_id for s in sources] if sources
                    else ([str(item) for item in group]
                          if isinstance(group, (list, tuple))
                          else [repr(group)]),
                    "skill_id": None, "outcome": "skipped", "reason": skip,
                }))
                continue
            source_ids = [s.skill_id for s in sources]

            merged = None
            if in_flight is not None:
                if in_flight.merged_from == source_ids:
                    merged = in_flight  # G8 output survived the crash
                else:
                    # Not this unit's merge: roll it back as an audit
                    # record. Its serial stays burnt (ids are never reused).
                    _apply_reverted(store, in_flight, "interrupted_merge")
                in_flight = None
            if merged is None:
                try:
                    content = merge_skills(
                        sources, curator_handle, mode=mode,
                        usage=usage, run_dir=run_dir).strip()
                except InfrastructureError as exc:
                    record_failure(f"{unit_id}/merge", exc)
                    raise
                except ModelOutputError as exc:
                    record_failure(f"{unit_id}/merge", exc)
                    record_merge(dict(base, **{
                        "sources": source_ids, "skill_id": None,
                        "outcome": "skipped",
                        "reason": f"G8 merge failed: {_describe(exc)}",
                    }))
                    continue
                merged = Skill(
                    skill_id=ids.make_merged_skill_id(next_serial),
                    content=content,
                    # A merged skill spans several subtasks; the anchor is
                    # only a stable reference -- its regression exams come
                    # from merged_from, resolved transitively.
                    subtask_id=sources[0].subtask_id,
                    version=1,
                    status="pending",
                    merged_from=source_ids,
                )
                next_serial += 1
                store.save(merged, "skills")  # commit step 1

            discard_reason = "regression_failed"
            try:
                passed, reason = _regression_passes(
                    merged, exams, store, answerer_handle, judge_handle,
                    usage, run_dir)
            except InfrastructureError as exc:
                record_failure(f"{unit_id}/regression", exc)
                raise
            except ModelOutputError as exc:
                record_failure(f"{unit_id}/regression", exc)
                passed = False
                reason = f"regression exam unusable: {_describe(exc)}"
                discard_reason = "regression_error"

            if passed:
                record_merge(dict(base, **{
                    "sources": source_ids, "skill_id": merged.skill_id,
                    "outcome": "kept", "reason": "",
                }))
                _apply_kept(store, merged)
                members = [skill for skill in members
                           if skill.skill_id not in source_ids]
                members.append(merged)
                members.sort(key=lambda skill: skill.skill_id)
            else:
                record_merge(dict(base, **{
                    "sources": source_ids, "skill_id": merged.skill_id,
                    "outcome": "reverted", "reason": reason,
                    "discard_reason": discard_reason,
                }))
                _apply_reverted(store, merged, discard_reason)

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

    if in_flight is not None:
        # The journal ended without reaching this merge's unit: roll back.
        _apply_reverted(store, in_flight, "interrupted_merge")

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
        store.append_bank(skill.to_dict())
    return store.bank()
