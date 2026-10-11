"""Benchmark plugin foundation (layer 3).

The plugin interface (base), the name registry (registry) and the shared
stepwise ReAct loop for local benchmarks (react_loop). Concrete plugins live
in ``benchmarks/<name>/`` and register themselves at import; this package does
NOT import them, so consumers without a benchmark's dependencies (the
ALFWorld venv) can still use the foundation.
"""

from expedistill.benchmarks.base import (
    BaseEnv,
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
    environment_call,
)
from expedistill.benchmarks.react_loop import render_history, run_react
from expedistill.benchmarks.registry import (
    get_plugin,
    plugin_names,
    register_plugin,
)

__all__ = [
    "BaseEnv",
    "BenchmarkPlugin",
    "RunOutcome",
    "Task",
    "ViewRules",
    "environment_call",
    "get_plugin",
    "plugin_names",
    "register_plugin",
    "render_history",
    "run_react",
]
