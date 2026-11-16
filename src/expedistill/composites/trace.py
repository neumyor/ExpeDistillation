"""Exact provenance: build, read and follow a ``TraceRef``.

The interface:

* :func:`trace_at` -- the only constructor. It takes a baseline or practice
  ``AnswerSheet`` object and a step and checks ``0 <= step < len(steps)``.
  A caller that holds only a unit or a task cannot build a pointer, so the
  unit-shared-by-three-attempts ambiguity cannot be written.
* :func:`check_ref` -- the boundary check that a pointer still names a
  recorded step. One exact file lookup
  (``phases/<phase>/answer_sheets/<sheet>.json``), no directory scan, no
  priority order.
* :func:`trace_of_trajectory` -- the pointer at a step of a stored
  trajectory, found through the sheet's own trajectory id (a card's
  material segments are located this way).
* :func:`render_windows` -- the text a skill writer reads about pointers,
  with an explicit window per pointer (a rewrite's evidence: from a segment
  start or a card read to the failing step; the usual window opens
  ``WRITER_BEFORE_STEPS`` steps before the cut). Windows on one execution
  are merged, so a step is never sent twice when several cuts on one
  trajectory land in one prompt.

The readers take an ``instruction_for`` callable;
:func:`task_instruction` is the usual one, the benchmark's task prompt of
one task id.
"""

from pathlib import Path
from typing import (
    Any, Callable, Dict, List, Optional, Sequence, Tuple, Union)

from expedistill.benchmarks.registry import get_plugin
from expedistill.blocks import tasks as tasks_block
from expedistill.blocks.view import render_steps_view
from expedistill.composites.phase_bank import phase_bank_dir
from expedistill.composites.phase_report import ANSWER_SHEETS_DIR
from expedistill.core import ids
from expedistill.core.answer_sheet import AnswerSheet
from expedistill.core.trace import TraceRef
from expedistill.infra.reliability import CodeBugError, ModelOutputError
from expedistill.infra.store import object_filename, read_json
from expedistill.prompts import get as get_prompt

__all__ = [
    "WRITER_BEFORE_STEPS",
    "check_ref",
    "execution_instruction",
    "instruction_with_initial",
    "load_sheet",
    "render_windows",
    "task_instruction",
    "trace_at",
    "trace_of_trajectory",
]

#: How many steps before the cut a skill writer reads.
WRITER_BEFORE_STEPS = 15


def trace_at(sheet: AnswerSheet, step: int) -> TraceRef:
    """The exact location of ``step`` in ``sheet``'s execution.

    Refuses a sheet with no execution and a step outside the recorded
    steps. These are code bugs of the caller (it was handed the wrong
    object), not model output.
    """
    execution = sheet.execution
    if execution is None:
        raise CodeBugError(
            f"trace_at needs a present execution, got sheet "
            f"{sheet.sheet_id!r} without one")
    steps = execution.steps or []
    if isinstance(step, bool) or not isinstance(step, int) \
            or not 0 <= step < len(steps):
        raise CodeBugError(
            f"step {step!r} is outside sheet {sheet.sheet_id!r}'s "
            f"{len(steps)} recorded step(s)")
    return TraceRef(phase_label=sheet.phase_label, sheet_id=sheet.sheet_id,
                    step=step)


def load_sheet(run_dir: Union[str, Path], ref: TraceRef) -> AnswerSheet:
    """The one answer sheet a ``TraceRef`` names (exact lookup)."""
    directory = phase_bank_dir(run_dir, ref.phase_label) / ANSWER_SHEETS_DIR
    path = directory / object_filename(ref.sheet_id)
    if not path.exists():
        raise ModelOutputError(
            f"no answer sheet {ref.sheet_id!r} in phase "
            f"{ref.phase_label!r} ({path})")
    return AnswerSheet.from_dict(read_json(path))


def _real_execution(run_dir: Union[str, Path], ref: TraceRef):
    """The execution a ``TraceRef`` names and its steps; every reader of a
    location goes through this one lookup and its checks."""
    sheet = load_sheet(run_dir, ref)
    execution = sheet.execution
    if execution is None:
        raise ModelOutputError(
            f"answer sheet {ref.sheet_id!r} has no execution")
    steps = list(execution.steps or [])
    if not 0 <= ref.step < len(steps):
        raise ModelOutputError(
            f"TraceRef step {ref.step} is outside sheet {ref.sheet_id!r}'s "
            f"{len(steps)} step(s)")
    return execution, steps


def instruction_with_initial(metadata: Any, instruction: str) -> str:
    """The task instruction plus the env's reset observation, if recorded.

    Local benchmarks fold the reset observation into the executor's
    instruction (ALFWorld's room description is the only input listing the
    receptacles). The task table holds only the raw task line, so every
    reader rebuilding a situation from an execution must take the
    observation from the execution record itself; without it the text
    starts without the rooms the real run saw.
    """
    metrics = metadata if isinstance(metadata, dict) else {}
    initial = str(metrics.get("initial_observation") or "")
    if initial and initial not in instruction:
        return f"{instruction}\n\n{initial}" if instruction else initial
    return instruction


def task_instruction(benchmark: str, task_id: str) -> str:
    """The benchmark task's execution prompt for one task id (the usual
    ``instruction_for`` of the functions here)."""
    plugin = get_plugin(benchmark)
    raw_id = ids.raw_task_id(task_id, benchmark)
    loaded = tasks_block.load_tasks(benchmark, [raw_id])
    match = [task for task in loaded if task.task_id == raw_id]
    if not match:
        raise CodeBugError(
            f"benchmark {benchmark!r} did not load task {raw_id!r} for "
            f"{task_id!r}: the arms must never run a different set than "
            "the executions")
    # Every slot a registered task prompt may carry, rendered empty.
    return get_prompt(plugin.task_prompt_name).format(
        instruction=match[0].instruction, skills_block="", history="",
        terminal_state="")


def execution_instruction(
        execution: Any,
        instruction_for: Optional[Callable[[str], str]]) -> str:
    """The task instruction an execution ran under: the benchmark's task
    prompt (``instruction_for``), else the sheet's recorded initial prompt,
    plus the recorded reset observation."""
    instruction = ""
    if instruction_for is not None:
        try:
            instruction = instruction_for(execution.task_ref)
        except Exception:
            instruction = ""
    metrics = execution.metrics or {}
    instruction = instruction or str(metrics.get("initial_prompt") or "")
    return instruction_with_initial(metrics, instruction)


def check_ref(run_dir: Union[str, Path], ref: TraceRef) -> None:
    """Raise ModelOutputError unless ``ref`` names a recorded step of a
    stored execution."""
    _real_execution(run_dir, ref)


def trace_of_trajectory(run_dir: Union[str, Path], phase_label: str,
                        trajectory_id: str, step: int) -> TraceRef:
    """The ``TraceRef`` at ``step`` of a stored trajectory's answer sheet.

    The sheet of one attempt is the only sheet of ``phase`` whose recorded
    trajectory is ``trajectory_id``; finding it through the sheets' own
    ``artifacts.trajectory_id`` (not through the shared unit id) is what
    makes the attempt unambiguous. Zero or several matches are errors --
    the caller named a trajectory that phase did not run, or the phase
    stored two sheets for one attempt.
    """
    directory = phase_bank_dir(run_dir, phase_label) / ANSWER_SHEETS_DIR
    matches = []
    for path in sorted(directory.glob("*.json")):
        sheet = AnswerSheet.from_dict(read_json(path))
        execution = sheet.execution
        if execution is not None and (
                execution.artifacts or {}).get("trajectory_id") \
                == trajectory_id:
            matches.append(sheet)
    if not matches:
        raise ModelOutputError(
            f"phase {phase_label!r} has no answer sheet for trajectory "
            f"{trajectory_id!r}")
    if len(matches) > 1:
        raise CodeBugError(
            f"phase {phase_label!r} has {len(matches)} answer sheets for "
            f"trajectory {trajectory_id!r}; one attempt must have one sheet")
    return trace_at(matches[0], step)


def render_windows(run_dir: Union[str, Path],
                   windows: Sequence[Tuple[TraceRef, int, int]], *,
                   instruction_for: Optional[Callable[[str], str]] = None,
                   view_rules: Any = None,
                   header: str = ("STEPS (the trial went wrong at step "
                                  "{cuts}):"),
                   label: str = "execution") -> str:
    """The prompt text about explicit windows: each ``(ref, first, end)``
    shows the steps from ``first`` up to (not including) ``end`` of the
    execution ``ref`` names, clipped to the recorded steps.

    Windows on the same execution are rendered once: their steps are
    merged so a step never appears twice, and the task and its outcome are
    stated once per execution. Overlapping or adjacent windows read as one
    run of steps; a gap between windows is marked with the steps it skips.
    ``header`` names the cut steps (``{cuts}``); with several executions
    each block is numbered ``[<label> N]`` in first-appearance order.
    """
    by_sheet: Dict[str, List[Tuple[TraceRef, int, int]]] = {}
    for window in windows:
        by_sheet.setdefault(window[0].sheet_id, []).append(window)
    blocks = []
    for sheet_windows in by_sheet.values():
        execution, steps = _real_execution(run_dir, sheet_windows[0][0])
        wanted = set()
        for ref, first, end in sheet_windows:
            _real_execution(run_dir, ref)
            wanted.update(range(max(0, first), min(len(steps), end)))
        parts = []
        instruction = execution_instruction(execution, instruction_for)
        if instruction:
            parts.append(f"TASK:\n{instruction}")
        cuts = ", ".join(str(step) for step in sorted(
            {ref.step for ref, _, _ in sheet_windows}))
        parts.append(header.format(cuts=cuts))
        previous = None
        for first, last in _runs(sorted(wanted)):
            if previous is not None:
                parts.append(
                    f"[steps {previous + 1}-{first - 1} are not shown]")
            parts.append(render_steps_view(
                steps[first:last + 1], view_rules, start_index=first))
            previous = last
        if execution.raw_correctness is not None:
            parts.append("The execution this comes from "
                         + ("succeeded." if execution.raw_correctness
                            else "failed."))
        blocks.append("\n\n".join(parts))
    if len(blocks) == 1:
        return blocks[0]
    return "\n\n".join(
        f"[{label} {index}]\n{block}"
        for index, block in enumerate(blocks, start=1))


def _runs(indexes: Sequence[int]):
    """Contiguous ``(first, last)`` runs of a sorted index list."""
    first = last = None
    for index in indexes:
        if first is None:
            first = last = index
        elif index == last + 1:
            last = index
        else:
            yield first, last
            first = last = index
    if first is not None:
        yield first, last
