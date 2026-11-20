"""Routing of scripts/remote/vllm_lb.py: affinity keys and the bounded-load choice."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")

_PATH = Path(__file__).resolve().parents[3] / "scripts" / "remote" / "vllm_lb.py"
_spec = importlib.util.spec_from_file_location("vllm_lb", _PATH)
lb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lb)

TASK = re.compile(r"Your task is to: [^\n]*")


def _chat(user):
    return json.dumps({"messages": [
        {"role": "system", "content": "s"}, {"role": "user", "content": user}]}).encode()


def test_key_is_the_regex_match_and_ignores_the_changing_step_counter():
    one = _chat("A Your task is to: put a cool bread.\nstep 3")
    two = _chat("A Your task is to: put a cool bread.\nstep 4")
    assert lb.affinity_key(one, TASK, 150) == lb.affinity_key(two, TASK, 150) \
        == "Your task is to: put a cool bread."


def test_key_falls_back_to_the_leading_characters_without_a_match():
    assert lb.affinity_key(_chat("abcdef"), TASK, 3) == "abc"
    assert lb.affinity_key(_chat("abcdef"), None, 4) == "abcd"


def test_key_handles_content_blocks_and_completions():
    blocks = json.dumps({"messages": [{"role": "user", "content": [
        {"type": "text", "text": "abcdef"}]}]}).encode()
    assert lb.affinity_key(blocks, None, 3) == "abc"
    assert lb.affinity_key(json.dumps({"prompt": "xyz123"}).encode(), None, 3) == "xyz"


@pytest.mark.parametrize("body", [b"not json", b"[]", b"{}",
                                  json.dumps({"messages": []}).encode()])
def test_no_usable_text_means_no_key(body):
    assert lb.affinity_key(body, None, 10) is None


def test_a_key_keeps_its_backend_and_keys_spread():
    pool = lb.Pool(["a", "b", "c"])
    keys = [f"task {i}" for i in range(60)]
    first = {k: pool.order(k)[0] for k in keys}
    assert all(pool.order(k)[0] == first[k] for k in keys)
    counts = [sum(1 for v in first.values() if v == b) for b in "abc"]
    assert min(counts) >= 10


def test_a_busy_preferred_backend_spills_over_and_a_down_one_comes_last():
    pool = lb.Pool(["a", "b", "c"])
    key = "task 0"
    preferred = pool.order(key)[0]
    pool.inflight[preferred] = 50
    assert pool.order(key)[0] != preferred
    pool.inflight[preferred] = 0
    assert pool.order(key)[0] == preferred
    pool.mark_down(preferred)
    assert pool.order(key)[-1] == preferred


def test_without_a_key_the_least_busy_backend_goes_first():
    pool = lb.Pool(["a", "b"])
    pool.inflight["a"] = 3
    assert pool.order()[0] == "b"
