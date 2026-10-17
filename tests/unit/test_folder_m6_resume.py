"""Skill-folder M6, the C phase's true mid-run resume: a folder phase
interrupted partway (some units stored, some not) lands on the SAME
summary as a one-shot run -- byte for byte, read sets included.

The M4 resume test replayed a COMPLETED phase, so it could not tell
"K1 reused the stored units" from "everything ran again" (the resume
review's C-2). This file manufactures the interrupted state directly:
the one-shot run's own stored with-bank and control trajectories for
ONE task land in a fresh run's arm stores, exactly the way a killed
process's own writes would sit on disk; the phase then "resumes", the
plugin's call log must prove the stored units never re-ran, and the
final summary must be byte-identical to the one-shot run's.

The fake-plugin route of test_folder_m3_wiring.py (no network); the
catting behaviour is test_folder_m4_summary's CattingPlugin, so the
read sets under comparison are non-trivial (one unit cats one file,
one cats both, one cats nothing).
"""

import shutil
import sys
from pathlib import Path

import pytest

from expedistill.infra.store import Store
from expedistill.infra.usage import UsageRecorder
from expedistill.llm.fake import make_fake_handle
from expedistill.stages import phase_execute
from expedistill.stages.context import RunSettings, StageContext
from expedistill.stages.phase_execute.phase_disk import (
    SUMMARY_NAME,
    _arm_dir,
    _phase_dir,
)
from tests.unit.test_folder_m3_wiring import (
    _FAKE,
    _c_phase,
    _config,
    _group_skill,
    _install_plugin,
    _reset_registry,
    _write_bank,
)
from tests.unit.test_folder_m4_summary import CattingPlugin, _trajectory_id

_MODULE = f"expedistill.benchmarks.{_FAKE}"


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry()
    from expedistill.composites import verdicts
    verdicts.BENCHMARK_VERDICT_RULES.setdefault(_FAKE, "scorer")
    yield
    sys.modules.pop(_MODULE, None)
    _reset_registry()
    verdicts.BENCHMARK_VERDICT_RULES.pop(_FAKE, None)


def _ctx_at(root):
    """_ctx with an arbitrary run directory (one test, two runs)."""
    run_dir = Path(root)
    return StageContext(
        run_dir=run_dir,
        store=Store(run_dir),
        config=_config(),
        usage=UsageRecorder(run_dir / "llm_requests" / "usage.jsonl"),
        handles={role: make_fake_handle(role)
                 for role in ("executor_strong", "executor_weak",
                              "examiner", "answerer", "judge", "auditor")},
        workdir_root=run_dir / "workdirs",
        run_settings=RunSettings(),
    )


def test_a_folder_c_phase_resumed_mid_run_lands_on_the_one_shot_summary(
        monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, CattingPlugin(capable=True))
    phase = _c_phase()
    skills = [_group_skill("t1"), _group_skill("t2")]

    # The one-shot run: every unit executes once, the summary lands.
    ctx_once = _ctx_at(tmp_path / "one_shot")
    _write_bank(ctx_once, skills)
    phase_execute.run(ctx_once, phase)
    summary_once = (_phase_dir(ctx_once, phase) / SUMMARY_NAME).read_bytes()

    # The interrupted run: the crash happened after ONE with-bank unit
    # and ONE control unit stored (1 of 3 in each arm). Copy exactly
    # those stored trajectories into a fresh run's arm stores -- the
    # bytes the killed process itself had written.
    plugin.calls.clear()
    ctx_half = _ctx_at(tmp_path / "half")
    _write_bank(ctx_half, skills)
    stored_id = _trajectory_id("t1")
    for arm in ("with_bank", "control"):
        source = Store(_arm_dir(ctx_once, phase, arm) / "store")
        target = Store(_arm_dir(ctx_half, phase, arm) / "store")
        shutil.copy(source.path_for("trajectories", stored_id),
                    target.path_for("trajectories", stored_id))

    phase_execute.run(ctx_half, phase)

    # The resume REUSED the stored units: only t2 and t3 reached the
    # plugin in each arm (the one-shot run answered six calls, this
    # one four -- the stored units never re-ran).
    with_bank = [call for call in plugin.calls
                 if call["task"].meta.get("skill_folder") is not None]
    control = [call for call in plugin.calls
               if call["task"].meta.get("skill_folder") is None]
    assert sorted(call["task"].task_id for call in with_bank) == \
        ["t2", "t3"]
    assert sorted(call["task"].task_id for call in control) == \
        ["t2", "t3"]
    # And the whole summary -- read sets included -- is byte-identical
    # to the one-shot run's (the fresh units are deterministic, and the
    # pre-stored t1 trajectories are the one-shot run's own).
    summary_half = (_phase_dir(ctx_half, phase)
                    / SUMMARY_NAME).read_bytes()
    assert summary_half == summary_once
