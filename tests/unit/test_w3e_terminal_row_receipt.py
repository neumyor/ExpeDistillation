"""The terminal-group ROW receipt (W3-E): a keep/delete row whose group
holds no resolvable pending member still owes the ledger one receipt.

``_target_members`` resolves a group row through ``pending_groups`` -- only
tasks that still carry pending stage-3 candidates appear -- so a keep/delete
row pointing at an already-terminal group resolved to an EMPTY member list
and the member loop wrote nothing: the row ended the phase receiptless and
the module docstring's "skipped and recorded" promise failed at the row
level. The fix writes ONE target-level ``skipped_terminal`` receipt (the add
path's target-level shape: no skill_id, no version) when the member list is
empty AND the target carries no receipt yet -- pinned here: the trigger,
the crash-replay byte identity (the golden matrix's E5 discipline), the
member-level path staying untouched, and the audit's check 8 accepting the
new shape.
"""

import importlib.util
from pathlib import Path

from expedistill.core import ids
from expedistill.core.config import PhaseInstance
from expedistill.core.contracts import TaskListRow
from expedistill.core.models import Skill
from expedistill.infra.store import Store, append_jsonl, read_jsonl
from expedistill.stages import learn_tasks
from expedistill.stages.context import StageContext

BENCHMARK = "tb"
TASK_ID = ids.make_task_id(BENCHMARK, "t1")
LABEL = "p02:L"

_SCRIPT = (Path(__file__).resolve().parents[2]
           / "scripts" / "audit" / "audit_live_run.py")


def _load_audit_module():
    """The audit script is not a package (test_audit_live_run's loader)."""
    spec = importlib.util.spec_from_file_location("audit_live_run", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Config:
    """The config stand-in: the keep/delete landing reads none of it."""

    benchmark = BENCHMARK
    weak_profile = "a weak model that needs short sentences"
    n_attempts_strong = 1
    n_attempts_weak = 2
    rules = {}
    methods = {}


def _skill(segment, status="discarded", version=1):
    """One stage-3 candidate of the task's group, terminal by default (a
    live_no_improvement finalize is the shape the final learn phase sees)."""
    subtask_id = ids.make_subtask_id(
        ids.make_trajectory_id(TASK_ID, "strong", 0), segment)
    return Skill(
        skill_id=ids.make_skill_id(subtask_id), content="c", subtask_id=subtask_id,
        source_subtasks=[subtask_id], version=version, status=status,
        discard_reason="live_no_improvement" if status == "discarded"
        else None)


def _ctx(tmp_path):
    run_dir = tmp_path / "run"
    return StageContext(run_dir=run_dir, store=Store(run_dir),
                        config=_Config(), usage=None, handles={},
                        workdir_root=tmp_path / "work")


def _phase():
    return PhaseInstance(index=1, name="L", label=LABEL, spec=None,
                         task_ids=())


def _row(action, reason="live_no_improvement"):
    return TaskListRow(target_kind="group", target_id=TASK_ID,
                       action=action, reason=reason, basis_unit="u1")


def _execute(ctx, rows):
    return learn_tasks.execute_rows(ctx, _phase(), rows, None, None, 0, [])


def _outcomes(ctx):
    return _outcomes_of(ctx, LABEL)


def _outcomes_of(ctx, label):
    path = (learn_tasks.phase_bank_dir(ctx.run_dir, label)
            / learn_tasks.OUTCOMES_NAME)
    return list(read_jsonl(path)) if path.exists() else []


# ---------------------------------------------------------------------------
# The trigger: a terminal group's keep/delete row gets ONE row-level receipt
# ---------------------------------------------------------------------------

def test_a_keep_row_on_a_terminal_group_writes_one_row_level_receipt(tmp_path):
    ctx = _ctx(tmp_path)
    members = [_skill(0), _skill(1)]
    for skill in members:
        ctx.store.save(skill, "skills")

    _execute(ctx, [_row("keep")])

    assert _outcomes(ctx) == [{
        "schema": "learn-outcome-v1", "phase_label": LABEL,
        "target_kind": "group", "target_id": TASK_ID, "action": "keep",
        "skill_id": None, "outcome": "skipped_terminal", "version": None,
    }]
    # E5 semantics: nothing moved in the store, the receipt is pure ledger.
    for skill in members:
        stored = ctx.store.load("skills", skill.skill_id, Skill)
        assert stored.status == "discarded"
        assert stored.discard_reason == "live_no_improvement"


def test_a_delete_row_on_a_terminal_group_writes_one_row_level_receipt(
        tmp_path):
    ctx = _ctx(tmp_path)
    for segment in range(2):
        ctx.store.save(_skill(segment), "skills")

    _execute(ctx, [_row("delete")])

    assert _outcomes(ctx) == [{
        "schema": "learn-outcome-v1", "phase_label": LABEL,
        "target_kind": "group", "target_id": TASK_ID, "action": "delete",
        "skill_id": None, "outcome": "skipped_terminal", "version": None,
    }]


def test_a_revise_row_on_a_terminal_group_writes_one_row_level_receipt(
        tmp_path):
    """The revise row shares the keep/delete gap (the C review's point b):
    an empty member list ends the phase receiptless. The row-level receipt
    reuses the same helper -- the empty-actables branch returns before any
    evidence or author work, so no handles are needed here."""
    ctx = _ctx(tmp_path)
    for segment in range(2):
        ctx.store.save(_skill(segment), "skills")

    _execute(ctx, [_row("revise")])

    assert _outcomes(ctx) == [{
        "schema": "learn-outcome-v1", "phase_label": LABEL,
        "target_kind": "group", "target_id": TASK_ID, "action": "revise",
        "skill_id": None, "outcome": "skipped_terminal", "version": None,
    }]


def test_a_revise_row_replay_writes_no_second_receipt(tmp_path):
    """The receipt guard holds for the revise path too: a replay of the
    same row leaves the ledger byte-identical (E5's discipline)."""
    ctx = _ctx(tmp_path)
    for segment in range(2):
        ctx.store.save(_skill(segment), "skills")

    _execute(ctx, [_row("revise")])
    _execute(ctx, [_row("revise")])

    assert len(_outcomes(ctx)) == 1


# ---------------------------------------------------------------------------
# Resume idempotency: a replayed row never writes a second receipt
# ---------------------------------------------------------------------------

def test_the_row_level_receipt_is_idempotent_across_a_crash_replay(tmp_path):
    # The crash replay: the phase re-derives the same task-list row and
    # re-lands it; the receipt guard must leave the ledger byte-identical
    # to the first pass's (the golden matrix's E5 crash-resume contract).
    ctx = _ctx(tmp_path)
    for segment in range(2):
        ctx.store.save(_skill(segment), "skills")
    rows = [_row("keep")]

    _execute(ctx, rows)
    outcomes_path = (learn_tasks.phase_bank_dir(ctx.run_dir, LABEL)
                     / learn_tasks.OUTCOMES_NAME)
    first_pass = outcomes_path.read_bytes()

    _execute(ctx, rows)  # the replay after the "crash"

    assert outcomes_path.read_bytes() == first_pass
    assert [row["outcome"] for row in _outcomes(ctx)] == ["skipped_terminal"]


def test_an_earlier_member_receipt_covers_the_row(tmp_path):
    # The other resume shape: an earlier pass of this phase receipted the
    # members (say a delete that landed before the crash), so the group now
    # resolves to no pending member -- the existing receipts are the row's
    # proof and the row-level receipt must NOT be written on top of them.
    ctx = _ctx(tmp_path)
    skills = [_skill(segment) for segment in range(2)]
    for skill in skills:
        ctx.store.save(skill, "skills")
    phase_dir = learn_tasks.phase_bank_dir(ctx.run_dir, LABEL)
    phase_dir.mkdir(parents=True)
    for skill in skills:
        append_jsonl(phase_dir / learn_tasks.OUTCOMES_NAME, {
            "schema": "learn-outcome-v1", "phase_label": LABEL,
            "target_kind": "group", "target_id": TASK_ID, "action": "delete",
            "skill_id": skill.skill_id, "outcome": "discarded",
            "version": skill.version})

    _execute(ctx, [_row("delete")])

    assert [row["skill_id"] for row in _outcomes(ctx)] \
        == [skill.skill_id for skill in skills]


# ---------------------------------------------------------------------------
# The member-level path is untouched
# ---------------------------------------------------------------------------

def test_pending_members_still_land_member_level(tmp_path):
    # A resolvable group lands per member and writes no row-level
    # receipt; a SKILL row pointing at an already-terminal skill keeps
    # the MEMBER-level skipped_terminal receipt (skill_id and version
    # set) -- both existing behaviors, byte for byte.
    ctx = _ctx(tmp_path)
    pending = _skill(0, status="pending")
    terminal = _skill(1, status="discarded")
    ctx.store.save(pending, "skills")
    ctx.store.save(terminal, "skills")
    group_row = _row("keep")
    skill_row = TaskListRow(target_kind="skill", target_id=terminal.skill_id,
                            action="keep", reason="improved",
                            basis_unit="u2")

    _execute(ctx, [group_row, skill_row])

    outcomes = {row["skill_id"]: (row["outcome"], row["version"])
                for row in _outcomes(ctx)}
    assert outcomes == {pending.skill_id: ("accepted", 1),
                        terminal.skill_id: ("skipped_terminal", 1)}
    assert all(row["skill_id"] is not None for row in _outcomes(ctx))
    assert ctx.store.load(
        "skills", pending.skill_id, Skill).status == "accepted"
    assert ctx.store.load(
        "skills", terminal.skill_id, Skill).status == "discarded"


def test_a_fully_receipted_member_group_writes_no_row_level_receipt(tmp_path):
    # All members were receipted in an earlier pass of this phase: the
    # receipts already cover the row, and the guard skips every member
    # without writing anything -- no row-level receipt on top.
    ctx = _ctx(tmp_path)
    skills = [_skill(segment, status="pending") for segment in range(2)]
    for skill in skills:
        ctx.store.save(skill, "skills")
        skill.status = "accepted"  # the earlier pass landed them
        ctx.store.save(skill, "skills")
    phase_dir = learn_tasks.phase_bank_dir(ctx.run_dir, LABEL)
    phase_dir.mkdir(parents=True)
    for skill in skills:
        append_jsonl(phase_dir / learn_tasks.OUTCOMES_NAME, {
            "schema": "learn-outcome-v1", "phase_label": LABEL,
            "target_kind": "group", "target_id": TASK_ID, "action": "keep",
            "skill_id": skill.skill_id, "outcome": "accepted",
            "version": skill.version})

    _execute(ctx, [_row("keep")])

    assert [row["skill_id"] for row in _outcomes(ctx)] \
        == [skill.skill_id for skill in skills]


# ---------------------------------------------------------------------------
# Audit compatibility: check 8 accepts the row-level receipt
# ---------------------------------------------------------------------------

def test_the_audit_check8_accepts_the_row_level_receipt(tmp_path):
    audit_live_run = _load_audit_module()
    ctx = _ctx(tmp_path)
    for segment in range(2):
        ctx.store.save(_skill(segment), "skills")

    _execute(ctx, [_row("delete")])

    audit = audit_live_run.Audit(ctx.run_dir)
    skills = audit_live_run._load_skills(Store(ctx.run_dir))
    audit_live_run.check_learn_ledgers(audit, ctx.run_dir,
                                       [(1, "L", {"kind": "learn"})], skills)
    errors = [f for f in audit.findings
              if f["severity"] == audit_live_run.ERROR]
    assert errors == []
    # The not-finished INFO (no summary, no bank) is the only finding.
    assert all(f["severity"] == audit_live_run.INFO for f in audit.findings)
