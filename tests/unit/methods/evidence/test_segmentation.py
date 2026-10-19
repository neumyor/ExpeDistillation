"""The add path's segmentation wiring (Wave 3-S): the driver and its
learn-phase call site.

Two levels, per AGENTS.md section 8 rule 2 (the interrupt-resume test is
mandatory for anything that lands units):

* the DRIVER (``methods.evidence.segmentation.segment_add_material``)
  against K2 with a scripted segmenter: the named trajectory_select
  rule over the task's both-roles set, the strong-only cut, the
  0-step exclusion with its once-only audit records, mid-batch crash ->
  resume without re-segmentation, and terminal exhaustion;
* the LEARN PHASE (``stages.phase_learn`` through ``learn_tasks``)
  with NOTHING seeded but the baseline phase's on-disk trajectories: an add
  row produces its own subtasks, an add row with no strong material is
  recorded instead of raised, and a learn phase with no add row never
  touches the segmenter (the wiring's laziness).
"""


import pytest

import expedistill.benchmarks.tb  # noqa: F401  (registers the plugin + TB_TASK_PROMPT)
from expedistill.composites.phase_bank import load_phase_bank, phase_bank_dir
from expedistill.core import ids
from expedistill.core.config import (
    CompareSpec,
    ExecutePhaseSpec,
    LearnPhaseSpec,
    PhaseInstance,
)
from expedistill.core.models import Skill, Step, Subtask, Trajectory
from expedistill.infra.store import Store, read_jsonl
from expedistill.llm.fake import json_script, make_fake_handle
from expedistill.methods.evidence.segmentation import segment_add_material
from expedistill.stages import learn_tasks, phase_learn
from expedistill.stages.context import StageContext
from tests.helpers.plugins import ensure_plugin


BENCHMARK = "tb"
TASK_ID = ids.make_task_id(BENCHMARK, "t1")
BIG = 10 ** 6
WEAK_PROFILE = "a weak model that needs short sentences"


@pytest.fixture(autouse=True)
def _tb_plugin_registered():
    """The phase-level tests render the TB view rules; other suites
    leave the benchmark registry reset behind them (a pre-existing
    pattern), so re-register on demand instead of depending on import
    order -- the pattern tests/unit/stages/test_phase_learn.py established."""
    ensure_plugin(BENCHMARK)


# ---------------------------------------------------------------------------
# Fixtures: trajectories, the segmentation script, the phase stand-ins
# ---------------------------------------------------------------------------

def _trajectory(role, attempt, *, task_id=TASK_ID, n_steps=1,
                correctness=True):
    return Trajectory(
        trajectory_id=ids.make_trajectory_id(task_id, role, attempt),
        task_id=task_id, benchmark=BENCHMARK, model_role=role,
        model_name=f"{role}-model", attempt_index=attempt,
        steps=[Step(action=f"a{i}", observation=f"o{i}")
               for i in range(n_steps)],
        correctness=correctness)


def _cut(start=0, end=1, success=True):
    # One segment covering [start, end): D7 demands exact coverage.
    return {"start": start, "end": end, "label": "do it",
            "description": "the only segment", "success": success}


def _bad_cut():
    # Schema-valid but out of bounds: D7 rejects it, so a scripted
    # segmenter returning only this exhausts its retries.
    return {"start": 0, "end": 99, "label": "bad", "description": "nope",
            "success": True}


def _distill_script():
    return json_script({
        "when_to_use": "When the service rejects connections.",
        "steps": ["Run the status command on the service.",
                  "Restart the service with its restart command."],
        "verify": "The status command reports the service is running.",
        "not_applicable_when": "The service already reports running.",
        "summary": "restore a service that rejects connections",
    })


def _diagnose_script():
    return json_script({
        "when_to_use": "When the service keeps rejecting connections after "
                       "a restart: restarting again does not help.",
        "steps": ["Read the service log.",
                  "Fix the error the log names, then restart."],
        "verify": "The status command reports the service is running.",
        "not_applicable_when": "The service has never been started.",
        "summary": "read the log instead of restarting blindly",
    })


class _Config:
    """The v4 config stand-in: the sequence/phases products plus the
    globals the drivers read (the same shape tests/unit/stages/test_phase_learn.py
    uses)."""

    def __init__(self, sequence, phases):
        self.sequence = sequence
        self.phases = phases
        self.benchmark = BENCHMARK
        self.weak_profile = WEAK_PROFILE
        self.n_attempts_strong = 1
        self.n_attempts_weak = 2
        self.rules = {"task_select": "any_weak_failed",
                      "segment_select": "strong_success_weak_failed",
                      "trajectory_select": "all"}


def _baseline_spec():
    return ExecutePhaseSpec(
        role="baseline", tasks="train", delivery={"name": "none"},
        execution={"name": "real",
                   "arms": ["executor_strong", "executor_weak"]})


def _learn_spec(write_lessons=False):
    return LearnPhaseSpec(
        evidence={"name": "segment", "K": 5, "write_lessons": write_lessons},
        author={"name": "distill_diagnose"},
        curate={"name": "none", "token_budget": BIG})


def _exam_sequence(write_lessons=False):
    sequence = ["baseline", "learn", "exam", "learn", "test"]
    phases = {"baseline": _baseline_spec(), "learn": _learn_spec(write_lessons),
              "exam": ExecutePhaseSpec(
                  role="exam", tasks="train",
                  delivery={"name": "per_skill"},
                  execution={"name": "exam"}),
              "test": ExecutePhaseSpec(
                  role="test", tasks="test",
                  delivery={"name": "whole_bank"},
                  execution={"name": "real"},
                  compare=CompareSpec(against="baseline"))}
    return sequence, phases


def _make_ctx(tmp_path, sequence, phases, handles):
    return StageContext(
        run_dir=tmp_path / "run", store=Store(tmp_path / "run"),
        config=_Config(sequence, phases), usage=None, handles=handles,
        workdir_root=tmp_path / "work")


def _phase(sequence, phases, index):
    name = sequence[index]
    return PhaseInstance(index=index, name=name,
                         label=f"p{index + 1:02d}:{name}",
                         spec=phases[name], task_ids=())


def _seed_d_phase(store, *, strong=True, weak_attempts=2):
    """The baseline phase's on-disk state: the strong arm all-success, the weak
    arm failing every attempt (the baseline gap that authorizes add)."""
    if strong:
        store.save(_trajectory("strong", 0), "trajectories")
    for attempt in range(weak_attempts):
        store.save(_trajectory("weak", attempt, correctness=False),
                   "trajectories")


def _outcomes(run_dir, label):
    path = phase_bank_dir(run_dir, label) / learn_tasks.OUTCOMES_NAME
    return list(read_jsonl(path)) if path.exists() else []


def _params(**overrides):
    from expedistill.methods.evidence import SegmentationParams
    return SegmentationParams(**{"K": 5, "retry_limit": 1,
                                 "write_lessons": False, **overrides})


# ---------------------------------------------------------------------------
# The driver: rule, roles, empties, resume, exhaustion
# ---------------------------------------------------------------------------

def test_segments_the_selected_strong_trajectories_only(tmp_path):
    store = Store(tmp_path / "run")
    _seed_d_phase(store)
    store.save(_trajectory("strong", 0, task_id=ids.make_task_id(
        BENCHMARK, "t2")), "trajectories")   # another task: never touched
    handle = make_fake_handle("segmenter", [json_script([_cut()])])

    result = segment_add_material(
        store=store, task_id=TASK_ID, params=_params(),
        trajectory_select="all", view_rules=None, handle=handle,
        run_dir=tmp_path / "run")

    assert result == {"segmented": 1, "skipped": 0, "empty_skipped": 0}
    # Only the strong trajectory of THIS task paid a model call -- with
    # write_lessons off the weak arm's selection has no consumer, and the
    # other task never entered the rule's per-task set.
    assert len(handle.client.calls) == 1
    subtask_id = ids.make_subtask_id(
        ids.make_trajectory_id(TASK_ID, "strong", 0), 0)
    subtask = store.load("subtasks", subtask_id, Subtask)
    assert subtask.task_id == TASK_ID
    assert subtask.source == "strong" and subtask.success is True
    assert subtask.step_range == (0, 1)
    assert subtask.trajectory_success is True   # the strong arm succeeded
    assert store.ids("subtasks") == [subtask_id]


def test_the_named_rule_decides_how_many_strong_trajectories(tmp_path):
    store = Store(tmp_path / "run")
    for attempt in range(3):
        store.save(_trajectory("strong", attempt), "trajectories")
    store.save(_trajectory("weak", 0, correctness=False), "trajectories")
    handle = make_fake_handle("segmenter",
                              [json_script([_cut()])] * 2)

    result = segment_add_material(
        store=store, task_id=TASK_ID, params=_params(),
        trajectory_select="strong2_weak2", view_rules=None, handle=handle,
        run_dir=tmp_path / "run")

    # The quota rule passes over the both-roles set (old stage 2's
    # contract) and returns 2 strong + 1 weak; with write_lessons off
    # only the strong half is cut. Successes first, stable by trajectory_id: attempts 0 and 1.
    assert result["segmented"] == 2
    assert len(handle.client.calls) == 2
    expected = {ids.make_subtask_id(
                    ids.make_trajectory_id(TASK_ID, "strong", attempt), 0)
                for attempt in (0, 1)}
    assert set(store.ids("subtasks")) == expected


def test_empty_trajectories_are_excluded_and_audited_once(tmp_path):
    store = Store(tmp_path / "run")
    store.save(_trajectory("strong", 0, n_steps=0), "trajectories")
    store.save(_trajectory("weak", 0, n_steps=0, correctness=False),
              "trajectories")
    # Nothing reaches the rule, so nothing reaches the model: an empty
    # script fails the test on any call.
    handle = make_fake_handle("segmenter", [])

    result = segment_add_material(
        store=store, task_id=TASK_ID, params=_params(),
        trajectory_select="all", view_rules=None, handle=handle,
        run_dir=tmp_path / "run")

    assert result == {"segmented": 0, "skipped": 0, "empty_skipped": 2}
    audit = read_jsonl(tmp_path / "run"
                       / "stage2_segments" / "segmentation_audit.jsonl")
    assert audit == [
        {"trajectory_id": ids.make_trajectory_id(TASK_ID, role, 0),
         "status": "skipped",
         "reason": "empty trajectory"}
        for role in ("strong", "weak")]
    # The records are once-only: a second call leaves the audit alone.
    segment_add_material(
        store=store, task_id=TASK_ID, params=_params(),
        trajectory_select="all", view_rules=None, handle=handle,
        run_dir=tmp_path / "run")
    audit_again = read_jsonl(tmp_path / "run"
                             / "stage2_segments" / "segmentation_audit.jsonl")
    assert audit_again == audit


def test_a_mid_batch_crash_resumes_without_re_segmenting(tmp_path):
    store = Store(tmp_path / "run")
    store.save(_trajectory("strong", 0), "trajectories")
    store.save(_trajectory("strong", 1), "trajectories")
    crashing = make_fake_handle(
        "segmenter", [json_script([_cut()]), RuntimeError("crash")])

    # The first call segments trajectory 0, then dies on trajectory 1.
    with pytest.raises(RuntimeError, match="crash"):
        segment_add_material(
            store=store, task_id=TASK_ID, params=_params(),
            trajectory_select="all", view_rules=None, handle=crashing,
            run_dir=tmp_path / "run")
    first = ids.make_subtask_id(
        ids.make_trajectory_id(TASK_ID, "strong", 0), 0)
    assert store.ids("subtasks") == [first]

    # The resume segments ONLY the missing trajectory: K2's
    # stored-subtask skip means one model call, not two.
    resuming = make_fake_handle("segmenter", [json_script([_cut()])])
    result = segment_add_material(
        store=store, task_id=TASK_ID, params=_params(),
        trajectory_select="all", view_rules=None, handle=resuming,
        run_dir=tmp_path / "run")
    assert result == {"segmented": 2, "skipped": 0, "empty_skipped": 0}
    assert len(resuming.client.calls) == 1
    second = ids.make_subtask_id(
        ids.make_trajectory_id(TASK_ID, "strong", 1), 0)
    assert set(store.ids("subtasks")) == {first, second}


def test_exhaustion_is_reported_and_terminal_within_the_run(tmp_path):
    store = Store(tmp_path / "run")
    store.save(_trajectory("strong", 0), "trajectories")
    # retry_limit=1: two attempts, both out of bounds -> D7 exhaustion.
    exhausting = make_fake_handle(
        "segmenter", [json_script([_bad_cut()]), json_script([_bad_cut()])])

    result = segment_add_material(
        store=store, task_id=TASK_ID, params=_params(retry_limit=1),
        trajectory_select="all", view_rules=None, handle=exhausting,
        run_dir=tmp_path / "run")

    assert result == {"segmented": 0, "skipped": 1, "empty_skipped": 0}
    assert store.ids("subtasks") == []
    trajectory_id = ids.make_trajectory_id(TASK_ID, "strong", 0)
    audit = read_jsonl(tmp_path / "run"
                       / "stage2_segments" / "segmentation_audit.jsonl")
    assert [record["trajectory_id"] for record in audit] == [trajectory_id]
    assert audit[0]["status"] == "skipped"
    # Terminal: the re-run makes zero model calls (an empty script
    # fails the test on any) and reports the same shape.
    terminal = make_fake_handle("segmenter", [])
    again = segment_add_material(
        store=store, task_id=TASK_ID, params=_params(retry_limit=1),
        trajectory_select="all", view_rules=None, handle=terminal,
        run_dir=tmp_path / "run")
    assert again == result


# ---------------------------------------------------------------------------
# The learn phase: the add row produces its own material
# ---------------------------------------------------------------------------

def test_an_add_row_cuts_the_strong_trajectory_and_lands_a_skill(
        tmp_path):
    sequence, phases = _exam_sequence()
    handles = {"author": make_fake_handle("author", [_distill_script()]),
               "rewriter": make_fake_handle("rewriter", []),
               "segmenter": make_fake_handle("segmenter",
                                             [json_script([_cut()])])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    # Nothing but the baseline phase's trajectories is seeded -- no Subtask.
    _seed_d_phase(ctx.store)

    result = phase_learn.run(ctx, _phase(sequence, phases, 1))

    subtask_id = ids.make_subtask_id(
        ids.make_trajectory_id(TASK_ID, "strong", 0), 0)
    skill_id = ids.make_skill_id(subtask_id)
    skill = ctx.store.load("skills", skill_id, Skill)
    assert skill.status == "pending" and skill.version == 1
    assert [row["outcome"] for row in _outcomes(ctx.run_dir, "p02:learn")] \
        == ["added"]
    assert result.summary["task_list"] == {"add": 1, "superseded_rows": 0}
    # The cut really ran inside the phase, exactly once (write_lessons is
    # off: the weak arm's trajectories were never segmented).
    assert len(handles["segmenter"].client.calls) == 1
    assert [row["skill_id"] for row in
            load_phase_bank(ctx.run_dir, "p02:learn")] == [skill_id]


def test_an_add_row_without_strong_material_is_recorded_not_raised(
        tmp_path):
    sequence, phases = _exam_sequence()
    # The strong arm never came back (an accepted hole, say): the weak
    # arm's failure still authorizes the add row, and the old stage 3
    # answered this with silence. An empty script fails the test if
    # the wiring ever calls the segmenter on nothing.
    handles = {"author": make_fake_handle("author", []),
               "rewriter": make_fake_handle("rewriter", []),
               "segmenter": make_fake_handle("segmenter", [])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    _seed_d_phase(ctx.store, strong=False)

    result = phase_learn.run(ctx, _phase(sequence, phases, 1))

    assert result.summary["task_list"] == {"add": 1, "superseded_rows": 0}
    assert [row["outcome"] for row in _outcomes(ctx.run_dir, "p02:learn")] \
        == ["skipped_no_material"]
    assert ctx.store.ids("skills") == []
    assert load_phase_bank(ctx.run_dir, "p02:learn") == []
    # The phase FINISHED (no infra failure): a resume replays it whole
    # off the summary, and the replay is byte-identical.
    replay = phase_learn.run(ctx, _phase(sequence, phases, 1))
    assert replay.summary == result.summary
    assert handles["segmenter"].client.calls == []


def test_a_learn_phase_without_add_rows_never_segments(tmp_path):
    # Laziness: a bank that already covers the task decides NO add row,
    # so the segmenter is never consulted -- an empty script fails the
    # test on any call (exam-only and live-only sequences are the same
    # shape: no baseline gap, no add row, no cut).
    sequence = ["baseline", "learn", "L2", "exam", "L3", "test"]
    phases = {"baseline": _baseline_spec(), "learn": _learn_spec(),
              "L2": _learn_spec(), "L3": _learn_spec(),
              "exam": ExecutePhaseSpec(role="exam", tasks="train",
                                    delivery={"name": "per_skill"},
                                    execution={"name": "exam"}),
              "test": ExecutePhaseSpec(
                  role="test", tasks="test",
                  delivery={"name": "whole_bank"},
                  execution={"name": "real"},
                  compare=CompareSpec(against="baseline"))}
    handles = {"author": make_fake_handle("author", []),
               "rewriter": make_fake_handle("rewriter", []),
               "segmenter": make_fake_handle("segmenter", [])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    _seed_d_phase(ctx.store)
    subtask_id = ids.make_subtask_id(
        ids.make_trajectory_id(TASK_ID, "strong", 0), 0)
    covered = Skill(skill_id=ids.make_skill_id(subtask_id),
                    content="[When to use] x\n[Steps]\n1. y\n[Verify] z",
                    subtask_id=subtask_id, source_subtasks=[subtask_id],
                    version=1, status="accepted")
    ctx.store.save(covered, "skills")
    dest = phase_bank_dir(ctx.run_dir, "p02:learn")
    dest.mkdir(parents=True)
    from expedistill.methods.curation import none as curation_none
    curation_none.write_as_is(ctx.store, dest, BIG)

    result = phase_learn.run(ctx, _phase(sequence, phases, 2))

    assert result.summary["task_list"] == {"superseded_rows": 0}
    assert handles["segmenter"].client.calls == []


def test_the_add_path_resume_after_a_crash_cuts_only_the_missing(
        tmp_path):
    # Two strong D trajectories, a crash between the two cuts, then the
    # phase replay: the first trajectory's stored subtask is skipped and
    # only the second one is cut again.
    sequence, phases = _exam_sequence()
    handles = {"author": make_fake_handle("author", []),
               "rewriter": make_fake_handle("rewriter", []),
               "segmenter": make_fake_handle(
                   "segmenter",
                   [json_script([_cut()]), RuntimeError("crash")])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    _seed_d_phase(ctx.store)
    ctx.store.save(_trajectory("strong", 1), "trajectories")

    with pytest.raises(RuntimeError, match="crash"):
        phase_learn.run(ctx, _phase(sequence, phases, 1))
    first = ids.make_subtask_id(
        ids.make_trajectory_id(TASK_ID, "strong", 0), 0)
    assert ctx.store.ids("subtasks") == [first]

    # The replay: one cut (trajectory 1), then the author lands both.
    handles["segmenter"] = make_fake_handle(
        "segmenter", [json_script([_cut()])])
    handles["author"] = make_fake_handle("author",
                                         [_distill_script()] * 2)
    result = phase_learn.run(ctx, _phase(sequence, phases, 1))

    second = ids.make_subtask_id(
        ids.make_trajectory_id(TASK_ID, "strong", 1), 0)
    assert set(ctx.store.ids("subtasks")) == {first, second}
    assert len(handles["segmenter"].client.calls) == 1
    landed = {row["skill_id"] for row in _outcomes(ctx.run_dir, "p02:learn")
              if row["outcome"] == "added"}
    assert landed == {ids.make_skill_id(first), ids.make_skill_id(second)}
    assert result.summary["outcomes"] == {"added": 2}


# ---------------------------------------------------------------------------
# write_lessons (the 2026-10-19 ruling): weak failures are lesson material
# ---------------------------------------------------------------------------

def test_write_lessons_segments_the_weak_trajectories_too(tmp_path):
    store = Store(tmp_path / "run")
    _seed_d_phase(store, weak_attempts=1)
    handle = make_fake_handle("segmenter",
                              [json_script([_cut()]),
                               json_script([_cut(success=False)])])

    result = segment_add_material(
        store=store, task_id=TASK_ID, params=_params(write_lessons=True),
        trajectory_select="all", view_rules=None, handle=handle,
        run_dir=tmp_path / "run")

    assert result == {"segmented": 2, "skipped": 0, "empty_skipped": 0}
    roles = {store.load("subtasks", subtask_id, Subtask).source
             for subtask_id in store.ids("subtasks")}
    assert roles == {"strong", "weak"}


def test_write_lessons_is_on_by_default():
    from expedistill.methods.evidence import SegmentationParams
    assert SegmentationParams(K=5).write_lessons is True


def test_an_add_row_with_write_lessons_lands_a_distill_and_a_diagnose_skill(
        tmp_path):
    sequence, phases = _exam_sequence(write_lessons=True)
    # Sorted trajectory ids: the strong cut first, then the weak one;
    # the author writes in the same order (distill, then diagnose).
    handles = {"author": make_fake_handle(
                   "author", [_distill_script(), _diagnose_script()]),
               "rewriter": make_fake_handle("rewriter", []),
               "segmenter": make_fake_handle(
                   "segmenter", [json_script([_cut()]),
                                 json_script([_cut(success=False)])])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    _seed_d_phase(ctx.store, weak_attempts=1)

    phase_learn.run(ctx, _phase(sequence, phases, 1))

    strong = ids.make_subtask_id(
        ids.make_trajectory_id(TASK_ID, "strong", 0), 0)
    weak = ids.make_subtask_id(
        ids.make_trajectory_id(TASK_ID, "weak", 0), 0)
    prompts = [call["user"] for call in handles["author"].client.calls]
    assert len(prompts) == 2
    assert "The subtask SUCCEEDED" in prompts[0]
    assert "where it went wrong" in prompts[1]
    for subtask_id in (strong, weak):
        skill = ctx.store.load("skills", ids.make_skill_id(subtask_id), Skill)
        # One structure for both: the four sections, no [Trigger].
        assert skill.content.startswith("[When to use]")
        assert "[Trigger]" not in skill.content
    assert {row["skill_id"] for row in _outcomes(ctx.run_dir, "p02:learn")} \
        == {ids.make_skill_id(strong), ids.make_skill_id(weak)}


def test_write_lessons_still_obeys_the_segment_rule(tmp_path):
    # strong_success_weak_failed: a weak segment that SUCCEEDED is not
    # lesson material, so only the strong skill is written.
    sequence, phases = _exam_sequence(write_lessons=True)
    handles = {"author": make_fake_handle("author", [_distill_script()]),
               "rewriter": make_fake_handle("rewriter", []),
               "segmenter": make_fake_handle(
                   "segmenter", [json_script([_cut()]),
                                 json_script([_cut(success=True)])])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    _seed_d_phase(ctx.store, weak_attempts=1)

    phase_learn.run(ctx, _phase(sequence, phases, 1))

    strong = ids.make_subtask_id(
        ids.make_trajectory_id(TASK_ID, "strong", 0), 0)
    assert [row["skill_id"] for row in _outcomes(ctx.run_dir, "p02:learn")] \
        == [ids.make_skill_id(strong)]
