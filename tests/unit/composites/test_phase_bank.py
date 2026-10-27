"""R7 (W1-F): every learn phase ends with its own loadable bank artifact.

Covers the per-phase skill bank contract (plan item R7, problem G9): the
``phases/<label>/`` layout and its label validation, the loadable
artifact shape for both curation methods, the agentbrew merge_check
regression exams running through K3 in phase (dest) mode, the as-is
entry for intermediate learn phases, the isolation of the run-level
``skill_bank.jsonl``, the empty accepted set still producing an
artifact, the budget valve, and the second call being a pure rewrite.
Fake models only (AGENTS.md section 8); no network.
"""

import pytest

from expedistill.composites.phase_bank import (
    load_phase_bank,
    load_phase_bank_summary,
    phase_bank_dir,
    phase_bank_path,
)
from expedistill.core import ids
from expedistill.core.config import ConfigError, ExperimentConfig
from expedistill.core.models import Attempt, Exam, Skill
from expedistill.infra.reliability import ConfigError as InfraConfigError
from expedistill.infra.store import Store, read_json, write_jsonl
from expedistill.llm import make_fake_handle
from expedistill.methods.curation import phase_bank as phase_bank_entry
from expedistill.stages.context import StageContext
from tests.helpers.replies import (
    correct as _correct,
    grouping as _grouping,
    merged as _merged,
    similarity as _similarity,
)
from tests.helpers.skills import make_skill

BIG = 10 ** 6


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _subtask(index: int) -> str:
    return f"traj/tb:t{index}/strong/00/seg00"


def _skill_id(index: int) -> str:
    return ids.make_skill_id(_subtask(index))


def _add_skill(store: Store, index: int, content: str,
               status: str = "accepted") -> Skill:
    skill = make_skill(_subtask(index), content, status=status)
    store.save(skill, "skills")
    return skill


def _add_exam(store: Store, index: int) -> Exam:
    exam = Exam(exam_id=ids.make_exam_id(_subtask(index)),
                question_text=f"question {index}",
                reference_answer=f"reference {index}",
                subtask_id=_subtask(index),
                skill_id=_skill_id(index))
    store.save(exam, "exams")
    return exam


def _make_ctx(tmp_path, *, curator=(), answerer=(), judge=()):
    run_dir = tmp_path / "run"
    handles = {
        "curator": make_fake_handle("curator", list(curator)),
        "answerer": make_fake_handle("weak", list(answerer)),
        "judge": make_fake_handle("judge", list(judge)),
    }
    # The config only carries the phase-agnostic identity; the curate
    # mapping (v4: every learn phase carries its own) is an argument of
    # the produce entries now.
    config = ExperimentConfig(
        benchmark="tb", mode="split",
        models={"executor_strong": "strong-x", "executor_weak": "weak-7b"},
        sequence=["baseline", "learn", "test"],
        phases={
            "baseline": {"kind": "execute", "role": "baseline",
                  "delivery": {"name": "none"},
                  "execution": {"name": "real",
                                "arms": ["executor_strong",
                                         "executor_weak"]}},
            "learn": {"kind": "learn",
                  "review": {"name": "review", "K": 1},
                  "author": {"name": "distill_diagnose"},
                  "curate": {"name": "none", "token_budget": BIG}},
            "test": {"kind": "execute", "role": "test", "tasks": "test",
                  "delivery": {"name": "whole_bank"},
                  "execution": {"name": "real"},
                  "compare": {"against": "fresh_bare"}},
        })
    return StageContext(run_dir=run_dir, store=Store(run_dir), config=config,
                        usage=None, handles=handles,
                        workdir_root=run_dir / "work")


def _agentbrew_curate(budget=BIG, rounds=3, merge_check="exam"):
    return {"name": "agentbrew", "token_budget": budget,
            "rounds": rounds, "merge_check": merge_check}


def _none_curate(budget=BIG):
    return {"name": "none", "token_budget": budget}


_MERGE_SCRIPTS = dict(
    curator=(_similarity(groups=[[_skill_id(0), _skill_id(1)]]),
             _merged("Read the log file before doing anything else."),
             _grouping({"bank/merge/0000": "error handling"})),
    answerer=("first answer", "second answer"),
    judge=(_correct(), _correct()),
)


# ---------------------------------------------------------------------------
# Layout and label validation
# ---------------------------------------------------------------------------

def test_phase_dir_maps_the_r9_label_to_a_directory(tmp_path):
    assert phase_bank_dir(tmp_path, "p01:learn") == tmp_path / "phases" / "p01_learn"
    assert phase_bank_dir(tmp_path, "p00:freeze") == tmp_path / "phases" / "p00_freeze"
    assert phase_bank_path(tmp_path, "p02:x") == (tmp_path / "phases" / "p02_x"
                                                 / "skill_bank.jsonl")


@pytest.mark.parametrize("label", ["", "..", ".", "a/b", "a b", "p1:name!",
                                   "p1:\\", "p1\n", "p1", "p1:",
                                   "p1:a:b", "x1:learn", "p1_a"])
def test_unsafe_phase_labels_are_refused(tmp_path, label):
    # The label must be exactly 'pNN:<name>' (D3): a bare 'pNN' or the
    # colonless 'p1_a' would collide with the legal 'p1:a' once the
    # colon is mapped, so both are refused up front.
    with pytest.raises(InfraConfigError, match="pNN:<name>"):
        phase_bank_dir(tmp_path, label)


def test_the_colon_rewrite_cannot_collide_two_labels(tmp_path):
    # 'p1:a' and a hypothetical colonless 'p1_a' label map to the same
    # directory name only if both were legal; only the first is, and
    # two distinct legal labels never share a directory.
    assert phase_bank_dir(tmp_path, "p1:a") == tmp_path / "phases" / "p1_a"
    assert phase_bank_dir(tmp_path, "p1:a_a") == (tmp_path / "phases"
                                                  / "p1_a_a")
    assert phase_bank_dir(tmp_path, "p2:a") != phase_bank_dir(tmp_path,
                                                              "p1:a")


def test_loading_a_missing_artifact_names_the_phase(tmp_path):
    with pytest.raises(InfraConfigError, match="p99:never"):
        load_phase_bank(tmp_path, "p99:never")
    with pytest.raises(InfraConfigError, match="p99:never"):
        load_phase_bank_summary(tmp_path, "p99:never")


# ---------------------------------------------------------------------------
# produce: curation=none
# ---------------------------------------------------------------------------

def test_produce_none_writes_a_loadable_phase_artifact(tmp_path):
    # The artifact carries EVERY member: the accepted row verbatim and
    # the pending member passing through unchanged (the 2026-10-16
    # ruling -- the artifact is the only currency between phases, and
    # a learn phase's new skills are pending). Discarded and merged
    # members are not members.
    curate = _none_curate()
    ctx = _make_ctx(tmp_path)
    kept = _add_skill(ctx.store, 0, "keep me")
    _add_skill(ctx.store, 1, "deleted", status="discarded")
    pending = _add_skill(ctx.store, 2, "still pending", status="pending")

    summary = phase_bank_entry.produce(ctx, curate, "p01:learn")

    assert summary["members"] == [kept.skill_id, pending.skill_id]
    assert summary["size"] == 2
    assert summary["bank_reference"].startswith("bank:")
    rows = load_phase_bank(ctx.run_dir, "p01:learn")
    assert [row["skill_id"] for row in rows] == summary["members"]
    # Full skill dicts: the member status and version travel with the row.
    assert rows[0]["status"] == "accepted"
    assert rows[0]["version"] == 1
    assert rows[1]["status"] == "pending"
    assert load_phase_bank_summary(ctx.run_dir, "p01:learn") == summary
    # The run-level bank and the stage-5 journal are untouched (the
    # stage5_curation/ DIRECTORY itself is pre-created by the Store's
    # layout; only its contents would prove a stage-5 run).
    assert ctx.store.bank() == []
    assert not (ctx.run_dir / "stage5_curation"
                / "curation_log.json").exists()


def test_produce_none_pending_passthrough_is_idempotent(tmp_path):
    # A second call (a resume) finds the pending member already in the
    # artifact and lands on the same bytes; the pending content does
    # not go through the token-budget valve (the valve guards the
    # injectable accepted set).
    curate = _none_curate(budget=10)
    ctx = _make_ctx(tmp_path)
    accepted = _add_skill(ctx.store, 0, "short")
    pending = _add_skill(ctx.store, 1, "a pending member much longer "
                                      "than the tiny budget", status="pending")

    first = phase_bank_entry.produce(ctx, curate, "p01:learn")
    path = phase_bank_path(ctx.run_dir, "p01:learn")
    bytes_first = path.read_bytes()

    second = phase_bank_entry.produce(ctx, curate, "p01:learn")

    assert second == first
    assert path.read_bytes() == bytes_first
    assert first["members"] == [accepted.skill_id, pending.skill_id]


def test_produce_as_is_carries_pending_members_too(tmp_path):
    curate = _agentbrew_curate()
    ctx = _make_ctx(tmp_path)
    kept = _add_skill(ctx.store, 0, "one")
    pending = _add_skill(ctx.store, 2, "still under test",
                         status="pending")

    summary = phase_bank_entry.produce_as_is(ctx, curate, "p01:learn")

    assert summary["members"] == [kept.skill_id, pending.skill_id]
    rows = load_phase_bank(ctx.run_dir, "p01:learn")
    assert [row["status"] for row in rows] == ["accepted", "pending"]


def test_produce_agentbrew_passes_pending_members_through(tmp_path):
    # K5 curates the accepted set only; the pending member rides along
    # AFTER the merged product, and a second call (K5's resume path
    # reads the artifact) still lands on the same artifact.
    curate = _agentbrew_curate()
    ctx = _make_ctx(tmp_path, **_MERGE_SCRIPTS)
    _add_skill(ctx.store, 0, "Check the log file first.")
    _add_skill(ctx.store, 1, "Look at the log file before anything else.")
    for index in range(2):
        _add_exam(ctx.store, index)
    pending = _add_skill(ctx.store, 2, "a skill still under exam",
                         status="pending")

    first = phase_bank_entry.produce(ctx, curate, "p01:learn")

    assert first["members"] == ["bank/merge/0000", pending.skill_id]
    rows = load_phase_bank(ctx.run_dir, "p01:learn")
    assert [row["skill_id"] for row in rows] == first["members"]
    assert rows[0]["status"] == "accepted"
    assert rows[1]["status"] == "pending"

    second = phase_bank_entry.produce(ctx, curate, "p01:learn")

    assert second == first
    assert load_phase_bank(ctx.run_dir, "p01:learn") == rows


# ---------------------------------------------------------------------------
# produce: curation=agentbrew (K5 in dest mode)
# ---------------------------------------------------------------------------

def test_produce_agentbrew_merges_with_k3_regression_exams(tmp_path):
    curate = _agentbrew_curate()
    ctx = _make_ctx(tmp_path,
                     **_MERGE_SCRIPTS)
    skills = [_add_skill(ctx.store, 0, "Check the log file first."),
              _add_skill(ctx.store, 1, "Look at the log file before anything else.")]
    for index in range(2):
        _add_exam(ctx.store, index)

    summary = phase_bank_entry.produce(ctx, curate, "p02:learn")

    merged_id = "bank/merge/0000"
    assert summary["members"] == [merged_id]
    rows = load_phase_bank(ctx.run_dir, "p02:learn")
    assert [row["skill_id"] for row in rows] == [merged_id]
    assert rows[0]["status"] == "accepted"
    assert rows[0]["group"] == "error handling"
    # merge_check=exam: the regression exams ran through K3 (both source
    # exams answered and judged against the merged skill).
    assert len(ctx.handles["answerer"].client.calls) == 2
    assert len(ctx.handles["judge"].client.calls) == 2
    attempts = [ctx.store.load("attempts", aid, Attempt)
                for aid in ctx.store.ids("attempts")]
    assert len(attempts) == 2
    assert all(a.purpose == "regression" and a.skill_id == merged_id
               and a.judge_verdict is True for a in attempts)
    # The merge journal lives in the phase directory; the run-level bank
    # and the stage-5 directory never appear.
    journal = read_json(phase_bank_dir(ctx.run_dir, "p02:learn")
                        / "curation_log.json")
    assert journal["final_member_ids"] == [merged_id]
    assert ctx.store.bank() == []
    assert not (ctx.run_dir / "stage5_curation"
                / "curation_log.json").exists()
    # The shared skills store carries the merge, as in stage 5.
    for skill in skills:
        assert ctx.store.load("skills", skill.skill_id,
                              Skill).status == "merged"


def test_produce_agentbrew_merge_check_none_skips_k3(tmp_path):
    # Empty answerer/judge scripts: any K3 call would exhaust the script.
    curate = _agentbrew_curate(merge_check="none")
    ctx = _make_ctx(tmp_path,
                    curator=_MERGE_SCRIPTS["curator"],
                    answerer=[], judge=[])
    _add_skill(ctx.store, 0, "Check the log file first.")
    _add_skill(ctx.store, 1, "Look at the log file before anything else.")
    for index in range(2):
        _add_exam(ctx.store, index)

    summary = phase_bank_entry.produce(ctx, curate, "p01:learn")

    merged_id = "bank/merge/0000"
    assert summary["members"] == [merged_id]
    assert ctx.store.ids("attempts") == []
    assert load_phase_bank(ctx.run_dir, "p01:learn")[0]["skill_id"] == merged_id


def test_produce_agentbrew_merge_check_none_needs_no_exams(tmp_path):
    # The live-run shape: a live-only sequence has NO exam phase, so the
    # store holds no exams at all -- merge_check 'none' must still keep
    # the merge (the round-1 group re-execution is its proof). Requiring
    # the exams here would skip every merge of every live_run curation
    # with "no stored exam; merge unverifiable" -- the 2026-10-17 smoke
    # caught exactly that.
    curate = _agentbrew_curate(merge_check="none")
    ctx = _make_ctx(tmp_path,
                    curator=_MERGE_SCRIPTS["curator"],
                    answerer=[], judge=[])
    _add_skill(ctx.store, 0, "Check the log file first.")
    _add_skill(ctx.store, 1, "Look at the log file before anything else.")
    assert ctx.store.ids("exams") == []

    summary = phase_bank_entry.produce(ctx, curate, "p01:learn")

    merged_id = "bank/merge/0000"
    assert summary["members"] == [merged_id]
    assert ctx.store.ids("attempts") == []
    journal = read_json(phase_bank_dir(ctx.run_dir, "p01:learn")
                        / "curation_log.json")
    assert journal["merges"]["skipped"] == 0
    assert journal["merges"]["kept"] == 1
    assert load_phase_bank(ctx.run_dir, "p01:learn")[0]["skill_id"] == merged_id


def test_produce_agentbrew_second_call_is_a_pure_rewrite(tmp_path):
    # The scripts are consumed by the first call; the second (resume)
    # call must make zero model calls and land on the same artifact.
    curate = _agentbrew_curate()
    ctx = _make_ctx(tmp_path,
                    **_MERGE_SCRIPTS)
    _add_skill(ctx.store, 0, "Check the log file first.")
    _add_skill(ctx.store, 1, "Look at the log file before anything else.")
    for index in range(2):
        _add_exam(ctx.store, index)
    first = phase_bank_entry.produce(ctx, curate, "p01:learn")

    second = phase_bank_entry.produce(ctx, curate, "p01:learn")

    assert second == first
    assert load_phase_bank(ctx.run_dir, "p01:learn")[0]["skill_id"] \
        == "bank/merge/0000"


def test_produce_agentbrew_empty_accepted_set_writes_empty_artifact(tmp_path):
    # No skills at all: the phase still ends with a (loadable, empty)
    # bank artifact -- its existence is the point of R7.
    curate = _agentbrew_curate()
    ctx = _make_ctx(tmp_path)

    summary = phase_bank_entry.produce(ctx, curate, "p01:learn")

    assert summary["members"] == []
    assert summary["size"] == 0
    assert load_phase_bank(ctx.run_dir, "p01:learn") == []
    assert ctx.store.bank() == []


def test_produce_agentbrew_over_budget_stops_loudly(tmp_path):
    # One long skill against a tiny budget: K5 refuses after its rounds
    # and no phase artifact is written.
    curate = _agentbrew_curate(budget=2, rounds=1)
    ctx = _make_ctx(tmp_path,
                    curator=[_similarity()], judge=[], answerer=[])
    _add_skill(ctx.store, 0, "a much longer skill than the budget allows")

    with pytest.raises(InfraConfigError, match="bank_token_budget"):
        phase_bank_entry.produce(ctx, curate, "p01:learn")

    assert not phase_bank_path(ctx.run_dir, "p01:learn").exists()


# ---------------------------------------------------------------------------
# produce_as_is: the intermediate learn phases' artifact
# ---------------------------------------------------------------------------

def test_produce_as_is_ignores_the_configured_merge_loop(tmp_path):
    # The config asks for agentbrew, but produce_as_is writes the
    # accepted rows verbatim with no model call and no journal.
    curate = _agentbrew_curate()
    ctx = _make_ctx(tmp_path)
    kept = [_add_skill(ctx.store, 0, "one"),
            _add_skill(ctx.store, 1, "two")]
    _add_skill(ctx.store, 2, "gone", status="discarded")

    summary = phase_bank_entry.produce_as_is(ctx, curate, "p01:learn")

    assert summary["members"] == [skill.skill_id for skill in kept]
    rows = load_phase_bank(ctx.run_dir, "p01:learn")
    assert [row["skill_id"] for row in rows] == summary["members"]
    assert [row["content"] for row in rows] == ["one", "two"]
    for handle in ctx.handles.values():
        assert handle.client.calls == []
    assert not (phase_bank_dir(ctx.run_dir, "p01:learn")
                / "curation_log.json").exists()


def test_produce_as_is_keeps_the_budget_valve(tmp_path):
    curate = _agentbrew_curate(budget=2)
    ctx = _make_ctx(tmp_path)
    _add_skill(ctx.store, 0, "a much longer skill")

    with pytest.raises(ConfigError, match="without curation"):
        phase_bank_entry.produce_as_is(ctx, curate, "p01:learn")

    assert not phase_bank_path(ctx.run_dir, "p01:learn").exists()


# ---------------------------------------------------------------------------
# Every learn phase produces its own artifact
# ---------------------------------------------------------------------------

def test_each_learn_phase_ends_with_the_bank_of_its_own_round(tmp_path):
    # Two learn phases: the second sees a revised-and-extended accepted
    # set (one skill discarded, one added), and each phase directory
    # holds the bank of its own round -- the previous artifact is never
    # rewritten by a later phase.
    curate = _none_curate()
    ctx = _make_ctx(tmp_path)
    _add_skill(ctx.store, 0, "one")
    _add_skill(ctx.store, 1, "two")
    first = phase_bank_entry.produce(ctx, curate, "p01:learn")
    assert first["members"] == [_skill_id(0), _skill_id(1)]

    gone = ctx.store.load("skills", _skill_id(1), Skill)
    gone.status = "discarded"
    ctx.store.save(gone, "skills")
    _add_skill(ctx.store, 2, "three")
    second = phase_bank_entry.produce(ctx, curate, "p03:learn")

    assert second["members"] == [_skill_id(0), _skill_id(2)]
    assert [row["skill_id"]
            for row in load_phase_bank(ctx.run_dir, "p01:learn")] \
        == first["members"]
    assert [row["skill_id"]
            for row in load_phase_bank(ctx.run_dir, "p03:learn")] \
        == second["members"]
    # Nothing leaked to the run root.
    assert ctx.store.bank() == []


def test_bank_summary_agrees_with_the_artifact_rows(tmp_path):
    # The summary is the artifact's digest: same members, same size, and
    # the bank_reference recomputable from the artifact rows alone (what
    # stage 6 records as skills_injected today).
    from expedistill.inject import bank_reference
    curate = _none_curate()
    ctx = _make_ctx(tmp_path)
    _add_skill(ctx.store, 0, "one")
    _add_skill(ctx.store, 1, "two")
    phase_bank_entry.produce(ctx, curate, "p01:learn")
    rows = load_phase_bank(ctx.run_dir, "p01:learn")
    summary = load_phase_bank_summary(ctx.run_dir, "p01:learn")
    assert summary["size"] == len(rows)
    assert summary["members"] == [row["skill_id"] for row in rows]
    assert summary["bank_reference"] == bank_reference(rows)


# ---------------------------------------------------------------------------
# The dest short-circuit's pre-seeded refresh (Wave 3 review B1,
# the 2026-10-16 coordinator ruling)
# ---------------------------------------------------------------------------

def _record_of(store: Store, skill_id: str) -> dict:
    return read_json(store.path_for("skills", skill_id))


def test_the_dest_short_circuit_refreshes_a_pre_seeded_artifact(tmp_path):
    """An assembled run pre-seeds the last learn phase's bank artifact
    from the SOURCE run's curation, then its own phases accept members
    the artifact does not know (a ride-along pending member that passed
    its exam; a re-offered unit's new skill). The short-circuit must
    refresh the artifact from the store instead of refusing it stale:
    the seeded rows stay verbatim, the pending -> accepted row follows
    the store's own record, and a member with no row is appended in its
    on-disk record form, sorted by skill_id. Before the fix this exact
    state raised ConfigError ("skill bank is stale") and the assembled
    run could never finish."""
    from expedistill.composites import curation

    ctx = _make_ctx(tmp_path, curator=[], answerer=[], judge=[])
    seeded = _add_skill(ctx.store, 0, "A seeded curation member.")
    ride_along = _add_skill(ctx.store, 1, "A ride-along pending member.",
                            status="pending")
    dest = phase_bank_dir(ctx.run_dir, "p02:learn")
    dest.mkdir(parents=True)
    # What the assembler writes: the filtered bank (accepted rows) with
    # the pending members riding along.
    write_jsonl(dest / "skill_bank.jsonl",
                [_record_of(ctx.store, seeded.skill_id),
                 _record_of(ctx.store, ride_along.skill_id)])
    # The run's own phases: the keep row accepts the ride-along member,
    # and a re-offered unit lands a brand-new accepted skill.
    accepted = ctx.store.load("skills", ride_along.skill_id, Skill)
    accepted.status = "accepted"
    ctx.store.save(accepted, "skills")
    late = _add_skill(ctx.store, 2, "Accepted after the seeding.")

    bank = curation.curate_bank(
        store=ctx.store,
        answerer_handle=make_fake_handle("weak", []),
        judge_handle=make_fake_handle("judge", []),
        curator_handle=make_fake_handle("curator", []),
        bank_token_budget=BIG, curation_rounds=3, merge_check="exam",
        run_dir=ctx.run_dir, dest=dest)

    # The short-circuit returned the refreshed artifact's accepted rows
    # -- not a staleness refusal.
    assert [row["skill_id"] for row in bank] == [
        seeded.skill_id, ride_along.skill_id, late.skill_id]
    assert bank[1] == _record_of(ctx.store, ride_along.skill_id)
    assert bank[2] == _record_of(ctx.store, late.skill_id)
    # The seeded row is untouched: the source run's curation result is
    # never re-merged, edited or reordered.
    rows = load_phase_bank(ctx.run_dir, "p02:learn")
    assert rows[0] == _record_of(ctx.store, seeded.skill_id)


def test_the_pre_seeded_refresh_is_idempotent(tmp_path):
    """The refresh is a pure function of (artifact, store): a replay
    finds nothing left to do and leaves the file byte-identical, so a
    resumed short-circuit never drifts."""
    from expedistill.composites import curation

    ctx = _make_ctx(tmp_path, curator=[], answerer=[], judge=[])
    seeded = _add_skill(ctx.store, 0, "A seeded curation member.")
    ride_along = _add_skill(ctx.store, 1, "A ride-along pending member.",
                            status="pending")
    dest = phase_bank_dir(ctx.run_dir, "p02:learn")
    dest.mkdir(parents=True)
    write_jsonl(dest / "skill_bank.jsonl",
                [_record_of(ctx.store, seeded.skill_id),
                 _record_of(ctx.store, ride_along.skill_id)])
    accepted = ctx.store.load("skills", ride_along.skill_id, Skill)
    accepted.status = "accepted"
    ctx.store.save(accepted, "skills")
    late = _add_skill(ctx.store, 2, "Accepted after the seeding.")
    path = dest / "skill_bank.jsonl"

    curation._refresh_dest_artifact(ctx.store, dest)
    once = path.read_bytes()
    assert [row["skill_id"] for row in load_phase_bank(ctx.run_dir,
                                                       "p02:learn")] == [
        seeded.skill_id, ride_along.skill_id, late.skill_id]

    # The replay: nothing changes, not even the bytes.
    curation._refresh_dest_artifact(ctx.store, dest)
    assert path.read_bytes() == once


def test_the_run_mode_staleness_refusal_stands(tmp_path):
    """The refresh is a dest-mode remedy only: a run-level bank (dest
    None) that no longer covers the accepted skills is still refused
    loudly -- the pre-existing stage-5 semantics, unchanged."""
    from expedistill.composites import curation

    ctx = _make_ctx(tmp_path, curator=[], answerer=[], judge=[])
    seeded = _add_skill(ctx.store, 0, "A banked member.")
    ctx.store.append_bank(seeded.to_dict())
    late = _add_skill(ctx.store, 1, "Accepted after curation finished.")

    with pytest.raises(InfraConfigError, match="skill bank is stale"):
        curation.curate_bank(
            store=ctx.store,
            answerer_handle=make_fake_handle("weak", []),
            judge_handle=make_fake_handle("judge", []),
            curator_handle=make_fake_handle("curator", []),
            bank_token_budget=BIG, curation_rounds=3, merge_check="exam",
            run_dir=ctx.run_dir)
    assert late.status == "accepted"  # nothing was mutated by the refusal
