"""A minimal concrete benchmark plugin for registry, wiring and E1 tests.

Every member a test varies is a constructor argument; ``run`` records its
arguments, so one class replaces the per-file stubs.
"""

from expedistill.benchmarks.base import (
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
)

__all__ = ["StubPlugin"]


class StubPlugin(BenchmarkPlugin):
    """``tasks`` are served by id filter (all when the spec is None);
    ``run`` appends its arguments to ``calls`` and returns ``outcome`` or
    raises ``error``."""

    view_rules = ViewRules()
    task_prompt_name = "FAKE_TASK"

    def __init__(self, name="fakebench", *, tasks=(), outcome=None,
                 error=None, aux_metric_name=None,
                 skill_folder_prompt_name=None):
        self._name = name
        self._tasks = list(tasks)
        self.outcome = outcome if outcome is not None else RunOutcome()
        self.error = error
        self.aux_metric_name = aux_metric_name
        self.skill_folder_prompt_name = skill_folder_prompt_name
        self.calls = []

    @property
    def name(self):
        return self._name

    def load_tasks(self, tasks_spec):
        if tasks_spec is None:
            return list(self._tasks)
        return [t for t in self._tasks if t.task_id in tasks_spec]

    def run(self, task: Task, handle, skills_block, workdir):
        self.calls.append({"task": task, "handle": handle,
                           "skills_block": skills_block, "workdir": workdir})
        if self.error is not None:
            raise self.error
        return self.outcome
