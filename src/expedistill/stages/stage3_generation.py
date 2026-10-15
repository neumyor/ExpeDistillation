"""Stage 3: candidate skill and exam generation (README section 3, stage 3).

Layer-6 wiring only: the stage owns iteration order, per-unit disk writes and
resume; the method itself lives in blocks (D6 ``select``, D5 ``view``, G2
``write_skill``, G3 ``gen_exam``) and is connected here, never reimplemented.
Which model plays each role comes from the context's role table
(``author`` writes skills, ``examiner`` writes exams); the stage never names a
model itself.

Determinism: selected subtasks are dispatched in ``subtask_id`` order, the
fixed creation order the ids module pins down -- a resumed run replays it, so
serial-numbered ids elsewhere stay reproducible. Every summary list is
aggregated from the input-ordered unit outcomes, so it stays in that selection
order whatever order the workers finished in.

Parallel dispatch: the selected subtasks run through B5 ``run_units`` with
``ctx.run_settings.workers`` threads (default 1; stages 1/6 read the same
field). The
unit is one selected subtask, and every expected failure is dispositioned
INSIDE the worker, per run_units' contract: anything escaping it is fatal and
stops the batch (in-flight units run to completion, then the first failure by
unit order surfaces unwrapped with its original type and message, whatever
its kind, the full-failure ParallelError chained as its __cause__). The
worker state shared
across threads is safe by construction: each unit touches only its own
``terminal`` / ledger key, and every disk write goes through append_jsonl's
per-path lock or store.save's distinct atomic file. With workers > 1 the
order of ledger lines on disk varies run to run; nothing here reads it back
in order.

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
* Anything else (ConfigError, ProviderRefusalError, CodeBugError, ...) is
  fatal to the batch: no further unit is started, in-flight units finish,
  and the first failure by unit order is then surfaced (B5's rule above);
  no summary is written, exactly as before.

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
from expedistill.infra.parallel import run_units
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

#: Per-unit dispositions: the summary list a processed subtask belongs to.
#: The worker returns one of these; run() buckets the input-ordered outcomes
#: into the summary, which keeps them in selection order.
_GENERATED = "generated"
_MODEL_OUTPUT_FAILED = "model_output_failed"
_INFRA_FAILED = "infra_failed"
_EXAM_MISSING_ADVANCED = "exam_missing_advanced"
_DISPOSITIONS = (
    _GENERATED, _MODEL_OUTPUT_FAILED, _INFRA_FAILED, _EXAM_MISSING_ADVANCED)

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

    Safe under the stage's worker threads: within one run each (unit, error
    class) pair is decided by exactly one unit (dispositions are mutually
    exclusive inside a worker), so the check-then-add on ``_seen`` cannot
    race, and the appends themselves lock the ledger file (B6/B3).
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
    selection order (the unit outcomes run_units returns are input-ordered)
    and all describing the run directory after this call: the subtasks the
    D6 rule kept, those with a stored (skill, exam) pair, the terminal
    model-output failures, the infrastructure holes still open, and the
    advanced skills whose exam is missing (module docstring). Raises
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

    def process(subtask: Subtask) -> str:
        """One selected subtask -> its disposition (a _DISPOSITIONS tag).

        The whole per-unit body of the old loop, unchanged: with
        ``ctx.run_settings.workers > 1`` it runs on B5's pool, and every expected failure
        is dispositioned here so anything escaping is fatal to the batch.
        The state shared with sibling workers (``terminal``, the ledger) is
        safe by construction -- one key per unit, appends locked in B3/B6.
        """
        subtask_id = subtask.subtask_id
        skill_id = make_skill_id(subtask_id)
        exam_id = make_exam_id(subtask_id)
        skill_stored = store.exists("skills", skill_id)
        if skill_stored and store.exists("exams", exam_id):
            return _GENERATED
        if subtask_id in terminal:
            # Decided by an earlier call; restore a ledger row a crash
            # between the two writes may have lost.
            row = terminal[subtask_id]
            ledger.record_once(subtask_id, row["error_class"], row["message"])
            return _MODEL_OUTPUT_FAILED
        if skill_stored and not _untouched_since_stage3(
                store.load("skills", skill_id, Skill)):
            ledger.record_once(
                subtask_id, "ExamMissingError",
                f"exam {exam_id} is missing but skill {skill_id} has already "
                "advanced past stage 3; not regenerating it")
            return _EXAM_MISSING_ADVANCED

        # A selected subtask without its trajectory is a broken run, not a
        # skippable unit: fail loudly instead of silently shrinking the stage.
        trajectory = store.load("trajectories", subtask.trajectory_id,
                               Trajectory)
        start, end = subtask.step_range
        steps_view = render_steps_view(trajectory.steps[start:end], view_rules)

        # Strong segments are distilled, weak ones diagnosed (README stage 3);
        # ``source`` is vocab-checked to strong/weak by the model, so the
        # branch covers every legal value. Protocol v2 ties each mode to a
        # stored content structure: "distill" renders a four-section
        # procedure skill (core.models.PROCEDURE_MARKERS), "diagnose" a
        # failure reference (FAILURE_REFERENCE_MARKERS), and write_skill's
        # precondition guard refuses a subtask outcome that contradicts the
        # mode (fatal here, AGENTS section 6 rule 6). This mode->structure
        # mapping is the extension point for a third skill kind: a new mode
        # on this line comes with its sections contract in blocks.author
        # (SKILL_SECTION_FIELDS) and its marker set in core.models.
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
            return _MODEL_OUTPUT_FAILED
        except InfrastructureError as exc:
            ledger.record(subtask_id, exc, exc)
            return _INFRA_FAILED

        store.save(skill, "skills")
        store.save(exam, "exams")
        return _GENERATED

    # Selection stays serial and deterministic; only the per-subtask units
    # parallelize. A fatal escape raises out of run_units (no summary is
    # written, exactly as before), so a returned outcomes list holds one
    # disposition per unit.
    outcomes = run_units(process, selected, ctx.run_settings.workers)

    # Buckets keep the selection order: outcomes are input-ordered, never
    # completion-ordered, however the workers interleaved.
    buckets: Dict[str, List[str]] = {name: [] for name in _DISPOSITIONS}
    for subtask, disposition in zip(selected, outcomes):
        buckets[disposition].append(subtask.subtask_id)
    summary = {
        "selected": [subtask.subtask_id for subtask in selected],
        "generated": buckets[_GENERATED],
        "model_output_failed": buckets[_MODEL_OUTPUT_FAILED],
        "infra_failed": buckets[_INFRA_FAILED],
        "exam_missing_advanced": buckets[_EXAM_MISSING_ADVANCED],
    }
    write_json(ctx.run_dir / STAGE3_DIR / SUMMARY_FILENAME, summary, indent=2)
    if summary["infra_failed"]:
        raise InfrastructureError(
            f"stage 3: {len(summary['infra_failed'])} unit(s) (subtasks) "
            f"failed on infrastructure errors, recorded in "
            f"{ledger.recorder.path}; resume the run to retry them")
    return summary
