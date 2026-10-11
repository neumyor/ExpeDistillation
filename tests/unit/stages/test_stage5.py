"""Stage 5 tests: the stage wires K5 and records the bank (README stage 5).

Fake-model handles (llm.fake) and a real Store on a temp run directory; the
bank is fabricated either directly (append_bank rows -- the resume path K5
guarantees) or through scripted G7/G8/K3/G9 rounds, so no test touches a real
model (AGENTS.md section 8). Pinned behaviours under test: the summary and
its bank_reference round-trip, the empty bank is a valid outcome, an
over-budget bank stops for a human (ConfigError propagates; no summary is
written), and a second run is a pure rewrite (no model calls).
"""

import pytest

from expedistill.core import ids
from expedistill.core.models import Exam, Skill
from expedistill.infra.reliability import ConfigError
from expedistill.infra.store import Store, read_json
from expedistill.inject import bank_reference
from expedistill.llm import json_script, make_fake_handle
from expedistill.stages import stage5_curation
from expedistill.stages.context import StageContext


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _subtask(index: int) -> str:
    return f"traj/tb:t{index}/strong/00/seg00"


def _skill_id(index: int) -> str:
    return ids.make_skill_id(_subtask(index))


def _add_skill(store, index, content, status="accepted"):
    skill = Skill(skill_id=_skill_id(index), content=content,
                  subtask_id=_subtask(index), status=status)
    store.save(skill, "skills")
    return skill


def _add_exam(store, index):
    exam = Exam(exam_id=ids.make_exam_id(_subtask(index)),
                question_text=f"question {index}",
                reference_answer=f"reference {index}",
                subtask_id=_subtask(index))
    store.save(exam, "exams")
    return exam


def _make_ctx(tmp_path, *, budget=10 ** 6, rounds=3, curator="",
              answerer="", judge=""):
    run_dir = tmp_path / "run"
    handles = {
        "answerer": make_fake_handle("weak", answerer),
        "judge": make_fake_handle("judge", judge),
        "curator": make_fake_handle("curator", curator),
    }
    from expedistill.core.config import ExperimentConfig
    config = ExperimentConfig(
        benchmark="tb", mode="split",
        models={"executor_strong": "strong-x", "executor_weak": "weak-7b"},
        weak_profile="a weak model that needs short sentences",
        bank_token_budget=budget, curation_rounds=rounds)
    ctx = StageContext(run_dir=run_dir, store=Store(run_dir), config=config,
                       usage=None, handles=handles,
                       workdir_root=run_dir / "work")
    return ctx


def _client_calls(ctx):
    return [call for handle in ctx.handles.values()
            for call in handle.client.calls]


def _summary_path(ctx):
    return (ctx.run_dir / stage5_curation.SUMMARY_DIR
            / stage5_curation.SUMMARY_NAME)


def _similarity(groups=(), conflicts=()):
    return json_script({"similar_groups": [list(g) for g in groups],
                         "conflicts": [list(c) for c in conflicts]})


def _grouping(mapping):
    return json_script({"groups": mapping})


_CORRECT = lambda: json_script({"verdict": "correct",  # noqa: E731
                                "error_description": ""})


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_fabricated_bank_summary_roundtrip_and_resume(tmp_path):
    ctx = _make_ctx(tmp_path)
    rows = [
        {"skill_id": _skill_id(0), "version": 1,
         "content": "Check the log file first.", "group": "debugging"},
        {"skill_id": _skill_id(1), "version": 2,
         "content": "Read errors bottom-up.", "group": None},
    ]
    for row in rows:
        ctx.store.append_bank(row)

    summary = stage5_curation.run(ctx)

    assert summary["members"] == [row["skill_id"] for row in rows]
    assert summary["size"] == 2
    assert summary["bank_reference"] == bank_reference(rows)
    # The summary is on disk and identical to the returned value.
    assert read_json(_summary_path(ctx)) == summary
    # K5's resume path: a written bank means no curation work, so no model
    # call happened at all.
    assert _client_calls(ctx) == []

    # A second run is a pure rewrite: same summary, still no model calls.
    again = stage5_curation.run(ctx)
    assert again == summary
    assert _client_calls(ctx) == []


def test_empty_bank_is_a_valid_outcome(tmp_path):
    ctx = _make_ctx(tmp_path)
    summary = stage5_curation.run(ctx)
    assert summary["members"] == []
    assert summary["size"] == 0
    assert summary["bank_reference"] == bank_reference([])
    assert read_json(_summary_path(ctx)) == summary
    # No accepted skills, so K5 never calls a model.
    assert _client_calls(ctx) == []


def test_scripted_merge_flows_into_summary(tmp_path):
    ctx = _make_ctx(
        tmp_path,
        curator=[_similarity(groups=[[_skill_id(0), _skill_id(1)]]),
                 "Read the log file before doing anything else.",
                 _grouping({"bank/merge/0000": "error handling"})],
        answerer=["first answer", "second answer"],
        judge=[_CORRECT(), _CORRECT()])
    _add_skill(ctx.store, 0, "Check the log file first.")
    _add_skill(ctx.store, 1, "Look at the log file before anything else.")
    _add_exam(ctx.store, 0)
    _add_exam(ctx.store, 1)

    summary = stage5_curation.run(ctx)

    bank = ctx.store.bank()
    assert [row["skill_id"] for row in bank] == ["bank/merge/0000"]
    assert summary["members"] == ["bank/merge/0000"]
    assert summary["size"] == 1
    assert summary["bank_reference"] == bank_reference(bank)


# ---------------------------------------------------------------------------
# Over-budget: the stage stops for a human
# ---------------------------------------------------------------------------

def test_over_budget_propagates_and_writes_no_summary(tmp_path):
    # Two accepted skills that G7 sees no reason to merge, under a budget
    # no bank of theirs could ever meet: after one round the bank is still
    # over budget and the stage must stop for a human (README stage 5).
    ctx = _make_ctx(
        tmp_path, budget=1, rounds=1,
        curator=[_similarity(),
                 _grouping({_skill_id(0): "debugging",
                            _skill_id(1): "debugging"})])
    _add_skill(ctx.store, 0, "Check the log file first, always.")
    _add_skill(ctx.store, 1, "Read errors bottom-up before restarting.")

    with pytest.raises(ConfigError) as excinfo:
        stage5_curation.run(ctx)
    assert "bank_token_budget" in str(excinfo.value)

    # Stopped for a human: no bank was written, no summary exists.
    assert ctx.store.bank() == []
    assert not _summary_path(ctx).exists()


# ---------------------------------------------------------------------------
# Infrastructure failure: the stage stops, a re-run resumes
# ---------------------------------------------------------------------------

def test_infrastructure_error_stops_stage_and_rerun_resumes(tmp_path):
    from expedistill.infra.reliability import InfrastructureError
    ctx = _make_ctx(
        tmp_path,
        curator=[_similarity(groups=[[_skill_id(0), _skill_id(1)]]),
                 InfrastructureError("endpoint down")])
    _add_skill(ctx.store, 0, "Check the log file first.")
    _add_skill(ctx.store, 1, "Look at the log file before anything else.")
    _add_exam(ctx.store, 0)
    _add_exam(ctx.store, 1)

    with pytest.raises(InfrastructureError):
        stage5_curation.run(ctx)
    assert ctx.store.bank() == []
    assert not _summary_path(ctx).exists()

    # Re-run with a healthy curator: G7 is NOT asked again (journaled), the
    # merge goes ahead -- no merge was dropped because the network failed.
    ctx.handles["curator"] = make_fake_handle(
        "curator", ["Read the log file before doing anything else.",
                    _grouping({"bank/merge/0000": "error handling"})])
    ctx.handles["answerer"] = make_fake_handle("weak", ["a", "b"])
    ctx.handles["judge"] = make_fake_handle("judge", [_CORRECT(), _CORRECT()])
    summary = stage5_curation.run(ctx)
    assert summary["members"] == ["bank/merge/0000"]
    assert ctx.handles["curator"].client.script == []
