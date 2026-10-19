"""Re-registering a real benchmark plugin after another suite reset the
global plugin registry.

The registry is global and several suites reset it; a reset strands the
already-imported plugin packages, so re-registering needs a reload of
the plugin module (its prompt registrations) and the package (its plugin
registration). The reload re-registers the prompts, so every prompt the
benchmark owns must be popped first or the duplicate raises. The prompts
are found by the naming convention every plugin follows
(``<NAME>_TASK_PROMPT`` and its variants, e.g. ``TB_TASK_PROMPT_FOLDER``
and ``TB_TASK_PROMPT_FOLDER_INLINE``), so a new variant needs no edit
here.
"""

import importlib

from expedistill.benchmarks.base import BenchmarkPlugin
from expedistill.benchmarks.registry import get_plugin
from expedistill.infra.reliability import ConfigError
from expedistill.prompts.registry import PROMPTS

__all__ = ["benchmark_prompt_names", "ensure_plugin", "reload_plugin"]


def benchmark_prompt_names(name: str):
    """Every registered prompt the benchmark ``name`` owns."""
    prefix = f"{name.upper()}_TASK_PROMPT"
    return sorted(prompt for prompt in PROMPTS if prompt.startswith(prefix))


def reload_plugin(name: str) -> None:
    """Re-execute the benchmark's plugin module and package against the
    current registries (their import side effects run again)."""
    for prompt in benchmark_prompt_names(name):
        PROMPTS.pop(prompt, None)
    importlib.reload(importlib.import_module(
        f"expedistill.benchmarks.{name}.plugin"))
    importlib.reload(importlib.import_module(f"expedistill.benchmarks.{name}"))


def ensure_plugin(name: str) -> BenchmarkPlugin:
    """The registered plugin ``name``, re-registered if a reset dropped
    it.

    A plugin-registry reset leaves the prompts registered, so only the
    package is reloaded: the plugin module (and so its plugin class) stays
    the same object, and a suite holding that class keeps passing its
    isinstance checks. Only when the benchmark's task prompt is gone too
    does the whole plugin reload."""
    try:
        return get_plugin(name)
    except ConfigError:
        pass
    if f"{name.upper()}_TASK_PROMPT" in PROMPTS:
        importlib.reload(importlib.import_module(
            f"expedistill.benchmarks.{name}"))
    else:
        reload_plugin(name)
    return get_plugin(name)
