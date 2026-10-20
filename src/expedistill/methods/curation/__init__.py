"""The curation slot's contracts (stage 5): both methods and the layout
constants they share.

The agentbrew curator (K5, composites.curation) merges and groups the
accepted skills down to the bank budget; the ``none`` method is the
ablation -- the accepted skills ARE the bank, and the budget stays a
hard safety valve (an over-budget bank still stops the run loudly, it
just cannot be fixed by merging).

``token_budget: null`` is the NO-FENDER spelling (user ruling
2026-10-16: the token ceilings left the pre-registered parameter set;
``None`` = the valve never closes). The pre-v4 world ran whole
campaigns under it, so the reuse identity walk needs the value to load
verbatim; a positive int still gates exactly as before.
"""

from dataclasses import dataclass
from typing import Optional

from expedistill.core.config import ConfigError

__all__ = ["AgentBrewParams", "NoneParams", "SUMMARY_DIR",
           "SUMMARY_NAME"]

#: The stage's directory and summary file (the curation audit log
#: ``curation_log.json`` sits beside them; README section 2 layout).
SUMMARY_DIR = "stage5_curation"
SUMMARY_NAME = "stage5_summary.json"


@dataclass(frozen=True)
class AgentBrewParams:
    """K5's bank budget, merge-round limit, regression-exam switch and
    the two 2026-10-19 switches (docs/decisions/
    curation_generalize_20261019.md).

    ``merge_check`` (plan section 3's curate family, M3): ``exam`` --
    every kept merge re-answers each source's exam; ``none`` skips the
    regression exams (the merge product goes pending -> accepted with
    zero answerer/judge calls; the marker gates and the budget valve are
    unchanged). The default is ``none`` since 2026-10-19 (it was
    ``exam``): a config that omits the key no longer runs the regression
    exams. The v3 -> v4 upgrade always writes the key explicitly, so only
    a hand-written v4 config without it changes behaviour.

    ``drop_low_value``: G7 lists low-value skills; ``True`` discards them
    before the round's merges, ``False`` (default) only journals them.

    ``similarity_input``: what G7 sees of each skill -- ``full``
    (default, the content) or ``summary`` (the one-line summary; a skill
    without one stops the curation loudly).

    ``grouping_input``: what G9 sees of each skill -- ``full`` (default,
    the content) or ``summary`` (the one-line summary; a skill without
    one stops the curation loudly). Independent of ``similarity_input``:
    G7 and G9 have different input sizes and failure modes.
    """

    token_budget: Optional[int]
    rounds: int = 3
    merge_check: str = "none"
    drop_low_value: bool = False
    similarity_input: str = "full"
    grouping_input: str = "full"

    def __post_init__(self) -> None:
        if self.token_budget is not None and self.token_budget < 1:
            raise ConfigError(
                "methods.curation.agentbrew token_budget must be >= 1 or "
                f"null (the no-fender ruling), got {self.token_budget}")
        if self.rounds < 1:
            raise ConfigError(
                f"methods.curation.agentbrew rounds must be >= 1, "
                f"got {self.rounds}")
        if self.merge_check not in ("exam", "none"):
            raise ConfigError(
                "methods.curation.agentbrew merge_check must be 'exam' or "
                f"'none', got {self.merge_check!r}")
        if self.similarity_input not in ("full", "summary"):
            raise ConfigError(
                "methods.curation.agentbrew similarity_input must be 'full' "
                f"or 'summary', got {self.similarity_input!r}")
        if self.grouping_input not in ("full", "summary"):
            raise ConfigError(
                "methods.curation.agentbrew grouping_input must be 'full' "
                f"or 'summary', got {self.grouping_input!r}")


@dataclass(frozen=True)
class NoneParams:
    """The ablation has no rounds -- but keeps the budget valve (a null
    budget is the no-fender ruling, so the valve never closes)."""

    token_budget: Optional[int]

    def __post_init__(self) -> None:
        if self.token_budget is not None and self.token_budget < 1:
            raise ConfigError(
                "methods.curation.none token_budget must be >= 1 or null "
                f"(the no-fender ruling), got {self.token_budget}")
