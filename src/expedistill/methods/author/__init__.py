"""The author family: writing and revising skills from evidence.

R6 (docs/decisions/execute_learn_repair_plan_20261016.md) turned this
family from a params-only package into a real driver:
``methods.author.write`` receives the decision module's task list rows
and the evidence module's packages, and produces skill content --
``revise_group`` for an authorized revision (per-skill keep / revise /
delete decided HERE from the facts, the revision-flow delete carrying
``live_revision_deleted``) and ``write_new`` for an authorized add. The
writing dispatches by the EVIDENCE PACKAGE's shape, never by the report
kind (the old exam-uses-G6 / live-uses-G11 split is gone). The learn
phase driver (stages/phase_learn.py through learn_tasks) is the
consumer.

``AuthoringParams`` is the family's registered ``distill_diagnose``
contract: an empty parameter set (the v3 ``exams`` switch has no v4
image -- exam generation is implied by every sequence's exam phases).
"""

from dataclasses import dataclass

from expedistill.core.config import ConfigError

__all__ = ["AuthoringParams", "UsageReviseParams"]

#: The usage-aware revision policies the author driver understands.
USAGE_REVISE_POLICIES = ("usage_v1",)


@dataclass(frozen=True)
class AuthoringParams:
    """The ``distill_diagnose`` authoring method's (empty) parameters."""


@dataclass(frozen=True)
class UsageReviseParams:
    """The ``usage_revise`` group author's parameters."""

    policy: str = "usage_v1"

    def __post_init__(self) -> None:
        if self.policy not in USAGE_REVISE_POLICIES:
            raise ConfigError(
                "methods.author.usage_revise policy must be one of "
                f"{USAGE_REVISE_POLICIES}, got {self.policy!r}")
