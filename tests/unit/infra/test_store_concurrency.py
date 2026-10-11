"""append_jsonl is safe for concurrent writers (threads and processes):
the torn-tail repair must never truncate away a record another writer
committed in the meantime."""

import multiprocessing
import threading
import time

from expedistill.infra import store as store_mod
from expedistill.infra.reliability import UnitFailureRecorder
from expedistill.infra.store import append_jsonl, read_jsonl

TORN = b'{"n": 0}\n{"n": "torn", "pa'


def test_repair_cannot_truncate_a_record_committed_meanwhile(tmp_path,
                                                             monkeypatch):
    # Reviewer's reproduction: B reads the torn tail and stalls; A commits
    # lineA; B then truncates back to its stale offset, deleting lineA.
    path = tmp_path / "failed_units.jsonl"
    path.write_bytes(TORN)
    b_in_window = threading.Event()
    a_done = threading.Event()

    def widen(_path):
        if threading.current_thread().name == "B":
            b_in_window.set()
            # Unlocked: A commits within this window. Locked: A cannot get
            # in, so the wait times out and B finishes first.
            a_done.wait(1.0)

    monkeypatch.setattr(store_mod, "_repair_hook", widen)

    def writer_a():
        b_in_window.wait(5)
        append_jsonl(path, {"n": "A"})
        a_done.set()

    a = threading.Thread(target=writer_a, name="A")
    b = threading.Thread(target=append_jsonl, args=(path, {"n": "B"}),
                         name="B")
    a.start()
    b.start()
    a.join(10)
    b.join(10)
    rows = read_jsonl(path)
    assert sorted(str(r["n"]) for r in rows) == ["0", "A", "B"]
    assert path.read_bytes().endswith(b"}\n")


def test_many_threads_with_torn_start_lose_nothing(tmp_path, monkeypatch):
    path = tmp_path / "failed_units.jsonl"
    path.write_bytes(TORN)
    monkeypatch.setattr(store_mod, "_repair_hook",
                        lambda _path: time.sleep(0.02))
    recorder = UnitFailureRecorder(tmp_path)
    start = threading.Barrier(16)

    def worker(index):
        start.wait()
        recorder.record(f"u{index}", "stage1", "InfrastructureError", "x")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    rows = recorder.list_failures()
    assert rows[0] == {"n": 0}  # the committed record before the torn tail
    units = sorted(r["unit_id"] for r in rows[1:])
    assert units == sorted(f"u{i}" for i in range(16))
    # Exactly one writer saw (and preserved) the torn tail.
    evidence = path.with_name(path.name + ".interrupted-tail")
    assert evidence.read_bytes() == b'{"n": "torn", "pa\n'


def _stalled_child_append(path, marker):
    def widen(_path):
        open(marker, "w").close()
        time.sleep(1.0)

    store_mod._repair_hook = widen
    append_jsonl(path, {"n": "child"})


def test_repair_is_serialised_across_processes(tmp_path):
    path = tmp_path / "ledger.jsonl"
    marker = tmp_path / "child-in-window"
    path.write_bytes(TORN)
    child = multiprocessing.get_context("spawn").Process(
        target=_stalled_child_append, args=(path, marker))
    child.start()
    deadline = time.time() + 30
    while not marker.exists():
        assert time.time() < deadline and child.is_alive()
        time.sleep(0.01)
    append_jsonl(path, {"n": "parent"})  # child is mid-repair right now
    child.join(30)
    assert child.exitcode == 0
    rows = read_jsonl(path)
    assert sorted(str(r["n"]) for r in rows) == ["0", "child", "parent"]
