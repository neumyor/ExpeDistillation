"""The learn phase's task-list applier (layer 7).

Everything between the decision and the bank: the multi-version merge,
the per-row landing (keep / delete / revise / add) and the resume
ledgers. Split out of phase_learn.py because the driver exceeded the
plan's 400-line split rule (repair plan section 8) -- phase_learn.py
keeps the phase wiring (reports, sequence, curation, summary), this
module owns what happens to ONE decided target.

**The multi-version merge**: the exam report reader emits one row
per (skill, version) the run-level attempts store ever judged, so one
skill can carry several task-list rows -- including contradictory ones
(an old version's miss next to the current version's pass, or an old
version's leak beside a newer version's verdict).
:func:`merge_task_list` keeps ONE effective action per skill: the row
of the HIGHEST version decides (the newest content is what the bank
holds and what the next phase re-measures; an older version's
measurement or leak verdict says nothing about it), every lower-version
row is dropped as superseded. Visibility rows (zero_execution) never
reach the merge -- the decision entry already skips them.

**Landing**: keep accepts the pending target(s) -- the bank's
"accepted" is the learn phase's verdict, measurement validity travels
in the versions and reports; delete discards pending or accepted
members with the decision's reason (an accepted skill re-measured into
a miss is deleted exactly like a pending one; already-terminal skills
keep their own earlier truth -- rows pointing at them are visibility
leftovers of the run-level attempts store, skipped and recorded, never
resurrected); a revise that produced no new version is NO CHANGE
(``decision.revise_outcome``, boundaries section 4.3 rule 2) -- the
version stays, nothing is discarded, the next learn phase tries again;
add distills the task's strong segments into new PENDING skills, the
stage-3 grain and status exactly as stage 3 always wrote them -- and
produces those segments first when they do not exist yet (the evidence
family's segmentation wiring, ``methods.evidence.segmentation``; Wave
3-S), so the whole chain from the D-phase trajectories to the new
skill runs inside the phase world with nothing seeded.

**Evidence and author** (boundaries sections 2.5/2.6): a revise row
collects fact-only evidence packages and hands them to the author
driver. A group row (live) goes through
``methods.evidence.live_group.collect_group_evidence`` plus
``methods.author.write.revise_group``; a skill row (exam) goes through
``methods.evidence.exam_sheet.collect_exam_evidence`` (the exam, the
judged attempt's weak answer and judge reason -- evidence gathering
belongs to the evidence family).

**Resume** is the replay guard on a per-phase ledger: one
``revisions.jsonl`` row per skill BEFORE the author call records
``from_version``; a resumed call finds the skill at
``from_version + 1`` and knows the revision already landed (the
version number is the proof of completion, decision section 2.4),
finds a receipt row in ``outcomes.jsonl`` and skips the skill
outright, and re-asks only what neither covers (the standing K3/K4
model-call exemption). The receipt guard covers every action: keep /
delete / revise skip members that already carry a receipt, and an add
target with any receipt is done -- a re-derived row never writes a
second receipt, so a resumed phase's ledger equals an uninterrupted
one's byte for byte (on top of that, the landings themselves are
idempotent: statuses and reasons rewritten to the same bytes, the add
path skips experiences whose skill already exists).
The ledgers live in the phase's own directory beside the bank
artifact (composites.phase_bank's layout):

* ``task_list.jsonl`` -- the effective task list, one row per target,
  with the superseded rows named (rewritten whole on every call: the
  decision is a pure function of frozen on-disk reports, so a resume
  re-derives the same bytes);
* ``revisions.jsonl`` -- the per-skill revision plan rows;
* ``outcomes.jsonl`` -- one receipt row per landed skill (schema
  ``learn-outcome-v1``, fields ``phase_label`` / ``target_kind`` /
  ``target_id`` / ``action`` / ``skill_id`` (null on a target-level
  row) / ``outcome`` / ``version``) -- the successor to the old
  attributions.jsonl; the audit scripts and golden regeneration of
  the later waves align to THIS shape.
"""

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from expedistill.benchmarks.registry import get_plugin
from expedistill.blocks import tasks as tasks_block
from expedistill.composites import decision
from expedistill.composites.live_measurement import pending_groups
from expedistill.composites.phase_bank import phase_bank_dir
from expedistill.composites.skill_status import discard_skill
from expedistill.core import ids
from expedistill.core.config import ExecutePhaseSpec, PhaseInstance
from expedistill.core.contracts import AcceptanceReportRow, TaskListRow
from expedistill.core.models import Skill
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import (
    Store,
    append_jsonl,
    read_jsonl,
    write_jsonl,
)
from expedistill.methods import resolve_params
from expedistill.methods.author import write as author
from expedistill.methods.evidence import add_path, exam_sheet, live_group
from expedistill.methods.evidence import segmentation
from expedistill.methods.evidence.package import EvidencePackage
from expedistill.prompts import get as get_prompt

__all__ = [
    "OUTCOMES_NAME",
    "REVISIONS_NAME",
    "TASK_LIST_NAME",
    "execute_rows",
    "merge_task_list",
]

#: The learn phase's own files inside ``phases/<label>/``.
TASK_LIST_NAME = "task_list.jsonl"
REVISIONS_NAME = "revisions.jsonl"
OUTCOMES_NAME = "outcomes.jsonl"

_TASK_LIST_SCHEMA = "learn-task-list-v1"
_REVISION_SCHEMA = "learn-revision-v1"
_OUTCOME_SCHEMA = "learn-outcome-v1"

#: The statuses a learn phase may still act on. "merged" is curation's
#: product and "discarded" is terminal: decision rows pointing at either
#: are visibility leftovers, never actions.
_ACTABLE_STATUSES = ("pending", "accepted")


# ---------------------------------------------------------------------------
# The D4 merge: one effective action per skill
# ---------------------------------------------------------------------------

def merge_task_list(rows: Sequence[TaskListRow],
                    report_rows: Sequence[AcceptanceReportRow]
                    ) -> Tuple[List[TaskListRow], List[str]]:
    """Collapse the multi-version rows of one skill into ONE effective
    action (module docstring); returns ``(effective rows, superseded
    basis units)``.

    Non-skill rows pass through untouched (a live phase reports one
    row per group). A report row missing from the inputs, or two rows
    claiming the same version, is a CodeBugError: the join is contract,
    never data to guess with.
    """
    by_unit = {row.unit_id: row for row in report_rows}
    best: Dict[str, Tuple[int, str]] = {}
    superseded: List[str] = []
    for row in rows:
        if row.target_kind != "skill":
            continue
        report_row = by_unit.get(row.basis_unit)
        if report_row is None:
            raise CodeBugError(
                f"task-list row for {row.target_id!r} names basis unit "
                f"{row.basis_unit!r}, which the current report does not "
                "carry: the decision and the merge disagree")
        version = exam_sheet.injected_version(report_row)
        current = best.get(row.target_id)
        if current is None:
            best[row.target_id] = (version, row.basis_unit)
        elif version > current[0]:
            superseded.append(current[1])
            best[row.target_id] = (version, row.basis_unit)
        elif version < current[0]:
            superseded.append(row.basis_unit)
        else:
            raise CodeBugError(
                f"two task-list rows for skill {row.target_id!r} share "
                f"version {version} ({current[1]!r} and {row.basis_unit!r}"
                "); the report carries one row per (skill, version)")
    dropped = set(superseded)
    effective = [row for row in rows if row.basis_unit not in dropped]
    return effective, superseded


# ---------------------------------------------------------------------------
# The executor: one row at a time
# ---------------------------------------------------------------------------

def execute_rows(ctx: Any, phase: PhaseInstance,
                 rows: Sequence[TaskListRow],
                 current_rows: Optional[List[AcceptanceReportRow]],
                 current_spec: ExecutePhaseSpec, current_round: int,
                 superseded: Sequence[str]) -> Set[str]:
    """Land every effective task-list row; return the infra-failed
    target ids (the caller raises on them after its summary).

    The ledger is written BEFORE the first landing. Per target: an
    InfrastructureError is recorded and the target keeps its state (the
    phase raises at its end -- exit 3, resumable as-is); a
    ModelOutputError that survived a driver's own repair budget is
    recorded and NEVER fatal -- the target gets nothing this round and
    the next learn phase or a resume tries again.
    """
    phase_dir = phase_bank_dir(ctx.run_dir, phase.label)
    recorder = UnitFailureRecorder(ctx.run_dir)
    _write_task_list(phase_dir, phase.label, rows, superseded)
    infra_failed: Set[str] = set()
    for row in rows:
        try:
            _execute_row(ctx, phase, row, current_rows, current_spec,
                         current_round, phase_dir)
        except InfrastructureError as exc:
            recorder.record(row.target_id, phase.label, exc, exc)
            infra_failed.add(row.target_id)
        except ModelOutputError as exc:
            recorder.record(row.target_id, phase.label, exc, exc)
    return infra_failed


def _execute_row(ctx: Any, phase: PhaseInstance, row: TaskListRow,
                 current_rows: Optional[List[AcceptanceReportRow]],
                 current_spec: ExecutePhaseSpec, current_round: int,
                 phase_dir) -> None:
    """Land one effective task-list row (every landing is idempotent:
    each action re-reads the store and rewrites the same bytes)."""
    if row.action == decision.KEEP:
        _land_keep(ctx, phase, row, phase_dir)
    elif row.action == decision.DELETE:
        _land_delete(ctx, phase, row, phase_dir)
    elif row.action == decision.ADD:
        _land_add(ctx, phase, row, phase_dir)
    elif row.action == decision.REVISE:
        _land_revise(ctx, phase, row, current_rows, current_spec,
                     current_round, phase_dir)
    else:
        raise CodeBugError(
            f"the task list carries action {row.action!r} on target "
            f"{row.target_id!r}; the closed vocabulary is keep / revise / "
            "delete / add")


def _target_members(store: Store, row: TaskListRow) -> List[Skill]:
    """The skills a keep/delete/revise row acts on: the named skill, or
    the task's whole pending group (the live phase's measurement unit;
    boundary section 3 rule 8 -- the verdict only reaches the pack)."""
    if row.target_kind == "skill":
        if not store.exists("skills", row.target_id):
            raise CodeBugError(
                f"task-list row targets skill {row.target_id!r}, which "
                "the store does not carry: the decision and the store "
                "disagree")
        return [store.load("skills", row.target_id, Skill)]
    if row.target_kind == "group":
        return list(pending_groups(store).get(row.target_id, []))
    raise CodeBugError(
        f"a {row.action!r} row targets {row.target_kind!r} "
        f"{row.target_id!r}; only a skill or a group is actionable")


def _receipted_skills(phase: PhaseInstance, row: TaskListRow,
                      phase_dir) -> Set[Any]:
    """The skill ids this target already receipted in this phase.

    The receipt is the proof of completion -- including for the
    resume-proof discipline: a re-derived row whose target landed in an
    earlier pass of the same phase must not write a SECOND receipt, or
    a resumed run's ledger differs from an uninterrupted one's byte for
    byte (the golden matrix's E5 crash-resume contract)."""
    return {record.get("skill_id")
            for record in _rows_of(phase_dir / OUTCOMES_NAME, phase.label,
                                   row.target_id)}


def _terminal_group_receipt(phase: PhaseInstance, row: TaskListRow,
                            phase_dir, members: Sequence[Skill],
                            receipted: Set[Any]) -> bool:
    """The row-level receipt for a keep/delete/revise row whose group holds
    no resolvable pending member: the member loop would land nothing and the
    row would end the phase receiptless -- the docstring's "skipped and
    recorded" must hold at the ROW level too. One target-level row (the
    add path's shape: no skill_id, no version); the receipt guard makes a
    replay skip, so a resumed ledger equals an uninterrupted one's byte
    for byte. Members this phase already receipted cover the row already
    (their receipts exist), so the row-level receipt is written only
    when the target has no receipt at all.

    Known window (pre-existing, accepted by the W3-E review): a member
    saved this phase whose receipt append crashed resolves as gone on the
    replay, so a single-member group's row says skipped_terminal while
    the member's own store row carries its true (accepted) state. The
    store stays the truth; this ledger row only notes the row landed."""
    if members or receipted:
        return False
    _append_outcome(phase, row, None, "skipped_terminal", None, phase_dir)
    return True


def _land_keep(ctx: Any, phase: PhaseInstance, row: TaskListRow,
               phase_dir) -> None:
    """Keep: the pending target(s) enter the bank. An already-accepted
    target is a no-op (its re-measurement passed again); a member this
    phase already receipted owes nothing (the receipt guard)."""
    store: Store = ctx.store
    receipted = _receipted_skills(phase, row, phase_dir)
    members = _target_members(store, row)
    if _terminal_group_receipt(phase, row, phase_dir, members, receipted):
        return
    for skill in members:
        if skill.skill_id in receipted:
            continue
        stored = store.load("skills", skill.skill_id, Skill)
        if stored.status not in _ACTABLE_STATUSES:
            _append_outcome(phase, row, stored.skill_id, "skipped_terminal",
                            stored.version, phase_dir)
            continue
        if stored.status == "pending":
            stored.status = "accepted"
            store.save(stored, "skills")
            _append_outcome(phase, row, stored.skill_id, "accepted",
                            stored.version, phase_dir)
        else:
            _append_outcome(phase, row, stored.skill_id, "already_accepted",
                            stored.version, phase_dir)


def _land_delete(ctx: Any, phase: PhaseInstance, row: TaskListRow,
                 phase_dir) -> None:
    """Delete: pending AND accepted members leave the bank with the
    decision's reason; already-terminal members keep their own truth.
    A member this phase already receipted owes nothing (the receipt
    guard)."""
    store: Store = ctx.store
    receipted = _receipted_skills(phase, row, phase_dir)
    members = _target_members(store, row)
    if _terminal_group_receipt(phase, row, phase_dir, members, receipted):
        return
    for skill in members:
        if skill.skill_id in receipted:
            continue
        stored = store.load("skills", skill.skill_id, Skill)
        if stored.status not in _ACTABLE_STATUSES:
            _append_outcome(phase, row, stored.skill_id, "skipped_terminal",
                            stored.version, phase_dir)
            continue
        discard_skill(store, stored, row.reason)
        _append_outcome(phase, row, stored.skill_id, "discarded",
                        stored.version, phase_dir)


def _land_add(ctx: Any, phase: PhaseInstance, row: TaskListRow,
              phase_dir) -> None:
    """Add: distill the task's strong segments into new pending skills
    (the stage-3 grain: one segment, one skill). The segments are
    produced first if they do not exist yet: no execute phase cuts
    them, so the landing runs the evidence family's segmentation
    wiring (``segmentation.segment_add_material`` -- K2 over the task's
    trajectory_select-selected strong D-phase trajectories) right
    before collecting, lazily -- only an add row pays for the cut, and
    K2's stored-subtask skip makes a resumed add row free. Experiences
    whose skill already exists are skipped -- the add path's other
    resume guard: a crashed add re-derives the same task row and
    writes only what is missing. A ModelOutputError that survives the
    write block's own budget propagates to the per-target handler
    (recorded, no skill)."""
    if _rows_of(phase_dir / OUTCOMES_NAME, phase.label, row.target_id):
        return  # this add already landed; its receipt is the proof
    store: Store = ctx.store
    segmentation.segment_add_material(
        store=store,
        task_id=row.target_id,
        params=resolve_params("evidence", phase.spec.evidence),
        trajectory_select=ctx.config.rules["trajectory_select"],
        view_rules=get_plugin(ctx.config.benchmark).view_rules,
        handle=ctx.handle("segmenter"),
        usage=ctx.usage,
        run_dir=ctx.run_dir)
    try:
        package = add_path.collect_add_evidence(
            task_row=row, store=store,
            rule_name=ctx.config.rules["segment_select"])
    except add_path.NoAddMaterial:
        # A data fact the old stage 3 answered with silence (no subtask,
        # no skill): the weak arm's failure authorized the add, but the
        # strong arm holds no learnable material -- not run, all cut
        # away by the segment rule, or K2-exhausted (terminal within
        # this run, so raising here would brick the phase on a resume
        # that re-derives the same row forever). Recorded, not raised.
        _append_outcome(phase, row, None, "skipped_no_material", None,
                        phase_dir)
        return
    fresh = [experience for experience in package.source_experiences
             if not store.exists(
                 "skills",
                 ids.make_skill_id(experience["subtask"].subtask_id))]
    if not fresh:
        _append_outcome(phase, row, None, "skipped_existing", None,
                        phase_dir)
        return
    fresh_ids = {experience["subtask"].subtask_id
                 for experience in fresh}
    filtered = EvidencePackage(
        target=package.target, facts=list(package.facts),
        segments=[segment for segment in package.segments
                  if segment["subtask_id"] in fresh_ids],
        source_experiences=fresh)
    skills = author.write_new(
        task_row=row, package=filtered,
        author_handle=ctx.handle("author"),
        weak_profile=ctx.config.weak_profile, usage=ctx.usage,
        run_dir=ctx.run_dir)
    for skill in skills:
        store.save(skill, "skills")
        _append_outcome(phase, row, skill.skill_id, "added", skill.version,
                        phase_dir)


def _land_revise(ctx: Any, phase: PhaseInstance, row: TaskListRow,
                 current_rows: Optional[List[AcceptanceReportRow]],
                 current_spec: ExecutePhaseSpec, current_round: int,
                 phase_dir) -> None:
    """Revise: collect the evidence, author the revision, land it (the
    module docstring's resume protocol)."""
    store: Store = ctx.store
    members = _target_members(store, row)
    actable = [skill for skill in members
               if skill.status in _ACTABLE_STATUSES]
    if not actable:
        receipted = _receipted_skills(phase, row, phase_dir)
        if _terminal_group_receipt(phase, row, phase_dir, members, receipted):
            return
        for skill in members:
            if skill.skill_id in receipted:
                continue
            _append_outcome(phase, row, skill.skill_id, "skipped_terminal",
                            skill.version, phase_dir)
        return
    revisions = _revision_rows(phase, row, phase_dir)
    receipts = _outcome_skills(phase, row, phase_dir)
    todo: List[Skill] = []
    for skill in actable:
        if skill.skill_id in receipts:
            continue
        from_version = revisions.get(skill.skill_id)
        if from_version is None:
            todo.append(skill)
        elif skill.version == from_version + 1:
            # The revision landed but its receipt did not (the crash
            # window): the version number is the proof, the receipt is
            # its echo. Never re-asked.
            _append_outcome(phase, row, skill.skill_id, "revised",
                            skill.version, phase_dir)
        elif skill.version != from_version:
            raise CodeBugError(
                f"skill {skill.skill_id!r} is at version {skill.version} "
                f"but this phase's revision ledger recorded "
                f"{from_version}: neither the pre-revision nor the "
                "post-revision version -- the store drifted from the "
                "ledger")
        else:
            # In flight: the revision plan row exists but the version
            # never moved -- the author call crashed or is running in
            # another process. Asked again (a previous call's
            # "unchanged" outcome never reaches this branch: its
            # receipt carries the skill_id and the receipts check above
            # already skipped it).
            todo.append(skill)
    if not todo:
        return

    if row.target_kind == "group":
        packages = live_group.collect_group_evidence(
            task_row=row, store=store, round_=current_round,
            n_attempts=_n_attempts(ctx, current_spec),
            task_instruction=_task_instruction(ctx, row.target_id),
            weak_profile=ctx.config.weak_profile,
            author_handle=ctx.handle("author"),
            view_rules=get_plugin(ctx.config.benchmark).view_rules,
            usage=ctx.usage, run_dir=ctx.run_dir)
        decide = None  # the author's own default per-skill policy
    else:
        report_row = _report_row_of(current_rows, row.basis_unit)
        packages = [exam_sheet.collect_exam_evidence(
            store, report_row,
            store.load("skills", row.target_id, Skill))]
        # A single-skill revision is already authorized: the author's
        # per-skill discretion exists for GROUP revisions, and the
        # default policy would keep a package with no skill_problem
        # fact -- the answer-sheet package never carries one.
        decide = _always_revise
    todo_ids = {skill.skill_id for skill in todo}
    to_author = [package for package in packages
                 if package.target in todo_ids]
    for package in to_author:
        if package.target in revisions:
            # An earlier pass of this phase already logged this plan (the
            # "in flight" branch above): the ledger carries ONE row per
            # (target, skill) -- the resume's re-ask must not append a
            # second copy of the same from_version, or a resumed run's
            # ledger differs from an uninterrupted one's byte for byte.
            continue
        append_jsonl(phase_dir / REVISIONS_NAME, {
            "schema": _REVISION_SCHEMA,
            "phase_label": phase.label,
            "target_kind": row.target_kind,
            "target_id": row.target_id,
            "skill_id": package.target,
            "from_version": package.old_skill.version,
        })
    outcomes = author.revise_group(
        task_row=row, packages=to_author,
        rewriter_handle=ctx.handle("rewriter"),
        weak_profile=ctx.config.weak_profile, decide=decide,
        usage=ctx.usage, run_dir=ctx.run_dir)
    by_target = {package.target: package for package in to_author}
    for outcome in outcomes:
        _land_revision(ctx, phase, row, by_target[outcome.skill_id],
                       outcome, phase_dir)


def _always_revise(package: Any) -> str:
    """The exam path's author policy: the decision module authorized
    THIS skill's revision; the package only carries the material."""
    return "revise"


def _land_revision(ctx: Any, phase: PhaseInstance, row: TaskListRow,
                   package: Any, outcome: Any, phase_dir) -> None:
    """Land one author outcome on the store (terminal state first, the
    receipt row after -- the store is the truth a resume reads)."""
    store: Store = ctx.store
    skill_id = outcome.skill_id
    if outcome.outcome == "kept":
        _append_outcome(phase, row, skill_id, "kept",
                        package.old_skill.version, phase_dir)
        return
    if outcome.outcome == "deleted":
        discard_skill(store, package.old_skill,
                      decision.LIVE_REVISION_DELETED)
        _append_outcome(phase, row, skill_id, "deleted", None, phase_dir)
        return
    if outcome.outcome in ("unchanged", "infra_failed"):
        # No new version (C8 ruling) / the service was down: the skill
        # stays exactly as it is, at its version. The infra failure is
        # raised for the per-target handler to record.
        _append_outcome(phase, row, skill_id, outcome.outcome,
                        package.old_skill.version, phase_dir)
        if outcome.outcome == "infra_failed":
            raise InfrastructureError(
                f"the revision of {skill_id!r} failed on infrastructure")
        return
    if outcome.outcome != "revised":
        raise CodeBugError(
            f"the author driver reported outcome {outcome.outcome!r} for "
            f"{skill_id!r}; the closed set is kept / deleted / unchanged "
            "/ infra_failed / revised")
    stored = store.load("skills", skill_id, Skill)
    expected = package.old_skill.version + 1
    if stored.version == package.old_skill.version:
        stored.rewrite_history.append({
            "content": stored.content,
            # The OLD summary is evidence of what the reading agent saw
            # before the rewrite (schema v3): it goes into history next
            # to the old content, then the fresh one replaces it.
            "summary": stored.summary,
            "error_category": outcome.failure_category,
            "reason": outcome.revision_reason,
        })
        stored.content = outcome.content
        stored.summary = outcome.summary
        stored.version = expected
        store.save(stored, "skills")
    elif not (stored.version == expected
              and stored.content == outcome.content
              and stored.summary == outcome.summary):
        raise CodeBugError(
            f"skill {skill_id!r} is at version {stored.version} with "
            "content the author did not produce; the store drifted from "
            "the revision ledger")
    _append_outcome(phase, row, skill_id, "revised", expected, phase_dir)


def _report_row_of(current_rows: Optional[List[AcceptanceReportRow]],
                   basis_unit: str) -> AcceptanceReportRow:
    if not current_rows:
        raise CodeBugError(
            f"task-list row names basis unit {basis_unit!r} but the "
            "current report is empty: the decision and the executor "
            "disagree")
    for row in current_rows:
        if row.unit_id == basis_unit:
            return row
    raise CodeBugError(
        f"task-list row names basis unit {basis_unit!r}, which the "
        "current report does not carry")


# ---------------------------------------------------------------------------
# The live group's evidence inputs (mirrors the retired live driver)
# ---------------------------------------------------------------------------

def _task_instruction(ctx: Any, task_id: str) -> str:
    """The rendered task prompt the student saw at step 0 (decision
    section 2.3): instruction slot filled, skills and history slots
    empty -- the skills reach the attributor through the group listing,
    the attempts' evidence through the views."""
    benchmark = ctx.config.benchmark
    plugin = get_plugin(benchmark)
    raw_id = ids.raw_task_id(task_id, benchmark)
    loaded = tasks_block.load_tasks(benchmark, [raw_id])
    match = [task for task in loaded if task.task_id == raw_id]
    if not match:
        raise CodeBugError(
            f"benchmark {benchmark!r} did not load task {raw_id!r} for "
            f"group {task_id!r}: the arms must never run a different "
            "set than the groups")
    return get_prompt(plugin.task_prompt_name).format(
        instruction=match[0].instruction, skills_block="", history="")


def _n_attempts(ctx: Any, spec: ExecutePhaseSpec) -> int:
    """The live phase's weak-model depth (its own, else the global)."""
    return spec.execution.get("n_attempts") or ctx.config.n_attempts_weak


# ---------------------------------------------------------------------------
# The phase's ledgers
# ---------------------------------------------------------------------------

def _write_task_list(phase_dir, label: str, rows: Sequence[TaskListRow],
                     superseded: Sequence[str]) -> None:
    """The effective task list plus one row naming the superseded
    units (the whole file is rewritten on every call: the decision is
    a pure function of frozen on-disk reports, so a resume re-derives
    the same bytes)."""
    rows_out = [{
        "schema": _TASK_LIST_SCHEMA,
        "phase_label": label,
        "target_kind": row.target_kind,
        "target_id": row.target_id,
        "action": row.action,
        "reason": row.reason,
        "basis_unit": row.basis_unit,
    } for row in rows]
    if superseded:
        rows_out.append({
            "schema": _TASK_LIST_SCHEMA,
            "phase_label": label,
            "superseded_units": sorted(superseded),
        })
    write_jsonl(phase_dir / TASK_LIST_NAME, rows_out)


def _append_outcome(phase: PhaseInstance, row: TaskListRow,
                    skill_id: Optional[str], outcome: str,
                    version: Optional[int], phase_dir) -> None:
    """One receipt row (the store is the truth, the receipt its echo)."""
    append_jsonl(phase_dir / OUTCOMES_NAME, {
        "schema": _OUTCOME_SCHEMA,
        "phase_label": phase.label,
        "target_kind": row.target_kind,
        "target_id": row.target_id,
        "action": row.action,
        "skill_id": skill_id,
        "outcome": outcome,
        "version": version,
    })


def _rows_of(path, phase_label: str, target_id: str) -> List[dict]:
    """One target's rows from one of the phase's ledgers."""
    if not path.exists():
        return []
    return [row for row in read_jsonl(path)
            if row.get("phase_label") == phase_label
            and row.get("target_id") == target_id]


def _revision_rows(phase: PhaseInstance, row: TaskListRow,
                   phase_dir) -> Dict[str, int]:
    """This target's revision rows, first row per skill sticks (the
    from_version of the plan a resume replays against)."""
    out: Dict[str, int] = {}
    for record in _rows_of(phase_dir / REVISIONS_NAME, phase.label,
                           row.target_id):
        out.setdefault(record["skill_id"], record["from_version"])
    return out


def _outcome_skills(phase: PhaseInstance, row: TaskListRow,
                    phase_dir) -> Set[str]:
    """The skill ids this target already landed (a receipt row is the
    proof of completion for every outcome that changes nothing)."""
    return {record["skill_id"]
            for record in _rows_of(phase_dir / OUTCOMES_NAME, phase.label,
                                   row.target_id)
            if record.get("skill_id") is not None}
