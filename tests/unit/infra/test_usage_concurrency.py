"""usage.jsonl stays parseable under concurrent appends (threads + processes)
and after a torn write, now that append_jsonl repairs a torn tail."""

import multiprocessing
import threading

from expedistill.infra.store import read_jsonl
from expedistill.infra.usage import UsageRecorder


def _record_many(path, worker, count):
    recorder = UsageRecorder(path)
    for index in range(count):
        recorder.record("weak", "p", "m", index, 1, f"{worker}-{index}")


def test_concurrent_threads_lose_no_lines(tmp_path):
    path = tmp_path / "usage.jsonl"
    threads = [threading.Thread(target=_record_many, args=(path, w, 50))
               for w in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    rows = read_jsonl(path)
    assert sorted(r["request_id"] for r in rows) == sorted(
        f"{w}-{i}" for w in range(8) for i in range(50))


def test_concurrent_processes_lose_no_lines(tmp_path):
    path = tmp_path / "usage.jsonl"
    context = multiprocessing.get_context("spawn")
    procs = [context.Process(target=_record_many, args=(path, w, 40))
             for w in range(4)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(60)
        assert proc.exitcode == 0
    rows = read_jsonl(path)
    assert len(rows) == 160
    assert len({r["request_id"] for r in rows}) == 160
    assert UsageRecorder(path).totals()["overall"]["request_count"] == 160


def test_usage_survives_torn_tail(tmp_path):
    path = tmp_path / "usage.jsonl"
    _record_many(path, "a", 2)
    with path.open("ab") as stream:
        stream.write(b'{"role": "weak", "in_tok')
    _record_many(path, "b", 2)
    rows = read_jsonl(path)
    assert [r["request_id"] for r in rows] == ["a-0", "a-1", "b-0", "b-1"]
    assert UsageRecorder(path).totals()["overall"]["request_count"] == 4
