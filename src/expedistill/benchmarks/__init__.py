"""Benchmark plugin foundation (layer 3).

The plugin interface (base), the name registry (registry) and the shared
stepwise ReAct loop for local benchmarks (react_loop). Concrete plugins live
in ``benchmarks/<name>/`` and register themselves at import; this package does
NOT import them, so consumers without a benchmark's dependencies (the
ALFWorld venv) can still use the foundation.
"""
