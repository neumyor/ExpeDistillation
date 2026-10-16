"""K6 attribution: attribute a failed round (G10) and apply its plan as
per-skill revisions (G11) (layer 5).

The attribution part of the former composites/live_acceptance.py, split
out mechanically in R0 (docs/decisions/execute_learn_repair_plan_20261016.md).
It reads the round store, the baseline and the frozen audit line through
the measurement part (composites/live_measurement.py) and commits
statuses through ``composites.skill_status.discard_skill``; the
discard-reason vocabulary comes from composites.live_verdicts (its
definition site is composites.decision).

R6 MIDDLE STATE (docs/decisions/execute_learn_repair_plan_20261016.md,
item R6): the target architecture moves G10's fact reporting to the
evidence family (methods/evidence/live_group.collect_group_evidence,
fact-only packages) and the per-skill decisions to the author family
(methods/author/write.revise_group). Those drivers are in place with
their own tests, but the stage still calls THIS module's
``attribute_group`` / ``apply_revisions`` pair, so the legacy
action-plan path stays byte-identical here until Wave 2 rewires
phase_learn: its attributions.jsonl row shape is pinned by the stage's
``_revisions_complete`` and by scripts/audit/audit_live_run.py, and the
byte-for-byte golden matrix (tests/fixtures/golden) exercises it. Its
budget-exhaustion discards also stay: the group-abandon on attribution
exhaustion moves to the decision module with the Wave 2 rewiring, and
the revision-exhaustion discard changes to "no change" (the C8 ruling)
on the same move -- methods/author/write.py already implements the new
behavior.

State is derived from disk only (decision section 2.4 -- no new state
objects):

* an attribution happened: an attributions.jsonl row for the (task,
  round) exists -- first judgment sticks, G10 is never asked twice;
* a revision was applied: the stored version moved past the attribution
  row's recorded version (version == from_version -> apply,
  from_version + 1 -> skip, anything else -> CodeBugError).

Persistence order is what makes resume equal an uninterrupted run:

* the attribution row is appended atomically BEFORE any revision acts
  on it (a plan that never landed is asked again -- see below);
* a successful revision is store.save'd immediately (the version number
  is the proof of completion), and so is every discard;
* the budget-exhausting ledger line is deliberately written AFTER the
  group's terminal discards (the discard is the truth, the line is its
  receipt; decision section 2.5).

Known non-equivalence (the standing K3/K4 exemption): a model call whose
result was not yet persisted is asked again on resume. That covers a G10
plan lost between the call and its ledger append, a G11 rewrite lost
between the call and its save, and -- because of the receipt-after-truth
order above -- the last attribution try of a run that crashed between
the exhausting failure and the completed discards.

The M2 de-rounding seam (plan section 3, D5/P5): the ``_``-prefixed
cores are round-agnostic -- every ledger path, round-store directory and
round label is an explicit parameter. The public functions are thin
shells that keep today's exact signatures and compute today's exact
paths; the shells are deleted in M4, when the phase drivers pass the
phase directory's paths in.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from expedistill.blocks.attribution import attribute_failure, revise_skill
from expedistill.blocks.author import skill_markers
from expedistill.blocks.view import final_status_word, \
    render_trajectory_view
from expedistill.composites import decision
from expedistill.composites.live_measurement import (
    ATTRIBUTIONS_LEDGER,
    ATTRIBUTION_FAILURES_LEDGER,
    INJECTION_AUDIT_NAME,
    REVISION_FAILURES_LEDGER,
    _check_round,
    _first_row_from,
    _ledger_path,
    _require_audit_row_from,
    _require_group,
    _rows_from,
    _trajectories_from,
    load_baseline,
    round_dir,
)
from expedistill.composites.live_verdicts import (
    LIVE_REVISION_DELETED,
    MODEL_OUTPUT_ERROR,
)
from expedistill.composites.skill_status import discard_skill
from expedistill.core.models import Skill
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import Store, append_jsonl
from expedistill.llm.call import ModelHandle

__all__ = ["FAILURE_STAGE", "apply_revisions", "attribute_group"]

#: Stage label for this module's failed_units.jsonl lines (an infra
#: failure of one skill's revision); the stage reuses it for group-level
#: units so one grep finds the whole stage's failures.
FAILURE_STAGE = "stage4_live"

#: Composite-level repair budget of one G10 call (decision section 2.3),
#: on top of call_model's own format repairs. The same budget as K4's
#: rewrites; each failed try is ledgered as it happens so a resumed run
#: continues the same budget.
_ATTRIBUTION_ATTEMPTS = 2

#: Composite-level repair budget of one G11 revision for one skill.
_REVISION_ATTEMPTS = 2

#: Hard char budgets on the G10 / G11 trajectory-view inputs (review fix
#: 2026-10-14, three-reviewer round; re-anchored 2026-10-15 by reviewer C's
#: envelope accounting). The views are otherwise unbounded: the pilot's
#: real mteb group rendered 562k chars across three views (~140k
#: est-tokens), and a context overflow comes back as 400/413 -- a
#: permanent provider refusal that is neither a repair-budget item nor an
#: InfrastructureError, so the first failed group with long trajectories
#: would hard-stop the whole stage (and re-hit it on every resume).
#: 140k chars targets the REQUEST ENVELOPE, not just the input: ~35k
#: est-tokens, calibrated with the endpoint's measured low-estimate
#: factor (~1.38) to ~48k real input tokens, plus the author/rewriter
#: roles' max_tokens=65536 lands the whole request at ~120k tokens --
#: within the largest request envelope this endpoint has ever accepted
#: (~119k) whether or not it counts max_tokens against the window.
#: Clipping keeps each view's head and tail (the tail is where terminal-
#: benchmark failures actually show) and marks the elided middle. The
#: values are pre-registered in the runbook and deliberately not
#: config keys: they are a fender, not an experimental variable (same
#: discipline as the ViewRules constants in the benchmark plugins).
_LIVE_VIEWS_BUDGET_CHARS = 140_000
_FAILURE_VIEW_BUDGET_CHARS = 80_000

#: Marker inserted where a view was clipped (both budget constants feed
#: _clip_view; the marker's own length is accounted for in the clip).
_ELIDED_NOTE = "\n[... {n} chars elided to fit the attribution view budget ...]\n"


def _clip_view(text: str, budget: int) -> str:
    """Clip one rendered trajectory view to ``budget`` chars, keeping the
    head (setup) and tail (where TB failures evidence themselves) and
    marking the elided middle. A no-op under budget."""
    if len(text) <= budget:
        return text
    head = max(1, int(budget * 0.6))
    note = _ELIDED_NOTE.format(n=len(text) - budget)
    tail = max(0, budget - head - len(note))
    return text[:head] + note + text[len(text) - tail:]


# ---------------------------------------------------------------------------
# Attribution (G10)
# ---------------------------------------------------------------------------

def _attribute_group(
    *,
    store: Store,
    task_id: str,
    round_: int,
    n_attempts: int,
    task_instruction: str,
    weak_profile: str,
    author_handle: ModelHandle,
    view_rules: Any = None,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
    attributions_path: Path,
    failures_path: Path,
    round_store_dir: Path,
    audit_path: Path,
) -> Optional[Dict[str, Any]]:
    """The round-agnostic core of ``attribute_group`` (module docstring):
    the G10 pipeline against explicit ledger / round-store / audit paths,
    ``round_`` a row label only."""
    stored_row = _first_row_from(attributions_path, task_id, round_)
    if stored_row is not None:
        return stored_row
    members = _require_group(store, task_id)

    failures = _rows_from(failures_path, task_id, round_)
    if decision.live_attribution_exhausted(failures, _ATTRIBUTION_ATTEMPTS):
        # Unreachable through the stage (the exhausted row follows the
        # discards, so exhaustion with pending members only happens when
        # a crash ate the discards' tail); completing the discards here
        # keeps this function total and idempotent.
        _discard_group(store, members, MODEL_OUTPUT_ERROR)
        return None

    baseline_trajectories, _ = load_baseline(store, task_id, n_attempts)
    baseline_summary = "\n".join(
        # The same display word the evidence driver renders, frozen
        # byte-for-byte: this text feeds the G10 prompt.
        f"attempt {traj.attempt_index}: "
        f"{final_status_word(traj.correctness)}"
        for traj in baseline_trajectories)
    round_trajs = _trajectories_from(round_store_dir, task_id, round_)
    per_view_budget = _LIVE_VIEWS_BUDGET_CHARS // max(1, len(round_trajs))
    live_views = "\n\n".join(
        _clip_view(render_trajectory_view(traj, view_rules),
                   per_view_budget)
        for traj in round_trajs)
    audit = _require_audit_row_from(audit_path, round_, task_id)

    for try_index in range(len(failures), _ATTRIBUTION_ATTEMPTS):
        try:
            plan = attribute_failure(
                members, task_instruction, baseline_summary, live_views,
                weak_profile, author_handle, usage=usage, run_dir=run_dir)
        except ModelOutputError as exc:
            if try_index + 1 >= _ATTRIBUTION_ATTEMPTS:
                # Terminal state first, ledger row second (module
                # docstring): a crash in between leaves a partially
                # discarded group whose budget rows say "one try left",
                # and the last try is simply asked again.
                _discard_group(store, members, MODEL_OUTPUT_ERROR)
                append_jsonl(
                    failures_path,
                    _failure_row(task_id, round_, try_index=try_index,
                                 skill_id=None, skill_version=None,
                                 exhausted=True, message=str(exc)))
                return None
            append_jsonl(
                failures_path,
                _failure_row(task_id, round_, try_index=try_index,
                             skill_id=None, skill_version=None,
                             exhausted=False, message=str(exc)))
            continue
        versions = {skill.skill_id: store.load(
                        "skills", skill.skill_id, Skill).version
                    for skill in members}
        row = {
            "schema": "live-attribution-v1",
            "task_id": task_id,
            "round": round_,
            "diagnosis": plan["diagnosis"],
            "skill_actions": [
                {"skill_id": entry["skill_id"],
                 "version": versions[entry["skill_id"]],
                 "action": entry["action"],
                 "problem": entry["problem"]}
                for entry in plan["skill_actions"]],
            "injection_block_sha256": audit["injection_block_sha256"],
        }
        append_jsonl(attributions_path, row)
        return row

    raise CodeBugError(  # pragma: no cover - the loop always returns
        f"attribution budget loop for {task_id!r} fell through")


def attribute_group(
    *,
    store: Store,
    task_id: str,
    round_: int,
    n_attempts: int,
    task_instruction: str,
    weak_profile: str,
    author_handle: ModelHandle,
    view_rules: Any = None,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Optional[Dict[str, Any]]:
    """Attribute one group's failed round (G10) and append its
    attributions.jsonl row; return the row, or None when the repair budget
    ran out (the group was terminally discarded as model_output_error).

    First judgment sticks: a stored row for the (task, round) is returned
    without any model call. The G10 inputs are composed here so every
    call sees the same bytes -- ``baseline_summary`` from the re-loaded
    stage-1 baseline (one status line per attempt), ``live_views`` from
    every trajectory the round stored for this task (successes included:
    the attributor must see what the skills did, not only that they
    failed), and the frozen audit row's injection sha. The skill versions
    in the row are read from the store at append time, never taken from
    the model (decision ruling A3) -- the plan is applied against exactly
    the versions recorded here. ``task_instruction`` is the rendered task
    prompt the student actually saw (the stage renders it; under TB the
    Task record's instruction is just the name, decision section 2.3).

    The repair budget is ``_ATTRIBUTION_ATTEMPTS`` calls. Each failed try
    is ledgered to attribution_failures.jsonl as it happens, so a
    resumed run continues the same budget. On exhaustion the group's
    terminal discards land FIRST (the discard is the truth a resume
    reads; the failure line is its receipt -- decision section 2.5),
    then the exhausting row with ``exhausted: true`` is appended.
    Infrastructure errors are not budget items: they propagate and the
    group stays pending for the stage to record and retry.

    Thin shell over ``_attribute_group`` (module docstring): checks the
    pinned round and computes today's exact paths; deleted in M4.
    """
    _check_round(round_)
    directory = round_dir(store, round_)
    return _attribute_group(
        store=store, task_id=task_id, round_=round_, n_attempts=n_attempts,
        task_instruction=task_instruction, weak_profile=weak_profile,
        author_handle=author_handle, view_rules=view_rules, usage=usage,
        run_dir=run_dir,
        attributions_path=_ledger_path(store, ATTRIBUTIONS_LEDGER),
        failures_path=_ledger_path(store, ATTRIBUTION_FAILURES_LEDGER),
        round_store_dir=directory,
        audit_path=directory / INJECTION_AUDIT_NAME)


def _discard_group(store: Store, members: List[Skill], reason: str) -> None:
    """Terminalize every member that is still pending, in skill_id order;
    already-terminal members keep their own (earlier) truth."""
    for skill in members:
        stored = store.load("skills", skill.skill_id, Skill)
        if stored.status == "pending":
            discard_skill(store, stored, reason)


def _failure_row(task_id: str, round_: int, *, try_index: int,
                 skill_id: Optional[str], skill_version: Optional[int],
                 exhausted: bool, message: str) -> Dict[str, Any]:
    """One attribution_failures / revision_failures line (K4's
    rewrite_failures shape): one row per failed repair try, the
    exhausting try marked, so the budget survives crashes and the audit
    sees every spent call."""
    return {
        "schema": "live-repair-failure-v1",
        "task_id": task_id,
        "round": round_,
        "skill_id": skill_id,
        "skill_version": skill_version,
        "try_index": try_index,
        "exhausted": exhausted,
        "message": " ".join(str(message).split()),
    }


# ---------------------------------------------------------------------------
# Applying a plan (G11)
# ---------------------------------------------------------------------------

def _apply_revisions(
    *,
    store: Store,
    task_id: str,
    round_: int,
    rewriter_handle: ModelHandle,
    weak_profile: str,
    view_rules: Any = None,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
    attributions_path: Path,
    revision_failures_path: Path,
    round_store_dir: Path,
) -> List[Dict[str, str]]:
    """The round-agnostic core of ``apply_revisions`` (module docstring):
    the G11 pipeline against explicit ledger / round-store paths,
    ``round_`` a row label only."""
    row = _first_row_from(attributions_path, task_id, round_)
    if row is None:
        raise CodeBugError(
            f"no attribution row for task {task_id!r} round {round_}: "
            "attribute the group before applying its plan")
    failed = [traj for traj in _trajectories_from(round_store_dir, task_id,
                                                  round_)
              if traj.correctness is False]
    per_view_budget = _FAILURE_VIEW_BUDGET_CHARS // max(1, len(failed))
    failure_view = "\n\n".join(
        _clip_view(render_trajectory_view(traj, view_rules),
                   per_view_budget)
        for traj in failed)
    recorder = UnitFailureRecorder(
        Path(run_dir) if run_dir is not None else store.run_dir)

    outcomes: List[Dict[str, str]] = []
    for entry in row["skill_actions"]:
        skill_id = entry["skill_id"]
        action = entry["action"]
        try:
            stored = store.load("skills", skill_id, Skill)
        except FileNotFoundError:
            raise CodeBugError(
                f"attribution row names skill {skill_id!r} but the store "
                "has no such skill") from None
        if stored.status != "pending":
            # Already terminal: a delete applied before a crash, or a
            # discard an interrupted exhaustion run landed. The stored
            # status is the truth; no plan entry rewrites it.
            outcomes.append({"skill_id": skill_id, "action": action,
                             "outcome": "skipped_terminal"})
            continue
        if action == "keep":
            outcomes.append({"skill_id": skill_id, "action": action,
                             "outcome": "kept"})
            continue
        if action == "delete":
            discard_skill(store, stored, LIVE_REVISION_DELETED)
            outcomes.append({"skill_id": skill_id, "action": action,
                             "outcome": "deleted"})
            continue
        # action == "revise"
        from_version = entry["version"]
        if stored.version == from_version + 1:
            outcomes.append({"skill_id": skill_id, "action": action,
                             "outcome": "already_revised"})
            continue
        if stored.version != from_version:
            raise CodeBugError(
                f"skill {skill_id!r} is at version {stored.version} but "
                f"the attribution row recorded {from_version}: neither "
                "the pre-revision nor the post-revision version -- the "
                "store drifted from the ledger")
        attribution_report = (f"{row['diagnosis']}\n\n"
                              f"This skill's problem: {entry['problem']}")
        outcome = _revise_one(
            store, task_id, round_, stored, from_version,
            attribution_report, failure_view, weak_profile,
            rewriter_handle, recorder, revision_failures_path,
            usage=usage, run_dir=run_dir)
        outcomes.append({"skill_id": skill_id, "action": action,
                         "outcome": outcome})
    return outcomes


def apply_revisions(
    *,
    store: Store,
    task_id: str,
    round_: int,
    rewriter_handle: ModelHandle,
    weak_profile: str,
    view_rules: Any = None,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> List[Dict[str, str]]:
    """Apply the group's stored attribution plan to its skills, one skill
    at a time; return the per-skill outcomes (``{"skill_id", "action",
    "outcome"}``, in plan order).

    The plan is read back from attributions.jsonl (first judgment sticks)
    -- never re-asked. keep leaves the skill alone; delete terminalizes it
    as ``live_revision_deleted`` immediately; revise runs G11 with the
    composite-level marker check (``skill_markers(new) ==
    skill_markers(old)``; an unmarked original is passed through, the
    K4/K5 precedent). A marker failure is a ModelOutputError inside the
    skill's own repair budget of ``_REVISION_ATTEMPTS`` tries, and a
    budget exhaustion discards ONLY that skill (model_output_error)
    while the group continues (decision section 2.5). A successful
    revision bumps the version, appends the rewrite_history entry and
    saves immediately -- the version number is the proof of completion.

    Replay guard per plan entry (decision section 2.4): version ==
    from_version means apply, == from_version + 1 means the revision
    already landed (skip -- the version number is the completion
    proof), anything else is a CodeBugError. Terminal skills are
    skipped with ``skipped_terminal`` -- a discard that an interrupted
    budget run landed is the truth, and no plan entry rewrites it.

    An InfrastructureError from one skill's revision is recorded to
    failed_units.jsonl (stage label ``FAILURE_STAGE``) and that skill
    stays pending for the resume while the rest of the group continues
    (decision section 2.5); the ``infra_failed`` outcome is what tells
    the stage to raise at its end.

    Thin shell over ``_apply_revisions`` (module docstring): checks the
    pinned round and computes today's exact paths; deleted in M4.
    """
    _check_round(round_)
    return _apply_revisions(
        store=store, task_id=task_id, round_=round_,
        rewriter_handle=rewriter_handle, weak_profile=weak_profile,
        view_rules=view_rules, usage=usage, run_dir=run_dir,
        attributions_path=_ledger_path(store, ATTRIBUTIONS_LEDGER),
        revision_failures_path=_ledger_path(store, REVISION_FAILURES_LEDGER),
        round_store_dir=round_dir(store, round_))


def _revise_one(
    store: Store,
    task_id: str,
    round_: int,
    stored: Skill,
    from_version: int,
    attribution_report: str,
    failure_view: str,
    weak_profile: str,
    rewriter_handle: ModelHandle,
    recorder: UnitFailureRecorder,
    revision_failures_path: Path,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> str:
    """Run one skill's G11 revision inside its own repair budget; return
    the outcome recorded for it."""
    prior = [row for row in _rows_from(revision_failures_path, task_id,
                                       round_)
             if row.get("skill_id") == stored.skill_id
             and row.get("skill_version") == from_version]
    for try_index in range(len(prior), _REVISION_ATTEMPTS):
        try:
            result = revise_skill(
                stored.content, attribution_report, failure_view,
                weak_profile, rewriter_handle, usage=usage, run_dir=run_dir)
            new_text = result["skill_text"]
            markers = skill_markers(stored.content)
            # The marker-preservation check is composite-level on purpose
            # (blocks may not import each other, and G11's block stays
            # single-purpose); and a structural failure must ride the
            # SAME ModelOutputError budget as a format failure -- the K5
            # lesson that a structural raise outside the budget's scope
            # kills whole units.
            if markers is not None and skill_markers(new_text) != markers:
                raise ModelOutputError(
                    "LIVE_SKILL_REVISION: the rewritten skill must keep "
                    f"the same four bracket sections as the original "
                    f"({' / '.join(markers)}), got "
                    f"{skill_markers(new_text)!r}")
        except InfrastructureError as exc:
            # Not a budget item: the work is valid, the service was not.
            # The skill stays pending; the resume re-enters this function
            # and the replay guard keeps the already-landed siblings.
            recorder.record(stored.skill_id, FAILURE_STAGE, exc, exc)
            return "infra_failed"
        except ModelOutputError as exc:
            if try_index + 1 >= _REVISION_ATTEMPTS:
                # Only this skill is discarded; the group continues
                # (decision section 2.5). Terminal state first, then the
                # closing ledger row, like the attribution path.
                discard_skill(store, stored, MODEL_OUTPUT_ERROR)
                append_jsonl(
                    revision_failures_path,
                    _failure_row(task_id, round_, try_index=try_index,
                                 skill_id=stored.skill_id,
                                 skill_version=from_version, exhausted=True,
                                 message=str(exc)))
                return "discarded_model_output_error"
            append_jsonl(
                revision_failures_path,
                _failure_row(task_id, round_, try_index=try_index,
                             skill_id=stored.skill_id,
                             skill_version=from_version, exhausted=False,
                             message=str(exc)))
            continue
        stored.rewrite_history.append({
            "content": stored.content,
            "error_category": result["failure_category"],
            "reason": result["revision_reason"],
        })
        stored.content = new_text
        stored.version = from_version + 1
        store.save(stored, "skills")
        return "revised"
    raise CodeBugError(  # pragma: no cover - the loop always returns
        f"revision budget loop for {stored.skill_id!r} fell through")
