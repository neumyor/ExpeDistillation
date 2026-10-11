"""Unit tests for the generic named registry."""
import pytest

from expedistill.core.registry import make_registry


def test_register_and_get():
    reg = make_registry("rule")
    @reg.register("all_fail")
    def all_fail(items):
        return items
    assert reg.get("all_fail") is all_fail
    assert reg.names() == ["all_fail"]


def test_duplicate_name_raises():
    reg = make_registry("prompt")
    reg.register("segment")("text")
    with pytest.raises(ValueError, match="already registered"):
        reg.register("segment")("other text")


def test_unknown_name_lists_known_names():
    reg = make_registry("rule")
    reg.register("alpha")(1)
    reg.register("beta")(2)
    with pytest.raises(KeyError) as exc:
        reg.get("gamma")
    assert "alpha" in str(exc.value)
    assert "beta" in str(exc.value)


def test_unknown_name_when_empty():
    reg = make_registry("plugin")
    with pytest.raises(KeyError, match="nothing registered"):
        reg.get("anything")


def test_names_are_sorted():
    reg = make_registry()
    for name in ("c", "a", "b"):
        reg.register(name)(name)
    assert reg.names() == ["a", "b", "c"]


def test_contains():
    reg = make_registry()
    reg.register("x")(None)
    assert "x" in reg
    assert "y" not in reg


def test_decorator_returns_the_object():
    reg = make_registry()
    @reg.register("f")
    def f():
        return 42
    assert f() == 42
    assert reg.get("f") is f
