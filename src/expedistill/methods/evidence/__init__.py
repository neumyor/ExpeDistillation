"""The evidence family: segmenting experience into fact-only packages.

R6 (docs/decisions/execute_learn_repair_plan_20261016.md) turned this
family from a params-only package into a real driver family. Two
collectors stand next to the params class:

* ``methods.evidence.add_path.collect_add_evidence`` -- the add path's
  evidence (a task's strong segments, plus its weak segments when
  ``write_lessons`` is on, as source experiences);
* ``methods.evidence.segmentation.segment_add_material`` -- the add
  path's segmentation wiring (Wave 3-S): K2 over the task's
  trajectory_select-selected baseline-phase trajectories (strong only, or
  both roles when ``write_lessons`` is on), called by
  the landing right before ``collect_add_evidence`` reads the store --
  the add row's material is produced, not assumed;
* ``methods.evidence.exam_sheet.collect_exam_evidence`` -- the exam
  path's answer-sheet package (the exam, the judged attempt's weak
  answer and judge reason; moved there from learn_add's inline
  assembly at the Wave 2 integration).

P04/P06's live group attribution lives in ``methods/attribution``, not
in this family.

``methods.evidence.package`` defines the evidence-package shape
(target, facts, segments, old_skill, source_experiences) and the
fact-only guard. The phase driver (stages/phase_learn.py through
learn_add / learn_apply) is the consumer of all three.

``SegmentationParams`` is the family's registered ``segment`` method
(the add path's K, re-segmentation budget and ``write_lessons``); the
registry's ``(evidence, segment)`` entry resolves it here.

``write_lessons`` (the 2026-10-19 ruling, docs/decisions/
unified_skill_structure_20261019.md, default True): the add path also
learns from the weak model's FAILED segments, written in diagnose mode
into the same three-section structure as a strong success. False keeps
the add path to strong segments only (and segments no weak trajectory).
"""

from dataclasses import dataclass

from expedistill.core.config import ConfigError

__all__ = ["SegmentationParams"]


@dataclass(frozen=True)
class SegmentationParams:
    """Max segments per trajectory, the re-segmentation budget, and
    whether the add path also learns from weak failures."""

    K: int
    retry_limit: int = 2
    write_lessons: bool = True

    def __post_init__(self) -> None:
        if self.K < 1:
            raise ConfigError(
                "evidence method 'segment': K must be >= 1, "
                f"got {self.K}")
        if self.retry_limit < 0:
            raise ConfigError(
                "evidence method 'segment': retry_limit must be >= 0, "
                f"got {self.retry_limit}")
