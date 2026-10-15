"""Cross-stage interleave: a resumed stage 4 between a crashed and a resumed K5.

K5 stores an in-flight merge (``bank/merge/NNNN``) as "pending" before its
regression exams. If stage 5 crashes there and the pipeline is resumed from
stage 4, stage 4 sees a pending skill -- it must leave it to K5 (it is not a
stage-3 candidate) instead of accepting it on its anchor's exam alone. Then
the resumed K5 must end exactly where an uninterrupted curation ends.

Uses the two-round scenario of test_curation_resume.py.
"""

from expedistill.core.config import ExperimentConfig
from expedistill.core.models import Skill
from expedistill.llm import json_script, make_fake_handle
from expedistill.stages import stage4_acceptance
from expedistill.stages.context import StageContext

import pytest

from tests.composites.test_curation_resume import (
    M0,
    Crash,
    GuardStore,
    _handles,
    _remaining,
    _run,
    _scripts,
    _seed,
    _snapshot,
)


def _stage4_ctx(store):
    config = ExperimentConfig(
        benchmark="tb", mode="full",
        models={"executor_strong": "strong-model",
                "executor_weak": "weak-model"},
        weak_profile="weak",
        methods={"acceptance": {"name": "exam", "retry_limit": 1}})
    # Scripted so that a stage 4 which DID drive the merged skill would
    # accept it on the first try (bare answer wrong, with-skill right).
    handles = {
        "answerer": make_fake_handle("weak", ["bare", "with skill"]),
        "judge": make_fake_handle("judge", [
            json_script({"verdict": "incorrect", "error_description": "x"}),
            json_script({"verdict": "correct", "error_description": ""})]),
        "rewriter": make_fake_handle("rewriter", []),
        "auditor": make_fake_handle("auditor", []),
    }
    return StageContext(run_dir=store.run_dir, store=store, config=config,
                        usage=None, handles=handles,
                        workdir_root=store.run_dir / "work")


def test_stage4_between_crashed_and_resumed_curation(tmp_path):
    reference_store = GuardStore(tmp_path / "reference")
    _seed(reference_store)
    _run(reference_store, _handles(_scripts()))
    reference = _snapshot(reference_store)

    # K5 crashes during merge A's first regression exam: bank/merge/0000 is
    # stored "pending", its sources are still "accepted".
    scripts = _scripts()
    scripts["answerer"].insert(0, Crash("first regression answer"))
    store = GuardStore(tmp_path / "run")
    _seed(store)
    handles = _handles(scripts)
    with pytest.raises(Crash):
        _run(store, handles)
    assert store.load("skills", M0, Skill).status == "pending"

    # The pipeline resumes from stage 4 on the same store.
    ctx = _stage4_ctx(GuardStore(tmp_path / "run"))
    stage4_acceptance.run(ctx)
    after_stage4 = ctx.store.load("skills", M0, Skill)
    assert after_stage4.status == "pending", (
        "stage 4 drove K5's in-flight merged skill "
        f"(status now {after_stage4.status!r})")
    assert after_stage4.version == 1
    assert not any(h.client.calls for h in ctx.handles.values())

    # Then K5 resumes and ends exactly like the uninterrupted curation.
    resume_handles = _handles(_remaining(handles))
    _run(GuardStore(tmp_path / "run"), resume_handles)
    assert _remaining(resume_handles) == {
        "curator": [], "answerer": [], "judge": []}
    assert _snapshot(GuardStore(tmp_path / "run")) == reference
