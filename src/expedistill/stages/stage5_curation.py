"""Stage 5: curate the accepted skills into the final bank (README stage 5).

The stage is a thin wire: K5 (``composites.curation.curate_bank``) owns every
method decision and all disk IO for the curation itself; the stage only passes
the frozen config fields and the role-table handles, then records what came
out. Two pinned behaviours live here:

* over-budget stops for a human: ``curate_bank`` raises ConfigError when the
  bank still exceeds ``bank_token_budget`` after ``curation_rounds`` rounds,
  and the stage deliberately lets it propagate (README stage 5: "阶段 5 停下
  报告，由人决定" -- raising the budget or changing the merge strategy is a
  new experiment, and auto-deleting accepted skills is never an option);
* an empty bank is a valid bank: it is written down like any other outcome so
  the run record never silently lacks a stage-5 result.

Resume is K5's: a non-empty ``skill_bank.jsonl`` short-circuits every model
call, so re-running the stage is idempotent and merely rewrites the summary;
an interrupted curation is continued unit by unit from K5's journal
(``curation_log.json``). Failure disposition is K5's too and the stage adds
no handler on purpose: an InfrastructureError (or a G7/G9 ModelOutputError)
propagates and stops the stage without a summary -- re-running it resumes --
while a ModelOutputError confined to one merge is logged by K5 as that merge
skipped / reverted, and curation carries on.

Parallelism is K5's wave pool, fed by the same run setting as stages 1-4
and 6: ``ctx.run_settings.workers`` threads per wave, serial by default, output
bit-identical to the serial run (K5's module docstring has the design and
the equivalence argument).
"""

from typing import Any, Dict, List

from expedistill.composites.curation import curate_bank
from expedistill.methods import resolve_params
from expedistill.infra.store import write_json
from expedistill.inject import bank_reference

__all__ = ["run"]

#: Directory inside the run where the stage summary lives (K5's audit log
#: ``curation_log.json`` sits next to it; README section 2 layout).
SUMMARY_DIR = "stage5_curation"
SUMMARY_NAME = "stage5_summary.json"


def run(ctx: Any) -> Dict[str, Any]:
    """Curate the bank and write ``stage5_curation/stage5_summary.json``.

    Returns ``{"members": [skill_id, ...], "size": int, "bank_reference":
    "bank:<12hex>"}`` -- the member ids in bank order, the bank size, and the
    canonical digest stage 6 will record as ``skills_injected``. Raises
    ConfigError (from K5) when the bank is still over budget after
    ``curation_rounds`` rounds; nothing is written in that case.
    """
    curation = resolve_params("curation", ctx.config)
    bank = curate_bank(
        store=ctx.store,
        weak_profile=ctx.config.weak_profile,
        answerer_handle=ctx.handle("answerer"),
        judge_handle=ctx.handle("judge"),
        curator_handle=ctx.handle("curator"),
        bank_token_budget=curation.token_budget,
        curation_rounds=curation.rounds,
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
