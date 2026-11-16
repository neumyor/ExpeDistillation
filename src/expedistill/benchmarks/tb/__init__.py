"""TerminalBench plugin package (layer 3).

Import side effects are registration and nothing else: the plugin instance
goes into the benchmark registry, and its executor texts (registered at
import of ``protocol``) into the B2 registry. No environment is read, no
network touched -- E1 imports this package to make ``tb`` resolvable by
name.
"""

from expedistill.benchmarks.registry import register_plugin
from expedistill.benchmarks.tb.plugin import TBPlugin

__all__ = ["TBPlugin"]

register_plugin(TBPlugin())
