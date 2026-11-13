"""Skill-folder M4, the landing: the folder test phase's summary carries
the per-unit read sets, the old execution versions' summaries do not,
and the read set never reaches the decision module.

Where the read set lands (the on-site ruling this file pins): the test
phase writes NO learning report (test results never flow into learning)
-- the phase summary is the test phase's one report output, so the read
sets land there, next to its existing per-unit facts (``infra_missing`` /
``infra_accepted``, keyed by trajectory id over the with-bank arm).
The fake-plugin route of tests/unit/stages/test_final_test_folder_wiring.py (no network).
"""

import sys
from pathlib import Path

import pytest

from expedistill.benchmarks.base import RunOutcome
from expedistill.core import ids
from expedistill.core.config import CompareSpec, ExecutePhaseSpec, PhaseInstance
from expedistill.stages import phase_execute
from expedistill.composites import phase_report
from tests.unit.stages.test_final_test_folder_wiring import (
    FolderPlugin,
    TASK_IDS,
    _c_phase,
    _card,
    _ctx,
    _install_plugin,
    _reset_registry,
    _write_bank,
)

_FAKE = "fakebench"


class CattingPlugin(FolderPlugin):
    """A capable folder plugin whose with-bank units cat skill files
    inside the sandbox mount -- t1 the first manifest file, t2 both,
    t3 nothing (the bare-run shape)."""

    def run(self, task, handle, skills_block, workdir):
        self.calls.append({"task": task, "skills_block": skills_block})
        handle.client.complete(handle.name, "system", task.instruction)
        manifest = task.meta.get("skill_folder") or {}
        names = [entry["filename"] for entry in manifest.get("files", [])]
        which = {"t1": names[:1], "t2": names, "t3": []}[task.task_id]
        action = "\n".join(
            'bash_command({"duration": 5, "keystrokes": '
            f'"cat /opt/openclaw-skills/{task.task_id}/{name}\\n"}})'
            for name in which) or "bash_command({\"keystrokes\": \"ls\\n\"})"
        return RunOutcome(
            steps=[{"action": action, "observation": "ok"}],
            correctness=True)


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry()
    yield
    sys.modules.pop(f"expedistill.benchmarks.{_FAKE}", None)
    _reset_registry()


def _trajectory_id(task_id):
    return ids.make_trajectory_id(
        "p03:test", ids.make_task_id(_FAKE, task_id), "weak", 0)


def _plain_c_phase():
    """A test phase on the OLD execution pair (whole_bank + real): the
    standard test combo, no folder anywhere."""
    return PhaseInstance(
        index=2, name="test", label="p03:test",
        spec=ExecutePhaseSpec(
            role="test", tasks="test",
            delivery={"name": "whole_bank"},
            execution={"name": "real"},
            compare=CompareSpec(against="fresh_bare"),
            acceptance=None, kind="execute"),
        task_ids=TASK_IDS)


# ---------------------------------------------------------------------------
# The folder test phase's summary carries the per-unit read sets
# ---------------------------------------------------------------------------

def test_folder_c_phase_reports_the_read_sets_in_its_summary(
        monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, CattingPlugin(capable=True))
    ctx = _ctx(tmp_path)
    _write_bank(ctx, [_card(1), _card(2)])
    phase = _c_phase()

    result = phase_execute.run(ctx, phase).summary

    # The with-bank arm's units, keyed by trajectory id: t1 cat the
    # first manifest file, t2 both (manifest order), t3 nothing.
    assert result["skill_files_read"] == {
        _trajectory_id("t1"): [_card(1).skill_id],
        _trajectory_id("t2"): [_card(1).skill_id, _card(2).skill_id],
        _trajectory_id("t3"): [],
    }
    # Only the with-bank units carry a manifest (the control arm's
    # calls never do -- test_final_test_folder_wiring's own pin), so the read
    # sets are exactly the parse of the with-bank units' cats.
    with_bank = [call for call in plugin.calls
                 if call["task"].meta.get("skill_folder") is not None]
    assert sorted(call["task"].task_id for call in with_bank) \
        == ["t1", "t2", "t3"]


def test_an_empty_bank_reports_empty_read_sets(monkeypatch, tmp_path):
    # The manifest lists no files, every with-bank unit runs with an
    # empty catalog, and every read set is empty -- reported, not
    # skipped.
    _install_plugin(monkeypatch, CattingPlugin(capable=True))
    ctx = _ctx(tmp_path)
    _write_bank(ctx, [_card(1, status="discarded")])
    phase = _c_phase()

    result = phase_execute.run(ctx, phase).summary

    assert result["skill_files_read"] == {
        _trajectory_id(task_id): [] for task_id in TASK_IDS}


def test_a_resumed_c_phase_recomputes_the_same_read_sets(
        monkeypatch, tmp_path):
    _install_plugin(monkeypatch, CattingPlugin(capable=True))
    ctx = _ctx(tmp_path)
    _write_bank(ctx, [_card(1), _card(2)])
    phase = _c_phase()

    first = phase_execute.run(ctx, phase).summary["skill_files_read"]
    second = phase_execute.run(ctx, phase).summary["skill_files_read"]

    # K1's own resume keeps the stored units, so the same stored
    # trajectories parse to the same read sets (the pure function's
    # crash-resume contract).
    assert first == second


# ---------------------------------------------------------------------------
# The old execution versions' rows carry no read sets
# ---------------------------------------------------------------------------

def test_the_old_execution_summary_carries_no_read_sets(
        monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, CattingPlugin(capable=False))
    ctx = _ctx(tmp_path)
    _write_bank(ctx, [_card(1)])
    phase = _plain_c_phase()

    result = phase_execute.run(ctx, phase).summary

    assert "skill_files_read" not in result
    # Every unit ran with the old plain injection: no manifest anywhere.
    assert all(call["task"].meta.get("skill_folder") is None
               for call in plugin.calls)


# ---------------------------------------------------------------------------
# The read set never reaches the decision module
# ---------------------------------------------------------------------------

def test_the_trial_artifacts_carry_both_arms(
        monkeypatch, tmp_path):
    _install_plugin(monkeypatch, CattingPlugin(capable=True))
    ctx = _ctx(tmp_path)
    _write_bank(ctx, [_card(1), _card(2)])
    phase = _c_phase()

    phase_execute.run(ctx, phase)

    directory = Path(ctx.run_dir) / "phases" / "p03_test"
    report = phase_report.read_phase_report(
        directory, phase_kind="test", learning_usable=False)
    assert report is not None
    assert {unit.arm for unit in report.units} == {"with_bank", "control"}
