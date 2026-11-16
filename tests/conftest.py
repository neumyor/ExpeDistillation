"""Shared pytest setup."""

import pytest

from expedistill.benchmarks.registry import _reset_registry
from expedistill.benchmarks.tb.plugin import RUNNER_ENV
from tests.helpers.golden_harness import GoldenPlugin, installed_plugin


@pytest.fixture(autouse=True)
def _tb_runner_script(monkeypatch):
    """The TB plugin has no default runner script: a host names its own.

    Tests drive a fake remote, so any absolute path will do; a test that
    checks the unset case deletes the variable itself."""
    monkeypatch.setenv(RUNNER_ENV, "/srv/work/scripts/run_tb21_runner.sh")


@pytest.fixture
def clean_registry():
    """A clean benchmark plugin registry around the test: it is module-global,
    so a leftover registration from a sibling test would make the next
    install a duplicate-name error. Request it for a whole file with
    ``pytestmark = pytest.mark.usefixtures("clean_registry")``."""
    _reset_registry()
    yield
    _reset_registry()


@pytest.fixture
def golden_plugin():
    """The golden matrix's stub benchmark, served through the import
    machinery for the test (``pytestmark = pytest.mark.usefixtures(...)``
    for a whole file)."""
    with installed_plugin(GoldenPlugin()):
        yield
