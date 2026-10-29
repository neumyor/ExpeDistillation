"""Shared pytest setup."""

import pytest

from expedistill.benchmarks.tb.plugin import RUNNER_ENV


@pytest.fixture(autouse=True)
def _tb_runner_script(monkeypatch):
    """The TB plugin has no default runner script: a host names its own.

    Tests drive a fake remote, so any absolute path will do; a test that
    checks the unset case deletes the variable itself."""
    monkeypatch.setenv(RUNNER_ENV, "/srv/work/scripts/run_tb21_runner.sh")
