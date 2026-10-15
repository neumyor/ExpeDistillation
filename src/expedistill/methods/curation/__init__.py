"""The curation slot's contract (stage 5, K5).

Driver-less for now (the AgentBrew-style curator runs inside
composites.curation and stage 5 consumes these params directly); P4
wraps it as a driver and adds the ``none`` method beside it.
"""

from dataclasses import dataclass

from expedistill.core.config import ConfigError

__all__ = ["CurationParams"]


@dataclass(frozen=True)
class CurationParams:
    """The bank's token budget and the merge-round limit."""

    token_budget: int
    rounds: int = 3

    def __post_init__(self) -> None:
        if self.token_budget < 1:
            raise ConfigError(
                "methods.curation.token_budget must be >= 1, "
                f"got {self.token_budget}")
        if self.rounds < 1:
            raise ConfigError(
                f"methods.curation.rounds must be >= 1, got {self.rounds}")
