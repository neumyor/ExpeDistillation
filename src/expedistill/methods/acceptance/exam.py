"""The exam acceptance driver: one skill at a time, a K3 text exam per
(skill, exam) pair -- the frozen v1 protocol (README section 3, stage 4).

The K4 loop itself is ``composites.acceptance.accept_skill`` (which owns
the per-skill disk IO and resume); this driver enumerates the stored
pending stage-3 candidates (id == ``make_skill_id(subtask_id)``;
stage-5 merged skills are K5's) in ``skill_id`` order, resolves each
one's exam through the shared ``subtask_id`` (the ONLY link between a
skill and its exam), dispatches one unit per (skill, exam) pair through
B5 ``run_units``, and tallies the outcomes.

Enumeration is serial and deterministic: store scan, stage-3-candidate
filter, pending filter, exam resolution. The outcomes come back in input
order, so the summary never depends on the dispatch interleaving; only
the APPEND ORDER of K4's ledgers does (they are written from several
threads; ``append_jsonl`` serialises the writes themselves).

A skill whose exam is missing is recorded in the summary under
``skipped_missing_exam`` and skipped, not crashed on: one orphaned skill
must not void the acceptance verdicts of every other skill in the run.
The pair gap is repairable by re-running stage 3 (it regenerates a
missing half), so the driver stays alive to process everything else.
Skills already in a terminal status (a resumed run) are never re-entered
(zero model calls on a completed stage).

One skill is one unit; its failure is recorded in the unit-failure
ledger (stage label ``STAGE_LABEL``) and never stops the other skills
(AGENTS.md section 6 rule 6). The disposition lives in the unit's
worker, not in K4:

* ModelOutputError (K3's repair budget exhausted): the skill is stored
  as discarded with ``discard_reason = "model_output_error"`` --
  terminal, resampling the same broken contract again would only burn
  tokens -- and only then given its ledger row (exactly one per skill; a
  crash between the two is completed by the next call);
* InfrastructureError (call-level retries exhausted): the skill stays
  pending exactly as K4 stored it, so a resume continues it; after every
  other unit ran and the summary (with ``infra_failed``) is written, the
  driver raises InfrastructureError, so the pipeline never moves on to
  stage 5 with pending skills missing from the bank;
* anything else is fatal to the batch (run_units' rule): no further unit
  starts, in-flight units run to completion, and the first failure by
  unit order is re-raised unwrapped.
"""

from typing import Any, Dict, List, Optional, Tuple

from expedistill.composites.acceptance import accept_skill
from expedistill.composites.skill_status import (
    discard_skill,
    stage3_candidates,
    tally_verdicts,
)
from expedistill.core.ids import make_exam_id
from expedistill.core.models import Exam, Skill
from expedistill.infra.parallel import run_units
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import write_json
from expedistill.methods.acceptance import STAGE4_DIR, SUMMARY_FILENAME

__all__ = ["MODEL_OUTPUT_DISCARD", "run"]


#: discard_reason of a skill whose K3 repair budget ran out.
MODEL_OUTPUT_DISCARD = "model_output_error"

#: Stage label of this driver's lines in the unit-failure ledger (the
#: live driver writes under its own label).
STAGE_LABEL = "stage4_acceptance"

#: Outcome kinds a unit's worker returns; aggregated in input order into the
#: summary (see run). A unit that reached a terminal status (accepted, or
#: discarded -- the model_output_error discard included) counts as processed.
_PROCESSED = "processed"
_INFRA_FAILED = "infra_failed"
_MISSING_EXAM = "missing_exam"


def run(ctx: Any) -> Dict[str, Any]:
    """Drive the K4 acceptance loop for every stored pending candidate.

    Returns and stores ``{"accepted", "discarded", "skipped_missing_exam",
    "infra_failed", "processed_this_call"}``. The first two are CUMULATIVE
    over all stage-3 candidates (tallied from the stored statuses, so a
    resumed or repeated call reports the run's verdicts, not just its own):
    the count that passed acceptance and a discard_reason -> count mapping.
    Then the pending candidates skipped for a missing exam, the ones left
    pending by an InfrastructureError, and how many candidates this call
    drove to a terminal status. Raises InfrastructureError after storing the
    summary when ``infra_failed`` is not empty (see module docstring).

    The units run through B5 ``run_units`` on ``ctx.run_settings.workers`` threads
    (default 1 = the old serial loop); outcomes arrive in input order, so
    the summary never depends on the dispatch interleaving.
    """
    store = ctx.store
    recorder = UnitFailureRecorder(ctx.run_dir)
    # Skills that already have their model_output_error ledger row.
    recorded = {row["unit_id"] for row in recorder.list_failures()
                if row.get("stage") == STAGE_LABEL
                and row.get("error_class") == ModelOutputError.__name__}

    def record_model_output(skill_id: str, message: str) -> None:
        if skill_id not in recorded:
            recorder.record(skill_id, STAGE_LABEL, ModelOutputError, message)
            recorded.add(skill_id)

    # Serial, deterministic enumeration (module docstring): store scan ->
    # stage-3-candidate filter -> pending filter -> exam resolution. A unit
    # is one (skill, exam) pair; a skill whose exam is missing carries None
    # and its worker only reports the skip. A terminal candidate is never a
    # unit, but a model_output_error discard whose ledger row a crash lost
    # gets the row completed here (the discard is the truth).
    candidates = stage3_candidates(store)
    units: List[Tuple[Skill, Optional[Exam]]] = []
    for skill in candidates:
        if skill.status != "pending":
            if (skill.status == "discarded"
                    and skill.discard_reason == MODEL_OUTPUT_DISCARD):
                # A crash between the discard and its ledger row: the
                # discard is the truth, the row is completed from it.
                record_model_output(
                    skill.skill_id, "discarded as model_output_error; the "
                    "original error message was lost to a crash before "
                    "this ledger row was written")
            continue
        exam_id = make_exam_id(skill.subtask_id)
        if not store.exists("exams", exam_id):
            units.append((skill, None))
            continue
        units.append((skill, store.load("exams", exam_id, Exam)))

    def accept_unit(
            unit: Tuple[Skill, Optional[Exam]]) -> Tuple[str, str]:
        """Run one unit: the per-skill body the serial loop always had (module
        docstring). Expected-failure dispositions live here; anything else
        escaping is fatal to the batch."""
        skill, exam = unit
        if exam is None:
            return _MISSING_EXAM, skill.skill_id
        try:
            result = accept_skill(
                store=store,
                skill=skill,
                exam=exam,
                weak_profile=ctx.config.weak_profile,
                answerer_handle=ctx.handle("answerer"),
                judge_handle=ctx.handle("judge"),
                rewriter_handle=ctx.handle("rewriter"),
                auditor_handle=ctx.handle("auditor"),
                retry_limit=ctx.config.retry_limit,
                usage=ctx.usage,
                run_dir=ctx.run_dir,
            )
        except ModelOutputError as exc:
            # Terminal state first, ledger row second: a crash in between
            # leaves a discard whose row the next call completes (above),
            # never a ledger row for a skill that a resume then accepts.
            result = discard_skill(store, skill, MODEL_OUTPUT_DISCARD)
            record_model_output(skill.skill_id, str(exc))
        except InfrastructureError as exc:
            recorder.record(skill.skill_id, STAGE_LABEL, exc, exc)
            return _INFRA_FAILED, skill.skill_id
        if result.status not in ("accepted", "discarded"):
            # accept_skill always ends in a terminal status; anything else is
            # a bug in the composite, not a stage outcome.
            raise CodeBugError(
                f"accept_skill left skill {skill.skill_id!r} in non-terminal "
                f"status {result.status!r}")
        return _PROCESSED, skill.skill_id

    outcomes = run_units(accept_unit, units, ctx.run_settings.workers)

    # Outcomes are in input order, so the summary is deterministic however
    # the units interleaved.
    skipped_missing_exam: List[str] = []
    infra_failed: List[str] = []
    processed = 0
    for kind, skill_id in outcomes:
        if kind == _MISSING_EXAM:
            skipped_missing_exam.append(skill_id)
        elif kind == _INFRA_FAILED:
            infra_failed.append(skill_id)
        else:
            processed += 1

    summary = tally_verdicts(store, candidates)
    summary.update({
        "skipped_missing_exam": skipped_missing_exam,
        "infra_failed": infra_failed,
        "processed_this_call": processed,
    })
    write_json(ctx.run_dir / STAGE4_DIR / SUMMARY_FILENAME, summary, indent=2)
    if infra_failed:
        raise InfrastructureError(
            f"stage 4: {len(infra_failed)} skill(s) failed on infrastructure "
            f"errors and are still pending ({', '.join(infra_failed)}); see "
            f"failed_units.jsonl and resume the run to retry them")
    return summary
