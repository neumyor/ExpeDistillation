"""The acceptance report reader (layer 5, read-only): ONE report shape.

R1 (docs/decisions/execute_learn_repair_plan_20261016.md) dissolves G6:
the three former readers (``baseline_report`` / ``exam_report`` /
``live_report``, three different structures) are replaced by ONE reader
that maps a run directory's on-disk state onto the one acceptance report
shape -- ``core.contracts.AcceptanceReportRow``, one row per unit,
assembled in memory (the D3 ruling: no new persisted object, and this
module writes nothing anywhere; one test snapshots the run tree before
and after and proves not one byte moved).

The judgment itself lives in ``composites/verdicts.py`` (the run-verdict
rules, the status/cause vocabulary, ``summarize_unit``'s checks); this
module only reads the same ledgers, stores and round directories the
former readers read and hands each found run to the verdict core:

* ``kind="baseline"`` -- the baseline phase's bare arms over the run-level
  trajectory store and stage 1's accepted-shortfall record: one row per
  (task, role) (baseline phases split by role), ``runs`` one entry per expected
  unit (a stored trajectory judged by its benchmark's default named rule
  through ``verdict_from_run``, or an invalid
  ``missing`` entry for a unit whose infra retries were exhausted; an
  accepted hole is no run at all);
* ``kind="exam"`` -- the exam phase over the shared exams/attempts stores:
  one control row per exam (the judged bare answer) plus one row per
  (skill, version) the exam measurement touched -- judged with-skill
  attempts as ``runs``, a leaked version as ``not_testable``/``leak`` with
  its measurement void, a candidate whose exam never landed, or whose
  answer's judge budget died, as ``not_testable``/``exam_generation_failed``
  (the exam pipeline's terminal failure), and a merely unanswered sheet
  as the ``not_testable``/``zero_execution`` visibility fact;
* ``kind="live"`` -- the live phase over the verdicts / injection-audit
  ledgers and the round stores: one row per (group, round); the frozen
  verdict row's zero-execution terminals map onto the status/cause
  vocabulary (weak_already_knows -> not_run_control_perfect, overflow /
  undetermined -> not_testable), a judged row is normal with its counts,
  and a dispatched-but-unjudged group reports the runs it has on disk.

What deliberately moved OUT of the report (the former readers' fields the
decision module now reads elsewhere -- boundaries doc §2.4: acceptance
judges absolutely, decision compares):

* baseline ``F`` (the task-select hits): decision applies its own named
  task_select rule to the weak rows;
* live ``better`` and the baseline counts slot (C7): comparison is
  decision's parameter; the frozen verdicts ledger keeps the judged row
  for its resume protocol;
* exam ``attempt`` (the P7b rewrite source) and ``rewrite_exhausted``:
  evidence and round accounting -- the runs' ``basis`` carries the judge
  reason and each run's key is the attempt id, so the evidence side
  loads the attempt itself.

Private-name seams kept for the merge: ``live_measurement``'s and
``acceptance_skill_status``'s helpers are imported under their current
private names (W1-B / W1-C own those files in this wave); the leak lookup
is public since R1 (``acceptance_leak.find_leak_verdict``).
"""

from typing import Any, Dict, List, Optional

from expedistill.composites import verdicts
from expedistill.composites import exam_grading
from expedistill.composites import live_measurement as lm
from expedistill.composites import shortfall
from expedistill.composites.acceptance_leak import (
    LEAK_VERDICTS_LEDGER,
    find_leak_verdict,
)
from expedistill.composites.acceptance_skill_status import (
    exam_attempts,
    find_bare,
    _skill_records,
)
from expedistill.composites.skill_status import stage3_candidates
from expedistill.core import ids
from expedistill.core.contracts import AcceptanceReportRow, RunRecord
from expedistill.core.models import Trajectory
from expedistill.infra.reliability import ConfigError
from expedistill.infra.store import Store, read_jsonl

__all__ = ["acceptance_report"]

#: The reader's measurement kinds (the three universes of the former
#: readers; the ROW shape is the same for all three).
_KINDS = ("baseline", "exam", "live")

#: The baseline phase's two bare arms (role split, core/contracts.py).
_ROLES = ("strong", "weak")


def run_record_from_trajectory(traj: Trajectory) -> RunRecord:
    """A stored Trajectory as the RunRecord acceptance judges (R2b: the
    ``verdict_from_trajectory`` bridge is gone; the record's interface
    view is the same facts -- the scorer verdict plus the run metrics the
    plugin recorded in ``meta``)."""
    return RunRecord(
        unit_id=traj.trajectory_id,
        task_ref=traj.task_id,
        skills_injected=traj.skills_injected,
        skills_block="",
        model_role=traj.model_role,
        model_name=traj.model_name,
        sample_index=traj.attempt_index,
        dispatch_index=0,
        correctness=traj.correctness,
        metrics=dict(traj.meta or {}),
        aux={},
        artifacts={},
    )


def run_record_from_attempt(attempt) -> RunRecord:
    """A judged exam Attempt as the RunRecord acceptance judges (R2b: the
    ``verdict_from_attempt`` bridge is gone; an exam has no scorer, so
    ``correctness`` is None by the frozen contract and the G5 judge's
    verdict travels ``aux`` exactly where the ``exam_judge`` rule reads
    it)."""
    return RunRecord(
        unit_id=attempt.attempt_id,
        task_ref=attempt.exam_id,
        skills_injected=([{"skill_id": attempt.skill_id,
                            "version": attempt.skill_version}]
                         if attempt.skill_id is not None else None),
        skills_block="",
        model_role="weak",
        model_name="",
        sample_index=ids.parse_attempt_id(attempt.attempt_id)[1],
        dispatch_index=attempt.retry_round,
        correctness=None,
        metrics={},
        aux={"judge_verdict": attempt.judge_verdict,
             "judge_reason": attempt.judge_reason},
        artifacts={},
    )


def acceptance_report(store: Store, *, kind: str, benchmark: str,
                      phase_label: str,
                      n_attempts_strong: Optional[int] = None,
                      n_attempts_weak: Optional[int] = None,
                      round_: Optional[int] = None
                      ) -> List[AcceptanceReportRow]:
    """The one acceptance report of a run directory's on-disk state:
    ``AcceptanceReportRow`` rows in a deterministic order (task order,
    then role / version order), read-only.

    ``kind`` picks the measurement universe (module docstring);
    ``benchmark`` and ``phase_label`` feed the unit ids (the frozen
    construction rule in composites.verdicts). Kind-specific inputs:

    * ``baseline``: ``n_attempts_strong`` / ``n_attempts_weak``, the
      frozen per-role sampling depths (the expected unit sets -- absent
      units are reported as invalid runs, never silently dropped from
      the denominator);
    * ``exam``: none;
    * ``live``: ``round_``, the round whose groups the report covers.
    """
    if kind == "baseline":
        if n_attempts_strong is None or n_attempts_weak is None:
            raise ConfigError(
                "kind='baseline' needs n_attempts_strong and "
                "n_attempts_weak (the frozen per-role sampling depths)")
        return _baseline_rows(store, benchmark=benchmark,
                              phase_label=phase_label,
                              n_attempts_strong=n_attempts_strong,
                              n_attempts_weak=n_attempts_weak)
    if kind == "exam":
        return _exam_rows(store, benchmark=benchmark,
                          phase_label=phase_label)
    if kind == "live":
        if round_ is None:
            raise ConfigError(
                "kind='live' needs round_ (the round whose groups the "
                "report covers)")
        return _live_rows(store, benchmark=benchmark,
                          phase_label=phase_label, round_=round_)
    raise ConfigError(
        f"unknown report kind {kind!r}; known: {', '.join(_KINDS)}")


# ---------------------------------------------------------------------------
# baseline: the baseline phase's trajectory store + accepted shortfall
# ---------------------------------------------------------------------------

def _baseline_rows(store: Store, *, benchmark: str, phase_label: str,
                   n_attempts_strong: int, n_attempts_weak: int
                   ) -> List[AcceptanceReportRow]:
    """One row per (task, role) of the baseline phase (module docstring)."""
    accepted = set(shortfall.stage1_accepted_unit_ids(store.run_dir))
    trajectories = [store.load("trajectories", trajectory_id, Trajectory)
                    for trajectory_id in store.ids("trajectories")]
    universe = {traj.task_id for traj in trajectories}
    for unit_id in accepted:
        task_id, _, _ = ids.parse_trajectory_id(unit_id)
        universe.add(task_id)

    depths = {"strong": n_attempts_strong, "weak": n_attempts_weak}
    rows: List[AcceptanceReportRow] = []
    for task_id in sorted(universe):
        for role in _ROLES:
            rows.append(_baseline_unit_row(
                trajectories, task_id, role,
                n_attempts=depths[role], accepted=accepted,
                benchmark=benchmark, phase_label=phase_label))
    return rows


def _baseline_unit_row(trajectories: List[Trajectory], task_id: str,
                       role: str, *, n_attempts: int, accepted: set,
                       benchmark: str, phase_label: str
                       ) -> AcceptanceReportRow:
    """One (task, role) bare unit's row: one run per expected unit.

    A stored trajectory is judged by its benchmark's default named rule
    (R2b: through ``verdict_from_run``); an accepted hole is
    no run at all; a unit that is neither was dispatched but never
    completed (its infra retries were exhausted) and is an invalid run --
    the honest shape of "not testable", never a silently shrunken
    denominator. Zero valid runs means one of three things: every unit
    was a hole (all_holes), every stored run came back without a scorer
    verdict (all_holes -- the zero-measurement cause, the same state the
    live reader reports through the live_undetermined terminal), or at
    least one unit never completed (invalid_redispatch_exhausted -- the
    dispatch budget died)."""
    of_role = {traj.trajectory_id: traj
               for traj in trajectories
               if traj.task_id == task_id and traj.model_role == role}
    rule_name = verdicts.default_verdict_rule(benchmark)
    runs: List[Dict[str, Any]] = []
    missing = 0
    for attempt in range(n_attempts):
        unit_trajectory_id = ids.make_trajectory_id(task_id, role, attempt)
        if unit_trajectory_id in accepted:
            continue
        traj = of_role.get(unit_trajectory_id)
        if traj is not None:
            verdict, basis = verdicts.verdict_from_run(
                run_record_from_trajectory(traj), rule_name=rule_name)
        else:
            missing += 1
            verdict, basis = (verdicts.RUN_INVALID,
                              "missing: dispatched but never completed "
                              "(infra retries exhausted)")
        runs.append({"run": unit_trajectory_id,
                     "verdict": verdict, "basis": basis})

    if any(run["verdict"] != verdicts.RUN_INVALID for run in runs):
        status, cause = verdicts.STATUS_NORMAL, ""
    elif not runs or missing == 0:
        # Either nothing was executed at all, or every unit came back
        # stored with no scorer verdict: no measurement either way.
        status, cause = (verdicts.STATUS_NOT_TESTABLE,
                         verdicts.CAUSE_ALL_HOLES)
    else:
        status, cause = (verdicts.STATUS_NOT_TESTABLE,
                         verdicts.CAUSE_INVALID_REDISPATCH_EXHAUSTED)
    return verdicts.summarize_unit(
        unit_id=verdicts.make_unit_id(
            phase_label=phase_label, benchmark=benchmark,
            task_ref=task_id, role=role, skills_injected=None),
        phase_label=phase_label, role=role, injected_skills=None,
        runs=runs, status=status, cause=cause,
        artifacts={"trajectory_ids": [run["run"] for run in runs]})


# ---------------------------------------------------------------------------
# exam: the shared exams/attempts stores + leak verdicts
# ---------------------------------------------------------------------------

def _exam_rows(store: Store, *, benchmark: str, phase_label: str
               ) -> List[AcceptanceReportRow]:
    """One control row per exam plus one row per measured (skill,
    version), in skill_id then version order (module docstring)."""
    rows: List[AcceptanceReportRow] = []
    for skill in stage3_candidates(store):
        exam_id = ids.make_exam_id(skill.subtask_id)
        rows.extend(_exam_unit_rows(store, skill, exam_id,
                                    benchmark=benchmark,
                                    phase_label=phase_label))
    return rows


def _exam_unit_rows(store: Store, skill, exam_id: str, *, benchmark: str,
                    phase_label: str) -> List[AcceptanceReportRow]:
    """One candidate's exam rows: the bare control plus one row per
    (skill, version) the exam measurement touched (judged attempts, a
    leak verdict, or the stored version -- the stored version always has
    a row so the decision side can see "not yet answered" as
    not_testable/zero_execution instead of a missing unit)."""
    if not store.exists("exams", exam_id):
        # The exam never landed (its generation budget died): the unit
        # cannot be tested at all.
        return [verdicts.summarize_unit(
            unit_id=verdicts.make_unit_id(
                phase_label=phase_label, benchmark=benchmark,
                task_ref=exam_id, role="weak",
                skills_injected=[{"skill_id": skill.skill_id,
                                  "version": skill.version}]),
            phase_label=phase_label, role="weak",
            injected_skills=[{"skill_id": skill.skill_id,
                              "version": skill.version}],
            runs=[], status=verdicts.STATUS_NOT_TESTABLE,
            cause=verdicts.CAUSE_EXAM_GENERATION_FAILED,
            artifacts={"skill_id": skill.skill_id})]

    attempts = exam_attempts(store, exam_id)
    rows = [_exam_bare_row(attempts, exam_id, benchmark=benchmark,
                          phase_label=phase_label)]

    with_skill = [attempt for attempt in attempts
                  if attempt.purpose == "acceptance"
                  and attempt.skill_id == skill.skill_id
                  and attempt.skill_version is not None]
    versions = {attempt.skill_version for attempt in with_skill}
    for record in _skill_records(store, LEAK_VERDICTS_LEDGER,
                                 skill.skill_id):
        if record.get("skill_version") is not None:
            versions.add(record["skill_version"])
    versions.add(skill.version)

    for version in sorted(versions):
        injected = [{"skill_id": skill.skill_id, "version": version}]
        leak = find_leak_verdict(store, skill.skill_id, version)
        if leak is not None and leak.get("leaked"):
            # A leaked version's measurement is void: the round was
            # consumed without a with-skill attempt for it.
            runs: List[Dict[str, Any]] = []
            status, cause = (verdicts.STATUS_NOT_TESTABLE,
                             verdicts.CAUSE_LEAK)
        else:
            judged = sorted((attempt for attempt in with_skill
                             if attempt.skill_version == version
                             and attempt.judge_verdict is not None),
                            key=_serial_of)
            runs = []
            for attempt in judged:
                verdict, basis = verdicts.verdict_from_run(
                    run_record_from_attempt(attempt),
                    rule_name=verdicts.default_verdict_rule("exam"))
                runs.append({"run": attempt.attempt_id,
                             "verdict": verdict, "basis": basis})
            if runs:
                status, cause = verdicts.STATUS_NORMAL, ""
            elif any(exam_grading.judge_budget_exhausted(attempt)
                     for attempt in with_skill
                     if attempt.skill_version == version):
                # The judge's repair budget died: the exam pipeline
                # terminally failed to produce this run's measurement
                # (decision deletes it as model_output_error).
                status, cause = (verdicts.STATUS_NOT_TESTABLE,
                                 verdicts.CAUSE_EXAM_GENERATION_FAILED)
            else:
                # No completed run yet (nothing answered, or a judge
                # still inside its budget): a visibility fact.
                status, cause = (verdicts.STATUS_NOT_TESTABLE,
                                 verdicts.CAUSE_ZERO_EXECUTION)
        rows.append(verdicts.summarize_unit(
            unit_id=verdicts.make_unit_id(
                phase_label=phase_label, benchmark=benchmark,
                task_ref=exam_id, role="weak", skills_injected=injected),
            phase_label=phase_label, role="weak", injected_skills=injected,
            runs=runs, status=status, cause=cause,
            artifacts={"exam_id": exam_id,
                       "attempt_ids": [run["run"] for run in runs]}))
    return rows


def _serial_of(attempt) -> int:
    """The attempt's numeric serial (attempt order within the exam)."""
    return ids.parse_attempt_id(attempt.attempt_id)[1]


def _exam_bare_row(attempts, exam_id: str, *, benchmark: str,
                   phase_label: str) -> AcceptanceReportRow:
    """The exam's bare control row: the judged bare answer as its one
    run. No completed run yet is the zero_execution visibility fact,
    except a bare answer whose judge budget died -- that is the exam
    pipeline's terminal failure (exam_generation_failed), never
    in-flight work."""
    bare = find_bare(attempts)
    runs: List[Dict[str, Any]] = []
    if bare is not None:
        verdict, basis = verdicts.verdict_from_run(
            run_record_from_attempt(bare),
            rule_name=verdicts.default_verdict_rule("exam"))
        runs.append({"run": bare.attempt_id, "verdict": verdict,
                     "basis": basis})
    if runs:
        status, cause = verdicts.STATUS_NORMAL, ""
    elif any(exam_grading.judge_budget_exhausted(attempt)
             for attempt in attempts
             if attempt.purpose == "bare" and attempt.skill_id is None):
        status, cause = (verdicts.STATUS_NOT_TESTABLE,
                         verdicts.CAUSE_EXAM_GENERATION_FAILED)
    else:
        status, cause = (verdicts.STATUS_NOT_TESTABLE,
                         verdicts.CAUSE_ZERO_EXECUTION)
    return verdicts.summarize_unit(
        unit_id=verdicts.make_unit_id(
            phase_label=phase_label, benchmark=benchmark,
            task_ref=exam_id, role="weak", skills_injected=None),
        phase_label=phase_label, role="weak", injected_skills=None,
        runs=runs, status=status, cause=cause,
        artifacts={"exam_id": exam_id,
                   "attempt_ids": [run["run"] for run in runs]})


# ---------------------------------------------------------------------------
# live: the verdicts / injection-audit ledgers + the round store
# ---------------------------------------------------------------------------

def _live_rows(store: Store, *, benchmark: str, phase_label: str,
               round_: int) -> List[AcceptanceReportRow]:
    """One row per group the round touched, in task_id order (module
    docstring). The universe is every group with a frozen injection-audit
    row (dispatched) or a verdicts row (judged, including the
    zero-execution terminals) -- the same universe the former live
    reader derived."""
    verdicts_path = lm.ledger_path(store, lm.VERDICTS_LEDGER)
    audit_path = lm.round_dir(store, round_) / lm.INJECTION_AUDIT_NAME
    directory = lm.round_dir(store, round_)

    universe = {row["task_id"] for row in read_jsonl(verdicts_path)
                if row.get("round") == round_}
    universe |= {row["task_id"] for row in read_jsonl(audit_path)}

    pending = lm.pending_groups(store)
    rows: List[AcceptanceReportRow] = []
    for task_id in sorted(universe):
        audit = lm._audit_row_from(audit_path, round_, task_id)
        if audit is not None:
            injected = [{"skill_id": member["skill_id"],
                         "version": member["version"]}
                        for member in audit["members"]]
        else:
            # Never dispatched (a zero-execution terminal): the group
            # as it stands, pending members only.
            injected = [{"skill_id": skill.skill_id,
                         "version": skill.version}
                        for skill in pending.get(task_id, [])]

        row = lm.first_row_from(verdicts_path, task_id, round_)
        terminal = row.get("verdict") if row is not None else None
        runs: List[Dict[str, Any]] = []
        if terminal is not None:
            status, cause = verdicts.status_cause_for_terminal(terminal)
        else:
            if directory.exists():
                rule_name = verdicts.default_verdict_rule(benchmark)
                for traj in lm._trajectories_from(directory, task_id,
                                                  round_):
                    verdict, basis = verdicts.verdict_from_run(
                        run_record_from_trajectory(traj),
                        rule_name=rule_name)
                    runs.append({"run": traj.trajectory_id,
                                 "verdict": verdict, "basis": basis})
            if any(run["verdict"] != verdicts.RUN_INVALID for run in runs):
                status, cause = verdicts.STATUS_NORMAL, ""
            else:
                status, cause = (verdicts.STATUS_NOT_TESTABLE,
                                 verdicts.CAUSE_ZERO_EXECUTION)
        rows.append(verdicts.summarize_unit(
            unit_id=verdicts.make_unit_id(
                phase_label=phase_label, benchmark=benchmark,
                task_ref=task_id, role="weak", skills_injected=injected),
            phase_label=phase_label, role="weak", injected_skills=injected,
            runs=runs, status=status, cause=cause,
            artifacts={"round_dir": str(directory),
                       "trajectory_ids": [run["run"] for run in runs]}))
    return rows
