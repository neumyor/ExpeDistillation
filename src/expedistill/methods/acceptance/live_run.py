"""Stage 4, live_run mode: accept a task's skill group by real execution.

The live_run acceptance driver (stages/stage4_acceptance.py dispatches here by
config's acceptance mode): instead of a text exam, one acceptance unit is one
train task's GROUP of pending stage-3 candidates. The group universe comes
from K6's ``pending_groups``; one round injects the whole group into the weak
executor through K1 (``n_attempts`` real executions), the named
ACCEPTANCE_IMPROVE rule compares the result against the stage-1 weak bare
baseline, and two rounds are pinned (user ruling 8): round 0 over the full
group; a miss buys one G10 group attribution plus per-skill G11 revisions
(K6 drives both), then round 1 over the survivors; a second miss discards
them (docs/decisions/acceptance_live_run_20261014.md).

This module is the DRIVER (ruling A16): round sequencing, the K1 dispatch
and its re-dispatch set, the injection_audit first write, the
accepted-shortfall records, the summary and the stage-end raise. Every
per-group step -- baseline loading, judging, attribution, revision, the
no-op guard, the finalization sweep -- is a K6 function called with
explicit parameters; no ledger write or validation is re-implemented here.

The round is the barrier (ruling A7): ONE K1 big batch per round over every
group that still owes it, so K1's wave/heartbeat/stagger/cursor machinery
runs once per round instead of once per task, and phase derivation stays a
pure disk read. The price -- round 1 waits for the slowest round-0 group --
is paid knowingly (the per-task pipeline alternative was rejected as an
engineering preference, not a correctness claim).

Re-dispatch set (ruling A10, revision 3): a group is dispatched only when
it is NOT terminal AND its round store is incomplete (every expected unit
stored or an accepted hole). The "not terminal" half is what keeps
zero-execution terminals (weak_already_knows, live_injection_overflow) and
decided groups out of K1 forever after: the entry sweep finalizes them from
their verdict rows so they leave the pending universe, and a judged group's
store is complete by construction (judge_round refuses to judge an
incomplete one). The completeness test is skip-aware: a round whose every
unit is an accepted hole is complete without one stored trajectory (such a
round judges to live_undetermined).

Phase derivation is disk-only (decision section 2.4), so a resumed stage
re-enters exactly where the crash cut it:

* the entry sweep runs ``finalize_group`` over every pending group,
  finishing any commit a crash left hanging on a verdict row;
* the entry guard loads every group's baseline BEFORE anything executes
  (an unaccepted missing unit is an InfrastructureError pointing at stage
  1; a valid==0 baseline -- every trial unscored or an accepted hole,
  R2b's normal reachable state -- terminalizes the group
  live_undetermined with zero executions) and short-circuits an
  all-success baseline under a strict-family rule to weak_already_knows
  with zero executions
  (the strict-family limit lives in the decision rule the stage calls;
  judge_round enforces it too -- a pass@N-style rule can still improve
  on a rate-1.0 baseline);
* a group without a round-0 verdict row owes round 0 (dispatch when the
  store is incomplete, then judge);
* a group judged not better owes the attribution/revision pipeline until
  its plan fully landed on disk (``_revisions_complete``: version numbers
  and statuses are the proof). An infra-failed revision or attribution
  keeps the group there for the resume and out of round 1 -- dispatching a
  half-revised group would freeze a round-1 audit row the landing revision
  would then contradict;
* the no-op guard (ruling A4) runs at the round-0/round-1 seam, before any
  round-1 audit row is frozen: survivors whose rendered block is still
  byte-identical to round 0's frozen audit line are discarded
  (live_no_improvement) and round 1 never runs -- re-sampling a group
  until it lucks into a pass is what the guard exists to prevent. The
  guard writes no ledger row, so a crash mid-discard is completed from
  the one durable tell: a member already discarded live_no_improvement
  with no round-1 verdict row (the only other source of that reason, a
  round-1 miss, writes its verdict row before committing anything -- the
  verdicts row's round field is what tells the two apart, ruling A13).
  The frozen round-1 audit row is the durable record that a group's
  round-0 pipeline finished and the guard let it through -- the stage
  re-derives the phase from the ledgers on every call, the row is what
  an audit script can check;
* a group with a round-1 verdict row is decided; the sweep committed it.

injection_audit (ruling A10): before a round's dispatch the stage freezes
every dispatch-set group's row (content from K6's ``audit_row`` -- one
constructor for the writer and the checker, so they cannot drift) and a
resumed stage re-derives the row and compares byte for byte; a mismatch is
a CodeBugError (the store or the stage drifted under the frozen line). An
injection over ``max_injection_tokens`` never reaches K1: the overflow
verdict (carrying the audit row's own token evidence) terminalizes the
group as live_injection_overflow with zero executions.

Shortfall (ruling A8, section 2.5): live units that exhaust their infra
retries are holes like stage 1's. Without ``--allow-infra-shortfall`` the
group stays pending, its units are listed under ``infra_missing`` and the
stage raises InfrastructureError at the end (exit 3: resumable as-is).
With the flag the holes are durably accepted per round
(``live_run/roundNN/accepted_infra_shortfall.json``, written BEFORE any
judgment of the shrunken sample), the judgment runs on the remaining
valid sample and the verdict row carries ``judged_with_shortfall``; a
round whose every unit is a hole judges to live_undetermined. Each
round's skip set is stage 1's accepted weak-arm holes of this task UNION
that round's own accepted holes (ruling A9); round 1 deliberately does
NOT inherit round 0's -- its remote sandbox is a fresh instance, so
"round 0 was broken" says nothing about round 1 (a policy of skipping
both would have to be pre-registered, not implemented as a side effect).

The summary (``stage4_summary.json``, the same filename the exam mode
writes) keeps the exam mode's top level -- ``accepted`` /
``discarded`` (reason -> count, CUMULATIVE from the stored statuses) /
``infra_failed`` / ``processed_this_call`` -- and adds the live view:
``mode``, ``improvement_rule``, per-group details (both rounds' verdicts
and injection tokens), ``infra_missing`` / ``infra_accepted`` per round
and the pipeline's ``infra_shortfall_accepted`` flag. live's
``infra_failed`` lists TASK ids (a live unit is a group, not a skill) and
there is no ``skipped_missing_exam``; a model call whose result was not
yet persisted is asked again on resume (the standing K3/K4 exemption --
K6's docstring spells out every crash window).
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from expedistill.benchmarks.aux import aux_wiring
from expedistill.benchmarks.registry import get_plugin
from expedistill.blocks import tasks as tasks_block
from expedistill.blocks.rules import get_acceptance_improve
from expedistill.composites import (
    live_attribution as lat,
    live_measurement as lm,
    live_verdicts as lv,
)
from expedistill.composites import shortfall
from expedistill.composites.decision import (
    live_injection_overflows,
    live_zero_execution_short_circuit,
)
from expedistill.composites.skill_status import (
    discard_skill,
    stage3_candidates,
    tally_verdicts,
)
from expedistill.composites.batch_execute import batch_execute
from expedistill.core import ids
from expedistill.core.models import Skill
from expedistill.infra.parallel import run_units
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    UnitFailureRecorder,
)
from expedistill.infra.store import Store, append_jsonl, read_jsonl, write_json
from expedistill.prompts import get as get_prompt
from expedistill.methods import resolve_fixed
from expedistill.methods.acceptance import (LiveRunParams, STAGE4_DIR,
                                         SUMMARY_FILENAME)

__all__ = ["SHORTFALL_NAME", "run"]

#: The per-round accepted-hole record (decision section 2.5), inside the
#: round directory: same name and shape as stage 1's record, scoped to one
#: round because round 1's remote sandbox is a fresh instance.
SHORTFALL_NAME = shortfall.SHORTFALL_NAME


# ---------------------------------------------------------------------------
# Small readers over K6's on-disk state (the driver's dispatch-set inputs)
# ---------------------------------------------------------------------------

def _ledger_path(store: Store, name: str) -> Path:
    return store.run_dir / lm.LIVE_DIR_NAME / name


def _verdict_rows(store: Store, task_id: str) -> Dict[int, Dict[str, Any]]:
    """The group's verdict rows keyed by round -- the FIRST row per round
    sticks (K6's append-once discipline; a hand-added duplicate fails
    judge_round's recompute check instead of switching verdicts here)."""
    rows: Dict[int, Dict[str, Any]] = {}
    for row in read_jsonl(_ledger_path(store, lm.VERDICTS_LEDGER)):
        if row.get("task_id") == task_id and row.get("round") not in rows:
            rows[row["round"]] = row
    return rows


def _attribution_row(store: Store, task_id: str,
                     round_: int) -> Optional[Dict[str, Any]]:
    """The group's stored attribution row of one round, or None (no row =
    the plan never landed, so G10 is asked again -- its result was never
    persisted, the standing resume exemption)."""
    for row in read_jsonl(_ledger_path(store, lm.ATTRIBUTIONS_LEDGER)):
        if (row.get("task_id") == task_id
                and row.get("round") == round_):
            return row
    return None


def _frozen_audit_row(store: Store, round_: int,
                       task_id: str) -> Optional[Dict[str, Any]]:
    """The group's frozen injection-audit row of one round, or None when
    the round was never dispatched for it."""
    for row in read_jsonl(lm.round_dir(store, round_)
                          / lm.INJECTION_AUDIT_NAME):
        if row.get("task_id") == task_id:
            return row
    return None


def _canonical_line(row: Dict[str, Any]) -> str:
    """The row as append_jsonl writes it: the frozen line and the
    re-derivation are compared as these exact bytes, never as loosely
    equal objects (ruling A10's byte-for-byte freeze)."""
    return json.dumps(row, ensure_ascii=False, sort_keys=True)


def _group_members(store: Store, task_id: str) -> List[Skill]:
    """Every candidate of one task, terminal members included (the tally
    and the no-op crash completion read the whole group, pending-only
    derivations go through K6's ``pending_groups``)."""
    members = []
    for skill in stage3_candidates(store):
        try:
            trajectory_id, _ = ids.parse_subtask_id(skill.subtask_id)
            if ids.parse_trajectory_id(trajectory_id)[0] == task_id:
                members.append(skill)
        except ValueError:
            continue
    return members


def _expected_units(task_id: str, n_attempts: int) -> List[str]:
    """The group's full unit-id set for one round, in attempt order."""
    return [ids.make_trajectory_id(task_id, "weak", attempt)
            for attempt in range(n_attempts)]


def _stored_units(store: Store, task_id: str,
                  round_: int) -> Set[str]:
    """The unit ids one round's store holds for one task (empty when the
    round directory was never created)."""
    directory = lm.round_dir(store, round_)
    if not directory.exists():
        return set()
    round_store = Store(directory)
    return {unit for unit in round_store.ids("trajectories")
            if ids.parse_trajectory_id(unit)[0] == task_id}


def _round_complete(store: Store, task_id: str, round_: int, n_attempts: int,
                    skip: Sequence[str]) -> bool:
    """Ruling A10's completeness half: every expected unit is stored or an
    accepted hole. Skip-aware on purpose -- the all-holes round is
    complete without a single trajectory (it judges to live_undetermined),
    and an incomplete-but-accepted group must never re-enter K1."""
    stored = _stored_units(store, task_id, round_)
    skipped = set(skip)
    return all(unit in stored or unit in skipped
               for unit in _expected_units(task_id, n_attempts))



def _round_holes(store: Store, task_ids: Sequence[str], round_: int,
                 n_attempts: int, stage1_holes: Sequence[str],
                 accepted: Sequence[str]) -> List[str]:
    """The round's still-unaccepted holes (expected, neither stored nor
    accepted), sorted -- the units a resume retries or the flag accepts."""
    holes: Set[str] = set()
    for task_id in task_ids:
        skip = set(lm.skip_ids_for(stage1_holes, accepted, task_id))
        holes.update(unit for unit in _expected_units(task_id, n_attempts)
                     if unit not in skip)
    holes -= _union_of_stored(store, task_ids, round_)
    return sorted(holes)


def _union_of_stored(store: Store, task_ids: Sequence[str],
                     round_: int) -> Set[str]:
    stored: Set[str] = set()
    for task_id in task_ids:
        stored |= _stored_units(store, task_id, round_)
    return stored


# ---------------------------------------------------------------------------
# Phase-derivation helpers
# ---------------------------------------------------------------------------

def _revisions_complete(store: Store, task_id: str) -> bool:
    """Whether the group's round-0 attribution plan fully landed on disk
    (decision section 2.4: version numbers and statuses are the proof --
    there is no "revisions done" flag to read). A group whose plan has not
    landed owes the round-0 pipeline (the resume re-enters K6's replay
    guards); only a complete plan may proceed to the round-1 seam."""
    row = _attribution_row(store, task_id, 0)
    if row is None:
        return False
    for entry in row["skill_actions"]:
        skill = store.load("skills", entry["skill_id"], Skill)
        if entry["action"] == "keep":
            continue
        if skill.status != "pending":
            # delete landed, or an exhausted revision discarded the skill
            continue
        if entry["action"] == "delete":
            return False
        # revise: still pending at the recorded version means the revision
        # never landed (an infra failure mid-plan); anything above
        # from_version + 1 is a store drift apply_revisions will refuse.
        if skill.version != entry["version"] + 1:
            return False
    return True


def _complete_no_op(store: Store, task_id: str) -> bool:
    """Resolve the no-op guard for a round-1 candidate; True when the group
    is done at round 0 (ruling A4: the survivors' rendered block still
    equals round 0's frozen audit line -> discard as live_no_improvement,
    round 1 must not run).

    The guard fires inside K6 (discarding every survivor). A crash in the
    middle leaves a SMALLER survivor set whose re-rendered block no longer
    matches the frozen line, so re-running the guard would wrongly send
    the half-discarded group to round 1. The durable tell that the guard
    already fired is a member discarded live_no_improvement with no
    round-1 verdict row: the only other source of that reason (a round-1
    miss) writes its verdict row BEFORE finalize commits anything, and
    such a group is terminal and out of the pending universe anyway.
    """
    if lv.no_op_guard(store, task_id, 0):
        return True
    if 1 in _verdict_rows(store, task_id):
        # Unreachable through the stage (the sweep finalized any decided
        # group); a pending group with a round-1 row means the store was
        # edited under the ledgers, which is not this function's call to
        # forgive -- K6's recompute check will name it at the next judge.
        return False
    members = _group_members(store, task_id)
    if not any(skill.status == "discarded"
               and skill.discard_reason == lv.LIVE_NO_IMPROVEMENT
               for skill in members):
        return False
    for skill in members:
        if skill.status == "pending":
            discard_skill(store, skill, lv.LIVE_NO_IMPROVEMENT)
    return True


# ---------------------------------------------------------------------------
# Task objects and the rendered instruction
# ---------------------------------------------------------------------------

def _task_objects(ctx: Any,
                  task_ids: Sequence[str]) -> Dict[str, Any]:
    """Rebuild the groups' Task objects through D1, keyed by the prefixed
    task id the groups carry (``raw_task_id`` strips the prefix the plugin
    never wants to see; the load is deterministic from the frozen config).
    A task coming back under an id the group never named is a ConfigError:
    the arms must never silently run a different set than the groups."""
    benchmark = ctx.config.benchmark
    raw_ids = sorted({ids.raw_task_id(task_id, benchmark)
                      for task_id in task_ids})
    loaded = tasks_block.load_tasks(benchmark, raw_ids)
    by_raw = {task.task_id: task for task in loaded}
    unexpected = sorted(set(by_raw) - set(raw_ids))
    if unexpected:
        raise CodeBugError(
            f"benchmark {benchmark!r} loaded tasks outside the live "
            f"groups' ids: {unexpected}")
    return {task_id: by_raw[ids.raw_task_id(task_id, benchmark)]
            for task_id in task_ids}


def _task_instruction(plugin: Any, task: Any) -> str:
    """The rendered task prompt (decision section 2.3): the execution
    prompt shape the student saw at step 0 -- instruction slot filled,
    skills and history slots empty. The group's skills reach the attributor
    separately (``skills_listing``) and the attempts' evidence as
    ``live_views``, so filling those slots here would only duplicate them.
    Under TB the Task's instruction is just the task name; the full
    instruction reaches the attributor through the views' first-step
    context (recorded in the prompt's design note, not papered over)."""
    return get_prompt(plugin.task_prompt_name).format(
        instruction=task.instruction, skills_block="", history="")


def _round_detail(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """One round's verdict as the summary reports it (the verdicts ledger
    stays the audit's complete fact source; the summary carries the
    decision-relevant fields)."""
    if row is None:
        return None
    return {
        "baseline": row["baseline"],
        "live": row["live"],
        "better": row["better"],
        "verdict": row.get("verdict"),
        "aux": row["aux"],
        "injection_tokens": row["injection_tokens"],
        "judged_with_shortfall": row["judged_with_shortfall"],
    }


# ---------------------------------------------------------------------------
# The driver
# ---------------------------------------------------------------------------

def run(ctx: Any, params: LiveRunParams) -> Dict[str, Any]:
    """Drive both live rounds over every group that still owes work.

    Returns and stores the stage-4 summary (module docstring); raises
    InfrastructureError AFTER storing it when any group is still pending
    on infrastructure errors (exit 3 -- the same disposition as stages 1,
    4-exam and 6: re-running the same command resumes and retries only
    the holes).
    """
    config = ctx.config
    store = ctx.store
    n_attempts = params.n_attempts
    rule_name = params.improvement
    max_injection_tokens = params.max_injection_tokens
    # Stage 0 validated the name against the registry; resolving it again
    # keeps a direct call honest for the same one-line cost.
    get_acceptance_improve(rule_name)

    # The auxiliary metric's NAME is frozen in the manifest (stage 0); its
    # COUNTS ride the verdict rows' aux slot (user ruling 11), which the
    # summary's per-group detail reports verbatim.
    _, aux_scorer = aux_wiring(config.benchmark)
    plugin = get_plugin(config.benchmark)
    view_rules = plugin.view_rules
    stage1_holes = shortfall.stage1_accepted_unit_ids(ctx.run_dir)
    recorder = UnitFailureRecorder(ctx.run_dir)

    # -- finalization sweep: finish any commit a crash left hanging -----
    for task_id in sorted(lm.pending_groups(store)):
        lv.finalize_group(store, task_id)

    # -- entry guards: baselines before a single execution is spent -----
    universe = sorted(lm.pending_groups(store))
    tasks_by_id = _task_objects(ctx, universe)
    baselines: Dict[str, Dict[str, int]] = {}
    for task_id in universe:
        _, baseline_counts = lm.load_baseline(
            store, task_id, n_attempts, aux_scorer=aux_scorer)
        baselines[task_id] = baseline_counts
        if (0 not in _verdict_rows(store, task_id)
                and baseline_counts["valid"] == 0):
            # R2b: the baseline itself was never measured (its stage-1
            # trials all came back without a scorer verdict, or are all
            # accepted holes) -- no comparison can exist, ever. Zero
            # executions: the group is terminal not_testable
            # (live_undetermined); judge_round verifies the fact before
            # writing the row.
            lv.judge_round(
                store=store, task_id=task_id, round_=0, rule_name=rule_name,
                baseline_counts=baseline_counts, n_attempts=n_attempts,
                skip_ids=[], terminal=lv.LIVE_UNDETERMINED,
                max_injection_tokens=max_injection_tokens)
            lv.finalize_group(store, task_id)
        elif (0 not in _verdict_rows(store, task_id)
                # The strict-family limit lives in the decision rule
                # (judge_round enforces it too), so a pass@N-style rule
                # still gets executed.
                and live_zero_execution_short_circuit(
                    rule_name=rule_name, baseline_counts=baseline_counts)):
            lv.judge_round(
                store=store, task_id=task_id, round_=0, rule_name=rule_name,
                baseline_counts=baseline_counts, n_attempts=n_attempts,
                skip_ids=[], terminal=lv.WEAK_ALREADY_KNOWS,
                max_injection_tokens=max_injection_tokens)
            lv.finalize_group(store, task_id)

    infra_failed: Set[str] = set()

    # ======================= round 0 ==================================
    groups0 = sorted(lm.pending_groups(store))
    unjudged0 = [task_id for task_id in groups0
                 if 0 not in _verdict_rows(store, task_id)]
    accepted0 = shortfall.read_unit_ids(lm.round_dir(store, 0) / SHORTFALL_NAME)

    def skip0_for(task_id: str) -> List[str]:
        return lm.skip_ids_for(stage1_holes, accepted0, task_id)

    dispatch0: List[str] = []
    for task_id in unjudged0:
        if _round_complete(store, task_id, 0, n_attempts, skip0_for(task_id)):
            # A previous call stored everything (or accepted every hole);
            # the group owes only its judgment. Its audit row was frozen
            # before that dispatch and is never re-derived here -- a
            # judged group's members have moved (revisions), so only the
            # judging path may read the frozen line.
            continue
        row = lm.audit_row(store, task_id, 0)
        frozen = _frozen_audit_row(store, 0, task_id)
        if frozen is not None:
            if _canonical_line(frozen) != _canonical_line(row):
                raise CodeBugError(
                    f"the frozen injection_audit row of task {task_id!r} "
                    "round 0 differs from the re-derivation:\nstored: "
                    f"{_canonical_line(frozen)}\nrecomputed: "
                    f"{_canonical_line(row)}; the group's pending members "
                    "changed under the frozen line (a revision landed "
                    "before its round was judged) -- refusing to re-dispatch")
        else:
            # First write, before the dispatch (ruling A10): the audit
            # names the exact injection this dispatch is about to render.
            append_jsonl(lm.round_dir(store, 0) / lm.INJECTION_AUDIT_NAME,
                         row)
        if live_injection_overflows(
                max_injection_tokens=max_injection_tokens,
                injection_tokens=row["injection_tokens"]):
            # Zero executions wasted: the overflow verdict carries the
            # audit row's own evidence and the group never enters K1.
            lv.judge_round(
                store=store, task_id=task_id, round_=0, rule_name=rule_name,
                baseline_counts=baselines[task_id], n_attempts=n_attempts,
                skip_ids=[], terminal=lv.LIVE_INJECTION_OVERFLOW,
                max_injection_tokens=max_injection_tokens)
            lv.finalize_group(store, task_id)
            continue
        dispatch0.append(task_id)

    if dispatch0:
        _dispatch_round(ctx, store, round_=0, task_ids=dispatch0,
                        tasks_by_id=tasks_by_id, n_attempts=n_attempts)

    # Shortfall first (durable BEFORE any judgment of the shrunken sample),
    # then the judge set on the updated skip sets.
    accepted0 = _accept_shortfall(ctx, store, round_=0, task_ids=[
        task_id for task_id in unjudged0
        if 0 not in _verdict_rows(store, task_id)], n_attempts=n_attempts,
        stage1_holes=stage1_holes)

    units0: List[str] = []
    for task_id in unjudged0:
        if 0 in _verdict_rows(store, task_id):
            continue  # the overflow terminal fired in the dispatch loop
        if _round_complete(store, task_id, 0, n_attempts, skip0_for(task_id)):
            units0.append(task_id)
        else:
            infra_failed.add(task_id)
    # Groups whose round-0 verdict says "not better" but whose plan has
    # not fully landed owe the attribution/revision pipeline (an infra
    # failure in it keeps them here for the resume, out of round 1).
    for task_id in groups0:
        verdict = _verdict_rows(store, task_id).get(0)
        if (verdict is not None and "verdict" not in verdict
                and verdict["better"] is False
                and not _revisions_complete(store, task_id)):
            units0.append(task_id)

    def round0_unit(task_id: str):
        """One group's whole round-0 pipeline: judge, then (on a miss) G10
        attribution and G11 revisions through K6. InfrastructureError is
        the unit's expected failure (the group stays pending, is
        recorded, and the stage raises at its end); anything escaping is
        fatal to the batch by run_units' rule."""
        try:
            verdict = lv.judge_round(
                store=store, task_id=task_id, round_=0, rule_name=rule_name,
                baseline_counts=baselines[task_id], n_attempts=n_attempts,
                skip_ids=skip0_for(task_id), aux_scorer=aux_scorer,
                max_injection_tokens=max_injection_tokens)
            if verdict.get("better") or verdict.get("verdict") is not None:
                # better -> accept; live_undetermined (all holes) ->
                # discard: the verdict row is the decision either way.
                lv.finalize_group(store, task_id)
                return ("processed", task_id)
            plan = lat.attribute_group(
                store=store, task_id=task_id, round_=0,
                n_attempts=n_attempts,
                task_instruction=_task_instruction(
                    plugin, tasks_by_id[task_id]),
                weak_profile=config.weak_profile,
                author_handle=ctx.handle("author"),
                view_rules=view_rules, usage=ctx.usage, run_dir=ctx.run_dir)
            if plan is None:
                # The G10 budget ran out; the terminal model_output_error
                # discards landed inside (the discard is the truth, the
                # failure line its receipt).
                return ("processed", task_id)
            revisions = lat.apply_revisions(
                store=store, task_id=task_id, round_=0,
                rewriter_handle=ctx.handle("rewriter"),
                weak_profile=config.weak_profile,
                view_rules=view_rules, usage=ctx.usage, run_dir=ctx.run_dir)
            if any(outcome["outcome"] == "infra_failed"
                   for outcome in revisions):
                return ("infra_failed", task_id)
            return ("awaiting_round1", task_id)
        except InfrastructureError as exc:
            recorder.record(task_id, lat.FAILURE_STAGE, exc, exc)
            return ("infra_failed", task_id)

    for kind, task_id in run_units(round0_unit, sorted(set(units0)),
                                   ctx.run_settings.workers):
        if kind == "infra_failed":
            infra_failed.add(task_id)

    # ======================= round 1 ==================================
    groups1 = sorted(lm.pending_groups(store))
    candidates1: List[str] = []
    for task_id in groups1:
        rows = _verdict_rows(store, task_id)
        round0 = rows.get(0)
        if (round0 is None or "verdict" in round0
                or round0["better"] is not False or 1 in rows):
            continue
        if not _revisions_complete(store, task_id):
            continue
        candidates1.append(task_id)

    accepted1 = shortfall.read_unit_ids(lm.round_dir(store, 1) / SHORTFALL_NAME)

    def skip1_for(task_id: str) -> List[str]:
        return lm.skip_ids_for(stage1_holes, accepted1, task_id)

    guard_passed1: List[str] = []
    dispatch1: List[str] = []
    for task_id in candidates1:
        if _complete_no_op(store, task_id):
            continue
        guard_passed1.append(task_id)
        if _round_complete(store, task_id, 1, n_attempts, skip1_for(task_id)):
            continue  # a previous call's round-1 batch already filled it
        row = lm.audit_row(store, task_id, 1)
        frozen = _frozen_audit_row(store, 1, task_id)
        if frozen is not None:
            if _canonical_line(frozen) != _canonical_line(row):
                raise CodeBugError(
                    f"the frozen injection_audit row of task {task_id!r} "
                    "round 1 differs from the re-derivation:\nstored: "
                    f"{_canonical_line(frozen)}\nrecomputed: "
                    f"{_canonical_line(row)}; the survivors changed under "
                    "the frozen line -- refusing to re-dispatch")
        else:
            append_jsonl(lm.round_dir(store, 1) / lm.INJECTION_AUDIT_NAME,
                         row)
        if live_injection_overflows(
                max_injection_tokens=max_injection_tokens,
                injection_tokens=row["injection_tokens"]):
            lv.judge_round(
                store=store, task_id=task_id, round_=1, rule_name=rule_name,
                baseline_counts=baselines[task_id], n_attempts=n_attempts,
                skip_ids=[], terminal=lv.LIVE_INJECTION_OVERFLOW,
                max_injection_tokens=max_injection_tokens)
            lv.finalize_group(store, task_id)
            continue
        dispatch1.append(task_id)

    if dispatch1:
        _dispatch_round(ctx, store, round_=1, task_ids=dispatch1,
                        tasks_by_id=tasks_by_id, n_attempts=n_attempts)

    accepted1 = _accept_shortfall(ctx, store, round_=1, task_ids=[
        task_id for task_id in guard_passed1
        if 1 not in _verdict_rows(store, task_id)], n_attempts=n_attempts,
        stage1_holes=stage1_holes)

    units1: List[str] = []
    for task_id in guard_passed1:
        if 1 in _verdict_rows(store, task_id):
            continue  # the overflow terminal fired in the dispatch loop
        if _round_complete(store, task_id, 1, n_attempts, skip1_for(task_id)):
            units1.append(task_id)
        else:
            infra_failed.add(task_id)

    def round1_unit(task_id: str):
        """One survivor group's round-1 judgment and commit: the verdict
        row decides (better -> accept, miss -> live_no_improvement, all
        holes -> live_undetermined) and the sweep body commits it."""
        try:
            lv.judge_round(
                store=store, task_id=task_id, round_=1, rule_name=rule_name,
                baseline_counts=baselines[task_id], n_attempts=n_attempts,
                skip_ids=skip1_for(task_id), aux_scorer=aux_scorer,
                max_injection_tokens=max_injection_tokens)
            lv.finalize_group(store, task_id)
            return ("processed", task_id)
        except InfrastructureError as exc:
            recorder.record(task_id, lat.FAILURE_STAGE, exc, exc)
            return ("infra_failed", task_id)

    for kind, task_id in run_units(round1_unit, units1, ctx.run_settings.workers):
        if kind == "infra_failed":
            infra_failed.add(task_id)

    # ======================= summary ==================================
    candidates = stage3_candidates(store)
    summary = tally_verdicts(store, candidates)
    pending_now = lm.pending_groups(store)
    processed = sum(1 for task_id in universe if not pending_now.get(task_id))

    detail_tasks = sorted(
        set(universe)
        | {row["task_id"] for row in read_jsonl(
            _ledger_path(store, lm.VERDICTS_LEDGER))})
    group_details: Dict[str, Any] = {}
    for task_id in detail_tasks:
        rows = _verdict_rows(store, task_id)
        members = _group_members(store, task_id)
        statuses = [skill.status for skill in members]
        if members and all(status in ("accepted", "merged")
                           for status in statuses):
            outcome = "accepted"
        elif members and all(status == "discarded" for status in statuses):
            outcome = "discarded"
        elif members and all(
                status != "pending" for status in statuses):
            # Fully terminal but mixed (e.g. one member deleted by the G10
            # plan or exhausted in G11 while the survivors passed round 1):
            # the group is done, it is not pending, and "accepted"/
            # "discarded" would each hide half the truth (three-reviewer
            # round fix -- this used to report "pending").
            outcome = "mixed"
        else:
            outcome = "pending"
        reasons: Dict[str, int] = {}
        for skill in members:
            if skill.status == "discarded":
                reason = skill.discard_reason or "unspecified"
                reasons[reason] = reasons.get(reason, 0) + 1
        group_details[task_id] = {
            "round0": _round_detail(rows.get(0)),
            "round1": _round_detail(rows.get(1)),
            "outcome": outcome,
            "discard_reasons": reasons,
        }

    # What the run still owes, per round, over the groups that still owe
    # it (a terminal group's never-dispatched units are not holes).
    infra_missing: Dict[str, List[str]] = {"round00": [], "round01": []}
    for task_id in pending_now:
        rows = _verdict_rows(store, task_id)
        round0 = rows.get(0)
        if round0 is None:
            skip = set(skip0_for(task_id))
            infra_missing["round00"].extend(
                unit for unit in _expected_units(task_id, n_attempts)
                if unit not in skip
                and unit not in _stored_units(store, task_id, 0))
        elif ("verdict" not in round0 and round0["better"] is False
                and 1 not in rows and _revisions_complete(store, task_id)):
            skip = set(skip1_for(task_id))
            infra_missing["round01"].extend(
                unit for unit in _expected_units(task_id, n_attempts)
                if unit not in skip
                and unit not in _stored_units(store, task_id, 1))

    summary.update({
        "mode": "live_run",
        "improvement_rule": rule_name,
        "infra_failed": sorted(infra_failed),
        "processed_this_call": processed,
        "group_details": group_details,
        "infra_missing": infra_missing,
        "infra_accepted": {"round00": accepted0, "round01": accepted1},
        "infra_shortfall_accepted": bool(accepted0 or accepted1),
    })
    write_json(Path(ctx.run_dir) / STAGE4_DIR / SUMMARY_FILENAME, summary,
               indent=2)
    if infra_failed:
        raise InfrastructureError(
            f"stage 4 (live_run): {len(infra_failed)} group(s) still have "
            f"units pending on infrastructure errors "
            f"({', '.join(sorted(infra_failed))}); see failed_units.jsonl "
            "and the summary's infra_missing, then resume the run to "
            "retry them")
    return summary


# ---------------------------------------------------------------------------
# Dispatch and shortfall (the stage-owned writes)
# ---------------------------------------------------------------------------

def _dispatch_round(ctx: Any, store: Store, *, round_: int,
                    task_ids: List[str], tasks_by_id: Dict[str, Any],
                    n_attempts: int) -> None:
    """One K1 big batch for one round (ruling A7): every group that owes
    the round goes into ONE batch_execute call over the round's own store
    (round 1's re-execution never collides with round 0's trajectories),
    a per-task injection resolver, and the round's workdir subtree (the
    retry cursor is per workdir and trajectory ids repeat across rounds --
    sharing one tree would make round 1 inherit round 0's cursor).

    The resolver comes from the delivery registry (methods modules never
    import each other; the registry is the only seam): the group_by_task
    driver wraps K6's ``survivors_and_block``, the same derivation the
    frozen audit line carries."""
    config = ctx.config
    benchmark = config.benchmark
    stage1_holes = shortfall.stage1_accepted_unit_ids(ctx.run_dir)
    accepted = shortfall.read_unit_ids(lm.round_dir(store, round_) / SHORTFALL_NAME)
    skip_unit_ids: Set[str] = set()
    for task_id in task_ids:
        skip_unit_ids.update(
            lm.skip_ids_for(stage1_holes, accepted, task_id))
    delivery, delivery_params = resolve_fixed("delivery", "group_by_task")
    method_kwargs, _ = delivery.resolve(ctx, delivery_params, store,
                                        benchmark)
    batch_execute(
        store=Store(lm.round_dir(store, round_)),
        benchmark=benchmark,
        tasks=[tasks_by_id[task_id] for task_id in task_ids],
        role="weak",
        model_name=config.models["executor_weak"],
        handle=ctx.handle("executor_weak"),
        n_attempts=n_attempts,
        skills_for_task=method_kwargs["skills_for_task"],
        workdir_root=Path(ctx.workdir_root) / "stage4_live"
        / f"round{round_:02d}",
        usage=ctx.usage,
        run_dir=ctx.run_dir,
        workers=ctx.run_settings.workers,
        dispatch_stagger=ctx.run_settings.k1_stagger,
        skip_unit_ids=skip_unit_ids,
    )


def _accept_shortfall(ctx: Any, store: Store, *, round_: int,
                      task_ids: List[str], n_attempts: int,
                      stage1_holes: List[str]) -> List[str]:
    """Accept the round's still-unaccepted holes when the run setting says
    so, durably and BEFORE any judgment reads the shrunken sample; return
    the round's accepted ids either way.

    Without the flag the holes stay: the caller marks their groups
    infra-failed (pending for the resume) and the stage raises at its end.
    Acceptance is a per-run decision recorded per round (module
    docstring) -- a later call honours it with or without the flag because
    every skip set and completeness test reads this record from disk."""
    accepted = set(shortfall.read_unit_ids(lm.round_dir(store, round_) / SHORTFALL_NAME))
    holes = _round_holes(store, task_ids, round_, n_attempts, stage1_holes,
                         sorted(accepted))
    if holes and ctx.run_settings.allow_infra_shortfall:
        accepted.update(holes)
        shortfall.write_unit_ids(
            lm.round_dir(store, round_) / SHORTFALL_NAME, accepted)
    return sorted(accepted)
