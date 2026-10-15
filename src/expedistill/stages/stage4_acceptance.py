"""Stage 4 front (layer 7): resolve the acceptance method by name and call.

The config's ``acceptance`` key names the protocol (decision doc
2026-10-14): ``exam`` -- the key absent, ``{}`` or ``{mode: exam}``, the
frozen v1 default -- and ``live_run`` (two pinned rounds over real
executions with G10/G11 attribution/revision). The drivers live in
``methods/acceptance/``; this module only resolves and calls (R1 of the
multi-method plan): the registry table imports the driver on demand, so
an exam process still never loads the live modules -- the frozen
prompt-name surface stays the import-time snapshot, exactly as before.
"""

from typing import Any, Dict

from expedistill.core.config import acceptance_mode
from expedistill.methods import get_method

__all__ = ["run"]


def run(ctx: Any) -> Dict[str, Any]:
    """Dispatch to the configured acceptance driver and return its summary."""
    return get_method(
        "acceptance", acceptance_mode(ctx.config)).run(ctx)
