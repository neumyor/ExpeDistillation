"""Wire-id rotation for a gateway that rate-limits per request id."""

import pytest

from expedistill.infra.reliability import ConfigError
from expedistill.llm.client import AliasPool, parse_aliases


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_parse_reads_several_models_and_drops_duplicates():
    table = parse_aliases("a=x,y,x; b=z")

    assert table == {"a": ["x", "y"], "b": ["z"]}


@pytest.mark.parametrize("raw", ["a", "a=", "=x", "a=x;a=y"])
def test_a_malformed_table_is_a_config_error_without_echoing_values(raw):
    with pytest.raises(ConfigError) as excinfo:
        parse_aliases(raw)

    assert "EXPEDISTILL_LLM_MODEL_ALIASES" in str(excinfo.value)
    assert "=x" not in str(excinfo.value)


def test_an_empty_table_is_no_aliases():
    assert parse_aliases("") == {} and parse_aliases(" ; ") == {}


def test_a_model_without_aliases_goes_out_unchanged():
    pool = AliasPool({"a": ["x"]})

    assert pool.pick("other") == "other"
    assert AliasPool().pick("a") == "a"


def test_requests_rotate_evenly_over_the_aliases():
    pool = AliasPool({"a": ["x", "y", "z"]})

    picks = [pool.pick("a") for _ in range(9)]

    assert picks == ["x", "y", "z"] * 3


def test_a_rate_limited_alias_rests_until_its_cooldown_ends():
    clock = Clock()
    pool = AliasPool({"a": ["x", "y", "z"]}, cooldown_seconds=20, clock=clock)
    pool.rate_limited("y")

    during = [pool.pick("a") for _ in range(6)]
    clock.now += 21
    after = [pool.pick("a") for _ in range(6)]

    assert "y" not in during and {"x", "z"} <= set(during)
    assert set(after) == {"x", "y", "z"}


def test_when_every_alias_rests_the_earliest_to_recover_is_used():
    clock = Clock()
    pool = AliasPool({"a": ["x", "y"]}, cooldown_seconds=20, clock=clock)
    pool.rate_limited("x")
    clock.now += 5
    pool.rate_limited("y")

    assert pool.pick("a") == "x"


def test_rotation_is_per_logical_model():
    pool = AliasPool({"a": ["a1", "a2"], "b": ["b1", "b2"]})

    assert [pool.pick("a"), pool.pick("b"), pool.pick("a"), pool.pick("b")] \
        == ["a1", "b1", "a2", "b2"]


def test_concurrent_picks_stay_balanced():
    import threading

    pool = AliasPool({"a": ["x", "y", "z", "w"]})
    seen = []
    lock = threading.Lock()

    def work():
        for _ in range(100):
            alias = pool.pick("a")
            with lock:
                seen.append(alias)

    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert {alias: seen.count(alias) for alias in "xyzw"} == {
        "x": 200, "y": 200, "z": 200, "w": 200}
