"""The author family: writing new skills and exam revisions.

``methods.author.write`` owns the P02 add writer (``write_new``) and the
exam group writer (``revise_group``, used with answer-sheet evidence).
P04/P06 revisions live in ``methods.author.revise`` and read
only ``SkillAttemptEvidence``. The P02 add and exam paths keep the old
``EvidencePackage`` shape; the new writer does not.
"""

from dataclasses import dataclass

__all__ = ["AuthoringParams", "ClusterAuthoringParams"]

@dataclass(frozen=True)
class AuthoringParams:
    """The ``distill_diagnose`` authoring method's (empty) parameters."""


@dataclass(frozen=True)
class ClusterAuthoringParams:
    """The ``cluster_by_trigger`` P02 authoring method's parameters.

    The method works in three independent model steps (trigger family,
    family clustering, cluster skill). The limits are input/output
    guards, not experimental tuning knobs:

    - ``max_cards_per_prompt``: a family larger than this is clustered in
      deterministic subtask-id chunks (no cross-chunk merge in v1);
    - ``max_chars_per_card``: the per-subtask card's steps-view budget;
    - ``min_cluster_size``: the smallest accepted model cluster (one is
      legal, so an outlier can become its own skill);
    - the three retry limits are the business-repair budgets after
      ``call_model``'s own JSON-format budget.
    """

    max_cards_per_prompt: int = 40
    max_chars_per_card: int = 1200
    min_cluster_size: int = 1
    classify_retry_limit: int = 2
    cluster_retry_limit: int = 2
    skill_retry_limit: int = 2
