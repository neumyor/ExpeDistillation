"""The curation=none ablation driver: bank = accepted skills, budget
stays a hard valve, zero model calls."""

import pytest

from expedistill.core import ids
from expedistill.core.config import ConfigError, ExperimentConfig
from expedistill.core.models import Skill
from expedistill.infra.store import Store
from expedistill.methods.curation import NoneParams
from expedistill.methods.curation import none as none_driver
from expedistill.stages.context import StageContext

BIG = 10 ** 6


def _ctx(tmp_path, budget=BIG):
    run_dir = tmp_path / "run"
    return StageContext(
        run_dir=run_dir, store=Store(run_dir),
        config=ExperimentConfig(
            benchmark="tb", mode="full",
            models={"executor_strong": "s", "executor_weak": "w"},
            methods={"curation": {"name": "none",
                                  "token_budget": budget}}),
        usage=None, handles={}, workdir_root=run_dir / "work")


def _skill(store, subtask_id, content="skill text", status="accepted"):
    skill = Skill(skill_id=ids.make_skill_id(subtask_id),
                  content=content, subtask_id=subtask_id)
    skill.status = status
    store.save(skill, "skills")
    return skill


def test_bank_is_the_accepted_skills_verbatim(tmp_path):
    ctx = _ctx(tmp_path)
    kept = _skill(ctx.store, "traj/tb:t1/strong/00/seg00", "keep me")
    _skill(ctx.store, "traj/tb:t1/strong/00/seg01", "gone",
           status="discarded")
    _skill(ctx.store, "traj/tb:t1/strong/00/seg02", "still pending",
           status="pending")

    summary = none_driver.run(ctx, NoneParams(token_budget=BIG))

    assert summary["members"] == [kept.skill_id]
    assert summary["size"] == 1
    assert summary["bank_reference"].startswith("bank:")
    bank = ctx.store.bank()
    assert [row["skill_id"] for row in bank] == [kept.skill_id]
    assert bank[0]["content"] == "keep me"
    # No curation log: that is K5's artifact, and K5 never ran.
    assert not (ctx.run_dir / "stage5_curation"
                / "curation_log.json").exists()


def test_over_budget_stops_loudly(tmp_path):
    ctx = _ctx(tmp_path, budget=2)
    _skill(ctx.store, "traj/tb:t1/strong/00/seg00", "a much longer skill")

    with pytest.raises(ConfigError, match="without curation"):
        none_driver.run(ctx, NoneParams(token_budget=2))
    assert ctx.store.bank() == []


def test_empty_accepted_set_is_a_valid_bank(tmp_path):
    ctx = _ctx(tmp_path)
    summary = none_driver.run(ctx, NoneParams(token_budget=BIG))
    assert summary["members"] == []
    assert summary["bank_reference"].startswith("bank:")


def test_rerun_is_idempotent_not_doubling(tmp_path):
    # The crash/repeat case: the driver writes the bank whole and
    # atomically, so calling it again lands on the same bytes.
    ctx = _ctx(tmp_path)
    _skill(ctx.store, "traj/tb:t1/strong/00/seg00", "one")
    _skill(ctx.store, "traj/tb:t1/strong/00/seg01", "two")
    params = NoneParams(token_budget=BIG)
    first = none_driver.run(ctx, params)
    second = none_driver.run(ctx, params)
    assert first == second
    bank = ctx.store.bank()
    assert [row["skill_id"] for row in bank] == first["members"]
    assert len(bank) == 2
