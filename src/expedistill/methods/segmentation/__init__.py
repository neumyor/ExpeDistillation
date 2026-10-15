"""The segmentation slot's contract (stage 2, K2 + G1 + D7).

Driver-less for now: the only segmentation method is the LLM splitter,
and stage 2 consumes these params directly. The slot exists so the
second segmentation method (fixed windows, none-for-ablation) registers
beside it without touching the stage.
"""

from dataclasses import dataclass

from expedistill.core.config import ConfigError

__all__ = ["SegmentationParams"]


@dataclass(frozen=True)
class SegmentationParams:
    """Max segments per trajectory, and K2's re-segmentation budget."""

    K: int
    retry_limit: int = 2

    def __post_init__(self) -> None:
        if self.K < 1:
            raise ConfigError(f"methods.segmentation.K must be >= 1, got {self.K}")
        if self.retry_limit < 0:
            raise ConfigError(
                "methods.segmentation.retry_limit must be >= 0, "
                f"got {self.retry_limit}")
