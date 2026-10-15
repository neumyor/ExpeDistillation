"""Benchmark plugin registry (layer 3): plugins by name.

Built on the generic core registry (``core/registry.make_registry``): names
are identity, duplicates are programming errors, and the config picks runners
by name (README 1.1 rule 3). A plugin registers itself at import of its
package -- the plugin's ``__init__`` constructs the plugin and calls
``register_plugin``; whoever resolves plugins (E1) imports the plugin packages
first so registration runs. The tb/searchqa/alfworld packages arrive in a
later wave and are deliberately not imported from here: importing them from
the foundation would make every consumer pay for benchmark-specific
dependencies (the ALFWorld plugin needs its own venv).

Failure taxonomy: a duplicate name is a code bug (two plugins claiming one
identity -> CodeBugError); an unknown name is a configuration problem (the
experiment config named a benchmark that was never registered ->
ConfigError), and the message always lists what is registered.
"""

import importlib
from typing import List

from expedistill.core.registry import make_registry
from expedistill.infra.reliability import CodeBugError, ConfigError
from expedistill.benchmarks.base import BenchmarkPlugin

__all__ = ["register_plugin", "get_plugin", "plugin_names",
           "resolve_plugin", "_reset_registry"]

_KIND = "benchmark plugin"

#: Package whose ``__init__`` registers the plugin.
_PLUGIN_PACKAGE_PREFIX = "expedistill.benchmarks."


def resolve_plugin(name: str) -> BenchmarkPlugin:
    """Import ``expedistill.benchmarks.<name>`` and return the plugin.

    THE one resolution step (five call sites used to paste their own
    import-then-get): a plugin registers itself at import of its package,
    so the import IS the resolution. An unimportable package is a
    configuration problem -- the config named a benchmark that is not
    installed or whose dependencies are missing -- and an imported but
    unregistered one is the same class of problem via get_plugin.
    """
    module_path = _PLUGIN_PACKAGE_PREFIX + name
    try:
        importlib.import_module(module_path)
    except ImportError as exc:
        raise ConfigError(
            f"benchmark plugin {name!r} is not available: importing "
            f"{module_path!r} failed ({type(exc).__name__}: {exc}). A "
            f"plugin must be a package at {module_path!r} whose import "
            f"registers it in expedistill.benchmarks.registry."
        ) from exc
    return get_plugin(name)

_REGISTRY = make_registry(_KIND)


def register_plugin(plugin: BenchmarkPlugin) -> BenchmarkPlugin:
    """File ``plugin`` under ``plugin.name``; duplicates raise CodeBugError."""
    if not isinstance(plugin, BenchmarkPlugin):
        raise CodeBugError(
            f"register_plugin expects a BenchmarkPlugin, got {type(plugin).__name__}")
    name = plugin.name
    if not isinstance(name, str) or not name.strip():
        raise CodeBugError(
            f"benchmark plugin of type {type(plugin).__name__} has an invalid "
            f"name: {name!r}")
    try:
        _REGISTRY.register(name)(plugin)
    except ValueError as exc:
        known = ", ".join(plugin_names()) or "(nothing registered)"
        raise CodeBugError(f"{exc}; known: {known}") from None
    return plugin


def get_plugin(name: str) -> BenchmarkPlugin:
    """Return the plugin registered under ``name``; unknown raises ConfigError."""
    if not isinstance(name, str):
        raise CodeBugError(f"benchmark name must be a str, got {type(name).__name__}")
    try:
        return _REGISTRY.get(name)
    except KeyError as exc:
        # The listing is already in the registry's KeyError message.
        raise ConfigError(str(exc)) from None


def plugin_names() -> List[str]:
    """Sorted names of every registered plugin."""
    return _REGISTRY.names()


def _reset_registry() -> None:
    """Test hook: drop every registered plugin."""
    global _REGISTRY
    _REGISTRY = make_registry(_KIND)
