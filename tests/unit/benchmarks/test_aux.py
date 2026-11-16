"""Unit tests for benchmarks.aux.aux_wiring (plugin contract only, no disk).

The wiring must resolve the plugin itself (import IS registration), hand
back the declared (name, scorer) pair, and answer (None, None) for a
plugin without an auxiliary metric. The strict-scorer contract violation
(declared name, default None answer) is exercised end-to-end through
stage 1 in tests/unit/stages/test_stage1.py.
"""


import pytest

from expedistill.benchmarks.aux import aux_wiring
from tests.helpers.fake_plugin import serve_plugin
from tests.helpers.stub_plugin import StubPlugin

pytestmark = pytest.mark.usefixtures("clean_registry")

_FAKE = "fakebench"


def test_aux_wiring_imports_the_plugin_package(monkeypatch):
    # The wiring must not depend on an execution having imported the plugin
    # (a pure-resume re-aggregation calls no model, but still summarizes).
    serve_plugin(monkeypatch, StubPlugin(aux_metric_name="fake_aux"))
    name, scorer = aux_wiring(_FAKE)
    assert name == "fake_aux"
    assert callable(scorer)


def test_aux_wiring_without_a_declaration_is_none_none(monkeypatch):
    serve_plugin(monkeypatch, StubPlugin())
    assert aux_wiring(_FAKE) == (None, None)
