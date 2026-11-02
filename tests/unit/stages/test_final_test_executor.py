"""The test phase's executor: ``real_folder`` may name the strong model.

The test phase ran the weak model only. ``execution.executor:
executor_strong`` hands the same bank to the strong model: the phase must
then dispatch the strong handle at the strong depth, name every unit,
trajectory and control record with the strong role, and read its external
control from whatever campaign column the config pins. The default stays
the weak model, byte for byte.

The fake-plugin route of tests/unit/stages/test_final_test_folder_wiring.py
(no network).
"""

import dataclasses
import hashlib
import json
import sys

import pytest

from expedistill.core import ids
from expedistill.core.config import (
    CompareSpec,
    ExecutePhaseSpec,
    ExternalControlSpec,
    PhaseInstance,
)
from expedistill.infra.reliability import ConfigError
from expedistill.infra.store import object_filename
from expedistill.stages import phase_execute
from expedistill.stages.phase_execute import resolve
from tests.unit.stages.test_final_test_folder_wiring import (
    FolderPlugin,
    TASK_IDS,
    _config,
    _ctx,
    _group_skill,
    _install_plugin,
    _reset_registry,
    _write_bank,
)

_FAKE = "fakebench"


class RoleRecordingPlugin(FolderPlugin):
    """A capable folder plugin that records which model role each call
    was dispatched for."""

    def __init__(self):
        super().__init__(capable=True)
        self.roles = []

    def run(self, task, handle, skills_block, workdir):
        self.roles.append(handle.role)
        return super().run(task, handle, skills_block, workdir)


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry()
    from expedistill.composites import verdicts
    verdicts.BENCHMARK_VERDICT_RULES.setdefault(_FAKE, "scorer")
    yield
    sys.modules.pop(f"expedistill.benchmarks.{_FAKE}", None)
    _reset_registry()
    verdicts.BENCHMARK_VERDICT_RULES.pop(_FAKE, None)


def _phase(*, executor=None, compare):
    execution = {"name": "real_folder"}
    if executor is not None:
        execution["executor"] = executor
    return PhaseInstance(
        index=2, name="test", label="p03:test",
        spec=ExecutePhaseSpec(
            role="test", tasks="test", delivery={"name": "folder"},
            execution=execution, compare=compare, acceptance=None,
            kind="execute"),
        task_ids=TASK_IDS)


def _deep_strong_ctx(tmp_path):
    """The folder-test context with distinct depths: strong 2, weak 1."""
    return dataclasses.replace(
        _ctx(tmp_path),
        config=dataclasses.replace(_config(), n_attempts_strong=2))


def _trajectory_files(run_dir, arm):
    directory = (run_dir / "phases" / "p03_test" / arm / "store"
                 / "stage1_baseline" / "trajectories")
    return sorted(path.name for path in directory.glob("*.json"))


# ---------------------------------------------------------------------------
# The params and the role helper
# ---------------------------------------------------------------------------

def test_the_executor_defaults_to_the_weak_model():
    spec = _phase(compare=None).spec
    assert resolve._test_role(resolve._execution_params(spec)) == "weak"


def test_a_plain_real_test_phase_always_runs_the_weak_model():
    spec = ExecutePhaseSpec(
        role="test", tasks="test", delivery={"name": "whole_bank"},
        execution={"name": "real"}, compare=None, acceptance=None,
        kind="execute")
    assert resolve._test_role(resolve._execution_params(spec)) == "weak"


def test_the_folder_execution_can_name_the_strong_executor():
    spec = _phase(executor="executor_strong", compare=None).spec
    assert resolve._test_role(resolve._execution_params(spec)) == "strong"


def test_an_unknown_executor_is_refused_loudly():
    spec = _phase(executor="executor_middle", compare=None).spec
    with pytest.raises(ConfigError, match="executor must be one of"):
        resolve._execution_params(spec)


# ---------------------------------------------------------------------------
# The phase: strong model, strong depth, strong ids
# ---------------------------------------------------------------------------

def test_a_strong_test_phase_dispatches_the_strong_handle_at_its_depth(
        monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, RoleRecordingPlugin())
    ctx = _deep_strong_ctx(tmp_path)
    _write_bank(ctx, [_group_skill("t1")])

    result = phase_execute.run(
        ctx, _phase(executor="executor_strong",
                    compare=CompareSpec(against="fresh_bare"))).summary

    # Both arms (with-bank and the fresh bare control) run the strong
    # handle, twice per task: strong depth 2, not the weak depth 1.
    assert plugin.roles == ["executor_strong"] * (2 * len(TASK_IDS) * 2)
    assert result["complete"] is True
    expected = sorted(
        object_filename(ids.make_trajectory_id(
            ids.make_task_id(_FAKE, task), "strong", attempt))
        for task in TASK_IDS for attempt in range(2))
    for arm in ("with_bank", "control"):
        assert _trajectory_files(ctx.run_dir, arm) == expected


def test_the_default_test_phase_is_unchanged(monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, RoleRecordingPlugin())
    ctx = _deep_strong_ctx(tmp_path)
    _write_bank(ctx, [_group_skill("t1")])

    phase_execute.run(
        ctx, _phase(compare=CompareSpec(against="fresh_bare")))

    # The weak depth is 1: one call per task and arm, all on the weak handle.
    assert plugin.roles == ["executor_weak"] * (2 * len(TASK_IDS))


def test_a_strong_test_phase_reads_the_pinned_campaign_column(
        monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, RoleRecordingPlugin())
    ctx = _deep_strong_ctx(tmp_path)
    _write_bank(ctx, [_group_skill("t1")])
    campaign = tmp_path / "campaign.json"
    campaign.write_text(json.dumps({"tasks": {
        f"dpsk:{task}": {"n": 3, "rewards": [1.0, 0.0, 1.0]}
        for task in TASK_IDS}}), encoding="utf-8")
    digest = hashlib.sha256(campaign.read_bytes()).hexdigest()
    compare = CompareSpec(
        against="external_campaign",
        external=ExternalControlSpec(
            path=str(campaign), sha256=digest, model_key="dpsk"))

    result = phase_execute.run(
        ctx, _phase(executor="executor_strong", compare=compare)).summary

    # Only the with-bank arm executes; the control is the archived column.
    assert plugin.roles == ["executor_strong"] * (len(TASK_IDS) * 2)
    # The control is aligned to the strong depth (2): the first two of the
    # archived rewards [1, 0, 1].
    assert result["control"]["avg_at_n"] == pytest.approx(0.5)
    sheets_dir = ctx.run_dir / "phases" / "p03_test" / "answer_sheets"
    external = [json.loads(path.read_text(encoding="utf-8"))
                for path in sheets_dir.glob("*.json")]
    external = [sheet["execution"] for sheet in external
                if sheet["execution"]["source"] == "external"]
    assert len(external) == len(TASK_IDS) * 2
    assert {record["role"] for record in external} == {"strong"}
    assert {record["model_role"] for record in external} == {"strong"}
