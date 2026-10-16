"""E1: execute one task on one benchmark through its plugin (layer 4).

The single "run a model on one task" entry the whole pipeline uses (K1 fans
it out over tasks and attempts). E1 is deliberately a thin assembler: the
benchmark knowledge is the plugin's (README 1.1 rule 3), retries are K1's
(B6 policy), and persistence is K1's -- this block runs one attempt, builds
the durable record around it, and touches nothing else.

Division of labour per the design review:

- E1 owns the plugin import. The foundation never imports plugin packages
  (a bare import of a plugin package must be cheap and dependency-free: the
  ALFWorld plugin loads its heavy deps inside its methods, not at import
  time), so the plugin package is imported lazily, here, right before use.
- Plugins own their own failures: expected infrastructure failures raise
  ``InfrastructureError`` (K1 retries them); anything else that raises out
  of ``plugin.run`` is a code bug and propagates unchanged. E1 never
  retries, never catches, never writes disk.
- E1 owns the Trajectory bookkeeping: the plugin's RunOutcome carries only
  what happened inside the environment; the identity fields (ids, role,
  attempt, injected-skills provenance) are attached here so every
  Trajectory in the store is assembled by exactly one code path.

Adapted from the reference repo's per-unit discipline (ExpeToSkill-merged
@ TB-eval 4f436af: evaluation/workers.py, l1/runner.py): one unit = one
plain record with program-owned identity fields, partial steps retained on
failure, and a strict separation between "the agent failed" and "the
infrastructure failed". The L1 repair method itself is not migrated (it is
that system's method, not this design's).
"""

from pathlib import Path
from typing import Any, Optional, Union

from expedistill.benchmarks.base import RunOutcome, Task
from expedistill.benchmarks.registry import resolve_plugin
from expedistill.core.ids import make_task_id, make_trajectory_id
from expedistill.core.models import Trajectory
from expedistill.infra.reliability import CodeBugError

__all__ = ["run_one", "make_task_id"]

#: Plugin packages live directly under this prefix; one benchmark = one
def run_one(
    *,
    benchmark: str,
    task: Task,
    model_role: str,
    model_name: str,
    attempt_index: int,
    skills_injected: Any,
    skills_block: str,
    handle: Any,
    workdir: Union[str, Path],
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Trajectory:
    """Execute one task once through its benchmark plugin; return the record.

    ``benchmark`` names the plugin (and the plugin package to import
    lazily). ``task`` is the plugin's own Task object; ``handle`` is the
    model handle the plugin's runner calls through; ``skills_block`` is the
    D9 block verbatim (empty for the bare control); ``workdir`` is where the
    plugin's runner may put its own execution artifacts (E1 itself writes
    nothing).

    ``skills_injected`` is recorded verbatim and never interpreted here:
    ``None`` for a bare run, a list of skill references for selected-skill
    runs, or the bank digest string for stage-6 whole-bank runs.

    ``usage`` and ``run_dir`` are accepted for call-site uniformity with the
    other model-calling blocks (K1 threads the same account objects through
    every block it drives). E1 itself uses neither: it never writes disk
    (K1 stores every Trajectory), and per-attempt usage accounting is owned
    by the runner's model-call wiring (local runners account inside
    ``call_model``; the TB runner's usage is read back from the Harbor
    trial by K1).

    ``model_role`` must be one of ``core.models.MODEL_ROLES`` and
    ``attempt_index`` >= 0; violations raise ValueError from the id rule
    (``core.ids.make_trajectory_id``) before anything is executed.
    """
    if not isinstance(benchmark, str) or not benchmark.strip():
        # Mirrors benchmarks.registry.get_plugin: a non-string benchmark name
        # is a programming error, not a configuration problem.
        raise CodeBugError(
            f"benchmark must be a non-empty str, got {benchmark!r}")
    plugin = resolve_plugin(benchmark)
    outcome = plugin.run(task, handle, skills_block, workdir)
    if not isinstance(outcome, RunOutcome):
        raise CodeBugError(
            f"plugin {benchmark!r} run() must return a RunOutcome, got "
            f"{type(outcome).__name__}")
    task_id = make_task_id(benchmark, task.task_id)
    meta = dict(outcome.meta)
    meta["workdir"] = str(workdir)
    return Trajectory(
        trajectory_id=make_trajectory_id(task_id, model_role, attempt_index),
        task_id=task_id,
        benchmark=benchmark,
        model_role=model_role,
        model_name=model_name,
        attempt_index=attempt_index,
        skills_injected=skills_injected,
        steps=list(outcome.steps),
        correctness=outcome.correctness,
        meta=meta,
    )
