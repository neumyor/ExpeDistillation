"""The no-curation ablation driver: the accepted skills ARE the bank.

No merges, no grouping, no model calls -- and the budget stays a hard
safety valve: an accepted set that cannot fit the injection budget
stops the run loudly (the valve exists so a bank can always be injected
whole; without curation there is no way to fix an over-budget bank, so
the refusal names the situation instead of silently overflowing).
"""

from pathlib import Path
from typing import Any, Dict, List

from expedistill.core.config import ConfigError
from expedistill.core.models import Skill
from expedistill.inject import bank_reference, count_bank_tokens
from expedistill.infra.store import write_json, write_jsonl
from expedistill.methods.curation import NoneParams, SUMMARY_DIR, SUMMARY_NAME

__all__ = ["run"]


def run(ctx: Any, params: NoneParams) -> Dict[str, Any]:
    """Write the bank straight from the accepted skills."""
    store = ctx.store
    skills = [store.load("skills", skill_id, Skill)
              for skill_id in store.ids("skills")]
    accepted = sorted((skill for skill in skills
                       if skill.status == "accepted"),
                      key=lambda skill: skill.skill_id)
    bank_tokens = count_bank_tokens(accepted)
    if bank_tokens > params.token_budget:
        raise ConfigError(
            f"skill bank exceeds token_budget without curation "
            f"(methods.curation.none): {bank_tokens} > "
            f"{params.token_budget}; with curation disabled there is no "
            "merge loop to shrink it -- raise the budget or curate")
    # The bank is written WHOLE and atomically (store.write_jsonl), not
    # appended: the content is a pure function of the accepted set, so an
    # overwrite is idempotent by construction -- a crashed or repeated
    # call lands on exactly the same bytes instead of doubling rows
    # (K5 appends because its merge loop is resumable mid-flight; none
    # has no loop to resume). Difference from K5, disclosed: K5 refuses a
    # STALE bank (accepted set changed after curation wrote it); none
    # has no such refusal -- a changed accepted set simply produces the
    # new bank on the next call. Unreachable in the pipeline (stage 4
    # precedes stage 5 and the manifest pins the protocol), but real for
    # direct driver calls.
    write_jsonl(Path(store.run_dir) / "skill_bank.jsonl",
                [skill.to_dict() for skill in accepted])
    members: List[str] = [skill.skill_id for skill in accepted]
    summary = {
        "members": members,
        "size": len(accepted),
        "bank_reference": bank_reference(accepted),
    }
    write_json(ctx.run_dir / SUMMARY_DIR / SUMMARY_NAME, summary, indent=2)
    return summary
