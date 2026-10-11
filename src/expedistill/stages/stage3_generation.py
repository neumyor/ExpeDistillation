"""Stage 3: candidate skill and exam generation (README section 3, stage 3).

Layer-6 wiring only: the stage owns iteration order, per-unit disk writes and
resume; the method itself lives in blocks (D6 ``select``, D5 ``view``, G2
``write_skill``, G3 ``gen_exam``) and is connected here, never reimplemented.
Which model plays each role comes from the context's role table
(``author`` writes skills, ``examiner`` writes exams); the stage never names a
model itself.

Determinism: selected subtasks are processed in ``subtask_id`` order, the
fixed creation order the ids module pins down -- a resumed run replays it, so
serial-numbered ids elsewhere stay reproducible.

Resume: a subtask whose (skill, exam) pair is already stored is skipped
without a model call. A pair missing either half (a crash between the two
saves) is regenerated in full: the skill and the exam are both functions of
the stored subtask alone, so the regeneration is idempotent under the
deterministic ids, and a skill whose exam never existed cannot have entered
acceptance, so overwriting it loses nothing. The one exception is a stored
skill that has already moved past its stage-3 state (status not "pending",
a version above 1, or a rewrite history -- stage 4 touched it): it is never
overwritten; the missing exam is recorded as a unit failure instead
(``exam_missing_advanced``), because regenerating would destroy later-stage
work and resuming cannot repair it.

Per-unit failure disposition (AGENTS.md section 6 rule 6; unit id =
subtask_id, ledger stage "stage3_generation"). The pair is saved only after
both G2 and G3 succeeded, so a failed unit leaves nothing behind:

* ``ModelOutputError`` that survived the block's bounded repair is TERMINAL
  within the run directory (a run is one frozen experiment; retrying with a
  resampled model means a new run, and stage 4 discards on the same error
  terminally too). One row is appended to
  ``stage3_generation/generation_failures.jsonl`` BEFORE the stage moves on,
  then the unit goes into the unit-failure ledger. A resume never re-offers a
  subtask listed there: it is reported under ``model_output_failed`` with no
  model call, and a ledger row lost to a crash between the two writes is
  restored, so the resumed result equals an uninterrupted run.
* ``InfrastructureError`` (call-level retries exhausted): recorded in the
  ledger, listed under ``infra_failed``, skipped; the remaining units still
  run, then the summary is written and the stage raises
  ``InfrastructureError`` so the pipeline never advances past a stage with
  holes. These units ARE re-offered on resume.
* Anything else (ConfigError, ProviderRefusalError, CodeBugError, ...)
  propagates immediately.

The summary describes the run directory's state after this call, not this
call's work: re-running a finished stage makes zero model calls and writes
the same summary.
"""

import importlib
from typing import Any, Dict, List, Set, Tuple

from expedistill.blocks.author import gen_exam, write_skill
from expedistill.blocks.select import select_subtasks
from expedistill.blocks.view import render_steps_view
from expedistill.benchmarks.registry import get_plugin
from expedistill.core.ids import make_exam_id, make_skill_id
from expedistill.core.models import Skill, Subtask, Trajectory
from expedistill.infra.reliability import (
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import append_jsonl, read_jsonl, write_json

__all__ = ["FAILURES_FILENAME", "STAGE3_DIR", "SUMMARY_FILENAME", "run"]

#: This stage's directory in the run layout (README section 2).
STAGE3_DIR = "stage3_generation"

#: Name of the stage summary inside ``STAGE3_DIR``.
SUMMARY_FILENAME = "stage3_summary.json"

#: Terminal per-unit failure records inside ``STAGE3_DIR`` (one row per
#: subtask whose bounded model-output repair was exhausted).
FAILURES_FILENAME = "generation_failures.jsonl"

#: Stage label written into the unit-failure ledger.
_FAILURE_STAGE = "stage3_generation"

#: Same cap as the unit-failure ledger's message field.
_MESSAGE_LIMIT = UnitFailureRecorder.MESSAGE_LIMIT

#: Plugin packages register at import; one benchmark = one package
#: (benchmarks/registry.py). Same pattern as blocks.execute.
_PLUGIN_PACKAGE_PREFIX = "expedistill.benchmarks."


def _resolve_plugin(benchmark: str) -> Any:
    """Import the benchmark's plugin package, then return the plugin.

    The import IS the resolution step (registration runs at import); a
    package that cannot be imported means the config named a benchmark that
    is not installed, hence ConfigError. Kept here instead of reaching into
    ``blocks.execute``'s private helper.
    """
    module_path = _PLUGIN_PACKAGE_PREFIX + benchmark
    try:
        importlib.import_module(module_path)
    except ImportError as exc:
        raise ConfigError(
            f"benchmark plugin {benchmark!r} is not available: importing "
            f"{module_path!r} failed ({type(exc).__name__}: {exc})") from exc
    return get_plugin(benchmark)


def _untouched_since_stage3(skill: Skill) -> bool:
    """True if ``skill`` is exactly what G2 produced (safe to regenerate)."""
    return (skill.status == "pending" and skill.version == 1
            and not skill.rewrite_history)


def _short(message: Any) -> str:
    text = " ".join(str(message).split())
    if len(text) > _MESSAGE_LIMIT:
        text = text[:_MESSAGE_LIMIT - 3] + "..."
    return text


class _Ledger:
    """The unit-failure ledger, with once-only recording for terminal units.

    Terminal failures are recorded once per (unit, error class) however
    often the stage is re-run; infrastructure failures are fresh events each
    time and use ``record`` directly.
    """

    def __init__(self, run_dir: Any) -> None:
        self.recorder = UnitFailureRecorder(run_dir)
        self._seen: Set[Tuple[str, str]] = {
            (row["unit_id"], row["error_class"])
            for row in self.recorder.list_failures()
            if row.get("stage") == _FAILURE_STAGE
        }

    def record(self, unit_id: str, error_class: Any, message: Any) -> None:
        self.recorder.record(unit_id, _FAILURE_STAGE, error_class, message)

    def record_once(self, unit_id: str, error_class: str,
                    message: Any) -> None:
        if (unit_id, error_class) in self._seen:
            return
        self.record(unit_id, error_class, message)
        self._seen.add((unit_id, error_class))


def _terminal_failures(path: Any) -> Dict[str, Dict[str, Any]]:
    """Subtask id -> its terminal failure row (first row wins)."""
    rows: Dict[str, Dict[str, Any]] = {}
    for row in read_jsonl(path):
        rows.setdefault(row["subtask_id"], row)
    return rows


def run(ctx: Any) -> Dict[str, Any]:
    """Generate one candidate skill and one exam per selected subtask.

    Returns and stores ``{"selected", "generated", "model_output_failed",
    "infra_failed", "exam_missing_advanced"}``, all subtask-id lists in
    processing order and all describing the run directory after this call:
    the subtasks the D6 rule kept, those with a stored (skill, exam) pair,
    the terminal model-output failures, the infrastructure holes still open,
    and the advanced skills whose exam is missing (module docstring). Raises
    ``InfrastructureError`` after storing the summary when ``infra_failed``
    is non-empty.
    """
    store = ctx.store
    # config.rules always carries every rule key after ExperimentConfig
    # validation, and stage 0 checked the name: no local fallback here.
    rule_name = ctx.config.rules["segment_select"]

    subtasks = [
        store.load("subtasks", subtask_id, Subtask)
        for subtask_id in store.ids("subtasks")
    ]
    # Sorted by subtask_id: the fixed creation order every serial-numbered
    # id downstream relies on (core.ids module docstring).
    selected = sorted(
        select_subtasks(subtasks, rule_name),
        key=lambda subtask: subtask.subtask_id)

    view_rules = _resolve_plugin(ctx.config.benchmark).view_rules
    author = ctx.handle("author")
    examiner = ctx.handle("examiner")
    ledger = _Ledger(ctx.run_dir)
    failures_path = ctx.run_dir / STAGE3_DIR / FAILURES_FILENAME
    terminal = _terminal_failures(failures_path)

    generated: List[str] = []
    model_output_failed: List[str] = []
    infra_failed: List[str] = []
    exam_missing_advanced: List[str] = []
    for subtask in selected:
        subtask_id = subtask.subtask_id
        skill_id = make_skill_id(subtask_id)
        exam_id = make_exam_id(subtask_id)
        skill_stored = store.exists("skills", skill_id)
        if skill_stored and store.exists("exams", exam_id):
            generated.append(subtask_id)
            continue
        if subtask_id in terminal:
            # Decided by an earlier call; restore a ledger row a crash
            # between the two writes may have lost.
            row = terminal[subtask_id]
            ledger.record_once(subtask_id, row["error_class"], row["message"])
            model_output_failed.append(subtask_id)
            continue
        if skill_stored and not _untouched_since_stage3(
                store.load("skills", skill_id, Skill)):
            ledger.record_once(
                subtask_id, "ExamMissingError",
                f"exam {exam_id} is missing but skill {skill_id} has already "
                "advanced past stage 3; not regenerating it")
            exam_missing_advanced.append(subtask_id)
            continue

        # A selected subtask without its trajectory is a broken run, not a
        # skippable unit: fail loudly instead of silently shrinking the stage.
        trajectory = store.load("trajectories", subtask.trajectory_id,
                               Trajectory)
        start, end = subtask.step_range
        steps_view = render_steps_view(trajectory.steps[start:end], view_rules)

        # Strong segments are distilled, weak ones diagnosed (README stage 3);
        # ``source`` is vocab-checked to strong/weak by the model, so the
        # branch covers every legal value.
        mode = "distill" if subtask.source == "strong" else "diagnose"
        try:
            skill = write_skill(
                subtask, steps_view, ctx.config.weak_profile, mode, author,
                usage=ctx.usage, run_dir=ctx.run_dir)
            exam = gen_exam(
                subtask, steps_view, examiner,
                usage=ctx.usage, run_dir=ctx.run_dir)
        except ModelOutputError as exc:
            # The terminal record is the decision and is written first; the
            # ledger row follows (restored on resume if a crash lands between).
            row = {"subtask_id": subtask_id,
                   "error_class": type(exc).__name__,
                   "message": _short(exc)}
            append_jsonl(failures_path, row)
            terminal[subtask_id] = row
            ledger.record_once(subtask_id, row["error_class"], row["message"])
            model_output_failed.append(subtask_id)
            continue
        except InfrastructureError as exc:
            ledger.record(subtask_id, exc, exc)
            infra_failed.append(subtask_id)
            continue

        store.save(skill, "skills")
        store.save(exam, "exams")
        generated.append(subtask_id)

    summary = {
        "selected": [subtask.subtask_id for subtask in selected],
        "generated": generated,
        "model_output_failed": model_output_failed,
        "infra_failed": infra_failed,
        "exam_missing_advanced": exam_missing_advanced,
    }
    write_json(ctx.run_dir / STAGE3_DIR / SUMMARY_FILENAME, summary, indent=2)
    if infra_failed:
        raise InfrastructureError(
            f"stage 3: {len(infra_failed)} unit(s) (subtasks) failed on "
            f"infrastructure errors, recorded in {ledger.recorder.path}; "
            "resume the run to retry them")
    return summary
