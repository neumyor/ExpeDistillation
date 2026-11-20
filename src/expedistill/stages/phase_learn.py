"""The learn phase driver (layer 7): the same five steps in every learn phase.

The input is the immediately preceding execute phase, a baseline or a
practice (a test phase never feeds learning): its weak model's failed
trials. The bank the phase starts from is the previous learn phase's
artifact (before the first learn phase, the run-level bank).

1. **Diagnose** every failed trial (``methods/diagnose``): the reason, the
   failing step, the scope and the two notes, one row per trial in
   ``findings.jsonl``. A diagnosis whose reply stays unusable is recorded in
   ``failed_units.jsonl`` and retried by the next run (below).
2. **Locate** (``composites/locate``): cut every failed trajectory (and, in
   the first learn phase, the strong baseline trajectories the
   ``trajectory_select`` rule keeps), then type every new segment into the
   run's subtask type table. The segment holding the failing step is the
   failure's segment, its type the failure's type.
3. **Route the evidence** (``composites/evidence``, pure): gap, trigger,
   content or none per located failure, in ``evidence.jsonl``.
4. **Evolve the profile, then the playbook** (``methods/evolve_text``); the
   playbook's facts are the pack's baseline facts in the first learn phase
   and the trigger-evidence counts afterwards.
5. **Evolve the bank** (``methods/cards``, ``methods/revise``): a new card
   per gap type, a content then a trigger rewrite per card with that
   evidence, the delete rule in place of a second content rewrite in a
   row. Receipts in ``outcomes.jsonl``.

A unit whose model output stays unusable after its in-call repairs (a
diagnosis, a cut, a typed segment, the profile, the playbook, a card or a
rewrite) is recorded in ``failed_units.jsonl`` and the phase stops at the
end of that unit's group with ResumableShortfall (exit code 3), so no later
group reads a partial input; the next run retries exactly those units. A
unit that has failed in ``MODEL_RUNS`` runs is given up: a trial without
diagnosis gives neither notes nor evidence, a segment stays untyped, a
card or rewrite gets its ``write_failed`` / ``revision_failed`` receipt, and
the profile or playbook keeps the previous learn phase's text.

Three ablation switches of ``review`` leave the flow as it is and change one
step. ``cards: false`` skips step 5 (and the strong baseline cuts that only
step 5 reads), so the bank is published as it was; each gap type gets a
``skipped_cards_off`` receipt. ``revise_scope`` sets the
out-of-scope evidence aside before step 5 plans its units, with a
``skipped_out_of_scope`` receipt. ``evolve_profile: false`` publishes an
empty profile in step 4 without a model call. The author parameter
``trigger_view: prefix`` changes how step 5 writes a new card
(``methods/cards/writer.write_card_prefix``).

Steps without a data dependency run in one pool (``--workers``), the groups
one after the other:

    A  diagnoses || failed-trajectory cuts || strong baseline cuts (first)
    B  typing (chunk by chunk) || the profile
    C  evidence routing
    D  the playbook
    E  one unit per card or gap type

Every ledger is committed in a fixed order (sheet id, subtask id, type id,
skill id), so a run with one worker and a run with eight write the same
bytes. Resume equals an uninterrupted run: a FINISHED phase (its summary
carries the "bank" entry, written last) is skipped whole; an interrupted
one reads back its findings, segments, types, texts and receipts and asks
only the model calls whose results were never persisted. Infrastructure
failures are recorded per unit and stop the phase at the end of their
group (in group E after the summary, before the bank artifact); a resume
retries exactly those units.
"""

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from expedistill.benchmarks.registry import get_plugin
from expedistill.composites import evidence as routing
from expedistill.composites import evolving_text, locate, phase_report
from expedistill.composites import learn_ledger as ledger
from expedistill.composites.phase_bank import (
    load_phase_bank,
    phase_bank_dir,
    write_phase_bank,
)
from expedistill.composites.provenance import (
    PROVENANCE_NAME,
    check_provenance,
    find_provenance,
    record_version,
)
from expedistill.composites.sheet_facts import SheetFacts, sheet_facts
from expedistill.composites.trace import (
    WRITER_BEFORE_STEPS,
    render_windows,
    task_instruction,
    trace_at,
    trace_of_trajectory,
)
from expedistill.core.answer_sheet import AnswerSheet, PhaseReport
from expedistill.core.config import (
    PHASE_ROLE_BASELINE,
    PHASE_ROLE_PRACTICE,
    ExecutePhaseSpec,
    LearnPhaseSpec,
    PhaseInstance,
    PhaseResult,
    phase_label,
)
from expedistill.core.contracts import TaskListRow
from expedistill.core.ids import make_skill_id, parse_trajectory_id
from expedistill.core.models import EvolvingText, Skill, Subtask, Trajectory
from expedistill.core.trace import Provenance, TraceRef
from expedistill.infra.parallel import run_units
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    ModelOutputError,
    ResumableShortfall,
    UnitFailureRecorder,
)
from expedistill.infra.store import (
    append_jsonl, read_json, read_jsonl, write_json, write_jsonl)
from expedistill.methods.registry import resolve_params
from expedistill.methods.cards.material import (
    collect_material,
    prefix_material,
)
from expedistill.methods.cards.writer import write_card, write_card_prefix
from expedistill.methods.diagnose import Finding, diagnose_trial
from expedistill.methods.evolve_text import evolve_text
from expedistill.methods.revise import (
    apply_revision,
    diagnosis_text,
    revise_content,
    revise_trigger,
)

__all__ = ["EVIDENCE_NAME", "FINDINGS_NAME", "SUMMARY_NAME", "run"]

#: The phase's summary file (the returned PhaseResult in JSON form).
SUMMARY_NAME = "learn_summary.json"
#: One row per diagnosed trial (``methods.diagnose.Finding``).
FINDINGS_NAME = "findings.jsonl"
#: One row per located failure (``composites.evidence.Evidence``).
EVIDENCE_NAME = "evidence.jsonl"

#: How many steps after the failing step a content rewrite reads.
AFTER_FAILURE_STEPS = 5

#: The ``failed_units.jsonl`` error class of a diagnosed trial that could
#: not be located (no segment or no type for its failing step).
UNLOCATED = "Unlocated"

#: The receipt of a gap whose type's one card id is taken by a discarded
#: card: no new card is written for it.
SKIPPED_DISCARDED = "skipped_discarded"

#: The receipt of evidence a ``revise_scope`` ablation does not rewrite.
SKIPPED_OUT_OF_SCOPE = "skipped_out_of_scope"

#: The receipt of a gap type in a phase that writes no cards
#: (``cards: false``).
SKIPPED_CARDS_OFF = "skipped_cards_off"

#: The reason suffix of a prefix-authoring card whose trigger side could
#: not come from a prefix: every material segment starts at step 0.
PREFIX_UNAVAILABLE = "prefix_unavailable_segments_start_at_step_0"

#: How many runs of the phase a unit whose model output fails gets before
#: it is given up (one failure record per run).
MODEL_RUNS = 3


def run(ctx: Any, phase: PhaseInstance) -> PhaseResult:
    """Drive one learn phase; return its summary as a PhaseResult."""
    if not isinstance(phase.spec, LearnPhaseSpec):
        raise CodeBugError(
            f"phase_learn received phase {phase.label!r} whose spec is a "
            f"{type(phase.spec).__name__}, not a LearnPhaseSpec")
    if phase.label != phase_label(phase.index, phase.name):
        raise CodeBugError(
            f"phase_learn received label {phase.label!r} for sequence "
            f"entry {phase.index} ({phase.name!r}); the frozen derivation "
            f"is {phase_label(phase.index, phase.name)!r}")

    phase_dir = phase_bank_dir(ctx.run_dir, phase.label)
    finished = _finished_summary(phase_dir)
    if finished is not None:
        return PhaseResult(label=phase.label, summary=finished)

    names, specs = _sequence(ctx.config, phase)
    phase_dir.mkdir(parents=True, exist_ok=True)
    learn = _Learn(ctx, phase, phase_dir, names, specs)
    summary = learn.run()
    return PhaseResult(label=phase.label, summary=summary)


class _Learn:
    """One learn phase's run: the inputs read once, then the five groups."""

    def __init__(self, ctx: Any, phase: PhaseInstance, phase_dir: Path,
                 names: List[str], specs: dict) -> None:
        self.ctx = ctx
        self.phase = phase
        self.phase_dir = phase_dir
        self.store = ctx.store
        self.workers = int(ctx.run_settings.workers)
        self.benchmark = ctx.config.benchmark
        plugin = get_plugin(self.benchmark)
        self.pack = plugin.prompt_pack
        self.view_rules = plugin.view_rules
        self.review = resolve_params("review", phase.spec.review)
        self.author = resolve_params("author", phase.spec.author)
        self.recorder = UnitFailureRecorder(ctx.run_dir)
        self.lock = threading.Lock()
        self._instructions: Dict[str, str] = {}
        #: Model-output failures per unit before this run, and the units
        #: that failed in this run with runs left.
        self.prior_failures = locate.model_failure_counts(ctx.run_dir,
                                                          phase.label)
        self.retry: Set[str] = set()

        self.names = names
        self.specs = specs
        source = _input_position(names, specs, phase.index)
        self.source_label = phase_label(source, names[source])
        self.baseline_label = _baseline_label(names, specs)
        self.previous_learn = evolving_text.previous_learn_label(
            names, specs, phase.index)
        self.first = self.previous_learn is None
        self.report = _load_report(ctx, names, source)
        self.bank = _previous_bank(ctx, self.previous_learn)
        self.failed = sorted(
            (sheet for sheet in self.report.sheets
             if sheet.phase_label == self.source_label and _is_failed(sheet)
             and sheet.execution.model_role == "weak"),
            key=lambda sheet: sheet.sheet_id)
        self.sheets = {sheet.sheet_id: sheet for sheet in self.failed}
        self.facts: Dict[str, SheetFacts] = {
            sheet.sheet_id: sheet_facts(sheet, self._stored_skill)
            for sheet in self.failed}

    # -- small helpers -----------------------------------------------------

    def _stored_skill(self, skill_id: str) -> Optional[Skill]:
        if not self.store.exists("skills", skill_id):
            return None
        return self.store.load("skills", skill_id, Skill)

    def instruction_for(self, task_id: str) -> str:
        with self.lock:
            if task_id not in self._instructions:
                self._instructions[task_id] = task_instruction(
                    self.benchmark, task_id)
            return self._instructions[task_id]

    def _given_up(self) -> Set[str]:
        """Unit ids whose model output failed in ``MODEL_RUNS`` runs."""
        counts = locate.model_failure_counts(self.ctx.run_dir,
                                             self.phase.label)
        return {unit_id for unit_id, count in counts.items()
                if count >= MODEL_RUNS}

    def _last_run(self, unit_id: str) -> bool:
        """True when a failure of ``unit_id`` in this run gives it up."""
        return self.prior_failures[unit_id] + 1 >= MODEL_RUNS

    def _record(self, unit_id: str, exc: Any) -> None:
        self.recorder.record(unit_id, self.phase.label, exc, exc)

    def _model_failed(self, unit_id: str, exc: Any) -> bool:
        """Record a model-output failure of ``unit_id``; True when the unit
        is given up, otherwise it is retried by the next run."""
        self._record(unit_id, exc)
        if self._last_run(unit_id):
            return True
        with self.lock:
            self.retry.add(unit_id)
        return False

    def _stop_for_retries(self, step: str) -> None:
        """End the phase at a group boundary while units wait for a retry."""
        if self.retry:
            raise ResumableShortfall(
                f"{self.phase.label}: {len(self.retry)} unit(s) of {step} "
                f"failed on model output ({', '.join(sorted(self.retry))}); "
                f"each gets up to {MODEL_RUNS} runs -- see failed_units.jsonl, "
                "then re-run the same command to retry them")

    # -- the phase -----------------------------------------------------------

    def run(self) -> Dict[str, Any]:
        findings, undiagnosed = self.group_a()
        self._stop_for_retries("diagnosis and cutting")
        profile = self.group_b(findings)
        self._stop_for_retries("typing and the profile")
        evidence, unlocated = self.group_c(findings)
        playbook = self.group_d(findings, evidence, profile)
        self._stop_for_retries("the playbook")
        infra_failed = self.group_e(evidence, findings, profile, playbook)
        summary = self.summary(findings, undiagnosed, unlocated, evidence,
                               profile, playbook)
        if infra_failed:
            summary["infra_failed"] = sorted(infra_failed)
            write_json(self.phase_dir / SUMMARY_NAME, summary, indent=2)
            raise InfrastructureError(
                f"{self.phase.label}: {len(infra_failed)} card unit(s) "
                f"still pending on infrastructure errors "
                f"({', '.join(sorted(infra_failed))}); see "
                "failed_units.jsonl, then resume the run to retry them")
        if self.retry:
            summary["model_retry"] = sorted(self.retry)
            write_json(self.phase_dir / SUMMARY_NAME, summary, indent=2)
            self._stop_for_retries("the cards and rewrites")
        self.check_provenance()
        summary["bank"] = write_phase_bank(self.store, self.ctx.run_dir,
                                           self.phase.label)
        write_json(self.phase_dir / SUMMARY_NAME, summary, indent=2)
        return summary

    # -- group A: diagnoses and cuts ------------------------------------------

    def _load_findings(self) -> Dict[str, Finding]:
        path = self.phase_dir / FINDINGS_NAME
        if not path.exists():
            return {}
        return {row["sheet_id"]: Finding.from_dict(row)
                for row in read_jsonl(path)}

    def group_a(self) -> Tuple[Dict[str, Finding], Set[str]]:
        findings = self._load_findings()
        undiagnosed = self._given_up() & set(self.sheets)
        units: List[Tuple[str, Any]] = [
            ("diagnose", sheet) for sheet in self.failed
            if sheet.sheet_id not in findings
            and sheet.sheet_id not in undiagnosed]
        units += [("cut", trajectory)
                  for trajectory in self._failed_trajectories()]
        if self.first and self.review.cards:
            units += [("cut", trajectory)
                      for trajectory in locate.strong_trajectories(
                          self.store, self.baseline_label,
                          self.ctx.config.rules["trajectory_select"])]
        handles = {"diagnose": self.ctx.handle("author"),
                   "cut": self.ctx.handle("segmenter")}

        def work(unit: Tuple[str, Any]) -> Tuple[str, Optional[Exception]]:
            kind, item = unit
            unit_id = (item.sheet_id if kind == "diagnose"
                       else item.trajectory_id)
            try:
                if kind == "diagnose":
                    finding = diagnose_trial(
                        item, self.facts[item.sheet_id],
                        handle=handles[kind],
                        instruction_for=self.instruction_for,
                        view_rules=self.view_rules,
                        report_prompt=self.pack.grader_report_prompt,
                        usage=self.ctx.usage, run_dir=self.ctx.run_dir)
                    with self.lock:
                        append_jsonl(self.phase_dir / FINDINGS_NAME,
                                     finding.to_dict())
                        findings[item.sheet_id] = finding
                else:
                    locate.segment_one(
                        store=self.store, trajectory=item,
                        phase_label=self.phase.label, K=self.review.K,
                        retry_limit=self.review.retry_limit,
                        view_rules=self.view_rules, handle=handles[kind],
                        usage=self.ctx.usage, run_dir=self.ctx.run_dir,
                        give_up=self._last_run(unit_id))
            except InfrastructureError as exc:
                self._record(unit_id, exc)
                return unit_id, exc
            except ModelOutputError as exc:
                if self._model_failed(unit_id, exc) and kind == "diagnose":
                    return unit_id, exc
                return unit_id, None
            return unit_id, None

        results = run_units(work, units, self.workers)
        undiagnosed.update(unit_id for unit_id, exc in results
                           if isinstance(exc, ModelOutputError))
        write_jsonl(self.phase_dir / FINDINGS_NAME,
                    [findings[sheet.sheet_id].to_dict()
                     for sheet in self.failed
                     if sheet.sheet_id in findings])
        infra = sorted(unit_id for unit_id, exc in results
                       if isinstance(exc, InfrastructureError))
        if infra:
            raise InfrastructureError(
                f"{self.phase.label}: {len(infra)} diagnosis or cut unit(s) "
                f"failed on infrastructure ({', '.join(infra)}); see "
                "failed_units.jsonl, then resume the run to retry them")
        return ({sheet.sheet_id: findings[sheet.sheet_id]
                 for sheet in self.failed if sheet.sheet_id in findings},
                undiagnosed & set(self.sheets))

    def _failed_trajectories(self) -> List[Trajectory]:
        ids = sorted({facts.trajectory_id for facts in self.facts.values()})
        return [self.store.load("trajectories", trajectory_id, Trajectory)
                for trajectory_id in ids]

    # -- group B: typing and the profile ----------------------------------------

    def group_b(self, findings: Dict[str, Finding]) -> EvolvingText:
        """Types the new segments and evolves the profile; returns the
        profile."""
        def work(kind: str) -> Optional[EvolvingText]:
            if kind == "types":
                typed = locate.type_pending(
                    store=self.store, run_dir=self.ctx.run_dir,
                    phase_label=self.phase.label,
                    chunk_size=self.author.typing_chunk_size,
                    max_chars=self.author.typing_chars,
                    view_rules=self.view_rules,
                    handle=self.ctx.handle("segmenter"),
                    usage=self.ctx.usage, runs=MODEL_RUNS)
                with self.lock:
                    self.retry.update(typed["retry"])
                return None
            if not self.review.evolve_profile:
                return self._empty_profile()
            return self._evolve(
                evolving_text.PROFILE,
                [(item.sheet_id, item.model_note)
                 for item in findings.values()],
                facts="", seed="", checks=(), profile="")

        return run_units(work, ["types", "profile"], self.workers)[1]

    def _empty_profile(self) -> EvolvingText:
        """The profile of a phase that does not evolve one: empty text,
        so every writer reads the neutral no-profile section."""
        existing = evolving_text.read_text(self.ctx.run_dir,
                                           self.phase.label,
                                           evolving_text.PROFILE)
        if existing is not None:
            return existing
        item = EvolvingText(kind=evolving_text.PROFILE, text="", version=1,
                            phase_label=self.phase.label)
        evolving_text.write_text(self.ctx.run_dir, item)
        return item

    def _evolve(self, kind: str, notes: Sequence[Tuple[str, str]], *,
                facts: str, seed: str, checks: Sequence[Any],
                profile: str) -> EvolvingText:
        """This phase's ``kind`` text: read back when already written,
        otherwise evolved from the previous learn phase's version."""
        existing = evolving_text.read_text(self.ctx.run_dir,
                                           self.phase.label, kind)
        if existing is not None:
            return existing
        previous = evolving_text.load_previous(
            self.ctx.run_dir, self.names, self.specs, self.phase.index,
            kind)
        old = previous.text if previous is not None else seed
        try:
            text = evolve_text(
                kind, old, [note for _, note in notes], facts,
                self.ctx.handle("author"), profile=profile, checks=checks,
                usage=self.ctx.usage, run_dir=self.ctx.run_dir)
        except ModelOutputError as exc:
            given_up = self._model_failed(kind, exc)
            # Given up: the phase keeps the previous text and its version,
            # fed by no note of this phase. Otherwise the item is never
            # written: the phase stops at the end of this group.
            item = EvolvingText(
                kind=kind, text=old,
                version=previous.version if previous is not None else 1,
                phase_label=self.phase.label)
            if given_up:
                evolving_text.write_text(self.ctx.run_dir, item)
            return item
        item = EvolvingText(
            kind=kind, text=text,
            version=(previous.version + 1) if previous is not None else 1,
            phase_label=self.phase.label,
            source_sheet_ids=[sheet_id for sheet_id, note in notes
                              if note.strip()])
        evolving_text.write_text(self.ctx.run_dir, item)
        return item

    # -- group C: evidence ------------------------------------------------------

    def group_c(self, findings: Dict[str, Finding]
                ) -> Tuple[List[routing.Evidence], int]:
        self.subtasks: Dict[str, Subtask] = {
            subtask_id: self.store.load("subtasks", subtask_id, Subtask)
            for subtask_id in self.store.ids("subtasks")}
        self.assignments = locate.load_assignments(self.ctx.run_dir)
        self.types = {row.type_id: row
                      for row in locate.load_type_table(self.ctx.run_dir)}
        by_trajectory: Dict[str, List[Subtask]] = {}
        for subtask in self.subtasks.values():
            by_trajectory.setdefault(subtask.trajectory_id, []).append(
                subtask)
        cards = {skill.subtask_type: skill.skill_id for skill in self.bank
                 if skill.status == "active" and skill.subtask_type}
        recorded = {row["unit_id"] for row in self.recorder.list_failures()
                    if row.get("stage") == self.phase.label
                    and row.get("error_class") == UNLOCATED}
        rows: List[routing.Evidence] = []
        unlocated = 0
        for sheet_id, finding in findings.items():
            facts = self.facts[sheet_id]
            segment = routing.error_segment(
                by_trajectory.get(facts.trajectory_id, []),
                finding.failing_step)
            type_id = (self.assignments.get(segment.subtask_id)
                       if segment is not None else None)
            if type_id is None:
                unlocated += 1
                if sheet_id not in recorded:
                    self.recorder.record(
                        sheet_id, self.phase.label, UNLOCATED,
                        f"no typed segment holds failing step "
                        f"{finding.failing_step} of "
                        f"{facts.trajectory_id!r}")
                continue
            rows.append(routing.route(
                sheet_id=sheet_id, failing_step=finding.failing_step,
                scope=finding.scope, subtask_id=segment.subtask_id,
                type_id=type_id, card=cards.get(type_id),
                opened=facts.opened))
        write_jsonl(self.phase_dir / EVIDENCE_NAME,
                    [row.to_dict() for row in rows])
        return rows, unlocated

    # -- group D: the playbook --------------------------------------------------

    def group_d(self, findings: Dict[str, Finding],
                evidence: Sequence[routing.Evidence],
                profile: EvolvingText) -> EvolvingText:
        if self.first:
            facts = (self.pack.playbook_facts(self.report.sheets)
                     if self.pack.playbook_facts is not None else "")
        else:
            facts = routing.trigger_facts(
                evidence, {type_id: row.name
                           for type_id, row in self.types.items()})
        return self._evolve(
            evolving_text.PLAYBOOK,
            [(item.sheet_id, item.playbook_note)
             for item in findings.values()],
            facts=facts, seed=self.pack.playbook_seed,
            checks=self.pack.playbook_checks, profile=profile.text)

    # -- group E: the cards -------------------------------------------------------

    def group_e(self, evidence: Sequence[routing.Evidence],
                findings: Dict[str, Finding], profile: EvolvingText,
                playbook: EvolvingText) -> Set[str]:
        if not self.review.cards:
            self.plans = []
            self._receipt_cards_off(evidence)
            return set()
        previous: List[routing.Evidence] = []
        if self.previous_learn is not None:
            path = phase_bank_dir(self.ctx.run_dir, self.previous_learn) \
                / EVIDENCE_NAME
            previous = [routing.Evidence.from_dict(row)
                        for row in read_jsonl(path)]
        in_scope, aside = routing.split_by_scope(evidence, self.review.scope)
        self._receipt_out_of_scope(aside)
        plans = routing.plan_cards(in_scope, previous)
        self.plans = plans
        discarded = routing.discarded_types(self.bank)

        def work(plan: routing.CardPlan) -> Optional[str]:
            target = plan.skill_id or plan.type_id
            try:
                if plan.skill_id is None:
                    self._new_card(plan, profile.text, playbook.text,
                                   plan.type_id in discarded)
                else:
                    self._revise_card(plan, findings, profile.text,
                                      playbook.text)
            except InfrastructureError as exc:
                self._record(target, exc)
                return target
            return None

        results = run_units(work, plans, self.workers)
        ledger.commit_order(self.phase_dir)
        return {target for target in results if target is not None}

    def _receipt_cards_off(self, evidence: Sequence[routing.Evidence]
                           ) -> None:
        """One receipt per gap type: the evidence is recorded, no card is
        written."""
        gaps: Dict[str, List[routing.Evidence]] = {}
        for item in evidence:
            if item.category == routing.GAP:
                gaps.setdefault(item.type_id, []).append(item)
        for type_id, items in sorted(gaps.items()):
            row = TaskListRow(target_kind="type", target_id=type_id,
                              action="add", reason=routing.GAP,
                              basis_unit=self._basis(items))
            if ledger.receipt_of(self.phase, row, self.phase_dir) is None:
                ledger.append_outcome(
                    self.phase, row, None, SKIPPED_CARDS_OFF, None,
                    self.phase_dir, row.reason)

    def _receipt_out_of_scope(self, aside: Sequence[routing.Evidence]
                              ) -> None:
        """One receipt per card and side whose evidence the revise scope
        sets aside; the rows stay in ``evidence.jsonl``."""
        by_card: Dict[Tuple[str, str], List[routing.Evidence]] = {}
        for item in aside:
            by_card.setdefault((item.skill_id, item.category), []).append(
                item)
        for (skill_id, side), items in sorted(by_card.items()):
            row = TaskListRow(target_kind="skill", target_id=skill_id,
                              action=f"revise_{side}",
                              reason=f"{side}_evidence",
                              basis_unit=self._basis(items))
            if ledger.receipt_of(self.phase, row, self.phase_dir) is None:
                ledger.append_outcome(
                    self.phase, row, skill_id, SKIPPED_OUT_OF_SCOPE, None,
                    self.phase_dir, row.reason)

    def _basis(self, items: Sequence[routing.Evidence]) -> str:
        return self.facts[items[0].sheet_id].unit_id

    def _new_card(self, plan: routing.CardPlan, profile: str, playbook: str,
                  discarded: bool) -> None:
        row = TaskListRow(target_kind="type", target_id=plan.type_id,
                          action="add", reason=routing.GAP,
                          basis_unit=self._basis(plan.gap))
        if ledger.receipt_of(self.phase, row, self.phase_dir) is not None:
            return
        skill_id = make_skill_id(plan.type_id)
        if discarded:
            ledger.append_outcome(self.phase, row, skill_id,
                                  SKIPPED_DISCARDED, None, self.phase_dir,
                                  routing.CONTENT_FAILED_TWICE)
            return
        if self.store.exists("skills", skill_id):
            origin = find_provenance(self.ctx.run_dir, skill_id, 1)
            if origin is None or origin.cause != "add" \
                    or origin.phase_label != self.phase.label:
                raise CodeBugError(
                    f"type {plan.type_id!r} has gap evidence but the store "
                    f"already holds {skill_id!r} from another phase")
            # Written here before a crash cut off the receipt.
            ledger.append_outcome(self.phase, row, skill_id, "written", 1,
                                  self.phase_dir, row.reason)
            return
        material = collect_material(
            type_id=plan.type_id, subtasks=self.subtasks,
            assignments=self.assignments,
            rule_name=self.ctx.config.rules["segment_select"],
            phase_label=self.phase.label,
            write_lessons=self.review.write_lessons,
            gap_tasks={self.facts[item.sheet_id].task_ref
                       for item in plan.gap},
            error_segments={item.subtask_id for item in plan.gap},
            max_segments=self.author.max_segments_per_card,
            load_trajectory=lambda trajectory_id: self.store.load(
                "trajectories", trajectory_id, Trajectory),
            view_rules=self.view_rules)
        if not material.strong:
            ledger.append_outcome(self.phase, row, None,
                                  "skipped_no_material", None,
                                  self.phase_dir, row.reason)
            return
        sources = [trace_of_trajectory(
            self.ctx.run_dir,
            parse_trajectory_id(item["subtask"].trajectory_id).phase_label,
            item["subtask"].trajectory_id, item["subtask"].step_range[0])
            for item in material.strong]
        sources += self._failing_refs(plan.gap)
        reason = row.reason
        try:
            if self.author.trigger_view == "prefix":
                skill, from_prefix = write_card_prefix(
                    self.types[plan.type_id], material,
                    prefix_material(
                        material, before_steps=WRITER_BEFORE_STEPS,
                        load_trajectory=lambda trajectory_id:
                        self.store.load("trajectories", trajectory_id,
                                        Trajectory),
                        view_rules=self.view_rules),
                    profile=profile, playbook=playbook,
                    handle=self.ctx.handle("author"), params=self.author,
                    usage=self.ctx.usage, run_dir=self.ctx.run_dir)
                if not from_prefix:
                    reason = f"{reason}; {PREFIX_UNAVAILABLE}"
            else:
                skill = write_card(
                    self.types[plan.type_id], material, profile=profile,
                    playbook=playbook, handle=self.ctx.handle("author"),
                    params=self.author, usage=self.ctx.usage,
                    run_dir=self.ctx.run_dir)
        except ModelOutputError as exc:
            if self._model_failed(plan.type_id, exc):
                ledger.append_outcome(self.phase, row, None, "write_failed",
                                      None, self.phase_dir, str(exc)[:300])
            return
        record_version(
            self.store, self.phase_dir, self.ctx.run_dir, skill,
            Provenance(skill_id=skill.skill_id, version=1,
                       phase_label=self.phase.label, cause="add",
                       sources=sources,
                       note=f"card for {self.types[plan.type_id].name}"))
        ledger.append_outcome(self.phase, row, skill.skill_id, "written", 1,
                              self.phase_dir, reason)

    def _failing_refs(self, items: Sequence[routing.Evidence]
                      ) -> List[TraceRef]:
        return [trace_at(self.sheets[item.sheet_id], item.failing_step)
                for item in items]

    def _windows(self, items: Sequence[routing.Evidence], side: str
                 ) -> List[Tuple[TraceRef, int, int]]:
        windows = []
        for item, ref in zip(items, self._failing_refs(items)):
            floor = item.failing_step - WRITER_BEFORE_STEPS
            if side == "trigger":
                start = self.subtasks[item.subtask_id].step_range[0]
                windows.append((ref, max(start, floor),
                                item.failing_step + 1))
            else:
                windows.append((ref, max(item.opened_step, floor),
                                item.failing_step + 1 + AFTER_FAILURE_STEPS))
        return windows

    def _revise_card(self, plan: routing.CardPlan,
                     findings: Dict[str, Finding], profile: str,
                     playbook: str) -> None:
        handle = self.ctx.handle("rewriter")
        if plan.content:
            if plan.delete:
                ledger.land_delete(
                    self.store, self.phase,
                    TaskListRow(target_kind="skill",
                                target_id=plan.skill_id, action="delete",
                                reason=routing.CONTENT_FAILED_TWICE,
                                basis_unit=self._basis(plan.content)),
                    self.phase_dir)
                return
            waiting = self._rewrite(
                plan.skill_id, plan.content, "content", findings,
                lambda skill, steps, diagnosis: revise_content(
                    skill, evidence_steps=steps,
                    diagnosis=diagnosis, playbook=playbook,
                    profile=profile, handle=handle,
                    usage=self.ctx.usage,
                    run_dir=self.ctx.run_dir))
            if waiting:
                # The trigger rewrite waits too: landing it now would put the
                # card one version past the content rewrite's plan, which the
                # next run would read as a content rewrite already landed.
                return
        if plan.trigger:
            catalog = self.facts[plan.trigger[0].sheet_id].catalog
            self._rewrite(plan.skill_id, plan.trigger, "trigger", findings,
                          lambda skill, steps, diagnosis: revise_trigger(
                              skill, catalog=catalog, evidence_steps=steps,
                              diagnosis=diagnosis, playbook=playbook,
                              profile=profile, handle=handle,
                              usage=self.ctx.usage,
                              run_dir=self.ctx.run_dir))

    def _rewrite(self, skill_id: str, items: Sequence[routing.Evidence],
                 side: str, findings: Dict[str, Finding], ask: Any) -> bool:
        """Land one rewrite; True when it failed and waits for the next run."""
        row = TaskListRow(target_kind="skill", target_id=skill_id,
                          action=f"revise_{side}",
                          reason=f"{side}_evidence",
                          basis_unit=self._basis(items))
        windows = self._windows(items, side)

        def write(skill: Skill):
            steps = render_windows(
                self.ctx.run_dir, windows,
                instruction_for=self.instruction_for,
                view_rules=self.view_rules, label="failed trial")
            diagnosis = diagnosis_text(
                [(item.failing_step, findings[item.sheet_id].reason)
                 for item in items])
            return ask(skill, steps, diagnosis)

        unit_id = f"{skill_id}/{row.action}"
        try:
            outcome = apply_revision(
                store=self.store, phase=self.phase, row=row,
                phase_dir=self.phase_dir, run_dir=self.ctx.run_dir,
                sources=[window[0] for window in windows], write=write,
                final=self._last_run(unit_id))
        except ModelOutputError as exc:
            return not self._model_failed(unit_id, exc)
        if outcome == "revision_failed":
            self._model_failed(unit_id, ModelOutputError(
                f"{row.action} of {skill_id!r} produced no valid rewrite"))
        return False

    # -- the commit point ----------------------------------------------------------

    def check_provenance(self) -> None:
        """Refuse to publish a phase whose new versions have a bad origin
        (only the versions THIS phase recorded are checked)."""
        path = self.phase_dir / PROVENANCE_NAME
        if not path.exists():
            return
        skills = []
        for row in read_jsonl(path):
            if row.get("phase_label") != self.phase.label:
                continue
            stored = self.store.load("skills", row["skill_id"], Skill)
            if stored.version == row["version"]:
                skills.append(stored)
        problems = check_provenance(self.ctx.run_dir, skills)
        if problems:
            raise CodeBugError(
                f"{self.phase.label}: {len(problems)} new skill version(s) "
                f"have a bad provenance: {problems[:5]}")

    def summary(self, findings: Dict[str, Finding], undiagnosed: Set[str],
                unlocated: int, evidence: Sequence[routing.Evidence],
                profile: EvolvingText,
                playbook: EvolvingText) -> Dict[str, Any]:
        """The phase's JSON-safe summary, read off its ledgers so a resume
        writes what an uninterrupted run writes."""
        outcomes: Dict[str, int] = {}
        path = self.phase_dir / ledger.OUTCOMES_NAME
        if path.exists():
            for record in read_jsonl(path):
                if record.get("phase_label") == self.phase.label:
                    name = record["outcome"]
                    outcomes[name] = outcomes.get(name, 0) + 1
        scopes = {scope: 0 for scope in ("subtask", "general")}
        for finding in findings.values():
            scopes[finding.scope] += 1
        new_types = sum(1 for row in self.types.values()
                        if row.phase_label == self.phase.label)
        given_up = (self._given_up()
                    & {subtask_id for subtask_id, subtask
                       in self.subtasks.items()
                       if subtask.phase_label == self.phase.label})
        return {
            "role": self.phase.spec.role,
            "input_phase": self.source_label,
            "first_learn": self.first,
            "failed_sheets": len(self.failed),
            "diagnosed": len(findings),
            "diagnosis_failed": len(undiagnosed),
            "scopes": scopes,
            "segments_cut": sum(1 for subtask in self.subtasks.values()
                                if subtask.phase_label == self.phase.label),
            "segments_untyped": len(given_up),
            "types": {"table": len(self.types), "new": new_types},
            "located": len(evidence),
            "unlocated": unlocated,
            "evidence": dict(sorted(
                routing.count_categories(evidence).items())),
            "profile_version": profile.version,
            "playbook_version": playbook.version,
            "card_units": len(self.plans),
            "outcomes": dict(sorted(outcomes.items())),
        }


# ---------------------------------------------------------------------------
# The sequence and the inputs
# ---------------------------------------------------------------------------

def _is_failed(sheet: AnswerSheet) -> bool:
    return (sheet.execution is not None and sheet.grading is not None
            and sheet.grading.verdict == "failure")


def _finished_summary(phase_dir: Path) -> Optional[Dict[str, Any]]:
    """The stored summary when the phase FINISHED (it carries "bank");
    None when the phase never ran or stopped before its commit point."""
    path = phase_dir / SUMMARY_NAME
    if not path.exists():
        return None
    summary = read_json(path)
    return summary if isinstance(summary, dict) and "bank" in summary \
        else None


def _sequence(config: Any, phase: PhaseInstance) -> Tuple[List[str], dict]:
    """The frozen sequence and its parsed specs."""
    names = config.sequence
    specs = config.phases
    if (not isinstance(names, list) or not names
            or not isinstance(specs, dict)):
        raise CodeBugError(
            "phase_learn needs config.sequence (a list of phase names) and "
            f"config.phases (name -> parsed spec); got sequence={names!r}, "
            f"phases={specs!r}")
    if not 0 <= phase.index < len(names):
        raise CodeBugError(
            f"phase index {phase.index} is outside the sequence of "
            f"{len(names)} phase(s)")
    if names[phase.index] != phase.name:
        raise CodeBugError(
            f"the sequence names {names[phase.index]!r} at index "
            f"{phase.index} but the phase instance says {phase.name!r}")
    missing = [name for name in names if name not in specs]
    if missing:
        raise CodeBugError(
            f"the sequence names phase(s) {missing} with no parsed spec")
    return list(names), specs


def _input_position(names: Sequence[str], specs: dict, index: int) -> int:
    """The position of the execute phase right before the learn phase; it
    must be a baseline or a practice (test results never flow into
    learning)."""
    for position in range(index - 1, -1, -1):
        spec = specs[names[position]]
        if not isinstance(spec, ExecutePhaseSpec):
            continue
        if spec.role not in (PHASE_ROLE_BASELINE, PHASE_ROLE_PRACTICE):
            raise CodeBugError(
                f"learn phase at index {index} would learn from the "
                f"{spec.role!r} phase {names[position]!r}: only a baseline "
                "or a practice phase feeds learning")
        return position
    raise CodeBugError(
        f"learn phase at index {index} has no preceding execute phase to "
        f"learn from: the sequence is {[names[j] for j in range(index)]!r}")


def _baseline_label(names: Sequence[str], specs: dict) -> str:
    """The label of the sequence's baseline phase (its first entry)."""
    for position, name in enumerate(names):
        spec = specs[name]
        if isinstance(spec, ExecutePhaseSpec) \
                and spec.role == PHASE_ROLE_BASELINE:
            return phase_label(position, name)
    raise CodeBugError(f"the sequence {list(names)!r} has no baseline phase")


def _load_report(ctx: Any, names: Sequence[str],
                 position: int) -> PhaseReport:
    """The input execute phase's trial-level report."""
    name = names[position]
    phase_dir = phase_bank_dir(ctx.run_dir, phase_label(position, name))
    report = phase_report.read_phase_report(phase_dir)
    if report is None:
        raise CodeBugError(
            f"phase {name!r} has no trial-level artifacts "
            "(unit_specs.jsonl / answer_sheets/ / coverage.jsonl)")
    return report


def _previous_bank(ctx: Any, previous_learn: Optional[str]) -> List[Skill]:
    """The bank the phase starts from, as the rows were published: the
    previous learn phase's artifact, or before the first learn phase the
    run-level bank (empty in a run built from scratch)."""
    rows = (ctx.store.bank() if previous_learn is None
            else load_phase_bank(ctx.run_dir, previous_learn))
    return [Skill.from_dict(row) for row in rows]
