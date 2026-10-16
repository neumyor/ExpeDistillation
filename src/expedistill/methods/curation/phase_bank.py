"""R7's per-phase entry: end a learn phase with its own bank artifact.

Every learn phase ends with a loadable skill bank (plan item R7,
problem G9). This module is the entry the phase driver (Wave 2's
``phase_learn``) calls; which treatment runs is the driver's
sequence-position branch (plan R9: curation runs only at the sequence's
last learn phase, the intermediate phases produce the as-is artifact):

* ``produce`` -- the CONFIGURED curation method (``agentbrew`` merges
  with its merge_check regression exams through K3; ``none`` passes the
  accepted set through unchanged);
* ``produce_as_is`` -- the none treatment whatever the config says,
  with the configured curation family's ``token_budget`` still the
  hard valve.

Both take the phase's OWN ``curate`` mapping (the v4 config has no
single curation slot -- every learn phase carries its own), resolve it
through the curation family, write the artifact into
``phases/<phase_label>/`` (composites.phase_bank's layout) and return
the summary dict (``members`` / ``size`` / ``bank_reference``) written
beside it.
"""

from typing import Any, Dict, Mapping

from expedistill.composites.phase_bank import (
    phase_bank_dir,
    phase_bank_summary_path,
)
from expedistill.infra.store import write_json
from expedistill.methods import resolve, resolve_params
from expedistill.methods.curation.none import write_as_is

__all__ = ["produce", "produce_as_is"]


def produce(ctx: Any, curate: Mapping[str, Any],
            phase_label: str) -> Dict[str, Any]:
    """The phase bank under the phase's configured curation method (R7)."""
    driver, params = resolve("curation", curate)
    return driver.produce_phase_bank(ctx, params, phase_label)


def produce_as_is(ctx: Any, curate: Mapping[str, Any],
                  phase_label: str) -> Dict[str, Any]:
    """The as-is artifact: accepted rows unchanged, no model calls.

    The intermediate learn phases' artifact (plan R9's position branch):
    merging is reserved for the sequence's last learn phase, but every
    learn phase still ends with a bank. The phase's curation family's
    ``token_budget`` stays the hard valve either way.
    """
    params = resolve_params("curation", curate)
    dest = phase_bank_dir(ctx.run_dir, phase_label)
    dest.mkdir(parents=True, exist_ok=True)
    summary = write_as_is(ctx.store, dest, params.token_budget)
    write_json(phase_bank_summary_path(ctx.run_dir, phase_label), summary,
               indent=2)
    return summary
