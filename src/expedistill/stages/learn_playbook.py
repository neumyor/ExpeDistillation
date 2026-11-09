"""The learned-playbook step of the learn phase that follows the baseline.

Switch: a delivery asks for it with ``playbook_source: learned``. Nothing
else in the config changes, and without that key this module does nothing.
The step reads the baseline report the phase is already reviewing, asks the
benchmark plugin for its facts text, has the author model write the
playbook with the plugin's prompt, and leaves ``playbook.md`` in the learn
phase's own directory. It is idempotent: an existing file is the commit
point, so a resumed phase writes nothing twice.
"""

from typing import Any

from expedistill.benchmarks.registry import get_plugin
from expedistill.blocks import playbook as playbook_block
from expedistill.composites import playbook_store
from expedistill.core.answer_sheet import PhaseReport
from expedistill.core.config import ExecutePhaseSpec

__all__ = ["LEARNED", "requested", "ensure_playbook"]

#: The ``playbook_source`` value that asks for a learned playbook.
LEARNED = "learned"


def requested(config: Any) -> bool:
    """True when any execute phase's delivery asks for a learned playbook."""
    return any(
        isinstance(spec, ExecutePhaseSpec)
        and spec.delivery.get("playbook_source") == LEARNED
        for spec in config.phases.values())


def ensure_playbook(ctx: Any, phase: Any, baseline: PhaseReport,
                    weak_profile: str) -> bool:
    """Write this phase's playbook from the baseline sheets unless it exists.

    Returns True when a playbook was written now.
    """
    path = playbook_store.playbook_path(ctx.run_dir, phase.label)
    if path.exists():
        return False
    plugin = get_plugin(ctx.config.benchmark)
    facts = plugin.playbook_facts(baseline.sheets)
    attempts: list = []
    text = playbook_block.write_playbook(
        ctx.handles["author"], facts,
        prompt_name=plugin.playbook_prompt_name,
        weak_profile=weak_profile, usage=ctx.usage, run_dir=ctx.run_dir,
        requirements=plugin.playbook_requirements(), record=attempts)
    playbook_store.write_learned_playbook(
        ctx.run_dir, phase.label, text, facts_chars=len(facts),
        attempts=attempts)
    return True
