"""Regression: concurrent call_model dumps never share a sequence number.

K1 runs units in B5 threads; the old ``_RequestLog`` scanned for the max seq
and then wrote ``NNNN_<prompt>.json`` (and a fixed ``.json.tmp`` name), so two
threads could pick the same seq and one dump silently replaced the other.
"""

import json
import threading

from expedistill.llm import call as call_module
from expedistill.llm import call_model, make_fake_handle

ANSWER_VARS = {"question_text": "q", "skills_block": ""}
THREADS = 8
CALLS_PER_THREAD = 15


def test_threaded_dumps_get_unique_sequence_numbers(tmp_path, monkeypatch):
    # Widen the race window between "pick a seq" and "publish the file" the
    # way a busy disk would; the dump content is serialized in between.
    real_dumps = json.dumps

    def slow_dumps(*args, **kwargs):
        threading.Event().wait(0.002)
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(call_module.json, "dumps", slow_dumps)
    barrier = threading.Barrier(THREADS)
    errors = []

    def worker(index):
        handle = make_fake_handle(role=f"t{index}", script=f"reply-{index}")
        barrier.wait()
        try:
            for _ in range(CALLS_PER_THREAD):
                call_model(handle, "ANSWER", ANSWER_VARS, run_dir=tmp_path)
        except Exception as exc:  # surfaced below; a thread must not die silently
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    monkeypatch.undo()

    assert errors == []
    dump_dir = tmp_path / "llm_requests"
    names = sorted(p.name for p in dump_dir.iterdir())
    total = THREADS * CALLS_PER_THREAD
    assert names == [f"{seq:04d}_ANSWER.json" for seq in range(1, total + 1)]
    records = [json.loads((dump_dir / name).read_text()) for name in names]
    assert [r["seq"] for r in records] == list(range(1, total + 1))
    per_role = {}
    for record in records:
        per_role[record["role"]] = per_role.get(record["role"], 0) + 1
    assert per_role == {f"t{i}": CALLS_PER_THREAD for i in range(THREADS)}


def test_existing_dump_is_never_overwritten(tmp_path):
    # Another writer (e.g. a second process) published the next seq between
    # two of our calls: ours must take the following number.
    handle = make_fake_handle(script="ours")
    call_model(handle, "ANSWER", ANSWER_VARS, run_dir=tmp_path)
    dump_dir = tmp_path / "llm_requests"
    foreign = dump_dir / "0002_ANSWER.json"
    foreign.write_text('{"foreign": true}', encoding="utf-8")
    call_model(handle, "ANSWER", ANSWER_VARS, run_dir=tmp_path)
    assert foreign.read_text(encoding="utf-8") == '{"foreign": true}'
    assert sorted(p.name for p in dump_dir.iterdir()) == [
        "0001_ANSWER.json", "0002_ANSWER.json", "0003_ANSWER.json"]
    assert json.loads((dump_dir / "0003_ANSWER.json").read_text())["seq"] == 3


def test_usage_ledger_beside_dumps_is_not_a_sequence(tmp_path):
    dump_dir = tmp_path / "llm_requests"
    dump_dir.mkdir()
    (dump_dir / "usage.jsonl").write_text("", encoding="utf-8")
    call_model(make_fake_handle(script="x"), "ANSWER", ANSWER_VARS, run_dir=tmp_path)
    assert sorted(p.name for p in dump_dir.iterdir()) == [
        "0001_ANSWER.json", "usage.jsonl"]
