"""SWE-bench Verified plugin package (layer 3).

Import side effects are registration and nothing else: the plugin instance
goes into the benchmark registry, and its prompts (``protocol`` and
``diagnose``) into the B2 registry. No environment is read and no
subprocess started -- E1 imports this package to make ``swebench``
resolvable by name.
"""

from expedistill.benchmarks.registry import register_plugin
from expedistill.benchmarks.swebench.plugin import SWEBenchPlugin

__all__ = ["SWEBenchPlugin"]

register_plugin(SWEBenchPlugin())
