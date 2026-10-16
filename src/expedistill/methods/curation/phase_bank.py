"""R7's per-phase entry: end a learn phase with its own bank artifact.

Every learn phase ends with a loadable skill bank (plan item R7,
problem G9). This module is the entry the phase driver
(``stages/phase_learn.py``) calls; which treatment runs is the driver's
sequence-position branch (curation runs only at the sequence's last
learn phase, the intermediate phases produce the as-is artifact):

* ``produce`` -- the CONFIGURED curation method (``agentbrew`` merges
  with its merge_check regression exams through K3; ``none`` passes the
  accepted set through unchanged);
* ``produce_as_is`` -- the none treatment whatever the config says,
  with the configured curation family's ``token_budget`` still the
  hard valve.

The artifact carries EVERY member, accepted and pending alike (the
Wave 2 review's 2026-10-16 ruling on the phase-bank currency): it is the
only thing one phase hands the next, and a learn phase's new skills are
pending -- without them in the artifact the exam phase's universe (the
pending members) would always be empty. Curation itself still works on
the accepted set only; the pending members pass through unchanged,
appended after the curated rows, and the token-budget valve stays over
the accepted set (pending members are the material the A and B phases
test, not the deliverable bank the whole-bank and retrieval drivers
inject).

Both take the phase's OWN ``curate`` mapping (the v4 config has no
single curation slot -- every learn phase carries its own), resolve it
through the curation family, write the artifact into
``phases/<phase_label>/`` (composites.phase_bank's layout) and return
the summary dict (``members`` / ``size`` / ``bank_reference``) written
beside it. The summary describes the ARTIFACT: ``members`` lists every
row in file order and the reference digests them all.
"""

from typing import Any, Dict, List, Mapping

from expedistill.composites.phase_bank import (
    BANK_NAME,
    phase_bank_dir,
    phase_bank_summary_path,
)
from expedistill.core.models import Skill
from expedistill.infra.store import read_jsonl, write_json, write_jsonl
from expedistill.inject import bank_reference
from expedistill.methods import resolve, resolve_params
from expedistill.methods.curation.none import write_as_is

__all__ = ["produce", "produce_as_is"]


def produce(ctx: Any, curate: Mapping[str, Any],
            phase_label: str) -> Dict[str, Any]:
    """The phase bank under the phase's configured curation method (R7),
    with the store's pending members passing through unchanged."""
    driver, params = resolve("curation", curate)
    driver.produce_phase_bank(ctx, params, phase_label)
    return _with_pending_members(ctx, phase_label)


def produce_as_is(ctx: Any, curate: Mapping[str, Any],
                  phase_label: str) -> Dict[str, Any]:
    """The as-is artifact: accepted rows unchanged, no model calls, plus
    the pending members passing through.

    The intermediate learn phases' artifact: merging is reserved for
    the sequence's last learn phase, but every learn phase still ends
    with a bank that carries all members. The phase's curation family's
    ``token_budget`` stays the hard valve over the accepted set either
    way."""
    params = resolve_params("curation", curate)
    dest = phase_bank_dir(ctx.run_dir, phase_label)
    dest.mkdir(parents=True, exist_ok=True)
    write_as_is(ctx.store, dest, params.token_budget)
    return _with_pending_members(ctx, phase_label)


def _with_pending_members(ctx: Any, phase_label: str) -> Dict[str, Any]:
    """Append the store's pending members to the phase artifact and
    write the summary over the full member list.

    Deterministic and idempotent: the pending rows (sorted by skill_id)
    follow the curated rows, the whole file is rewritten atomically, and
    members already present (a resumed call) are not duplicated -- so a
    repeated call lands on exactly the same bytes. A pending member can
    never be a curation product (curation works on the accepted set), so
    the pending-pass rows cannot collide with the curated ones.
    """
    path = phase_bank_dir(ctx.run_dir, phase_label) / BANK_NAME
    rows: List[Dict[str, Any]] = list(read_jsonl(path))
    present = {row["skill_id"] for row in rows}
    pending = sorted(
        (skill for skill in _stored_skills(ctx.store)
         if skill.status == "pending" and skill.skill_id not in present),
        key=lambda skill: skill.skill_id)
    if pending:
        rows = rows + [skill.to_dict() for skill in pending]
        write_jsonl(path, rows)
    summary = {
        "members": [row["skill_id"] for row in rows],
        "size": len(rows),
        "bank_reference": bank_reference(rows),
    }
    write_json(phase_bank_summary_path(ctx.run_dir, phase_label), summary,
               indent=2)
    return summary


def _stored_skills(store: Any) -> List[Skill]:
    """Every stored skill, in skill_id order."""
    return [store.load("skills", skill_id, Skill)
            for skill_id in store.ids("skills")]
