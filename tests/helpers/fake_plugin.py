"""Serving a stub benchmark plugin through the real import machinery.

``resolve_plugin`` imports ``expedistill.benchmarks.<name>`` and the
package's import registers the plugin. A stub plugin has no package on
disk, so a meta-path finder serves a synthetic one whose ``exec_module``
registers it: the code under test exercises the same import-then-register
path as a checked-in plugin, with no shortcut around importlib.
"""

import importlib.abc
import importlib.machinery
import sys
import types
from typing import Optional

from expedistill.benchmarks.base import BenchmarkPlugin
from expedistill.benchmarks.registry import get_plugin, register_plugin
from expedistill.infra.reliability import ConfigError

__all__ = ["plugin_finder", "serve_plugin"]

_PACKAGE = "expedistill.benchmarks."


class _Loader(importlib.abc.Loader):
    def __init__(self, plugin: Optional[BenchmarkPlugin]):
        self._plugin = plugin

    def create_module(self, spec):
        return types.ModuleType(spec.name)

    def exec_module(self, module):
        if self._plugin is not None:
            register_plugin(self._plugin)


class _Finder(importlib.abc.MetaPathFinder):
    def __init__(self, fullname: str, plugin: Optional[BenchmarkPlugin]):
        self._fullname = fullname
        self._plugin = plugin

    def find_spec(self, name, path=None, target=None):
        if name == self._fullname:
            return importlib.machinery.ModuleSpec(name, _Loader(self._plugin))
        return None


def plugin_finder(plugin: Optional[BenchmarkPlugin], name: Optional[str] = None):
    """A meta-path finder serving ``expedistill.benchmarks.<name>`` (default:
    the plugin's own name); importing it registers ``plugin``. With
    ``plugin=None`` the package imports cleanly but registers nothing (a
    broken plugin), and ``name`` is required."""
    return _Finder(_PACKAGE + (name or plugin.name), plugin)


def serve_plugin(monkeypatch, plugin: Optional[BenchmarkPlugin],
                 name: Optional[str] = None):
    """Install a finder for ``plugin`` for the rest of the test and drop any
    cached stub module so the next import re-executes it. Idempotent within
    a test: a plugin already registered from a cached module is left alone,
    since re-running the module body would register it twice."""
    fullname = _PACKAGE + (name or plugin.name)
    if plugin is not None and fullname in sys.modules:
        try:
            if get_plugin(plugin.name) is plugin:
                return plugin
        except ConfigError:
            pass
    sys.modules.pop(fullname, None)
    # Undo drops whatever module the test's import leaves behind, so the
    # next test's import re-executes (and re-registers) instead of hitting
    # a stale module.
    monkeypatch.setitem(sys.modules, fullname, types.ModuleType(fullname))
    del sys.modules[fullname]
    monkeypatch.setattr(sys, "meta_path",
                        [*sys.meta_path, plugin_finder(plugin, name)])
    return plugin
