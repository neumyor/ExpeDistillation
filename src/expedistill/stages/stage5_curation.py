"""Stage 5 front (layer 7): resolve the curation method by name and call.

The drivers live in ``methods/curation/``: ``agentbrew`` (K5, the merge
loop) and ``none`` (the ablation -- accepted skills are the bank, budget
stays a hard valve). This module only resolves and calls, exactly like
the stage-4 front.
"""

from typing import Any, Dict

from expedistill.methods import resolve

__all__ = ["run"]


def run(ctx: Any) -> Dict[str, Any]:
    """Dispatch to the configured curation driver and return its summary."""
    driver, params = resolve("curation", ctx.config)
    return driver.run(ctx, params)
