"""The agentbrew curation driver: K5 (composites.curation), thinly driven.

Everything method-shaped here already lived in the composite (the merge
loop, its journal, the budget refusal); the driver only threads the run
context and the method params in and writes the stage summary -- the
thin-wrapper shape the plan's P4 row pinned (K5's code stays put).

R7 adds the per-phase entry (``produce_phase_bank``): K5 runs in dest
mode, so the phase ends with its own loadable bank artifact while the
run-level ``skill_bank.jsonl`` and the stage-5 journal stay untouched.
K5 curates the accepted set only; the store's PENDING members are
appended to the artifact by the entry module (methods/curation/
phase_bank.py) after this driver returns.
"""

from typing import Any, Dict, List

from expedistill.composites.curation import curate_bank
from expedistill.composites.phase_bank import (
    phase_bank_dir,
    phase_bank_summary_path,
)
from expedistill.inject import bank_reference
from expedistill.infra.store import write_json
from expedistill.methods.curation import (AgentBrewParams, SUMMARY_DIR,
                                         SUMMARY_NAME)

__all__ = ["produce_phase_bank", "run"]


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
        answerer_handle=ctx.handle("answerer"),
        judge_handle=ctx.handle("judge"),
        curator_handle=ctx.handle("curator"),
        bank_token_budget=params.token_budget,
        curation_rounds=params.rounds,
        merge_check=params.merge_check,
        drop_low_value=params.drop_low_value,
        similarity_input=params.similarity_input,
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


def produce_phase_bank(ctx: Any, params: AgentBrewParams,
                      phase_label: str) -> Dict[str, Any]:
    """R7: curate the phase's bank into its own artifact directory.

    K5 runs in dest mode: the merge journal, the bank file and the
    unit-failure records all land in ``phases/<phase_label>/``
    (composites.phase_bank's layout) and the run-level
    ``skill_bank.jsonl`` is untouched. Every K5 behaviour -- the
    merge_check regression exams (K3) included -- is exactly the stage-5
    one; only the artifact locations differ. Returns the same summary
    shape as ``run``, written beside the artifact as
    ``bank_summary.json``.
    """
    dest = phase_bank_dir(ctx.run_dir, phase_label)
    dest.mkdir(parents=True, exist_ok=True)
    bank = curate_bank(
        store=ctx.store,
        answerer_handle=ctx.handle("answerer"),
        judge_handle=ctx.handle("judge"),
        curator_handle=ctx.handle("curator"),
        bank_token_budget=params.token_budget,
        curation_rounds=params.rounds,
        merge_check=params.merge_check,
        drop_low_value=params.drop_low_value,
        similarity_input=params.similarity_input,
        usage=ctx.usage,
        run_dir=ctx.run_dir,
        workers=ctx.run_settings.workers,
        dest=dest,
    )
    members: List[str] = [str(row["skill_id"]) for row in bank]
    summary = {
        "members": members,
        "size": len(bank),
        "bank_reference": bank_reference(bank),
    }
    write_json(phase_bank_summary_path(ctx.run_dir, phase_label), summary,
               indent=2)
    return summary
