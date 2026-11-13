"""The revision-window crash probe.

A learn phase revises one side of a card (REVISE_TRIGGER or
REVISE_CONTENT), and the next practice phase delivers the new version. The
probe crashes before the first execution that follows a revision: the
revised version is in the skills store, the learn phase is finished, and
nothing of the next practice has run. That boundary is where a resume must
neither lose nor redo the revision, and the matrix's per-step crash points
(all of them before a model call inside a learn phase) do not cover it.

The stub plugin makes no model call, so the crash is raised by the plugin
itself, at the first ``run`` after the first answered rewrite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List

from expedistill.cli import run_experiment
from expedistill.infra.store import KIND_DIRS
from expedistill.llm.fake_provider import REVISION_MARKER

from tests.golden.harness import (
    REVISE_ANCHOR,
    GoldenClient,
    GoldenPlugin,
    SimulatedCrash,
    installed_plugin,
    is_simulated_crash,
)


class _RevisionWindow:
    """The make_client hook and the plugin of the probe: answers like the
    harness, and the plugin raises SimulatedCrash at its first ``run``
    after an answered rewrite."""

    def __init__(self) -> None:
        self.revised = False
        self.fired = False

    def make_client(self, model_name: str) -> Any:
        window = self

        class _Client:
            def __init__(self) -> None:
                self.inner = GoldenClient()

            @property
            def calls(self):
                return self.inner.calls

            def complete(self, model, system, user, **kwargs):
                reply = self.inner.complete(model, system, user, **kwargs)
                if user.startswith(REVISE_ANCHOR):
                    window.revised = True
                return reply

        return _Client()

    def plugin(self) -> GoldenPlugin:
        window = self

        class _Plugin(GoldenPlugin):
            def run(self, task, handle, skills_block, workdir):
                if window.revised and not window.fired:
                    window.fired = True
                    raise SimulatedCrash(
                        "simulated crash in the revision-persisted window")
                return super().run(task, handle, skills_block, workdir)

        return _Plugin()


def crash_in_revision_window(config_path: Path, run_dir: Path
                             ) -> List[str]:
    """Run until the window crash fires, then prove the window was real:
    a stored skill carries the revision marker. Returns the stored skill
    file names carrying it. Owns its plugin install."""
    window = _RevisionWindow()
    with installed_plugin(window.plugin()):
        try:
            run_experiment(config_path=Path(config_path),
                           run_dir=Path(run_dir),
                           make_client=window.make_client)
        except Exception as exc:
            if not is_simulated_crash(exc):
                raise
        else:
            raise AssertionError("the revision-window crash never fired")
    skills_dir = Path(run_dir) / KIND_DIRS["skills"]
    revised = [path.name for path in sorted(skills_dir.glob("*.json"))
               if REVISION_MARKER in path.read_text(encoding="utf-8")]
    if not revised:
        raise AssertionError(
            "the revision-window crash fired before the revision reached "
            "the skills store -- not the revision window; fix the crash "
            "predicate, do not weaken the check")
    return revised
