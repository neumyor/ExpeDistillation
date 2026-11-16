"""Fixtures shared by the benchmark plugin tests."""

import pytest

from tests.helpers.searchqa_stub import install_eval_repo_stub


@pytest.fixture
def stub_eval_repo(monkeypatch):
    return install_eval_repo_stub(monkeypatch)
