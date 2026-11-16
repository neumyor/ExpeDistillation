"""Layer 0: data model, deterministic IDs, experiment config, registries.

Only the standard library is imported at module level here (the YAML
dependency of ``config.load_config`` is function-local; see that module's
docstring). Everything above this layer may import from ``expedistill.core``.
"""
