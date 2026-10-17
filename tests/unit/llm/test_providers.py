"""The provider registry (P5): a plain name -> builder table, the
environment names the provider, unknown names are loud."""

import pytest

from expedistill.core.config import ConfigError  # noqa: F401 -- see below
from expedistill.infra.reliability import ConfigError as InfraError
import expedistill.llm.providers as providers_mod
from expedistill.llm.providers import PROVIDERS, default_client


def test_the_registry_holds_the_default_provider():
    # 'fake' is the plan-scripted smoke client (llm/fake_provider.py);
    # the checked-in entries are exactly the two.
    assert set(PROVIDERS) == {"openai_compat", "fake"}


def test_default_is_openai_compat(monkeypatch):
    monkeypatch.delenv("EXPEDISTILL_LLM_PROVIDER", raising=False)
    seen = []
    monkeypatch.setitem(PROVIDERS, "openai_compat",
                        lambda timeout_sec: seen.append(timeout_sec))
    assert default_client(timeout_sec=12.0) is None
    assert seen == [12.0]


def test_unknown_provider_is_a_config_error(monkeypatch):
    monkeypatch.setenv("EXPEDISTILL_LLM_PROVIDER", "quantum")
    with pytest.raises(InfraError, match="quantum"):
        default_client()


def test_env_names_provider(monkeypatch):
    monkeypatch.setenv("EXPEDISTILL_LLM_PROVIDER", "stub")
    monkeypatch.setitem(providers_mod.PROVIDERS, "stub",
                        lambda timeout_sec: "stub-client")
    assert default_client(timeout_sec=1.0) == "stub-client"
