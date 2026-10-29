"""Exact provenance: build, read and follow a ``TraceRef``.

Five functions are the whole interface:

* :func:`trace_at` -- the only constructor. It takes an **execution**
  ``AnswerSheet`` object and a step, checks ``0 <= step < len(steps)``, and
  refuses an exam answer sheet. A caller that holds only a unit or a task
  cannot build a pointer, so the unit-shared-by-three-attempts ambiguity
  cannot be written; and a paper answer cannot become a scene source, so an
  "exam of an exam" cannot be written either.
* :func:`resolve` -- the only reader of source context. One exact file
  lookup (``phases/<phase>/answer_sheets/<sheet>.json``), no directory scan,
  no priority order. It renders the prefix and following steps with the
  benchmark's view rules, so what a writer saw and what the next exam is cut
  from are the same text.
* :func:`scene_of` -- an exam answer's way back to the real scene: the
  exam's own ``scene``.
* :func:`trace_of_trajectory` -- the P02 helper: the pointer at a step of
  a stored trajectory, found through the sheet's own trajectory id.
* :func:`render_scenes` -- the text a skill writer reads. It renders at most
  ``WRITER_BEFORE_STEPS`` steps before each cut and ``after_steps`` from it,
  and merges the windows of every pointer on one execution, so a step is
  never sent twice when several cuts on one trajectory land in one prompt.

A sheet is an *exam answer* when its ``task_ref`` is an exam id (the sheet
was made by ``execution_from_attempt``); every other kind of execution is a
legal scene source.
"""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from expedistill.blocks.view import render_steps_view
from expedistill.composites.phase_bank import phase_bank_dir
from expedistill.composites.phase_report import ANSWER_SHEETS_DIR
from expedistill.core import ids
from expedistill.core.answer_sheet import AnswerSheet, CoverageRecord
from expedistill.core.models import Exam
from expedistill.core.trace import TraceContext, TraceRef
from expedistill.infra.reliability import CodeBugError, ModelOutputError
from expedistill.infra.store import object_filename, read_json

__all__ = [
    "DEFAULT_AFTER_STEPS",
    "WRITER_BEFORE_STEPS",
    "is_exam_answer",
    "load_sheet",
    "render_scenes",
    "resolve",
    "scene_of",
    "trace_at",
    "trace_of_execution",
    "trace_of_trajectory",
]

#: How many steps after the cut a resolved context renders (the window the
#: exam scene writer has always been shown).
DEFAULT_AFTER_STEPS = 30

#: How many steps before the cut a skill writer reads. The exam scene writer
#: is not bound by it (see ``resolve``).
WRITER_BEFORE_STEPS = 15


def is_exam_answer(sheet: AnswerSheet) -> bool:
    """True for a paper exam answer: its task is an exam id, not a task."""
    execution = sheet.execution
    if execution is None:
        return False
    try:
        ids.parse_exam_id(execution.task_ref)
    except ValueError:
        return False
    return True


def trace_at(sheet: AnswerSheet, step: int) -> TraceRef:
    """The exact location of ``step`` in ``sheet``'s execution.

    Refuses a sheet with no execution, an exam answer, and a step outside
    the recorded steps. These are code bugs of the caller (it was handed
    the wrong object), not model output.
    """
    execution = sheet.execution
    if execution is None:
        raise CodeBugError(
            f"trace_at needs a present execution, got sheet "
            f"{sheet.sheet_id!r} without one")
    if is_exam_answer(sheet):
        raise CodeBugError(
            f"sheet {sheet.sheet_id!r} is an exam answer: a scene is cut "
            "from a real execution, not from a paper answer (follow "
            "scene_of(exam) back to the real scene instead)")
    steps = execution.steps or []
    if isinstance(step, bool) or not isinstance(step, int) \
            or not 0 <= step < len(steps):
        raise CodeBugError(
            f"step {step!r} is outside sheet {sheet.sheet_id!r}'s "
            f"{len(steps)} recorded step(s)")
    return TraceRef(phase_label=sheet.phase_label, sheet_id=sheet.sheet_id,
                    step=step)


def trace_of_execution(execution: Any, step: int) -> TraceRef:
    """``trace_at`` for a caller that holds an ``ExecutionRecord``.

    Attribution walks executions (each one already knows its own
    ``sheet_id`` and ``phase_label``), so a window's pointer is built from
    the very record whose steps it rendered -- the step and the attempt
    cannot come from different places.
    """
    sheet = AnswerSheet(
        sheet_id=execution.sheet_id, phase_label=execution.phase_label,
        coverage=CoverageRecord(
            unit_id=execution.unit_id, attempt_index=execution.sample_index,
            present=True, status="present"),
        execution=execution)
    return trace_at(sheet, step)


def scene_of(exam: Exam) -> TraceRef:
    """The real scene an exam was cut at."""
    return exam.scene


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
    scene goes through this one lookup and its checks."""
    sheet = load_sheet(run_dir, ref)
    execution = sheet.execution
    if execution is None:
        raise ModelOutputError(
            f"answer sheet {ref.sheet_id!r} has no execution")
    if is_exam_answer(sheet):
        raise CodeBugError(
            f"TraceRef {ref.sheet_id!r} names an exam answer; a scene "
            "reference must point at a real execution")
    steps = list(execution.steps or [])
    if not 0 <= ref.step < len(steps):
        raise ModelOutputError(
            f"TraceRef step {ref.step} is outside sheet {ref.sheet_id!r}'s "
            f"{len(steps)} step(s)")
    return execution, steps


def _instruction(execution: Any,
                 instruction_for: Optional[Callable[[str], str]]) -> str:
    instruction = ""
    if instruction_for is not None:
        try:
            instruction = instruction_for(execution.task_ref)
        except Exception:
            instruction = ""
    return instruction or str(
        (execution.metrics or {}).get("initial_prompt") or "")


def resolve(run_dir: Union[str, Path], ref: TraceRef, *,
            instruction_for: Optional[Callable[[str], str]] = None,
            view_rules: Any = None,
            after_steps: int = DEFAULT_AFTER_STEPS,
            before_steps: Optional[int] = None) -> TraceContext:
    """The text a prompt may read about ``ref``.

    ``before`` is the rendered prefix ``steps[:ref.step]`` and ``after`` the
    next ``after_steps`` steps; both keep the original step numbers.
    ``before_steps`` keeps only the last that many prefix steps (``None`` =
    the whole prefix). The exam scene writer passes nothing, so an exam is
    always cut from the full prefix; a skill writer passes a bound.
    ``instruction_for`` maps the execution's ``task_ref`` to the benchmark's
    task prompt (the stage layer owns that lookup; this layer cannot import
    it); when it is absent, or raises, the sheet's recorded initial prompt
    is used.
    """
    execution, steps = _real_execution(run_dir, ref)
    instruction = _instruction(execution, instruction_for)
    end = ref.step + max(0, after_steps)
    first = 0 if before_steps is None else max(0, ref.step - before_steps)
    return TraceContext(
        ref=ref,
        task_ref=execution.task_ref,
        task_instruction=instruction,
        before=render_steps_view(steps[first:ref.step], view_rules,
                                 start_index=first),
        after=render_steps_view(steps[ref.step:end], view_rules,
                                start_index=ref.step),
        outcome=execution.raw_correctness)


def trace_of_trajectory(run_dir: Union[str, Path], phase_label: str,
                        trajectory_id: str, step: int) -> TraceRef:
    """The ``TraceRef`` at ``step`` of a stored trajectory's answer sheet.

    The baseline-style sheet of one attempt is the only sheet of ``phase``
    whose recorded trajectory is ``trajectory_id``; finding it through the
    sheets' own ``artifacts.trajectory_id`` (not through the shared unit
    id) is what makes the attempt unambiguous. Zero or several matches are
    errors -- the caller named a trajectory that phase did not run, or the
    phase stored two sheets for one attempt.
    """
    directory = phase_bank_dir(run_dir, phase_label) / ANSWER_SHEETS_DIR
    matches = []
    for path in sorted(directory.glob("*.json")):
        sheet = AnswerSheet.from_dict(read_json(path))
        execution = sheet.execution
        if execution is not None and (
                execution.artifacts or {}).get("trajectory_id") \
                == trajectory_id and not is_exam_answer(sheet):
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


def render_scenes(run_dir: Union[str, Path], refs: Sequence[TraceRef], *,
                  instruction_for: Optional[Callable[[str], str]] = None,
                  view_rules: Any = None,
                  after_steps: int = DEFAULT_AFTER_STEPS,
                  before_steps: int = WRITER_BEFORE_STEPS) -> str:
    """The prompt text a skill writer reads about one or more locations.

    Locations on the same execution are rendered once: their windows
    (``before_steps`` before each cut, ``after_steps`` from it) are merged
    so a step never appears twice, and the task and its outcome are stated
    once per execution. Overlapping or adjacent windows read as one run of
    steps; a gap between windows is marked with the steps it skips.
    """
    by_sheet: Dict[str, List[TraceRef]] = {}
    for ref in refs:
        by_sheet.setdefault(ref.sheet_id, []).append(ref)
    blocks = []
    for sheet_refs in by_sheet.values():
        execution, steps = _real_execution(run_dir, sheet_refs[0])
        wanted = set()
        for ref in sheet_refs:
            _real_execution(run_dir, ref)
            wanted.update(range(max(0, ref.step - before_steps),
                                min(len(steps), ref.step + after_steps)))
        parts = []
        instruction = _instruction(execution, instruction_for)
        if instruction:
            parts.append(f"TASK:\n{instruction}")
        cuts = ", ".join(str(ref.step) for ref in sorted(
            sheet_refs, key=lambda item: item.step))
        parts.append(f"STEPS (the skill applies at step {cuts}):")
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
        f"[execution {index}]\n{block}"
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
