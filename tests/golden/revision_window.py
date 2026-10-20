"""The revision-window crash probe kept from the deleted M4.1 semantic
instrument (tests/golden/semantics.py).

Why this block survives the instrument's removal: it is the only mechanism
that exercises the "revision persisted, re-answer not yet run" crash point
(review C-1) -- a boundary the fraction-derived crash slots can miss, and
the point where a resume must not re-run or lose the G11 revision. The
golden-matrix crash+resume check runs it on every live combo.

The predicate: the skills block is a pure derivation from the skills store
(``live_measurement.survivors_and_block``), so the G11 marker in the block
means the revision IS persisted, and the task execution it belongs to is
the pending re-answer. The K1 dispatch stops at the first raise, so nothing
of the re-answer round persists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from expedistill.benchmarks.base import BenchmarkPlugin
from expedistill.cli import run_experiment
from expedistill.infra.parallel import ParallelError
from expedistill.infra.store import KIND_DIRS

from tests.golden.harness import (
    G11_REVISION_MARKER,
    SimulatedCrash,
    _ClientLog,
    installed_plugin,
    plugin_for,
)


class RevisionWindowCrash(BenchmarkPlugin):
    """Delegates to the wrapped plugin, but raises ``SimulatedCrash``
    before the first ``run()`` whose skills block carries the G11
    revision marker.

    The block is a pure derivation from the skills store
    (``live_measurement.survivors_and_block``), so marker-in-block means
    the revision IS persisted -- and the task execution it belongs to is
    the pending re-answer. The K1 dispatch stops at the first raise, so
    nothing of the re-answer round persists (verified by the standing
    self-check: zero round-1 trajectories at the crash point). And it is
    layout-agnostic by construction: it works unchanged when the rounds
    become B phases.
    """

    def __init__(self, inner: BenchmarkPlugin):
        self._inner = inner
        self.fired = False

    @property
    def name(self) -> str:
        return self._inner.name

    def load_tasks(self, tasks_spec):
        return self._inner.load_tasks(tasks_spec)

    def run(self, task, handle, skills_block, workdir):
        if not self.fired and G11_REVISION_MARKER in skills_block:
            self.fired = True
            raise SimulatedCrash(
                "simulated crash in the revision-persisted window "
                f"(task {task.task_id}, block carries the G11 marker)")
        return self._inner.run(task, handle, skills_block, workdir)

    @property
    def view_rules(self):
        return self._inner.view_rules

    @property
    def task_prompt_name(self) -> str:
        return self._inner.task_prompt_name


def _is_simulated_crash(exc: BaseException) -> bool:
    """True when ``exc`` is the harness's SimulatedCrash, however deep a
    pool wrapped it. The stage drivers unwrap their run_units ParallelError
    back into the unit's own exception; the live round dispatch does not,
    so the window runner accepts both surfaces."""
    while exc is not None:
        if isinstance(exc, SimulatedCrash):
            return True
        exc = exc.__cause__
    return False


def _crash_in_revision_window(config_path: Path, combo: Dict[str, str],
                               run_dir: Path) -> None:
    """Crash with the revision persisted and the re-answer pending (C-1),
    then prove the window was real before returning: the crash must have
    fired, and the skills store must already carry the revised content.
    (``skill_bank.jsonl`` is a stage-5 export; at this point the
    revision's only durable home is the store, and the round-1 block was
    rendered from exactly those files -- which is why marker-in-block
    proves persistence.)

    Owns its plugin install (installed_plugin refuses nested same-name
    registrations), so callers must NOT wrap this in another one.
    """
    wrapper = RevisionWindowCrash(plugin_for(combo))
    with installed_plugin(wrapper):
        try:
            run_experiment(config_path=Path(config_path),
                           run_dir=Path(run_dir),
                           make_client=_ClientLog().make_client)
            raise AssertionError("the revision-window crash never fired")
        except (SimulatedCrash, ParallelError) as exc:
            if not _is_simulated_crash(exc):
                raise
    skills_dir = Path(run_dir) / KIND_DIRS["skills"]
    persisted = [path.name for path in sorted(skills_dir.glob("*.json"))
                 if G11_REVISION_MARKER
                 in path.read_text(encoding="utf-8")]
    if not persisted:
        raise AssertionError(
            "the revision-window crash fired before the revision reached "
            "the skills store (no G11 marker in any stored skill) -- not "
            "the C-1 window; fix the crash predicate, do not weaken the "
            "check")
