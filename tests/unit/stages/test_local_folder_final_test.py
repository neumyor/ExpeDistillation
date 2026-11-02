"""The test phase's folder play on a LOCAL benchmark, end to end.

The real SearchQA plugin (its eval repo stubbed) runs the phase driver's
folder delivery: the bank materializes once, the with-bank unit's task
carries the folder, the plugin shows only the card list, the scripted weak
model opens one card, and the read lands in the phase summary's
``skill_files_read``. The control arm runs the plain prompt.

The fake-plugin wiring (materialize once, attach, refuse) is
test_final_test_folder_wiring.py's business; this file holds the part
that needs a plugin which really executes a folder unit.
"""

import json
import re
from pathlib import Path

import pytest

from expedistill.benchmarks.searchqa import env as searchqa_env
from expedistill.composites import phase_bank
from expedistill.core.config import (
    CompareSpec,
    ExecutePhaseSpec,
    ExperimentConfig,
    PhaseInstance,
)
from expedistill.core.ids import (
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill
from expedistill.infra.store import Store
from expedistill.infra.usage import UsageRecorder
from expedistill.llm.fake import make_fake_handle
from expedistill.stages import phase_execute
from expedistill.stages.context import RunSettings, StageContext
from tests.helpers.plugins import ensure_plugin
from tests.helpers.searchqa_stub import (
    fixture_items,
    install_eval_repo_stub,
    write_eval_dir,
)
from tests.helpers.skill_folder import CARD_BODY

BENCHMARK = "searchqa"


def _config():
    return ExperimentConfig(
        benchmark=BENCHMARK, mode="split",
        models={"executor_strong": "big-model",
                "executor_weak": "weak-7b"},
        weak_profile="a weak model",
        n_attempts_strong=1, n_attempts_weak=1,
        rules={}, sequence=["baseline"],
        phases={"baseline": {"kind": "execute", "role": "baseline",
                             "tasks": "train", "delivery": {"name": "none"},
                             "execution": {"name": "real",
                                           "arms": ["executor_strong",
                                                    "executor_weak"]}}})


def _weak_policy(system, user):
    """Open the first listed card once, then search and answer."""
    if "<available_skills>" in user and "action: ReadSkill" not in user:
        location = re.search(r"<location>(.*?)</location>", user).group(1)
        return f"ReadSkill[{location}]"
    if "action: Search" not in user:
        return "Search[red cent]"
    return "Finish[red cent]"


def _ctx(tmp_path, weak_script):
    run_dir = tmp_path / "run"
    handles = {role: make_fake_handle(role)
               for role in ("executor_strong", "examiner", "answerer",
                            "judge", "auditor")}
    handles["executor_weak"] = make_fake_handle(
        "executor_weak", script=weak_script)
    return StageContext(
        run_dir=run_dir, store=Store(run_dir), config=_config(),
        usage=UsageRecorder(run_dir / "llm_requests" / "usage.jsonl"),
        handles=handles, workdir_root=run_dir / "workdirs",
        run_settings=RunSettings())


def _skill(task_id):
    subtask_id = make_subtask_id(
        make_trajectory_id(make_task_id(BENCHMARK, task_id), "strong", 0), 0)
    return Skill(skill_id=make_skill_id(subtask_id), content=CARD_BODY,
                 summary="Look up the full name first.",
                 subtask_id=subtask_id, source_subtasks=[subtask_id],
                 version=1, status="accepted")


def _write_bank(ctx, skills):
    directory = phase_bank.phase_bank_dir(ctx.run_dir, "p02:learn")
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / phase_bank.BANK_NAME, "w",
              encoding="utf-8") as handle:
        for skill in skills:
            handle.write(json.dumps(skill.to_dict(), ensure_ascii=False)
                         + "\n")


def _phase(task_ids):
    return PhaseInstance(
        index=2, name="test", label="p03:test",
        spec=ExecutePhaseSpec(
            role="test", tasks="test",
            delivery={"name": "folder"},
            execution={"name": "real_folder"},
            compare=CompareSpec(against="fresh_bare"),
            acceptance=None, kind="execute"),
        task_ids=tuple(task_ids))


@pytest.fixture
def setup(monkeypatch, tmp_path):
    ensure_plugin(BENCHMARK)
    install_eval_repo_stub(monkeypatch)
    item = fixture_items()[0]
    eval_dir = write_eval_dir(tmp_path / "eval", {"test": [item]})
    monkeypatch.setenv(searchqa_env.EVAL_DIR_ENV, str(eval_dir))
    ctx = _ctx(tmp_path, _weak_policy)
    skill = _skill(item["id"])
    _write_bank(ctx, [skill])
    return ctx, _phase([item["id"]]), skill


def test_a_local_folder_unit_reads_a_card_and_the_read_reaches_the_summary(
        setup):
    ctx, phase, skill = setup

    summary = phase_execute.run(ctx, phase).summary

    assert summary["complete"] is True
    prompts = [call["user"]
               for call in ctx.handles["executor_weak"].client.calls]
    with_bank = [user for user in prompts if "<available_skills>" in user]
    control = [user for user in prompts if "<available_skills>" not in user]
    # With-bank: read, search, finish (3 model calls); control: 2.
    assert len(with_bank) == 3 and len(control) == 2
    assert all("ReadSkill" not in user for user in control)
    # Progressive disclosure: the card text appears only after the read.
    assert CARD_BODY not in with_bank[0]
    assert CARD_BODY in with_bank[1]
    # The folder the plugin read from is the phase's materialized one.
    folder_dir = Path(ctx.run_dir) / "phases" / "p03_test" / "skill_folder"
    assert any(path.name == "SKILL.md" for path in folder_dir.rglob("*"))
    # The read fact is the same one the TB folder play reports.
    assert list(summary["skill_files_read"].values()) == [[skill.skill_id]]


def test_the_local_folder_unit_is_scored_by_the_eval_repo(setup):
    ctx, phase, _ = setup

    summary = phase_execute.run(ctx, phase).summary

    assert summary["with_bank"]["pass_at_n"] == 1.0
    assert summary["control"]["pass_at_n"] == 1.0
