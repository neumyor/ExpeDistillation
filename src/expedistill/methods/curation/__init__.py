"""The curation slot's contracts (stage 5): both methods and the layout
constants they share.

The agentbrew curator (K5, composites.curation) merges and groups the
accepted skills down to the bank budget; the ``none`` method is the
ablation -- the accepted skills ARE the bank, and the budget stays a
hard safety valve (an over-budget bank still stops the run loudly, it
just cannot be fixed by merging).
"""

from dataclasses import dataclass

from expedistill.core.config import ConfigError

__all__ = ["AgentBrewParams", "NoneParams", "SUMMARY_DIR",
           "SUMMARY_NAME"]

#: The stage's directory and summary file (the curation audit log
#: ``curation_log.json`` sits beside them; README section 2 layout).
SUMMARY_DIR = "stage5_curation"
SUMMARY_NAME = "stage5_summary.json"


@dataclass(frozen=True)
class AgentBrewParams:
    """K5's bank budget and merge-round limit."""

    token_budget: int
    rounds: int = 3

    def __post_init__(self) -> None:
        if self.token_budget < 1:
            raise ConfigError(
                "methods.curation.agentbrew token_budget must be >= 1, "
                f"got {self.token_budget}")
        if self.rounds < 1:
            raise ConfigError(
                f"methods.curation.agentbrew rounds must be >= 1, "
                f"got {self.rounds}")


@dataclass(frozen=True)
class NoneParams:
    """The ablation has no rounds -- but keeps the budget valve."""

    token_budget: int

    def __post_init__(self) -> None:
        if self.token_budget < 1:
            raise ConfigError(
                f"methods.curation.none token_budget must be >= 1, "
                f"got {self.token_budget}")
