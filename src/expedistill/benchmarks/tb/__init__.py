"""TerminalBench plugin package (layer 3).

Import side effects are registration and nothing else: the plugin instance
goes into the benchmark registry, and its task prompt (registered at import
of ``plugin``) into the B2 registry. No environment is read, no network
touched -- E1 imports this package to make ``tb`` resolvable by name.
"""

from expedistill.benchmarks.registry import register_plugin
from expedistill.benchmarks.tb.plugin import TB_TASK_PROMPT, TBPlugin

__all__ = ["TB_TASK_PROMPT", "TBPlugin"]

register_plugin(TBPlugin())
