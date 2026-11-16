"""The run supervisor: exit-code decisions, the progress guard, and the
resume loop driven through a stand-in launch.sh."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "remote" / "supervise_run.py"
spec = importlib.util.spec_from_file_location("supervise_run", SCRIPT)
sup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sup)


def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_exit_codes_other_than_3_stop_with_a_reason():
    for code in (0, 1, 2, 137, -9, None):
        decision = sup.decide(code, stalled=0, max_stalled=5)
        assert decision.action == "stop"
        assert decision.reason
    assert "finished" in sup.decide(0, 0, 5).reason
    assert "human" in sup.decide(1, 0, 5).reason


def test_exit_3_resumes_until_too_many_stalled_attempts():
    assert sup.decide(3, stalled=0, max_stalled=5).action == "resume"
    assert sup.decide(3, stalled=4, max_stalled=5).action == "resume"
    assert sup.decide(3, stalled=5, max_stalled=5).action == "stop"


def test_stall_counter_resets_on_progress():
    assert sup.next_stalled(3, progressed=True) == 0
    assert sup.next_stalled(3, progressed=False) == 4


def test_progress_is_any_count_growing():
    base = sup.Snapshot(1, 10, 0)
    assert not sup.made_progress(base, sup.Snapshot(1, 10, 0))
    assert sup.made_progress(base, sup.Snapshot(2, 10, 0))
    assert sup.made_progress(base, sup.Snapshot(1, 11, 0))
    assert sup.made_progress(base, sup.Snapshot(1, 10, 1))


def test_snapshot_counts_ledger_phases_stores_and_learn_rows(tmp_path):
    run = tmp_path / "run"
    (run / "stage1_baseline" / "trajectories").mkdir(parents=True)
    (run / "stage1_baseline" / "trajectories" / "a.json").write_text("{}")
    arm = run / "phases" / "p03_test" / "with_bank" / "store" / \
        "stage1_baseline" / "trajectories"
    arm.mkdir(parents=True)
    (arm / "b.json").write_text("{}")
    (arm / "b.json.tmp").write_text("{}")
    learn = run / "phases" / "p02_learn"
    learn.mkdir(parents=True)
    (learn / "findings.jsonl").write_text('{"x": 1}\n{"x": 2}\n')
    (learn / "outcomes.jsonl").write_text('{"x": 1}\n\n')
    rows = [{"stage": "p00:freeze", "summary": {}},
            {"stage": "p01:baseline", "summary": {}},
            {"stage": "p01:baseline", "summary": {}},
            {"stage": "p02:learn"}]
    (run / "pipeline_log.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows) + "not json\n")
    assert sup.snapshot(run) == sup.Snapshot(2, 2, 3)
    assert sup.snapshot(tmp_path / "absent") == sup.Snapshot(0, 0, 0)


def _run_dir(tmp_path, first_exit, launch_exits):
    """A run whose driver is gone with ``first_exit``; launch.sh pops the
    next code of ``launch_exits`` into driver.pid.exit (a dead driver)."""
    run = tmp_path / "run"
    run.mkdir()
    (run / "driver.pid").write_text(f"{_dead_pid()}\n")
    (run / "driver.pid.exit").write_text(f"{first_exit}\n")
    (run / "codes").write_text(" ".join(str(code) for code in launch_exits))
    (run / "launch.sh").write_text(
        "#!/usr/bin/env bash\n"
        'here="$(cd "$(dirname "$0")" && pwd)"\n'
        'read -r first rest < "$here/codes" || true\n'
        'echo "$rest" > "$here/codes"\n'
        'echo "$first" > "$here/driver.pid.exit"\n'
        'echo launched >> "$here/launches"\n')
    return run


def _supervisor(run, max_stalled=5):
    return sup.Supervisor(run, backoff=0, max_stalled=max_stalled, poll=0.01,
                          sleep=lambda seconds: None)


def test_exit_3_relaunches_and_exit_0_stops(tmp_path, monkeypatch):
    run = _run_dir(tmp_path, first_exit=3, launch_exits=[3, 0])
    # Each attempt stores a trajectory, so no attempt is stalled.
    store = run / "stage1_baseline" / "trajectories"
    store.mkdir(parents=True)
    original = sup.snapshot
    calls = []

    def growing(run_dir):
        calls.append(1)
        (store / f"{len(calls)}.json").write_text("{}")
        return original(run_dir)

    monkeypatch.setattr(sup, "snapshot", growing)
    assert _supervisor(run, max_stalled=1).run() == 0
    assert (run / "launches").read_text().count("launched") == 2


def test_stalled_exit_3_attempts_stop_the_supervisor(tmp_path):
    run = _run_dir(tmp_path, first_exit=3, launch_exits=[3, 3, 3, 3])
    assert _supervisor(run, max_stalled=3).run() == 1
    # Attempts 1 and 2 end stalled and are resumed, attempt 3 stops it.
    assert (run / "launches").read_text().count("launched") == 2


def test_exit_1_never_relaunches(tmp_path):
    run = _run_dir(tmp_path, first_exit=1, launch_exits=[0])
    assert _supervisor(run).run() == 1
    assert not (run / "launches").exists()


def test_launch_that_starts_nothing_stops(tmp_path):
    run = _run_dir(tmp_path, first_exit=3, launch_exits=[])
    (run / "launch.sh").write_text("#!/usr/bin/env bash\nexit 2\n")
    assert _supervisor(run).run() == 1
    assert not (run / "driver.pid.exit").exists()


def test_nothing_to_supervise(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    assert _supervisor(run).run() == 1


def test_refuses_a_second_live_supervisor(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (run / "supervisor.pid").write_text(f"{holder.pid}\n")
        assert sup.main([str(run), "--foreground"]) == 1
    finally:
        holder.kill()
        holder.wait()
