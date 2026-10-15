"""Auxiliary-metric wiring (the plugin contract's strict scorer pair).

Lives in the benchmarks layer because it is pure plugin wiring: resolve
the plugin, read its declared metric, and wrap its scorer with the
contract check. Stage 1 and both stage-4 drivers and stage 6 all read the
auxiliary metric through this one function so the scoring stages agree by
construction.
"""

from typing import Callable, Optional, Tuple

from expedistill.benchmarks.registry import resolve_plugin
from expedistill.core.models import Trajectory
from expedistill.infra.reliability import CodeBugError

__all__ = ["aux_wiring"]


def aux_wiring(
    benchmark: str,
) -> Tuple[Optional[str], Optional[Callable[[Trajectory], bool]]]:
    """(aux metric name, strict scorer) the benchmark's plugin declares.

    Imports the plugin package first (registration at import, D1's
    discipline), so a pure-resume re-aggregation that executes nothing
    still resolves the plugin. A plugin that declares no auxiliary metric
    yields ``(None, None)`` -- the summary's ``aux`` is null and no scorer
    ever runs. A plugin that declares a name must answer True / False for
    every valid trajectory; the default method's None leaking through a
    declared name is a plugin contract violation and raises CodeBugError
    here (never silently a zero).
    """
    plugin = resolve_plugin(benchmark)
    name = plugin.aux_metric_name
    if name is None:
        return None, None

    def scorer(traj: Trajectory) -> bool:
        value = plugin.aux_success(traj)
        if not isinstance(value, bool):
            raise CodeBugError(
                f"benchmark {benchmark!r} declares auxiliary metric "
                f"{name!r} but aux_success answered {value!r} for "
                f"{traj.trajectory_id}; a declared scorer must answer "
                "True/False for every valid trajectory")
        return value

    return name, scorer
