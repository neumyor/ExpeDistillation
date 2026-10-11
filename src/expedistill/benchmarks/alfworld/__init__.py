"""ALFWorld benchmark plugin (layer 3); registers itself at import.

Importing this package must succeed with no ``ALFWORLD_*`` and no
``EXPEDISTILL_ALFWORLD_*`` environment variables set: the plugin never
imports alfworld/textworld (the env bridge runs them in the eval repo's own
venv via a subprocess), and every variable is resolved only when a task is
loaded or a run starts. A missing variable is then a ``ConfigError``, never
an ``ImportError`` -- so consumers without the ALFWorld environment can
still import, register and inspect the plugin.

The plugin instance is exposed as ``PLUGIN`` so tests (and only tests) can
re-register it after ``registry._reset_registry`` wipes the registry.
"""

from expedistill.benchmarks.alfworld.plugin import ALFWorldPlugin
from expedistill.benchmarks.registry import register_plugin

PLUGIN = ALFWorldPlugin()
register_plugin(PLUGIN)

__all__ = ["ALFWorldPlugin", "PLUGIN"]
