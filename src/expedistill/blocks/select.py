"""D6 segment selection (layer 4).

A thin block: it resolves a named segment_select rule from ``blocks/rules.py``
and filters the given Subtasks with it, preserving input order (the caller's
ordering is meaningful -- it mirrors the trajectories' segment order). The
rule itself -- which segments are worth a skill -- lives in the registry, so
changing the policy is a config-level name change, never a code edit here.
"""

from __future__ import annotations

from typing import List

from expedistill.core.models import Subtask

__all__ = ["select_subtasks"]


def select_subtasks(
    subtasks: List[Subtask],
    rule_name: str,
) -> List[Subtask]:
    """Return the Subtasks the named segment_select rule keeps, in input order.

    ``rule_name`` comes from the experiment config (``rules.segment_select``).
    Unknown names raise ConfigError listing the registered rules.
    """
    # rules.py is this layer's shared rule registry (the analogue of
    # benchmarks/registry.py), not a block to be composed, so resolving names
    # through it does not couple two blocks. The import is function-local so
    # importing this module stays free of side effects until a rule is needed.
    from expedistill.blocks.rules import get_segment_select

    rule = get_segment_select(rule_name)
    return [subtask for subtask in subtasks if rule(subtask)]
