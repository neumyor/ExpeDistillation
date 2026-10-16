"""The no-curation ablation driver: the accepted skills ARE the bank.

No merges, no grouping, no model calls -- and the budget stays a hard
safety valve: an accepted set that cannot fit the injection budget
stops the run loudly (the valve exists so a bank can always be injected
whole; without curation there is no way to fix an over-budget bank, so
the refusal names the situation instead of silently overflowing).

R7 adds the per-phase entry (``produce_phase_bank``): the same as-is
treatment with the artifact written into the phase directory
(composites.phase_bank's layout) instead of the run root, so a learn
phase ends with a loadable bank without any curation. The phase entry
writes the accepted set only; the store's PENDING members are appended
to the artifact by the entry module (methods/curation/phase_bank.py)
after this driver returns -- the artifact carries all members, the
curation treatment never sees a pending skill.
"""

from pathlib import Path
from typing import Any, Dict, List

from expedistill.composites.phase_bank import (
    BANK_NAME,
    phase_bank_dir,
    phase_bank_summary_path,
)
from expedistill.core.config import ConfigError
from expedistill.core.models import Skill
from expedistill.inject import bank_reference, count_bank_tokens
from expedistill.infra.store import write_json, write_jsonl
from expedistill.methods.curation import NoneParams, SUMMARY_DIR, SUMMARY_NAME

__all__ = ["produce_phase_bank", "run", "write_as_is"]


def write_as_is(store: Any, dest_dir: Path,
                token_budget: int) -> Dict[str, Any]:
    """The as-is treatment shared by the run-level and the phase entries.

    The accepted rows go in verbatim and the budget stays a hard valve.
    The bank is written WHOLE and atomically (store.write_jsonl), not
    appended: the content is a pure function of the accepted set, so an
    overwrite is idempotent by construction -- a crashed or repeated
    call lands on exactly the same bytes instead of doubling rows (K5
    appends because its merge loop is resumable mid-flight; none has no
    loop to resume). Difference from K5, disclosed: K5 refuses a STALE
    bank (accepted set changed after curation wrote it); none has no
    such refusal -- a changed accepted set simply produces the new bank
    on the next call. Unreachable in the pipeline (stage 4 precedes
    stage 5 and the manifest pins the protocol), but real for direct
    driver calls.
    """
    skills = [store.load("skills", skill_id, Skill)
              for skill_id in store.ids("skills")]
    accepted: List[Skill] = sorted(
        (skill for skill in skills if skill.status == "accepted"),
        key=lambda skill: skill.skill_id)
    bank_tokens = count_bank_tokens(accepted)
    if bank_tokens > token_budget:
        raise ConfigError(
            f"skill bank exceeds token_budget without curation "
            f"(methods.curation.none): {bank_tokens} > {token_budget}; "
            "with curation disabled there is no merge loop to shrink it "
            "-- raise the budget or curate")
    write_jsonl(dest_dir / BANK_NAME,
                [skill.to_dict() for skill in accepted])
    return {
        "members": [skill.skill_id for skill in accepted],
        "size": len(accepted),
        "bank_reference": bank_reference(accepted),
    }


def run(ctx: Any, params: NoneParams) -> Dict[str, Any]:
    """Write the bank straight from the accepted skills."""
    summary = write_as_is(ctx.store, Path(ctx.store.run_dir),
                          params.token_budget)
    write_json(ctx.run_dir / SUMMARY_DIR / SUMMARY_NAME, summary, indent=2)
    return summary


def produce_phase_bank(ctx: Any, params: NoneParams,
                       phase_label: str) -> Dict[str, Any]:
    """R7: the same as-is bank, written as the phase's own artifact.

    The artifact lands in ``phases/<phase_label>/`` (composites.phase_bank)
    and the run-level ``skill_bank.jsonl`` is never touched, so a phase
    bank coexists with the frozen stage-5 output. Every learn phase can
    end with a loadable bank this way, with or without curation.
    """
    dest = phase_bank_dir(ctx.run_dir, phase_label)
    dest.mkdir(parents=True, exist_ok=True)
    summary = write_as_is(ctx.store, dest, params.token_budget)
    write_json(phase_bank_summary_path(ctx.run_dir, phase_label), summary,
               indent=2)
    return summary
