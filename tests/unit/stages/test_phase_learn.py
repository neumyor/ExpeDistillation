"""The learn phase driver (W2-B): decision -> evidence -> author -> curate.

The Wave 2 wiring the Wave 1 products were built for, pinned here: the
report routing off the frozen sequence (first-learn baseline gap, exam
with the in-phase bare control, live against the D baseline), the D4
multi-version merge, the landing semantics (keep = accept, delete with
the decision's reason, revise = version bump, budget exhaustion = no
change), the resume ledgers (crash-window replay, add idempotency),
and the curation sequence-position branch.
"""


import dataclasses
import json
import threading
from types import SimpleNamespace

import pytest

import expedistill.benchmarks.tb  # noqa: F401  (registers the plugin + TB_TASK_PROMPT)
from expedistill.composites import answer_sheets as acceptance_sheets
from expedistill.composites import coverage as coverage_io
from expedistill.composites import live_measurement as lm
from expedistill.composites import phase_report
from expedistill.composites.acceptance_skill_status import exam_attempts
from expedistill.composites.phase_bank import (
    load_phase_bank,
    phase_bank_dir,
)
from expedistill.composites.phase_report import (
    ANSWER_SHEETS_DIR,
    COVERAGE_NAME,
    write_unit_specs,
)
from expedistill.composites.unit_ids import make_unit_id
from expedistill.methods.delivery.folder import skill_folder_manifest
from expedistill.methods.review import strategies
from expedistill.methods.review.contracts import ReviewItem
from expedistill.core import ids
from expedistill.core.config import (
    CompareSpec,
    ExecutePhaseSpec,
    LearnPhaseSpec,
    PhaseInstance,
)
from expedistill.core.answer_sheet import UnitSpec, UnitSummary
from expedistill.core.contracts import TaskListRow
from expedistill.core.models import (
    Attempt,
    Exam,
    Skill,
    Step,
    Subtask,
    Trajectory,
)
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    ModelOutputError,
)
from expedistill.infra.store import (
    Store,
    append_jsonl,
    read_jsonl,
)
from expedistill.llm import json_script, make_fake_handle
from expedistill.methods.author.write import RevisionOutcome
from expedistill.methods.curation import none as curation_none
from expedistill.methods.curation import phase_bank as curation_phase_bank
from expedistill.stages import learn_add, learn_ledger, phase_learn
from expedistill.stages.context import RunSettings, StageContext
from tests.helpers.plugins import ensure_plugin
from tests.helpers.skills import make_skill


@pytest.fixture(autouse=True)
def _tb_plugin_registered():
    """The live tests render the TB task prompt, and other suites leave
    the benchmark registry reset behind them (a pre-existing pattern:
    the registry is global and several tests reset it). Re-register on
    demand instead of depending on import order."""
    ensure_plugin(BENCHMARK)


BENCHMARK = "tb"
TASK_ID = ids.make_task_id(BENCHMARK, "t1")
TRAJECTORY_ID = ids.make_trajectory_id(TASK_ID, "strong", 0)
SUBTASK_ID = ids.make_subtask_id(TRAJECTORY_ID, 0)
SKILL_ID = ids.make_skill_id(SUBTASK_ID)
EXAM_ID = ids.make_exam_id(SKILL_ID, 1)
WEAK_PROFILE = "a weak model that needs short sentences"
BIG = 10 ** 6

MARKED_A = "\n".join([
    "[When to use] The service rejects connections and needs a restart.",
    "[Steps]",
    "1. Check the service status with the status command.",
    "2. Restart the service with the restart command.",
    "[Do not use when] The status command already reports running.",
])
MARKED_B = "\n".join([
    "[When to use] The service rejects connections and a restart is due.",
    "[Steps]",
    "1. Run the status command to see the service state.",
    "2. Restart the service with its restart command.",
    "[Do not use when] The status command already reports running.",
])


# ---------------------------------------------------------------------------
# Fixtures: the v4 config stand-in, the sequence, the seeded run directory
# ---------------------------------------------------------------------------

def _baseline_spec():
    return ExecutePhaseSpec(
        role="baseline", tasks="train", delivery={"name": "none"},
        execution={"name": "real",
                   "arms": ["executor_strong", "executor_weak"]})


def _exam_spec():
    return ExecutePhaseSpec(
        role="exam", tasks="train", delivery={"name": "exam_folder"},
        execution={"name": "exam"})


def _live_spec():
    return ExecutePhaseSpec(
        role="live", tasks="train", delivery={"name": "group_by_task"},
        execution={"name": "real", "n_attempts": 2},
        compare=CompareSpec(against="baseline", rule="strict_rate_gain"))


def _test_spec(against="fresh_bare", delivery="retrieval"):
    return ExecutePhaseSpec(
        role="test", tasks="test", delivery={"name": delivery},
        execution={"name": "real"}, compare=CompareSpec(against=against))


def _learn_spec():
    return LearnPhaseSpec(
        review={"name": "review", "K": 5, "write_lessons": False},
        author={"name": "distill_diagnose"},
        curate={"name": "none", "token_budget": BIG})


class _Config:
    """The v4 config stand-in: the sequence/phases products W2-C's flip
    puts on ExperimentConfig, plus the globals the driver reads."""

    def __init__(self, sequence, phases):
        self.sequence = sequence
        self.phases = phases
        self.benchmark = BENCHMARK
        self.weak_profile = WEAK_PROFILE
        self.use_weak_profile = True
        self.n_attempts_strong = 1
        self.n_attempts_weak = 2
        self.rules = {"task_select": "any_weak_failed",
                      "segment_select": "strong_success_weak_failed",
                      "trajectory_select": "all"}
        self.methods = {"curation": {"name": "none", "token_budget": BIG}}

    def effective_weak_profile(self):
        return self.weak_profile if self.use_weak_profile else ""


def _exam_sequence(retry_limit=1):
    """[D, L] + [A, L] x (retry_limit + 1) + [C] -- upgrade_v3_config's
    image of the exam method."""
    phases = {"baseline": _baseline_spec(), "exam": _exam_spec(), "learn": _learn_spec(),
              "test": _test_spec()}
    sequence = ["baseline", "learn"] + ["exam", "learn"] * (retry_limit + 1) + ["test"]
    return sequence, phases


def _live_sequence():
    return (["baseline", "learn", "live", "learn", "live", "learn", "test"],
            {"baseline": _baseline_spec(), "live": _live_spec(), "learn": _learn_spec(),
             "test": _test_spec(against="baseline", delivery="whole_bank")})


def _make_ctx(tmp_path, sequence, phases, handles, workers=1):
    return StageContext(
        run_dir=tmp_path / "run", store=Store(tmp_path / "run"),
        config=_Config(sequence, phases), usage=None, handles=handles,
        workdir_root=tmp_path / "work",
        run_settings=RunSettings(workers=workers))


def _phase(sequence, phases, index):
    name = sequence[index]
    return PhaseInstance(index=index, name=name,
                         label=f"p{index + 1:02d}:{name}",
                         spec=phases[name], task_ids=())


def _install_curation(monkeypatch):
    """Record which curation entry ran and with what mapping; delegate to
    the REAL entries so the artifact integration (the pending-member
    passthrough included) is exercised."""
    calls = []
    real_produce = curation_phase_bank.produce
    real_as_is = curation_phase_bank.produce_as_is

    def produce(ctx, curate, label):
        calls.append(("produce", curate, label))
        return real_produce(ctx, curate, label)

    def produce_as_is(ctx, curate, label):
        calls.append(("produce_as_is", curate, label))
        return real_as_is(ctx, curate, label)

    monkeypatch.setattr(curation_phase_bank, "produce", produce)
    monkeypatch.setattr(curation_phase_bank, "produce_as_is", produce_as_is)
    return calls


def _skill(content=MARKED_A, version=1, status="pending", segment=0):
    subtask_id = ids.make_subtask_id(TRAJECTORY_ID, segment)
    return make_skill(subtask_id, content, version=version, status=status)


def _seed_baseline(store, weak_correctness=False, attempts=2):
    """The baseline phase's on-disk state: both arms of one task."""
    strong = Trajectory(
        trajectory_id=ids.make_trajectory_id(TASK_ID, "strong", 0),
        task_id=TASK_ID, benchmark=BENCHMARK, model_role="strong",
        model_name="s", attempt_index=0,
        steps=[Step(action="do s", observation="ok")],
        correctness=True)
    store.save(strong, "trajectories")
    weak = []
    for attempt in range(attempts):
        trajectory = Trajectory(
            trajectory_id=ids.make_trajectory_id(TASK_ID, "weak", attempt),
            task_id=TASK_ID, benchmark=BENCHMARK, model_role="weak",
            model_name="w", attempt_index=attempt,
            steps=[Step(action="do w", observation="no")],
            correctness=weak_correctness)
        store.save(trajectory, "trajectories")
        weak.append(trajectory)
    specs = [
        UnitSpec(
            unit_id=make_unit_id(
                phase_label="p01:baseline", benchmark=BENCHMARK,
                task_ref=TASK_ID, role="strong", skills_injected=None),
            phase_label="p01:baseline", task_ref=TASK_ID, role="strong",
            arm="strong", expected_attempts=1, injected_skills=None,
            no_valid_cause="all_holes"),
        UnitSpec(
            unit_id=make_unit_id(
                phase_label="p01:baseline", benchmark=BENCHMARK,
                task_ref=TASK_ID, role="weak", skills_injected=None),
            phase_label="p01:baseline", task_ref=TASK_ID, role="weak",
            arm="weak", expected_attempts=attempts, injected_skills=None,
            no_valid_cause="all_holes"),
    ]
    cov = coverage_io.baseline_coverage(store, specs, [])
    executions = [
        acceptance_sheets.execution_from_trajectory(
            strong, unit_id=specs[0].unit_id,
            phase_label="p01:baseline", arm="strong")]
    executions.extend(
        acceptance_sheets.execution_from_trajectory(
            trajectory, unit_id=specs[1].unit_id,
            phase_label="p01:baseline", arm="weak")
        for trajectory in weak)
    sheets = acceptance_sheets.assemble_answer_sheets(
        executions, cov, phase_label="p01:baseline")
    acceptance_sheets.grade_answer_sheets(sheets, "tb_scorer")
    dest = phase_bank_dir(store.run_dir, "p01:baseline")
    acceptance_sheets.write_answer_sheets(
        sheets, dest / ANSWER_SHEETS_DIR)
    acceptance_sheets.write_coverage(cov, dest / COVERAGE_NAME)
    write_unit_specs(specs, dest)


def _seed_segment(store, success=True):
    subtask = Subtask(
        subtask_id=SUBTASK_ID, task_id=TASK_ID, trajectory_id=TRAJECTORY_ID,
        source="strong", trajectory_success=True, segment_index=0,
        step_range=(0, 1), label="restart the service",
        description="restart the stuck service and verify",
        success=success)
    store.save(subtask, "subtasks")
    return subtask


def _seed_finished_learn(store, run_dir, label="p02:learn"):
    """An earlier learn phase's artifact (empty: nothing accepted yet) --
    the exam learn phases read the previous learn phase's bank."""
    dest = phase_bank_dir(run_dir, label)
    dest.mkdir(parents=True, exist_ok=True)
    curation_none.write_as_is(store, dest, BIG)


def _seed_exam_phase(store, *, with_skill_verdict, version=1):
    """The exam phase's on-disk state: the skill, its exam, a wrong bare
    answer and one judged with-skill attempt of ``version``."""
    skill = _skill()
    store.save(skill, "skills")
    store.save(Exam(
        exam_id=EXAM_ID, skill_id=skill.skill_id, skill_version=1,
        scene_text="How do you restart?", expected_behavior="restart and verify",
        reference_answer="restart and verify",
        source_ref={"phase_label": "p01:baseline", "scene_step": 0}),
        "exams")
    _seed_attempt(store, 0, verdict=False, purpose="bare")
    _seed_attempt(store, 1, skill=skill.skill_id, version=version,
                  verdict=with_skill_verdict)
    _seed_exam_artifacts(store)
    return skill


def _seed_exam_artifacts(store, label="p03:exam"):
    """Write the exam phase's trial-level artifacts beside the store rows.

    The learn phase reads a completed execute phase as answer sheets +
    unit specs (``phase_report.read_phase_report``); the attempt rows
    alone are only the legacy fallback.  Mirrors the driver's
    construction (``stages/phase_execute/exam.py``): one bare control
    unit plus one measured unit per judged (skill, version).
    """
    bare_unit = make_unit_id(
        phase_label=label, benchmark=BENCHMARK, task_ref=EXAM_ID,
        role="weak", skills_injected=None)
    specs = [UnitSpec(
        unit_id=bare_unit, phase_label=label, task_ref=EXAM_ID,
        role="weak", arm=None, expected_attempts=1,
        injected_skills=None)]
    executions = []
    units_by_key = {}
    for attempt in exam_attempts(store, EXAM_ID):
        if attempt.judge_verdict is None:
            continue
        if attempt.purpose == "bare":
            executions.append(acceptance_sheets.execution_from_attempt(
                attempt, unit_id=bare_unit, phase_label=label))
            continue
        if attempt.purpose != "acceptance":
            continue
        key = (attempt.skill_id, attempt.skill_version)
        unit_id = units_by_key.get(key)
        if unit_id is None:
            injected = [{"skill_id": attempt.skill_id,
                         "version": attempt.skill_version}]
            unit_id = make_unit_id(
                phase_label=label, benchmark=BENCHMARK, task_ref=EXAM_ID,
                role="weak", skills_injected=injected)
            units_by_key[key] = unit_id
            specs.append(UnitSpec(
                unit_id=unit_id, phase_label=label, task_ref=EXAM_ID,
                role="weak", arm=None, expected_attempts=1,
                injected_skills=injected))
        executions.append(acceptance_sheets.execution_from_attempt(
            attempt, unit_id=unit_id, phase_label=label))
    sheets = acceptance_sheets.assemble_answer_sheets(
        executions, [], phase_label=label)
    acceptance_sheets.grade_answer_sheets(sheets, "exam_judge")
    dest = phase_bank_dir(store.run_dir, label)
    acceptance_sheets.write_answer_sheets(
        sheets, dest / ANSWER_SHEETS_DIR, render_markdown=False)
    acceptance_sheets.write_coverage([], dest / COVERAGE_NAME)
    write_unit_specs(specs, dest)


def _seed_attempt(store, serial, *, skill=None, version=None, verdict,
                  reason="the answer omits the verification step",
                  purpose="acceptance"):
    store.save(Attempt(
        attempt_id=ids.make_attempt_id(EXAM_ID, serial), exam_id=EXAM_ID,
        skill_id=skill, skill_version=version, purpose=purpose,
        weak_answer="I would restart it.", judge_verdict=verdict,
        judge_reason=reason if verdict is False else ""), "attempts")


def _seed_live_round(store, *, better=False):
    """The live phase's on-disk state: a failed weak baseline, the round-0
    store, a pending group and its frozen audit line."""
    _seed_baseline(store, weak_correctness=False)
    round_dir = store.run_dir / "stage4_acceptance" / "live_run" / "round00"
    round_store = Store(round_dir)
    skills = [_skill(segment=segment) for segment in range(2)]
    for skill in skills:
        store.save(skill, "skills")
    members = [{"skill_id": skill.skill_id, "version": skill.version}
               for skill in skills]
    manifest = skill_folder_manifest(skills, "when_to_use",
                                     accepted_only=False)
    cat = f"cat {manifest['files'][0]['filename']}\n"
    for attempt in range(2):
        round_store.save(Trajectory(
            trajectory_id=ids.make_trajectory_id(TASK_ID, "weak", attempt),
            task_id=TASK_ID, benchmark=BENCHMARK, model_role="weak",
            model_name="w", attempt_index=attempt,
            skills_injected=members,
            steps=[Step(action=cat, observation=""),
                   Step(action=f"do x {attempt}",
                        observation=f"obs {attempt}")],
            correctness=better), "trajectories")
    spec = UnitSpec(
        unit_id=make_unit_id(
            phase_label="p03:live", benchmark=BENCHMARK,
            task_ref=TASK_ID, role="weak", skills_injected=members),
        phase_label="p03:live", task_ref=TASK_ID, role="weak", arm=None,
        expected_attempts=2, injected_skills=members)
    cov = coverage_io.live_coverage(
        store, [spec], round_=0, terminal_overrides={})
    executions = [
        acceptance_sheets.execution_from_trajectory(
            round_store.load("trajectories",
                             ids.make_trajectory_id(TASK_ID, "weak", attempt),
                             Trajectory),
            unit_id=spec.unit_id, phase_label="p03:live", arm=None,
            folder=manifest)
        for attempt in range(2)]
    sheets = acceptance_sheets.assemble_answer_sheets(
        executions, cov, phase_label="p03:live")
    acceptance_sheets.grade_answer_sheets(sheets, "tb_scorer")
    live_dir = phase_bank_dir(store.run_dir, "p03:live")
    acceptance_sheets.write_answer_sheets(
        sheets, live_dir / ANSWER_SHEETS_DIR)
    acceptance_sheets.write_coverage(cov, live_dir / COVERAGE_NAME)
    write_unit_specs([spec], live_dir)
    _, block = lm.survivors_and_block(store, TASK_ID)
    append_jsonl(round_dir / lm.INJECTION_AUDIT_NAME, {
        "schema": "injection-audit-v1", "task_id": TASK_ID, "round": 0,
        "members": members,
        "injection_block_sha256": lm._block_sha(block),
        "injection_tokens": 40,
    })
    append_jsonl(store.run_dir / "stage4_acceptance/live_run/verdicts.jsonl", {
        "schema": "live-verdict-v1", "task_id": TASK_ID, "round": 0,
        "better": better,
    })
    return skills


def _group_plan(*actions):
    return json_script({
        "diagnosis": "the injected skill did not help",
        "skill_actions": [
            {"skill_id": ids.make_skill_id(
                ids.make_subtask_id(TRAJECTORY_ID, segment)),
             "action": action, "problem": problem}
            for segment, (action, problem) in enumerate(actions)],
    })


def _revision_script(text=MARKED_B):
    return json_script({"skill_text": text, "failure_category": "missing_step",
                        "revision_reason": "sharper trigger",
                        "summary": "a revised one-liner"})


def _rewrite_script(text=MARKED_B):
    return json_script({"skill_text": text, "failure_category": "missing_step",
                        "rewrite_reason": "spelled out verification",
                        "summary": "a rewritten one-liner"})


def _distill_script():
    return json_script({
        "when_to_use": "When the service rejects connections.",
        "steps": ["Run the status command on the service.",
                  "Restart the service with its restart command."],
        "not_applicable_when": "The service already reports running.",
        "summary": "restore a service that rejects connections",
    })


def _outcomes(run_dir, label):
    path = phase_bank_dir(run_dir, label) / learn_ledger.OUTCOMES_NAME
    return list(read_jsonl(path)) if path.exists() else []


def _task_list_rows(run_dir, label):
    path = phase_bank_dir(run_dir, label) / learn_ledger.TASK_LIST_NAME
    return list(read_jsonl(path)) if path.exists() else []


# ---------------------------------------------------------------------------
# The D4 merge (pure)
# ---------------------------------------------------------------------------

def _report_row(unit_id, version):
    return UnitSummary(
        unit_id=unit_id, phase_label="p03:exam", task_ref="",
        role="weak", arm=None, expected_attempts=1,
        injected_skills=[{"skill_id": SKILL_ID, "version": version}],
        valid_count=1, success_count=0, status="normal", cause="")


def test_merge_keeps_the_highest_version_row():
    old = TaskListRow(target_kind="skill", target_id=SKILL_ID,
                      action="keep", reason="improved", basis_unit="u1")
    new = TaskListRow(target_kind="skill", target_id=SKILL_ID,
                      action="revise", reason="no_gain", basis_unit="u2")
    group = TaskListRow(target_kind="group", target_id=TASK_ID,
                        action="keep", reason="improved", basis_unit="u3")

    effective, superseded = strategies.merge_exam_rows(
        [old, group, new], [_report_row("u1", 1), _report_row("u2", 2)])

    assert effective == [group, new]      # order preserved, v1 dropped
    assert superseded == ["u1"]


def test_merge_refuses_a_row_without_a_report_row():
    row = TaskListRow(target_kind="skill", target_id=SKILL_ID,
                      action="keep", reason="improved", basis_unit="u1")
    with pytest.raises(CodeBugError, match="does not carry"):
        strategies.merge_exam_rows([row], [])


def test_merge_refuses_two_rows_of_one_version():
    first = TaskListRow(target_kind="skill", target_id=SKILL_ID,
                       action="keep", reason="improved", basis_unit="u1")
    second = TaskListRow(target_kind="skill", target_id=SKILL_ID,
                         action="revise", reason="no_gain", basis_unit="u1b")
    with pytest.raises(CodeBugError, match="share version"):
        strategies.merge_exam_rows(
            [first, second], [_report_row("u1", 2), _report_row("u1b", 2)])


# ---------------------------------------------------------------------------
# The driver's contract and its loud refusals
# ---------------------------------------------------------------------------

def test_a_learn_phase_needs_the_v4_sequence_products(tmp_path):
    sequence, phases = _exam_sequence()
    ctx = _make_ctx(tmp_path, sequence, phases, handles={})
    ctx.config.sequence = None
    with pytest.raises(CodeBugError, match="v4 config products"):
        phase_learn.run(ctx, _phase(sequence, phases, 1))


def test_a_learn_phase_without_a_preceding_execute_phase_is_refused(tmp_path):
    sequence, phases = _exam_sequence()
    ctx = _make_ctx(tmp_path, ["learn", "baseline", "exam", "test"], phases, handles={})
    with pytest.raises(CodeBugError, match="no preceding execute phase"):
        phase_learn.run(ctx, _phase(["learn", "baseline", "exam", "test"], phases, 0))


def test_a_learn_phase_after_the_final_test_phase_is_refused(tmp_path):
    sequence = ["baseline", "learn", "test", "L2"]
    phases = {"baseline": _baseline_spec(), "learn": _learn_spec(),
              "test": _test_spec(), "L2": _learn_spec()}
    ctx = _make_ctx(tmp_path, sequence, phases, handles={})
    with pytest.raises(CodeBugError,
                       match="test results do not flow into learning"):
        phase_learn.run(ctx, _phase(sequence, phases, 3))


def test_a_label_off_the_frozen_derivation_is_refused(tmp_path):
    sequence, phases = _exam_sequence()
    ctx = _make_ctx(tmp_path, sequence, phases, handles={})
    phase = PhaseInstance(index=1, name="learn", label="p9:wrong",
                          spec=phases["learn"])
    with pytest.raises(CodeBugError, match="frozen derivation"):
        phase_learn.run(ctx, phase)


# ---------------------------------------------------------------------------
# The first learn phase: the baseline gap -> add path
# ---------------------------------------------------------------------------

def test_execute_add_rows_parallelizes_independent_targets(
        tmp_path, monkeypatch):
    sequence, phases = _exam_sequence()
    handles = {"author": make_fake_handle("author", []),
               "rewriter": make_fake_handle("rewriter", []),
               "segmenter": make_fake_handle("segmenter", [])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles, workers=4)
    phase = _phase(sequence, phases, 1)
    phase_bank_dir(ctx.run_dir, phase.label).mkdir(parents=True, exist_ok=True)
    items = [ReviewItem(row=TaskListRow(
        target_kind="task", target_id=f"t{index}",
        action="add", reason="", basis_unit=""))
        for index in range(4)]
    barrier = threading.Barrier(len(items))
    seen = []
    lock = threading.Lock()

    def fake_land(ctx, phase, item, phase_dir, source_phase_label=""):
        with lock:
            seen.append(item.row.target_id)
        barrier.wait(timeout=5)

    monkeypatch.setattr(learn_add, "_land_add", fake_land)
    failed = learn_add.execute_add_rows(ctx, phase, items)

    assert failed == set()
    assert sorted(seen) == ["t0", "t1", "t2", "t3"]
    assert len(_task_list_rows(ctx.run_dir, "p02:learn")) == 4


def test_the_first_learn_phase_adds_skills_for_failed_bare_tasks(
        tmp_path, monkeypatch):
    sequence, phases = _exam_sequence()
    # The segmenter is never called here (the segment is seeded), and an
    # empty script says so: the add path's segmentation wiring must
    # find the stored subtask and make zero model calls.
    handles = {"author": make_fake_handle("author", [_distill_script()]),
               "rewriter": make_fake_handle("rewriter", []),
               "segmenter": make_fake_handle("segmenter", [])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    calls = _install_curation(monkeypatch)
    _seed_baseline(ctx.store, weak_correctness=False)
    _seed_segment(ctx.store)

    result = phase_learn.run(ctx, _phase(sequence, phases, 1))

    assert result.label == "p02:learn"
    # One add row -> one pending skill, the stage-3 grain and status.
    skill = ctx.store.load("skills", SKILL_ID, Skill)
    assert skill.status == "pending" and skill.version == 1
    assert "[When to use]" in skill.content
    rows = _task_list_rows(ctx.run_dir, "p02:learn")
    assert rows[0]["action"] == "add"
    assert rows[0]["target_id"] == TASK_ID
    assert [row["outcome"] for row in _outcomes(ctx.run_dir, "p02:learn")] \
        == ["added"]
    assert result.summary["task_list"]["add"] == 1
    assert result.summary["outcomes"] == {"added": 1}
    assert result.summary["checks_left"] == 2      # the two later exam phases
    # The intermediate branch: as-is, not the configured curation, and
    # the curate mapping travels with the call (W2-C's frozen contract).
    assert calls == [("produce_as_is", phases["learn"].curate, "p02:learn")]
    # The artifact carries EVERY member: the new skill is pending, and
    # the next exam phase's universe comes out of this artifact.
    rows = load_phase_bank(ctx.run_dir, "p02:learn")
    assert [row["skill_id"] for row in rows] == [SKILL_ID]
    assert rows[0]["status"] == "pending"
    assert result.summary["bank"]["members"] == [SKILL_ID]


def test_a_finished_learn_phase_is_skipped_on_replay(tmp_path,
                                                       monkeypatch):
    # The replay-after-a-later-crash model: a COMPLETED learn phase is
    # skipped whole. Re-running it would double the outcome receipts
    # and rewrite the bank artifact from the CURRENT store (a later
    # phase's accepted skills would leak into this one's snapshot).
    sequence, phases = _exam_sequence()
    handles = {"author": make_fake_handle("author", [_distill_script()]),
               "rewriter": make_fake_handle("rewriter", []),
               "segmenter": make_fake_handle("segmenter", [])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    _install_curation(monkeypatch)
    _seed_baseline(ctx.store, weak_correctness=False)
    _seed_segment(ctx.store)
    phase = _phase(sequence, phases, 1)
    first = phase_learn.run(ctx, phase)
    artifact = (phase_bank_dir(ctx.run_dir, phase.label)
                / "skill_bank.jsonl")
    bytes_first = artifact.read_bytes()
    outcomes_first = _outcomes(ctx.run_dir, phase.label)
    calls_first = len(handles["author"].client.calls)

    second = phase_learn.run(ctx, phase)

    # Same summary, same artifact bytes, no receipt doubling -- and no
    # model call was even made.
    assert second.summary == first.summary
    assert artifact.read_bytes() == bytes_first
    assert _outcomes(ctx.run_dir, phase.label) == outcomes_first
    assert len(handles["author"].client.calls) == calls_first
    # A later phase accepting the skill does NOT leak into the replay.
    skill = ctx.store.load("skills", SKILL_ID, Skill)
    skill.status = "accepted"
    ctx.store.save(skill, "skills")
    third = phase_learn.run(ctx, phase)
    assert third.summary == first.summary
    assert artifact.read_bytes() == bytes_first


def test_the_add_path_is_idempotent_across_a_crash(tmp_path, monkeypatch):
    sequence, phases = _exam_sequence()
    handles = {"author": make_fake_handle("author", []),
               "rewriter": make_fake_handle("rewriter", []),
               "segmenter": make_fake_handle("segmenter", [])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    _install_curation(monkeypatch)
    _seed_baseline(ctx.store, weak_correctness=False)
    _seed_segment(ctx.store)
    ctx.store.save(_skill(), "skills")   # the crashed call already landed it

    result = phase_learn.run(ctx, _phase(sequence, phases, 1))

    assert handles["author"].client.calls == []      # nothing re-asked
    assert [row["outcome"] for row in _outcomes(ctx.run_dir, "p02:learn")] \
        == ["skipped_existing"]
    assert result.summary["task_list"]["add"] == 1
    assert result.summary["outcomes"] == {"skipped_existing": 1}


def test_the_first_learn_phase_reads_the_run_level_bank_fallback(
        tmp_path, monkeypatch):
    """The pre-learn bank fallback: the FIRST learn phase has no
    previous artifact, so its bank is the run-level frozen bank. A
    from-scratch run has none yet (empty -- the baseline-gap adds fire
    as before, the existing test above); a pre-seeded bank (the
    assembled run's currency) whose anchor covers the weak-failed task
    suppresses the re-offer, because the frozen bank is the measurement
    object. A bank anchored elsewhere does NOT block the path -- the
    uncovered task still gets its add."""
    sequence, phases = _exam_sequence()
    handles = {"author": make_fake_handle("author", [_distill_script()]),
               "rewriter": make_fake_handle("rewriter", []),
               "segmenter": make_fake_handle("segmenter", [])}
    _install_curation(monkeypatch)

    # A bank anchored on THIS task: the baseline gap is covered.
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    _seed_baseline(ctx.store, weak_correctness=False)
    _seed_segment(ctx.store)
    covered = _skill(status="accepted")
    ctx.store.save(covered, "skills")
    ctx.store.append_bank(covered.to_dict())

    result = phase_learn.run(ctx, _phase(sequence, phases, 1))

    assert result.summary["task_list"] == {"superseded_rows": 0}
    assert _task_list_rows(ctx.run_dir, "p02:learn") == []
    assert handles["author"].client.calls == []

    # A bank anchored on ANOTHER task: the gap stays open, the add fires.
    other_task = ids.make_task_id(BENCHMARK, "t2")
    other_traj = ids.make_trajectory_id(other_task, "strong", 0)
    other_subtask = ids.make_subtask_id(other_traj, 0)
    elsewhere = Skill(skill_id=ids.make_skill_id(other_subtask),
                      content=MARKED_A, subtask_id=other_subtask,
                      source_subtasks=[other_subtask], version=1,
                      status="accepted")
    ctx2 = StageContext(
        run_dir=tmp_path / "run-elsewhere",
        store=Store(tmp_path / "run-elsewhere"),
        config=_Config(sequence, phases), usage=None, handles=handles,
        workdir_root=tmp_path / "work-elsewhere")
    _seed_baseline(ctx2.store, weak_correctness=False)
    _seed_segment(ctx2.store)
    ctx2.store.save(elsewhere, "skills")
    ctx2.store.append_bank(elsewhere.to_dict())

    result2 = phase_learn.run(ctx2, _phase(sequence, phases, 1))

    assert result2.summary["task_list"]["add"] == 1
    assert result2.summary["outcomes"] == {"added": 1}


def test_the_previous_learn_artifact_covers_the_baseline_gap(
        tmp_path, monkeypatch):
    # A second baseline-gap learn over a bank that already holds the
    # task's skill decides no add row at all (the previous artifact,
    # not the store, is the decision's "no skill yet").
    sequence = ["baseline", "learn", "L2", "exam", "L3", "test"]
    phases = {"baseline": _baseline_spec(), "learn": _learn_spec(),
              "L2": _learn_spec(), "L3": _learn_spec(),
              "exam": _exam_spec(), "test": _test_spec()}
    handles = {"author": make_fake_handle("author", []),
               "rewriter": make_fake_handle("rewriter", [])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    _install_curation(monkeypatch)
    _seed_baseline(ctx.store, weak_correctness=False)
    _seed_segment(ctx.store)
    ctx.store.save(_skill(status="accepted"), "skills")
    dest = phase_bank_dir(ctx.run_dir, "p02:learn")
    dest.mkdir(parents=True)
    curation_none.write_as_is(ctx.store, dest, BIG)

    result = phase_learn.run(ctx, _phase(sequence, phases, 2))

    assert result.summary["task_list"] == {"superseded_rows": 0}
    assert _task_list_rows(ctx.run_dir, "p03:L2") == []
    assert handles["author"].client.calls == []


# ---------------------------------------------------------------------------
# The exam learn phases: keep / revise / delete
# ---------------------------------------------------------------------------

def test_an_improved_exam_row_accepts_the_skill(tmp_path, monkeypatch):
    sequence, phases = _exam_sequence()
    handles = {"author": make_fake_handle("author", []),
               "rewriter": make_fake_handle("rewriter", [])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    calls = _install_curation(monkeypatch)
    _seed_finished_learn(ctx.store, ctx.run_dir)
    skill = _seed_exam_phase(ctx.store, with_skill_verdict=True)

    result = phase_learn.run(ctx, _phase(sequence, phases, 3))

    stored = ctx.store.load("skills", skill.skill_id, Skill)
    assert stored.status == "accepted"
    assert [row["outcome"] for row in _outcomes(ctx.run_dir, "p04:learn")] \
        == ["accepted"]
    assert result.summary["task_list"] == {"keep": 1, "superseded_rows": 0}
    assert calls[0][0] == "produce_as_is"
    # The bank artifact now carries the accepted skill.
    assert result.summary["bank"]["members"] == [skill.skill_id]


def test_a_miss_with_a_check_left_revises_the_skill(tmp_path, monkeypatch):
    sequence, phases = _exam_sequence()
    handles = {"author": make_fake_handle("author", []),
               "rewriter": make_fake_handle("rewriter", [_rewrite_script()])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    _install_curation(monkeypatch)
    _seed_finished_learn(ctx.store, ctx.run_dir)
    skill = _seed_exam_phase(ctx.store, with_skill_verdict=False)

    result = phase_learn.run(ctx, _phase(sequence, phases, 3))

    stored = ctx.store.load("skills", skill.skill_id, Skill)
    assert stored.version == 2
    assert stored.content == MARKED_B
    assert stored.status == "pending"          # the status never moved
    assert stored.summary == "a rewritten one-liner"
    # The history entry keeps the OLD summary next to the old content:
    # what the reading agent saw before the rewrite.
    assert stored.rewrite_history[0]["reason"] == "spelled out verification"
    assert stored.rewrite_history[0]["summary"] == skill.summary
    prompt = handles["rewriter"].client.calls[0]["user"]
    assert "How do you restart?" in prompt    # the exam question
    assert "I would restart it." in prompt    # the weak answer
    assert "omits the verification step" in prompt
    assert result.summary["task_list"] == {"revise": 1, "superseded_rows": 0}


def test_a_revision_without_a_new_version_is_no_change(tmp_path, monkeypatch):
    sequence, phases = _exam_sequence()
    handles = {
        "author": make_fake_handle("author", []),
        "rewriter": make_fake_handle("rewriter", [
            ModelOutputError("SKILL_REWRITE: bad json"),
            ModelOutputError("SKILL_REWRITE: worse"),
        ])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    _install_curation(monkeypatch)
    _seed_finished_learn(ctx.store, ctx.run_dir)
    skill = _seed_exam_phase(ctx.store, with_skill_verdict=False)

    result = phase_learn.run(ctx, _phase(sequence, phases, 3))

    stored = ctx.store.load("skills", skill.skill_id, Skill)
    assert stored.version == 1 and stored.status == "pending"
    assert stored.content == MARKED_A
    assert result.summary["task_list"] == {"revise": 1,
                                          "superseded_rows": 0}
    assert result.summary["outcomes"] == {"unchanged": 1}
    assert len(handles["rewriter"].client.calls) == 2


def test_the_last_learn_phase_deletes_a_still_failing_skill(
        tmp_path, monkeypatch):
    # retry_limit=0: [D, L, A, L, C] -- the second L is the last learn
    # phase, no check follows, and the table deletes (retry_exhausted).
    sequence, phases = _exam_sequence(retry_limit=0)
    handles = {"author": make_fake_handle("author", []),
               "rewriter": make_fake_handle("rewriter", [])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    calls = _install_curation(monkeypatch)
    _seed_finished_learn(ctx.store, ctx.run_dir)
    skill = _seed_exam_phase(ctx.store, with_skill_verdict=False)

    result = phase_learn.run(ctx, _phase(sequence, phases, 3))

    stored = ctx.store.load("skills", skill.skill_id, Skill)
    assert stored.status == "discarded"
    assert stored.discard_reason == "retry_exhausted"
    assert [row["outcome"] for row in _outcomes(ctx.run_dir, "p04:learn")] \
        == ["discarded"]
    assert result.summary["task_list"] == {"delete": 1, "superseded_rows": 0}
    assert calls[0][0] == "produce"           # the LAST learn phase curates
    assert calls[0][1] == phases["learn"].curate
    assert result.summary["bank"]["members"] == []


def test_the_d4_merge_decides_for_the_current_version(tmp_path, monkeypatch):
    # v1 passed (an earlier phase's attempt is still in the shared
    # attempts store), v2 fails now: both rows come out of the
    # decision, the merge keeps v2's revise and names v1's keep.
    sequence, phases = _exam_sequence()
    handles = {"author": make_fake_handle("author", []),
               "rewriter": make_fake_handle("rewriter", [_rewrite_script()])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    _install_curation(monkeypatch)
    _seed_finished_learn(ctx.store, ctx.run_dir)
    skill = _skill(version=2)
    ctx.store.save(skill, "skills")
    ctx.store.save(Exam(
        exam_id=EXAM_ID, skill_id=skill.skill_id, skill_version=2,
        scene_text="How do you restart?", expected_behavior="restart and verify",
        reference_answer="restart and verify",
        source_ref={"phase_label": "p01:baseline", "scene_step": 0}),
        "exams")
    _seed_attempt(ctx.store, 0, verdict=False, purpose="bare")
    _seed_attempt(ctx.store, 1, skill=skill.skill_id, version=1,
                  verdict=True)
    _seed_attempt(ctx.store, 2, skill=skill.skill_id, version=2,
                  verdict=False)
    _seed_exam_artifacts(ctx.store)

    result = phase_learn.run(ctx, _phase(sequence, phases, 3))

    rows = _task_list_rows(ctx.run_dir, "p04:learn")
    assert [row["action"] for row in rows if "action" in row] == ["revise"]
    assert rows[-1]["superseded_units"]        # v1's keep is named
    stored = ctx.store.load("skills", skill.skill_id, Skill)
    assert stored.version == 3                 # v2 was rewritten, not v1
    assert result.summary["task_list"] == {"revise": 1, "superseded_rows": 1}


def test_a_crashed_revision_is_replayed_from_the_ledger(tmp_path, monkeypatch):
    # The crash window: the version bumped but the receipt row never
    # landed. The resume reads the revision ledger, recognizes the
    # landed revision and never re-asks the author.
    sequence, phases = _exam_sequence()
    handles = {"author": make_fake_handle("author", []),
               "rewriter": make_fake_handle("rewriter", [])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    _install_curation(monkeypatch)
    _seed_finished_learn(ctx.store, ctx.run_dir)
    skill = _seed_exam_phase(ctx.store, with_skill_verdict=False)
    skill.content = MARKED_B
    skill.version = 2
    ctx.store.save(skill, "skills")
    append_jsonl(phase_bank_dir(ctx.run_dir, "p04:learn")
                 / learn_ledger.REVISIONS_NAME, {
                     "schema": "learn-revision-v1",
                     "phase_label": "p04:learn", "target_kind": "skill",
                     "target_id": skill.skill_id,
                     "skill_id": skill.skill_id, "from_version": 1})

    result = phase_learn.run(ctx, _phase(sequence, phases, 3))

    assert result.summary["outcomes"] == {"revised": 1}
    assert handles["rewriter"].client.calls == []
    assert [row["outcome"] for row in _outcomes(ctx.run_dir, "p04:learn")] \
        == ["revised"]
    assert ctx.store.load("skills", skill.skill_id, Skill).version == 2


# ---------------------------------------------------------------------------
# The live learn phases: the group revision (the G10/G11 migration)
# ---------------------------------------------------------------------------

def test_a_live_miss_with_a_check_left_revises_every_member(
        tmp_path, monkeypatch):
    sequence, phases = _live_sequence()
    handles = {
        "author": make_fake_handle("author", [
            _attempt_attribution_script(
                [_skill(segment=0), _skill(segment=1)]) for _ in range(2)]),
        "rewriter": make_fake_handle("rewriter", [
            _revision_script(), _revision_script()])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    _install_curation(monkeypatch)
    _seed_finished_learn(ctx.store, ctx.run_dir)
    skills = _seed_live_round(ctx.store, better=False)

    result = phase_learn.run(ctx, _phase(sequence, phases, 3))

    first = ctx.store.load("skills", skills[0].skill_id, Skill)
    second = ctx.store.load("skills", skills[1].skill_id, Skill)
    assert first.version == 2 and first.content == MARKED_B
    assert second.version == 2 and second.content == MARKED_B
    outcomes = {row["skill_id"]: row["outcome"]
                for row in _outcomes(ctx.run_dir, "p04:learn")}
    assert outcomes == {skills[0].skill_id: "revised",
                        skills[1].skill_id: "revised"}
    assert result.summary["task_list"] == {"revise": 2,
                                          "superseded_rows": 0}


def _usage_attribution_script(skills):
    return json_script({
        "diagnosis": "one skill was read and ignored; the other was never read",
        "skills": [
            {"skill_id": skills[0].skill_id, "relevance": "relevant",
             "application": "not_applied", "evidence_steps": [1],
             "problem": "the steps were ignored"},
            {"skill_id": skills[1].skill_id, "relevance": "relevant",
             "application": "not_applied", "evidence_steps": [],
             "problem": "the trigger did not select it"},
        ],
    })


def _attempt_attribution_script(skills):
    return json_script({
        "skills": [
            {"skill_id": skill.skill_id,
             "trigger_context": "the window shows the task context",
             "behavior_after_trigger": "the model followed the procedure",
             "improvement": "name the concrete state"}
            for skill in skills],
    })


def _retrigger_script():
    return json_script({
        "summary": "a sharper one-liner",
        "when_to_use": "Use this when the service rejects connections.",
        "reason": "the old trigger was too vague",
    })


def test_skill_level_deletes_unread_and_revises_read(
        tmp_path, monkeypatch):
    sequence, phases = _live_sequence()
    phases["learn"] = LearnPhaseSpec(
        review={"name": "review", "K": 5, "write_lessons": False,
                "decision": "skill_level"},
        author={"name": "distill_diagnose"},
        curate={"name": "none", "token_budget": BIG})
    phases["live"] = dataclasses.replace(
        phases["live"], delivery={"name": "folder_by_task"})
    handles = {
        "author": make_fake_handle(
            "author", [_attempt_attribution_script(
                [_skill(segment=0)]) for _ in range(2)]),
        "rewriter": make_fake_handle("rewriter", [_revision_script()]),
    }
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    _install_curation(monkeypatch)
    _seed_finished_learn(ctx.store, ctx.run_dir)
    skills = _seed_live_round(ctx.store, better=False)

    result = phase_learn.run(ctx, _phase(sequence, phases, 3))

    first = ctx.store.load("skills", skills[0].skill_id, Skill)
    second = ctx.store.load("skills", skills[1].skill_id, Skill)
    assert first.version == 2
    assert second.status == "discarded"
    assert second.discard_reason == "skill_never_read"
    assert result.summary["task_list"] == {"delete": 1, "revise": 1,
                                          "superseded_rows": 0}


def test_a_partially_landed_group_revision_resumes_cleanly(
        tmp_path, monkeypatch):
    # Member 0 landed with its receipt; member 1 was never asked: the
    # resume collects the whole group's evidence once and authors only
    # what the ledger says is missing.
    sequence, phases = _live_sequence()
    handles = {
        "author": make_fake_handle(
            "author", [_attempt_attribution_script(
                [_skill(segment=1)]) for _ in range(2)]),
        "rewriter": make_fake_handle("rewriter", [_revision_script()])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    _install_curation(monkeypatch)
    _seed_finished_learn(ctx.store, ctx.run_dir)
    skills = _seed_live_round(ctx.store, better=False)
    landed = skills[0]
    landed.content = MARKED_B
    landed.version = 2
    ctx.store.save(landed, "skills")
    phase_dir = phase_bank_dir(ctx.run_dir, "p04:learn")
    phase_dir.mkdir(parents=True)
    append_jsonl(phase_dir / learn_ledger.REVISIONS_NAME, {
        "schema": "learn-revision-v1", "phase_label": "p04:learn",
        "target_kind": "skill", "target_id": landed.skill_id,
        "skill_id": landed.skill_id, "from_version": 1})
    append_jsonl(phase_dir / learn_ledger.OUTCOMES_NAME, {
        "schema": "learn-outcome-v1", "phase_label": "p04:learn",
        "target_kind": "skill", "target_id": landed.skill_id,
        "action": "revise", "skill_id": landed.skill_id,
        "outcome": "revised", "version": 2})

    result = phase_learn.run(ctx, _phase(sequence, phases, 3))

    assert len(handles["author"].client.calls) >= 1
    assert len(handles["rewriter"].client.calls) == 1  # only member 1
    assert ctx.store.load("skills", skills[1].skill_id, Skill).version == 2
    assert result.summary["outcomes"] == {"revised": 2}   # cumulative


def test_an_infra_failed_revision_raises_after_the_summary(
        tmp_path, monkeypatch):
    sequence, phases = _live_sequence()
    handles = {"author": make_fake_handle("author", [
        _attempt_attribution_script(
            [_skill(segment=0), _skill(segment=1)]) for _ in range(2)]),
        "rewriter": make_fake_handle("rewriter", [])}
    ctx = _make_ctx(tmp_path, sequence, phases, handles)
    calls = _install_curation(monkeypatch)
    _seed_finished_learn(ctx.store, ctx.run_dir)
    skills = _seed_live_round(ctx.store, better=False)
    from expedistill.stages import learn_apply
    monkeypatch.setattr(
        learn_apply, "revise_one",
        lambda *args, **kwargs: RevisionOutcome(
            skill_id=skills[0].skill_id, decision="revise",
            outcome="infra_failed"))

    with pytest.raises(InfrastructureError,
                       match="pending on infrastructure errors"):
        phase_learn.run(ctx, _phase(sequence, phases, 3))

    assert calls == []                      # no bank published
    outcomes = _outcomes(ctx.run_dir, "p04:learn")
    assert [row["outcome"] for row in outcomes] == ["infra_failed"]
    assert not (phase_bank_dir(ctx.run_dir, "p04:learn") / "skill_bank.jsonl") \
        .exists()


def test_a_reused_live_unit_is_visible_to_the_next_learn_phase(tmp_path):
    """Rule 1 must not hide a unit the next learn phase still decides."""
    sequence, phases = _live_sequence()
    ctx = _make_ctx(tmp_path, sequence, phases, handles={})
    _seed_live_round(ctx.store, better=False)

    source = phase_report.read_phase_report(
        phase_bank_dir(ctx.run_dir, "p03:live"),
        phase_kind="live", learning_usable=True)
    source_unit = next(unit for unit in source.units
                       if unit.injected_skills)
    new_id = source_unit.unit_id.replace("p03:live", "p05:live")
    carried = dataclasses.replace(
        source_unit, unit_id=new_id, phase_label="p05:live",
        sheet_ids=[],
        artifacts={**source_unit.artifacts,
                   "reused_from": source_unit.unit_id})
    dest = phase_bank_dir(ctx.run_dir, "p05:live")
    (dest / ANSWER_SHEETS_DIR).mkdir(parents=True)
    write_unit_specs([], dest)
    acceptance_sheets.write_coverage([], dest / COVERAGE_NAME)
    phase_report.write_carried_units([carried], dest)

    report = phase_learn._load_phase_report(ctx, sequence, phases, 4)
    assert [unit.unit_id for unit in report.units] == [new_id]
    assert report.sheets


def test_learn_summary_includes_cluster_outcomes(tmp_path):
    """The cluster add path has no task-level outcomes.jsonl.

    Its terminal receipts live in cluster_summary.json; the phase summary
    must include them instead of reporting an empty outcomes mapping.
    """
    phase_dir = tmp_path / "phase"
    phase_dir.mkdir()
    uncovered = [{
        "task_id": "tb:t2",
        "add_index": 2,
        "eligible": False,
        "strong_success": True,
        "weak_failure": False,
        "absorbed_by": ["cluster-1"],
    }]
    (phase_dir / "cluster_summary.json").write_text(
        json.dumps({"outcomes": {"added": 42},
                    "uncovered_add_tasks": uncovered}),
        encoding="utf-8")
    phase = SimpleNamespace(spec=SimpleNamespace(role="learn"))

    summary = phase_learn._summary(
        phase, [], [], phase_dir, current_phase="baseline",
        compare_rule="strict_rate_gain", checks_left=2)

    assert summary["outcomes"] == {"added": 42}
    assert summary["uncovered_add_tasks"] == uncovered
