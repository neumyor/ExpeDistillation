"""The agentbrew curation driver: K5 (composites.curation), thinly driven.

Everything method-shaped here already lived in the composite (the merge
loop, its journal, the budget refusal); the driver only threads the run
context and the method params in and writes the stage summary -- the
thin-wrapper shape the plan's P4 row pinned (K5's code stays put).
"""

from typing import Any, Dict, List

from expedistill.composites.curation import curate_bank
from expedistill.inject import bank_reference
from expedistill.infra.store import write_json
from expedistill.methods.curation import (AgentBrewParams, SUMMARY_DIR,
                                         SUMMARY_NAME)

__all__ = ["run"]


def run(ctx: Any, params: AgentBrewParams) -> Dict[str, Any]:
    """Curate the bank and write ``stage5_curation/stage5_summary.json``.

    Returns ``{"members": [skill_id, ...], "size": int, "bank_reference":
    "bank:<12hex>"}`` -- the member ids in bank order, the bank size, and
    the canonical digest stage 6 records as ``skills_injected``. Raises
    ConfigError (from K5) when the bank is still over budget after
    ``params.rounds`` rounds; nothing is written in that case.
    """
    bank = curate_bank(
        store=ctx.store,
        weak_profile=ctx.config.weak_profile,
        answerer_handle=ctx.handle("answerer"),
        judge_handle=ctx.handle("judge"),
        curator_handle=ctx.handle("curator"),
        bank_token_budget=params.token_budget,
        curation_rounds=params.rounds,
        merge_check=params.merge_check,
        usage=ctx.usage,
        run_dir=ctx.run_dir,
        workers=ctx.run_settings.workers,
    )
    members: List[str] = [str(row["skill_id"]) for row in bank]
    summary = {
        "members": members,
        "size": len(bank),
        "bank_reference": bank_reference(bank),
    }
    write_json(ctx.run_dir / SUMMARY_DIR / SUMMARY_NAME, summary, indent=2)
    return summary
