"""The evidence family: segmenting experience into fact-only packages.

R6 (docs/decisions/execute_learn_repair_plan_20261016.md) turned this
family from a params-only package into a real driver family. Two
entries stand next to the params class:

* ``methods.evidence.live_group.collect_group_evidence`` -- the live
  method's G10, reporting FACTS ONLY (measurement numbers, the group
  diagnosis, per-skill problems, failure views; the model prompt's
  keep/revise/delete vocabulary is dropped at this boundary);
* ``methods.evidence.add_path.collect_add_evidence`` -- the add path's
  evidence (a task's strong segments, as source experiences).

``methods.evidence.package`` defines the evidence-package shape
(target, facts, segments, old_skill, source_experiences) and the
fact-only guard. The stage drivers do not call any of these yet: the
legacy live path keeps its action-plan G10 (composites/
live_attribution.py) until Wave 2 rewires phase_learn.

``SegmentationParams`` is the family's registered ``segment`` method
(the add path's K and re-segmentation budget); the registry's
``(evidence, segment)`` entry resolves it here, and the segmentation
slot package re-exports it so the v3 config path stays byte-identical
until the slot dies.
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
