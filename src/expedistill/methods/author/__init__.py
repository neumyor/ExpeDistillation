"""The author family: writing and revising skills from evidence.

R6 (docs/decisions/execute_learn_repair_plan_20261016.md) turned this
family from a params-only package into a real driver:
``methods.author.write`` receives the decision module's task list rows
and the evidence module's packages, and produces skill content --
``revise_group`` for an authorized revision (per-skill keep / revise /
delete decided HERE from the facts, the revision-flow delete carrying
``live_revision_deleted``) and ``write_new`` for an authorized add. The
writing dispatches by the EVIDENCE PACKAGE's shape, never by the report
kind (the old exam-uses-G6 / live-uses-G11 split is gone). The stage
drivers do not call it yet: the legacy paths keep their own G6 / G11
wiring until Wave 2 rewires phase_learn.

``AuthoringParams`` is the family's registered ``distill_diagnose``
contract. The ``exams`` switch stays for the v3 ablation pair: the
stage-3 call point that reads it and the config checks that guard it
are out of this wave's scope (it goes away with the Wave 2 call-point
move), and removing the field now would break them.
"""

from dataclasses import dataclass

__all__ = ["AuthoringParams"]


@dataclass(frozen=True)
class AuthoringParams:
    """Whether the authoring stage generates exam questions."""

    exams: bool = True
