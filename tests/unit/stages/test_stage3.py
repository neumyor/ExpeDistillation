"""Stage 3 tests: fabricated subtasks/trajectories in a tmp store, scripted
llm.fake handles, no network. Covers generation, the per-source mode, the
segment_select rule, step_range slicing, resume (full pairs and half pairs),
the deterministic iteration order, and the protocol-v2 precondition guards
(a subtask outcome that contradicts its write_skill mode stops the batch
fatally, before any model call for that unit)."""

import json
import re
import threading
import time

import pytest

from expedistill.benchmarks.registry import plugin_names, register_plugin
from expedistill.blocks.author import render_skill_content, skill_markers
from expedistill.blocks.rules import register_segment_select
from expedistill.core.config import ExperimentConfig
from expedistill.core.ids import (
    make_exam_id,
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import (
    FAILURE_REFERENCE_MARKERS,
    PROCEDURE_MARKERS,
    Exam,
    Skill,
    Step,
    Subtask,
    Trajectory,
)
from expedistill.infra.reliability import (
    InfrastructureError,
    ModelOutputError,
    ProviderRefusalError,
    UnitFailureRecorder,
)
from expedistill.infra.store import Store, read_json, write_json
from expedistill.llm import json_script, make_fake_handle
from expedistill.llm.call import ModelHandle
from expedistill.prompts import get as get_prompt
from expedistill.stages import stage3_generation
from expedistill.stages.context import StageContext

BENCHMARK = "tb"
WEAK_PROFILE = "A small model; known to skip intermediate steps."


@pytest.fixture(autouse=True)
def tb_plugin_registered():
    """Keep the ``tb`` plugin registered for every test in this module.

    Registration normally happens once, at import of the plugin package.
    tests/unit/benchmarks/test_registry.py empties the registry to test it in
    isolation, and a later import of the package is a module-cache no-op, so
    by the time this module runs in the full suite ``tb`` may be gone. The
    stage resolves plugins by import-then-lookup, so re-register here instead
    of making the stage tolerate a wiped registry (a run process never wipes
    it; only the registry's own tests do).
    """
    import importlib

    # First import registers; after a registry reset the import is a no-op,
    # so the name check (and re-registration) must come after it.
    importlib.import_module("expedistill.benchmarks." + BENCHMARK)
    if BENCHMARK not in plugin_names():
        from expedistill.benchmarks.tb import TBPlugin
        register_plugin(TBPlugin())
    yield

# Distinct template openers, read from the B2 registry so a prompt edit does
# not silently break the mode assertion.
DISTILL_LINE = get_prompt("SKILL_DISTILL").splitlines()[0]
DIAGNOSE_LINE = get_prompt("SKILL_DIAGNOSE").splitlines()[0]

#: One valid JSON-contract payload per G2 mode (protocol v2), plus a second
#: distill payload so the two strong units and any regeneration are
#: distinguishable by content. The stored Skill.content is the deterministic
#: marker-text rendering of these sections.
DISTILL_SECTIONS = {
    "when_to_use": "when a service reports an unexpected state",
    "steps": ["inspect the reported state",
              "run the command that matches the state"],
    "verify": "the service reports the expected state",
    "not_applicable_when": "no state was reported",
}
DISTILL_SECTIONS_ALT = {
    "when_to_use": "when a task needs a fixed setup sequence",
    "steps": ["run the setup commands in order"],
    "verify": "the setup reports done",
    "not_applicable_when": "the setup already ran",
}
DIAGNOSE_SECTIONS = {
    "trigger": "acting on a report without inspecting it first",
    "why_it_fails": "the action does not match the actual state.",
    "do_instead": ["inspect the reported state",
                   "run the command that matches the state"],
    "not_applicable_when": "the state was already inspected",
}


def distill_script(sections=DISTILL_SECTIONS):
    """A scripted G2 distill response (the prompt's JSON contract)."""
    return json_script(sections)


def diagnose_script(sections=DIAGNOSE_SECTIONS):
    """A scripted G2 diagnose response (the prompt's JSON contract)."""
    return json_script(sections)


def make_config(**overrides):
    defaults = dict(
        benchmark=BENCHMARK,
        mode="full",
        models={"executor_strong": "strong-model",
                "executor_weak": "weak-model"},
        weak_profile=WEAK_PROFILE,
        rules={"segment_select": "strong_success_weak_failed"},
    )
    defaults.update(overrides)
    return ExperimentConfig(**defaults)


def make_ctx(tmp_path, *, author, examiner, config=None, run_dir=None):
    run_dir = run_dir or tmp_path / "run"
    store = Store(run_dir)
    ctx = StageContext(
        run_dir=run_dir,
        store=store,
        config=config or make_config(),
        usage=None,
        handles={"author": author, "examiner": examiner},
        workdir_root=tmp_path / "work",
    )
    return ctx, store


def make_trajectory(task, role, attempt, actions):
    task_id = make_task_id(BENCHMARK, task)
    trajectory_id = make_trajectory_id(task_id, role, attempt)
    steps = [Step(action=action, observation=f"obs after {action}")
             for action in actions]
    return Trajectory(
        trajectory_id=trajectory_id,
        task_id=task_id,
        benchmark=BENCHMARK,
        model_role=role,
        model_name=f"{role}-model",
        attempt_index=attempt,
        steps=steps,
        status="failure",
    )


def make_subtask(trajectory, index, source, success, step_range):
    return Subtask(
        subtask_id=make_subtask_id(trajectory.trajectory_id, index),
        task_id=trajectory.task_id,
        trajectory_id=trajectory.trajectory_id,
        source=source,
        trajectory_success=False,
        segment_index=index,
        step_range=step_range,
        label=f"segment {index} of {trajectory.trajectory_id}",
        description="does something concrete",
        success=success,
    )


def seed_store(store):
    """One strong and one weak trajectory, four subtasks over them.

    The D6 rule (strong_success_weak_failed, protocol v2) keeps the strong
    segments (both succeeded: procedure material), the failed weak segment
    (diagnosis material), and drops the weak segment that succeeded.
    """
    strong = make_trajectory("t1", "strong", 0, ["a0", "a1", "a2", "a3"])
    weak = make_trajectory("t1", "weak", 0, ["w0", "w1", "w2", "w3"])
    for trajectory in (strong, weak):
        store.save(trajectory, "trajectories")
    subtasks = [
        make_subtask(strong, 0, "strong", True, (0, 2)),
        make_subtask(strong, 1, "strong", True, (2, 4)),
        make_subtask(weak, 0, "weak", True, (0, 2)),     # dropped by the rule
        make_subtask(weak, 1, "weak", False, (2, 4)),
    ]
    for subtask in subtasks:
        store.save(subtask, "subtasks")
    return subtasks


def exam_script(index):
    return json_script({"question_text": f"question {index}",
                        "reference_answer": f"reference {index}"})


def expected_ids(subtasks):
    """The three kept subtask ids in subtask_id order (v2 D6: strong
    successes and weak failures)."""
    kept = [
        s for s in subtasks
        if (s.source == "strong" and s.success is True)
        or (s.source == "weak" and s.success is False)
    ]
    return sorted(s.subtask_id for s in kept)


def test_generates_skill_and_exam_per_selected_subtask(tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    author = make_fake_handle("strong",
                              [distill_script(),
                               distill_script(DISTILL_SECTIONS_ALT),
                               diagnose_script()])
    examiner = make_fake_handle("strong",
                                [exam_script(0), exam_script(1),
                                 exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)

    summary = stage3_generation.run(ctx)

    assert summary["selected"] == expected_ids(subtasks)
    assert summary["generated"] == expected_ids(subtasks)
    assert summary["model_output_failed"] == []
    assert summary["infra_failed"] == []
    assert summary["exam_missing_advanced"] == []
    # The weak segment that succeeded produced nothing: exactly one skill and
    # one exam per kept subtask, none for the dropped one.
    assert len(author.client.calls) == 3
    assert len(examiner.client.calls) == 3
    for subtask_id in expected_ids(subtasks):
        skill = ctx.store.load("skills", make_skill_id(subtask_id), Skill)
        exam = ctx.store.load("exams", make_exam_id(subtask_id), Exam)
        assert skill.subtask_id == subtask_id
        assert skill.status == "pending"
        assert exam.subtask_id == subtask_id
    dropped = [s.subtask_id for s in subtasks if s.source == "weak"
               and s.success][0]
    assert not ctx.store.exists("skills", make_skill_id(dropped))
    # The summary is also on disk, identical to the returned dict.
    stored = read_json(ctx.run_dir / "stage3_generation"
                       / "stage3_summary.json")
    assert stored == summary


def test_mode_follows_subtask_source(tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    author = make_fake_handle("strong",
                              [distill_script(),
                               distill_script(DISTILL_SECTIONS_ALT),
                               diagnose_script()])
    examiner = make_fake_handle("strong",
                                [exam_script(0), exam_script(1),
                                 exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)

    stage3_generation.run(ctx)

    # subtask_id order: strong seg00, strong seg01, weak seg01.
    users = [call["user"] for call in author.client.calls]
    assert DISTILL_LINE in users[0]
    assert DISTILL_LINE in users[1]
    assert DIAGNOSE_LINE in users[2]
    # The stored skill is the rendered marker text of the fake's sections,
    # one per subtask, in order: a strong source yields a procedure skill,
    # a weak source a failure reference (protocol v2's mode->structure
    # mapping, visible as the stored marker set).
    strong_skill = ctx.store.load("skills",
                                  make_skill_id(subtasks[0].subtask_id), Skill)
    assert strong_skill.content == render_skill_content(
        "distill", DISTILL_SECTIONS)
    assert strong_skill.content.splitlines()[0] == \
        "[When to use] when a service reports an unexpected state"
    assert skill_markers(strong_skill.content) == PROCEDURE_MARKERS
    weak_skill = ctx.store.load("skills",
                                make_skill_id(subtasks[3].subtask_id), Skill)
    assert weak_skill.content == render_skill_content(
        "diagnose", DIAGNOSE_SECTIONS)
    assert weak_skill.content.splitlines()[0] == \
        "[Trigger] acting on a report without inspecting it first"
    assert skill_markers(weak_skill.content) == FAILURE_REFERENCE_MARKERS


def test_steps_view_slices_step_range(tmp_path):
    seed_store(Store(tmp_path / "run"))
    author = make_fake_handle("strong",
                              [distill_script(),
                               distill_script(DISTILL_SECTIONS_ALT),
                               diagnose_script()])
    examiner = make_fake_handle("strong",
                                [exam_script(0), exam_script(1),
                                 exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)

    stage3_generation.run(ctx)

    # Call 1 is strong seg01, whose step_range is (2, 4): only actions a2 and
    # a3 appear, and the view renumbers the sliced steps from 0.
    skill_user = author.client.calls[1]["user"]
    exam_user = examiner.client.calls[1]["user"]
    for user in (skill_user, exam_user):
        assert "action: a2" in user
        assert "action: a3" in user
        assert "action: a0" not in user
        assert "action: w" not in user
    assert "step 0\naction: a2" in skill_user
    # Call 0 is strong seg00, range (0, 2): a0/a1 only.
    first_user = author.client.calls[0]["user"]
    assert "action: a0" in first_user
    assert "action: a2" not in first_user


def test_resume_skips_existing_pairs_with_zero_calls(tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    author = make_fake_handle("strong",
                              [distill_script(),
                               distill_script(DISTILL_SECTIONS_ALT),
                               diagnose_script()])
    examiner = make_fake_handle("strong",
                                [exam_script(0), exam_script(1),
                                 exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)
    stage3_generation.run(ctx)

    # Fresh handles: any resumed call would be recorded here.
    author2 = make_fake_handle("strong", [])
    examiner2 = make_fake_handle("strong", [])
    ctx2, _ = make_ctx(tmp_path, author=author2, examiner=examiner2)
    first = read_json(ctx.run_dir / "stage3_generation"
                      / "stage3_summary.json")
    summary = stage3_generation.run(ctx2)

    assert author2.client.calls == []
    assert examiner2.client.calls == []
    # The summary is cumulative: a re-run reproduces it exactly.
    assert summary == first
    assert summary["generated"] == expected_ids(subtasks)


def test_half_pair_is_regenerated(tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    author = make_fake_handle("strong",
                              [distill_script(),
                               distill_script(DISTILL_SECTIONS_ALT),
                               diagnose_script()])
    examiner = make_fake_handle("strong",
                                [exam_script(0), exam_script(1),
                                 exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)
    stage3_generation.run(ctx)

    # A crash between the two saves leaves the strong seg01 pair halved.
    halved = subtasks[1].subtask_id
    ctx.store.path_for("exams", make_exam_id(halved)).unlink()

    regen_sections = {
        "when_to_use": "when the halved pair is regenerated",
        "steps": ["regenerate the missing half"],
        "verify": "the pair is stored again",
        "not_applicable_when": "the pair is intact",
    }
    author2 = make_fake_handle("strong", [distill_script(regen_sections)])
    examiner2 = make_fake_handle("strong", [exam_script(9)])
    ctx2, _ = make_ctx(tmp_path, author=author2, examiner=examiner2)
    summary = stage3_generation.run(ctx2)

    assert summary["generated"] == expected_ids(subtasks)
    # Only the halved pair was regenerated, from the fresh response.
    assert len(author2.client.calls) == 1
    assert len(examiner2.client.calls) == 1
    skill = ctx2.store.load("skills", make_skill_id(halved), Skill)
    assert skill.content == render_skill_content("distill", regen_sections)


def test_iteration_order_is_deterministic(tmp_path):
    # Same seed data twice: identical call sequences and identical summaries.
    summaries = []
    for index in range(2):
        fresh = tmp_path / f"run{index}"
        seed_store(Store(fresh))
        author = make_fake_handle("strong",
                                  [distill_script(),
                                   distill_script(DISTILL_SECTIONS_ALT),
                                   diagnose_script()])
        examiner = make_fake_handle("strong",
                                    [exam_script(0), exam_script(1),
                                     exam_script(2)])
        ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner,
                          run_dir=fresh)
        summaries.append((
            stage3_generation.run(ctx),
            [call["user"] for call in author.client.calls],
        ))

    assert summaries[0] == summaries[1]


def test_uses_exactly_the_rule_named_in_config(tmp_path):
    # A rule that keeps only weak segments: the stage must apply exactly the
    # rule config.rules names, with no fallback of its own. Both kept weak
    # segments failed their own goal, so both are legal diagnose material
    # under the protocol-v2 guards.
    @register_segment_select("stage3_test_only_weak")
    def _only_weak(subtask):
        return subtask.source == "weak"

    store = Store(tmp_path / "run")
    strong = make_trajectory("t1", "strong", 0, ["a0", "a1", "a2", "a3"])
    weak = make_trajectory("t1", "weak", 0, ["w0", "w1", "w2", "w3"])
    for trajectory in (strong, weak):
        store.save(trajectory, "trajectories")
    subtasks = [
        make_subtask(strong, 0, "strong", True, (0, 2)),
        make_subtask(weak, 0, "weak", False, (0, 2)),
        make_subtask(weak, 1, "weak", False, (2, 4)),
    ]
    for subtask in subtasks:
        store.save(subtask, "subtasks")

    author = make_fake_handle("strong", [diagnose_script(),
                                         diagnose_script()])
    examiner = make_fake_handle("strong", [exam_script(0), exam_script(1)])
    ctx, _ = make_ctx(
        tmp_path, author=author, examiner=examiner,
        config=make_config(rules={"segment_select": "stage3_test_only_weak"}))

    summary = stage3_generation.run(ctx)

    weak_ids = sorted(s.subtask_id for s in subtasks if s.source == "weak")
    assert summary["selected"] == weak_ids
    assert summary["generated"] == weak_ids


# ---------------------------------------------------------------------------
# Per-unit failure disposition
# ---------------------------------------------------------------------------

def _summary_on_disk(ctx):
    return read_json(ctx.run_dir / "stage3_generation" / "stage3_summary.json")


def _ledger(ctx):
    return UnitFailureRecorder(ctx.run_dir).list_failures()


def _failing_write_skill(monkeypatch, target_id, exc):
    """Make G2 raise ``exc`` for one subtask, delegate for all others."""
    real = stage3_generation.write_skill

    def flaky(subtask, *args, **kwargs):
        if subtask.subtask_id == target_id:
            raise exc
        return real(subtask, *args, **kwargs)

    monkeypatch.setattr(stage3_generation, "write_skill", flaky)


def test_g2_model_output_error_skips_only_that_unit(monkeypatch, tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    target = subtasks[1].subtask_id  # strong seg01, the middle unit
    _failing_write_skill(monkeypatch, target,
                         ModelOutputError("skill text unusable"))
    author = make_fake_handle("strong", [distill_script(), diagnose_script()])
    examiner = make_fake_handle("strong", [exam_script(0), exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)

    summary = stage3_generation.run(ctx)

    others = [sid for sid in expected_ids(subtasks) if sid != target]
    assert summary["generated"] == others
    assert summary["model_output_failed"] == [target]
    assert summary["infra_failed"] == []
    assert _summary_on_disk(ctx) == summary
    assert not ctx.store.exists("skills", make_skill_id(target))
    assert not ctx.store.exists("exams", make_exam_id(target))
    ledger = _ledger(ctx)
    assert [(r["unit_id"], r["stage"], r["error_class"]) for r in ledger] == [
        (target, "stage3_generation", "ModelOutputError")]


def test_g3_model_output_error_skips_only_that_unit(tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    target = subtasks[1].subtask_id
    author = make_fake_handle("strong",
                              [distill_script(),
                               distill_script(DISTILL_SECTIONS_ALT),
                               diagnose_script()])
    # Valid JSON with an empty field: gen_exam's own contract check raises
    # ModelOutputError for the second unit (no format repair applies).
    bad_exam = json_script({"question_text": "", "reference_answer": "x"})
    examiner = make_fake_handle("strong",
                                [exam_script(0), bad_exam, exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)

    summary = stage3_generation.run(ctx)

    others = [sid for sid in expected_ids(subtasks) if sid != target]
    assert summary["generated"] == others
    assert summary["model_output_failed"] == [target]
    # The pair is all or nothing: the skill written before G3 failed is not
    # left behind as a half pair.
    assert not ctx.store.exists("skills", make_skill_id(target))
    assert not ctx.store.exists("exams", make_exam_id(target))
    assert [r["unit_id"] for r in _ledger(ctx)] == [target]

    # The terminal record was written for the unit.
    assert [r["subtask_id"] for r in _failures_file(ctx)] == [target]


# ---------------------------------------------------------------------------
# The 2026-10-13 incident regression (docs/decisions/
# g2_multiline_and_json_extraction_fix_20261014.md): both failure classes in
# one end-to-end pass. A raw reply shaped like dump 0178 (unfenced JSON
# object, heredoc steps, ``` code fences embedded in string values) used to
# die as model_output_failed -- the fence-priority bug returned a top-level
# list, and even when the object parsed, the newline rejection killed the
# multi-line steps. It must generate the unit's pair like any other reply.
# ---------------------------------------------------------------------------

_INCIDENT_DISTILL_REPLY = json.dumps({
    "when_to_use": "when a board image needs its piece masks extracted",
    "steps": [
        "Run `ls`; use the printed image file name as `<image-file>`.",
        "Run this probe:\n"
        "```\n"
        "python3 - <<'PY'\n"
        "from PIL import Image\n"
        "img = Image.open('<image-file>')\n"
        "cell = min(img.width, img.height) // 8\n"
        "print(cell[0])\n"
        "PY\n"
        "```",
        "Compare the printed `cell` with the expected board geometry.",
    ],
    "verify": "step 2 prints a `cell` greater than 0",
    "not_applicable_when": "the input is already text (FEN or a board list)",
}, ensure_ascii=False)


def test_incident_shaped_raw_reply_generates_the_unit(tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    author = make_fake_handle("strong", [_INCIDENT_DISTILL_REPLY,
                                         distill_script(DISTILL_SECTIONS_ALT),
                                         diagnose_script()])
    examiner = make_fake_handle("strong", [exam_script(0), exam_script(1),
                                           exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)

    summary = stage3_generation.run(ctx)

    assert summary["model_output_failed"] == []
    assert summary["generated"] == expected_ids(subtasks)
    # The heredoc item survives verbatim: first line numbered by the
    # renderer, continuation lines at column 0.
    skill = ctx.store.load("skills", make_skill_id(subtasks[0].subtask_id),
                           Skill)
    assert "python3 - <<'PY'" in skill.content
    assert "\nprint(cell[0])\n" in skill.content
    assert skill_markers(skill.content) == PROCEDURE_MARKERS


def test_infra_error_finishes_other_units_then_raises(monkeypatch, tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    target = subtasks[1].subtask_id
    _failing_write_skill(monkeypatch, target,
                         InfrastructureError("endpoint unreachable"))
    author = make_fake_handle("strong", [distill_script(), diagnose_script()])
    examiner = make_fake_handle("strong", [exam_script(0), exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)

    with pytest.raises(InfrastructureError) as info:
        stage3_generation.run(ctx)

    assert "1 unit" in str(info.value)
    assert "resume" in str(info.value)
    others = [sid for sid in expected_ids(subtasks) if sid != target]
    # The summary was written before the raise and names the hole.
    summary = _summary_on_disk(ctx)
    assert summary["generated"] == others
    assert summary["infra_failed"] == [target]
    assert summary["model_output_failed"] == []
    for sid in others:
        assert ctx.store.exists("exams", make_exam_id(sid))
    assert not ctx.store.exists("skills", make_skill_id(target))
    assert [(r["unit_id"], r["error_class"]) for r in _ledger(ctx)] == [
        (target, "InfrastructureError")]

    # Resume (service back): exactly the failed unit is processed.
    monkeypatch.undo()
    author2 = make_fake_handle("strong", [distill_script()])
    examiner2 = make_fake_handle("strong", [exam_script(1)])
    ctx2, _ = make_ctx(tmp_path, author=author2, examiner=examiner2)
    summary2 = stage3_generation.run(ctx2)

    assert summary2["generated"] == expected_ids(subtasks)
    assert summary2["infra_failed"] == []
    # Infrastructure failures are not terminal: no terminal record.
    assert _failures_file(ctx2) == []
    assert len(author2.client.calls) == 1
    assert len(examiner2.client.calls) == 1


def test_provider_refusal_propagates_immediately(monkeypatch, tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    first = expected_ids(subtasks)[0]
    _failing_write_skill(monkeypatch, first,
                         ProviderRefusalError("unknown model"))
    author = make_fake_handle("strong", [])
    examiner = make_fake_handle("strong", [])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)

    with pytest.raises(ProviderRefusalError):
        stage3_generation.run(ctx)

    # Stops at the first unit: no further calls, nothing recorded as a unit.
    assert author.client.calls == []
    assert examiner.client.calls == []
    assert _ledger(ctx) == []


# ---------------------------------------------------------------------------
# Protocol v2 precondition guards: a subtask outcome that contradicts its
# write_skill mode is a fatal stop, not a skipped unit (decision section 3.3)
# ---------------------------------------------------------------------------

def test_strong_failed_segment_into_distill_stops_the_batch(tmp_path):
    # The v1 segment rule (still registered) selects a FAILED strong
    # segment; its source routes it to distill, and write_skill's guard
    # refuses the contradiction before any model call for that unit. The
    # ValueError is fatal to the batch: the unit before it completed and
    # persists, later units never start, no summary is written.
    store = Store(tmp_path / "run")
    strong = make_trajectory("t1", "strong", 0, ["a0", "a1", "a2", "a3"])
    weak = make_trajectory("t1", "weak", 0, ["w0", "w1", "w2", "w3"])
    for trajectory in (strong, weak):
        store.save(trajectory, "trajectories")
    subtasks = [
        make_subtask(strong, 0, "strong", True, (0, 2)),    # runs, persists
        make_subtask(strong, 1, "strong", False, (2, 4)),   # the bad material
        make_subtask(weak, 1, "weak", False, (2, 4)),      # never started
    ]
    for subtask in subtasks:
        store.save(subtask, "subtasks")

    author = make_fake_handle("strong", [distill_script()])
    examiner = make_fake_handle("strong", [exam_script(0)])
    ctx, _ = make_ctx(
        tmp_path, author=author, examiner=examiner,
        config=make_config(rules={"segment_select": "strong_all_weak_failed"}))

    with pytest.raises(ValueError, match="subtask.success is True"):
        stage3_generation.run(ctx)

    # The unit before the fatal one completed and persisted its pair.
    assert len(author.client.calls) == 1
    assert len(examiner.client.calls) == 1
    assert ctx.store.exists("skills", make_skill_id(subtasks[0].subtask_id))
    assert ctx.store.exists("exams", make_exam_id(subtasks[0].subtask_id))
    # The refused unit made no model call and stored nothing; the unit
    # after it never started. Fatal: no summary, no ledger row.
    assert not ctx.store.exists("skills", make_skill_id(subtasks[1].subtask_id))
    assert not ctx.store.exists("skills", make_skill_id(subtasks[2].subtask_id))
    assert not (ctx.run_dir / "stage3_generation"
                / "stage3_summary.json").exists()
    assert _ledger(ctx) == []


def test_weak_succeeded_segment_into_diagnose_stops_the_batch(tmp_path):
    # The mirror guard: a weak segment that reached its own goal. Only a
    # rule that selects it regardless of the v2 material order can feed it
    # to diagnose; the guard refuses before any model call, so the very
    # first unit stops the batch with nothing stored.
    @register_segment_select("stage3_test_weak_succeeded")
    def _weak_succeeded(subtask):
        return subtask.source == "weak" and subtask.success is True

    seed_store(Store(tmp_path / "run"))
    author = make_fake_handle("strong", [])
    examiner = make_fake_handle("strong", [])
    ctx, _ = make_ctx(
        tmp_path, author=author, examiner=examiner,
        config=make_config(
            rules={"segment_select": "stage3_test_weak_succeeded"}))

    with pytest.raises(ValueError, match="subtask.success is False"):
        stage3_generation.run(ctx)

    assert author.client.calls == []
    assert examiner.client.calls == []
    assert ctx.store.ids("skills") == []
    assert ctx.store.ids("exams") == []
    assert not (ctx.run_dir / "stage3_generation"
                / "stage3_summary.json").exists()
    assert _ledger(ctx) == []


def test_advanced_skill_with_missing_exam_is_never_overwritten(tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    author = make_fake_handle("strong",
                              [distill_script(),
                               distill_script(DISTILL_SECTIONS_ALT),
                               diagnose_script()])
    examiner = make_fake_handle("strong",
                                [exam_script(0), exam_script(1),
                                 exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)
    stage3_generation.run(ctx)

    # Stage 4 already accepted the strong seg01 skill (a rewrite bumped its
    # version); then its exam file went missing.
    target = subtasks[1].subtask_id
    skill_path = ctx.store.path_for("skills", make_skill_id(target))
    advanced = read_json(skill_path)
    advanced.update(status="accepted", version=2,
                    content="REWRITTEN BY STAGE 4")
    write_json(skill_path, advanced)
    ctx.store.path_for("exams", make_exam_id(target)).unlink()

    author2 = make_fake_handle("strong", [])
    examiner2 = make_fake_handle("strong", [])
    ctx2, _ = make_ctx(tmp_path, author=author2, examiner=examiner2)
    summary = stage3_generation.run(ctx2)

    assert author2.client.calls == []
    assert examiner2.client.calls == []
    kept = ctx2.store.load("skills", make_skill_id(target), Skill)
    assert kept.content == "REWRITTEN BY STAGE 4"
    assert kept.status == "accepted"
    assert summary["generated"] == [sid for sid in expected_ids(subtasks)
                                    if sid != target]
    assert summary["exam_missing_advanced"] == [target]
    assert [(r["unit_id"], r["stage"]) for r in _ledger(ctx2)] == [
        (target, "stage3_generation")]

    # Re-running changes nothing and does not grow the ledger.
    ctx3, _ = make_ctx(tmp_path, author=make_fake_handle("strong", []),
                       examiner=make_fake_handle("strong", []))
    assert stage3_generation.run(ctx3) == summary
    assert len(_ledger(ctx3)) == 1


# ---------------------------------------------------------------------------
# Exhausted model-output repair is terminal within a run directory
# ---------------------------------------------------------------------------

def _g3_failure_handles():
    """Handles for a fresh run where the middle unit's G3 output is bad."""
    author = make_fake_handle("strong",
                              [distill_script(),
                               distill_script(DISTILL_SECTIONS_ALT),
                               diagnose_script()])
    bad_exam = json_script({"question_text": "", "reference_answer": "x"})
    examiner = make_fake_handle("strong",
                                [exam_script(0), bad_exam, exam_script(2)])
    return author, examiner


def _failures_file(ctx):
    from expedistill.infra.store import read_jsonl
    return read_jsonl(ctx.run_dir / "stage3_generation"
                      / "generation_failures.jsonl")


def test_rerun_of_finished_run_with_terminal_failure_is_a_noop(tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    target = subtasks[1].subtask_id
    author, examiner = _g3_failure_handles()
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)
    first = stage3_generation.run(ctx)
    assert first["model_output_failed"] == [target]

    # The model would now answer well, but the run already decided the unit.
    author2 = make_fake_handle("strong", ["SKILL STRONG 1 NOW GOOD"])
    examiner2 = make_fake_handle("strong", [exam_script(7)])
    ctx2, _ = make_ctx(tmp_path, author=author2, examiner=examiner2)
    second = stage3_generation.run(ctx2)

    assert author2.client.calls == []
    assert examiner2.client.calls == []
    assert second == first
    assert _summary_on_disk(ctx2) == first
    assert not ctx2.store.exists("skills", make_skill_id(target))
    assert len(_ledger(ctx2)) == 1
    assert [r["subtask_id"] for r in _failures_file(ctx2)] == [target]


class _Crash(BaseException):
    """Simulated process death (not an Exception: nothing may catch it)."""


def test_crash_after_failure_record_resumes_identically(monkeypatch,
                                                        tmp_path):
    # Reference: one uninterrupted run.
    seed_store(Store(tmp_path / "ref"))
    author, examiner = _g3_failure_handles()
    ref_ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner,
                          run_dir=tmp_path / "ref")
    reference = stage3_generation.run(ref_ctx)

    # Same run, but the process dies right after the terminal failure record
    # is written, before the ledger row and before moving on.
    seed_store(Store(tmp_path / "run"))
    author, examiner = _g3_failure_handles()
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)

    def crash(self, *args, **kwargs):
        raise _Crash()

    monkeypatch.setattr(UnitFailureRecorder, "record", crash)
    with pytest.raises(_Crash):
        stage3_generation.run(ctx)
    monkeypatch.undo()

    author2 = make_fake_handle("strong", [diagnose_script()])
    examiner2 = make_fake_handle("strong", [exam_script(2)])
    ctx2, _ = make_ctx(tmp_path, author=author2, examiner=examiner2)
    resumed = stage3_generation.run(ctx2)

    assert resumed == reference
    # Only the unit after the crash point was generated on resume.
    assert len(author2.client.calls) == 1
    assert len(examiner2.client.calls) == 1

    def rows(c):
        return [(r["unit_id"], r["stage"], r["error_class"])
                for r in _ledger(c)]

    assert rows(ctx2) == rows(ref_ctx)
    assert ([r["subtask_id"] for r in _failures_file(ctx2)]
            == [r["subtask_id"] for r in _failures_file(ref_ctx)])


# ---------------------------------------------------------------------------
# Parallel dispatch: ctx.workers > 1 (B5 run_units) must match serial runs
# ---------------------------------------------------------------------------

_LABEL_RE = re.compile(r"SUBTASK LABEL: (.+)")


class _PureClient:
    """Thread-safe FakeClient stand-in, built here because llm/fake.py's
    list script is consumed in arrival order, which a thread pool makes
    nondeterministic.

    Responses are a pure function of the request, so any interleaving
    yields the same per-subtask outputs; calls are still recorded for
    count/content assertions. ``respond`` runs OUTSIDE the calls lock, so a
    sleep or barrier inside it cannot block sibling threads from entering.
    """

    def __init__(self, respond):
        self.respond = respond
        self.calls = []
        self._lock = threading.Lock()

    def complete(self, model, system, user, *, temperature=0.0,
                 max_tokens=None, extra=None):
        with self._lock:
            self.calls.append(
                {"model": model, "system": system, "user": user})
        return self.respond(system, user)


def _pure_handle(respond):
    """A ModelHandle wired to _PureClient (mirrors make_fake_handle)."""
    return ModelHandle(name="fake-model", role="test",
                       client=_PureClient(respond))


def _subtask_label(user):
    match = _LABEL_RE.search(user)
    assert match, f"no subtask label in prompt: {user[:120]!r}"
    return match.group(1)


def _sections_for(mode, label):
    """A valid G2 JSON-contract payload per (mode, label): a pure function
    of the request, so any worker interleaving yields the same per-subtask
    sections, and the stored content is their rendering."""
    if mode == "distill":
        return {
            "when_to_use": f"when you face {label}",
            "steps": [f"act on {label}"],
            "verify": f"{label} is handled",
            "not_applicable_when": f"never {label}",
        }
    return {
        "trigger": f"failing at {label}",
        "why_it_fails": "the step was skipped.",
        "do_instead": [f"act on {label}"],
        "not_applicable_when": f"never {label}",
    }


def _author_responder(system, user):
    # The mode is readable from the template's opening line (DISTILL_LINE /
    # DIAGNOSE_LINE above; both G2 prompts carry the label), and the
    # response satisfies that mode's JSON contract (protocol v2).
    mode = "distill" if DISTILL_LINE in user else "diagnose"
    return json_script(_sections_for(mode, _subtask_label(user)))


def _exam_responder(system, user):
    label = _subtask_label(user)
    return json_script({"question_text": f"question {label}",
                        "reference_answer": f"reference {label}"})


def _slow_author_responder(system, user):
    # Hold the model call long enough that sibling units really interleave;
    # a pure-Python call can otherwise finish before the next worker thread
    # starts, and workers=4 would silently behave serially.
    time.sleep(0.02)
    return _author_responder(system, user)


def test_workers_4_run_matches_workers_1_run(tmp_path):
    # Two run directories, the same pure-function model, workers=1 vs
    # workers=4: identical summaries (field by field) and identical stores.
    subtasks = None
    summaries = {}
    for workers in (1, 4):
        run_dir = tmp_path / f"run-w{workers}"
        subtasks = seed_store(Store(run_dir))
        ctx, _ = make_ctx(tmp_path,
                          author=_pure_handle(_slow_author_responder),
                          examiner=_pure_handle(_exam_responder),
                          run_dir=run_dir)
        ctx.workers = workers
        summaries[workers] = stage3_generation.run(ctx)

    for field in ("selected", "generated", "model_output_failed",
                  "infra_failed", "exam_missing_advanced"):
        assert summaries[4][field] == summaries[1][field], field
    # And both are the correct result, not just mutually equal.
    assert summaries[4]["generated"] == expected_ids(subtasks)
    for kind_dir in ("skills", "exams"):
        files = {}
        for workers in (1, 4):
            directory = (tmp_path / f"run-w{workers}" / "stage3_generation"
                         / kind_dir)
            files[workers] = {path.name: read_json(path)
                              for path in sorted(directory.glob("*.json"))}
        assert files[4] == files[1]


def test_workers_4_generates_subtasks_concurrently(monkeypatch, tmp_path):
    # A barrier that only opens once every selected unit is in flight:
    # with serial dispatch the first unit would wait out the timeout and
    # the run would raise, so completing proves >= 2 (here: 3) units
    # really ran at the same time.
    subtasks = seed_store(Store(tmp_path / "run"))
    selected = expected_ids(subtasks)
    barrier = threading.Barrier(len(selected), timeout=10)
    real = stage3_generation.write_skill

    def gated(subtask, *args, **kwargs):
        barrier.wait()
        return real(subtask, *args, **kwargs)

    monkeypatch.setattr(stage3_generation, "write_skill", gated)
    author = _pure_handle(_author_responder)
    examiner = _pure_handle(_exam_responder)
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)
    ctx.workers = 4

    summary = stage3_generation.run(ctx)

    assert summary["generated"] == selected
    assert len(author.client.calls) == len(selected)
    assert len(examiner.client.calls) == len(selected)


def test_model_output_error_under_workers_4_is_isolated(monkeypatch, tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    target = subtasks[1].subtask_id  # the middle unit
    _failing_write_skill(monkeypatch, target,
                         ModelOutputError("skill text unusable"))
    author = _pure_handle(_author_responder)
    examiner = _pure_handle(_exam_responder)
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)
    ctx.workers = 4

    summary = stage3_generation.run(ctx)  # terminal, not fatal: no raise

    others = [sid for sid in expected_ids(subtasks) if sid != target]
    assert summary["generated"] == others
    assert summary["model_output_failed"] == [target]
    assert summary["infra_failed"] == []
    assert _summary_on_disk(ctx) == summary
    for sid in others:
        assert ctx.store.exists("skills", make_skill_id(sid))
        assert ctx.store.exists("exams", make_exam_id(sid))
    assert not ctx.store.exists("skills", make_skill_id(target))
    assert not ctx.store.exists("exams", make_exam_id(target))
    # The terminal failure was recorded exactly once.
    assert [r["subtask_id"] for r in _failures_file(ctx)] == [target]
    assert [(r["unit_id"], r["error_class"]) for r in _ledger(ctx)] == [
        (target, "ModelOutputError")]


def test_infra_error_under_workers_4_completes_others_then_raises(
        monkeypatch, tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    target = subtasks[1].subtask_id
    _failing_write_skill(monkeypatch, target,
                         InfrastructureError("endpoint unreachable"))
    author = _pure_handle(_author_responder)
    examiner = _pure_handle(_exam_responder)
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)
    ctx.workers = 4

    with pytest.raises(InfrastructureError) as info:
        stage3_generation.run(ctx)

    assert "1 unit" in str(info.value)
    others = [sid for sid in expected_ids(subtasks) if sid != target]
    # The summary was written before the raise and names the hole.
    summary = _summary_on_disk(ctx)
    assert summary["generated"] == others
    assert summary["infra_failed"] == [target]
    for sid in others:
        assert ctx.store.exists("exams", make_exam_id(sid))
    assert not ctx.store.exists("skills", make_skill_id(target))
    assert [(r["unit_id"], r["error_class"]) for r in _ledger(ctx)] == [
        (target, "InfrastructureError")]

    # Resume (service back): exactly the failed unit is re-offered.
    monkeypatch.undo()
    author2 = _pure_handle(_author_responder)
    examiner2 = _pure_handle(_exam_responder)
    ctx2, _ = make_ctx(tmp_path, author=author2, examiner=examiner2)
    ctx2.workers = 4
    summary2 = stage3_generation.run(ctx2)

    assert summary2["generated"] == expected_ids(subtasks)
    assert summary2["infra_failed"] == []
    # Infrastructure failures are not terminal: no terminal record.
    assert _failures_file(ctx2) == []
    assert len(author2.client.calls) == 1
    assert len(examiner2.client.calls) == 1


def test_resume_under_workers_4_regenerates_only_pending(tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    ctx, _ = make_ctx(tmp_path, author=_pure_handle(_author_responder),
                      examiner=_pure_handle(_exam_responder))
    ctx.workers = 4
    first = stage3_generation.run(ctx)

    # A partially filled store: one pair lost in full, one halved, one
    # intact (the crash points a resume can land on).
    ids = expected_ids(subtasks)
    ctx.store.path_for("skills", make_skill_id(ids[0])).unlink()
    ctx.store.path_for("exams", make_exam_id(ids[0])).unlink()
    ctx.store.path_for("exams", make_exam_id(ids[1])).unlink()

    author2 = _pure_handle(_author_responder)
    examiner2 = _pure_handle(_exam_responder)
    ctx2, _ = make_ctx(tmp_path, author=author2, examiner=examiner2)
    ctx2.workers = 4
    resumed = stage3_generation.run(ctx2)

    assert resumed == first
    # Only the two pending units made model calls; the intact pair was
    # skipped with zero calls.
    assert len(author2.client.calls) == 2
    assert len(examiner2.client.calls) == 2
    # The regenerated halves are exactly what an uninterrupted run writes
    # (pure-function model, deterministic ids): the rendered marker text of
    # the responder's sections for this subtask's label.
    skill = ctx2.store.load("skills", make_skill_id(ids[1]), Skill)
    exam = ctx2.store.load("exams", make_exam_id(ids[1]), Exam)
    assert skill.content == render_skill_content(
        "distill", _sections_for("distill", subtasks[1].label))
    assert skill_markers(skill.content) == PROCEDURE_MARKERS
    assert exam.question_text == f"question {subtasks[1].label}"
