"""SearchQA benchmark plugin package (layer 3).

Importing this package registers the plugin in the benchmark registry and
its task prompt in the B2 registry (the plugin contract,
benchmarks/base.py). Both side effects are safe without the outer eval
repo: every eval-repo import is lazy inside methods, so this import
succeeds even when benchmark/searchqa-eval or its dependencies are absent
(AGENTS.md section 9).
"""

from expedistill.benchmarks.registry import register_plugin
from expedistill.benchmarks.searchqa.plugin import SearchQAPlugin

__all__ = ["SearchQAPlugin"]

register_plugin(SearchQAPlugin())
