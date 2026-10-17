"""Provider registry (layer 2): name -> client builder.

The pipeline's default client builder resolves the provider NAME from
``EXPEDISTILL_LLM_PROVIDER`` (default ``openai_compat``) and hands the
run's per-request timeout to that provider's builder. Endpoint selection
stays operational (environment), never identity: the frozen manifest
records model NAMES, not transports. A new provider registers one entry
here plus its client module -- the same plain-table shape as the method
registry, deliberately no plugin auto-discovery.
"""

import os
from typing import Any, Callable, Dict

from expedistill.infra.reliability import ConfigError
from expedistill.llm import fake_provider
from expedistill.llm.client import OpenAICompatClient

__all__ = ["PROVIDERS", "default_client"]

#: Provider name -> ``builder(timeout_sec) -> client`` (one client per
#: unique model is the pipeline's layout; the builder is called with the
#: run's per-request timeout). The ``fake`` entry is the plan-scripted
#: smoke client (llm/fake_provider.py): same builder shape, no network.
PROVIDERS: Dict[str, Callable[..., Any]] = {
    "openai_compat": OpenAICompatClient.from_env,
    "fake": fake_provider.from_env,
}


def default_client(timeout_sec: float = 300.0) -> Any:
    """The provider the environment names (default openai_compat)."""
    name = os.environ.get("EXPEDISTILL_LLM_PROVIDER", "openai_compat")
    try:
        builder = PROVIDERS[name]
    except KeyError:
        known = ", ".join(sorted(PROVIDERS)) or "(nothing registered)"
        raise ConfigError(
            f"EXPEDISTILL_LLM_PROVIDER names unknown provider {name!r}; "
            f"known: {known}") from None
    return builder(timeout_sec=timeout_sec)
