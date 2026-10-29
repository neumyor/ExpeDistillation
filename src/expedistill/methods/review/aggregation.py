"""Merge trial findings into final skill actions and new requirements."""

import hashlib
from typing import Any, Dict, List, Sequence, Tuple

from expedistill.composites import decision
from expedistill.composites.evidence_package import EvidencePackage
from expedistill.composites.skill_attempt_evidence import SkillAttemptEvidence
from expedistill.core.answer_sheet import PhaseReport
from expedistill.core.contracts import TaskListRow
from expedistill.core.models import Skill
from expedistill.infra.reliability import (
    CodeBugError,
    ModelOutputError,
)
from expedistill.llm.call import call_model
from expedistill.methods.review import trial_evidence
from expedistill.methods.review.contracts import (
    NewSkillRequirement,
    ReviewContext,
    ReviewItem,
    TrialFinding,
)
from expedistill.methods.review.trial import _skill_cards

__all__ = ["aggregate_findings", "requirement_id_for"]


def aggregate_findings(
    *,
    findings: Sequence[TrialFinding],
    bank: Sequence[Skill],
    current: PhaseReport,
    context: ReviewContext,
    handle: Any,
    usage: Any = None,
    run_dir: Any = None,
) -> Tuple[List[ReviewItem], List[NewSkillRequirement]]:
    """One agent call turns all findings into the phase's final review."""
    measured = _measured_skills(current)
    parsed = call_model(
        handle, "TRIAL_REQUIREMENT_AGGREGATION",
        {
            "findings": _findings_text(findings),
            "skills": _skill_cards(bank),
            "measured_skills": _measured_text(measured),
        },
        output_json=True, usage=usage, run_dir=run_dir)
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            "TRIAL_REQUIREMENT_AGGREGATION: expected a JSON object, got "
            f"{type(parsed).__name__}")
    by_id = {skill.skill_id: skill for skill in bank}
    findings_by_skill: Dict[str, List[TrialFinding]] = {}
    for finding in findings:
        for skill_id in finding.skill_ids:
            findings_by_skill.setdefault(skill_id, []).append(finding)
    # An exam unit examines one skill version in the scene recorded for it,
    # so in an exam phase a skill answers only to its own exam: that exam is
    # its basis unit, its revision evidence and the source of its next scene.
    exam_units = (_exam_units(current) if context.phase_kind == "exam"
                  else None)
    items: List[ReviewItem] = []
    seen = set()
    for entry in parsed.get("skill_actions") or []:
        if not isinstance(entry, dict):
            raise ModelOutputError(
                "TRIAL_REQUIREMENT_AGGREGATION: skill_actions entries must "
                "be objects")
        skill_id = entry.get("skill_id")
        action = entry.get("action")
        reason = entry.get("reason")
        if skill_id not in by_id:
            raise ModelOutputError(
                "TRIAL_REQUIREMENT_AGGREGATION: unknown skill_id "
                f"{skill_id!r}")
        if skill_id in seen:
            raise ModelOutputError(
                "TRIAL_REQUIREMENT_AGGREGATION: duplicate skill_id "
                f"{skill_id!r}")
        if action not in ("keep", "revise", "delete"):
            raise ModelOutputError(
                "TRIAL_REQUIREMENT_AGGREGATION: unknown action "
                f"{action!r} for {skill_id!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise ModelOutputError(
                "TRIAL_REQUIREMENT_AGGREGATION: reason must be a non-empty "
                f"string for {skill_id!r}")
        if exam_units is not None:
            if skill_id not in exam_units:
                # Not examined in this phase: outside this review's scope.
                continue
            basis_unit = exam_units[skill_id]
            origin = [finding for finding in findings
                      if finding.unit_id == basis_unit]
            if not origin and action != "keep":
                action = "keep"
                reason = ("the skill's own exam produced no failure finding; "
                          "in an exam phase only the skill's own exam can "
                          "revise or delete it")
        else:
            origin = findings_by_skill.get(skill_id) or []
            if not origin and action != "keep":
                raise ModelOutputError(
                    "TRIAL_REQUIREMENT_AGGREGATION: no finding supports "
                    f"{action} for {skill_id!r}")
            basis_unit = (origin[0].unit_id if origin
                          else measured.get(skill_id))
            if basis_unit is None:
                raise ModelOutputError(
                    "TRIAL_REQUIREMENT_AGGREGATION: keep for a skill that "
                    f"was not measured: {skill_id!r}")
        if action == "revise" and context.checks_left == 0:
            # The frozen action table's last-check row: no later execution
            # phase could test a rewrite, so it is not written at all.
            action = "delete"
            reason = decision.RETRY_EXHAUSTED
        evidence = None
        if action == "revise":
            if exam_units is not None:
                evidence = _exam_revision_evidence(
                    skill_id, origin, current, by_id)
            else:
                windows = []
                for finding in origin:
                    package = finding.evidence.get(skill_id)
                    if package is not None:
                        windows.extend(package.windows)
                if not windows:
                    raise ModelOutputError(
                        "TRIAL_REQUIREMENT_AGGREGATION: revise needs evidence "
                        f"for {skill_id!r}")
                evidence = SkillAttemptEvidence(
                    skill_id=skill_id,
                    skill_version=by_id[skill_id].version,
                    windows=windows)
        items.append(ReviewItem(
            row=TaskListRow(
                target_kind="skill",
                target_id=skill_id,
                action=action,
                reason=reason.strip(),
                basis_unit=basis_unit),
            evidence=evidence))
        seen.add(skill_id)

    # The aggregation prompt asks for every measured skill, but the model
    # can still omit one.  Never let a measured skill fall through
    # unreviewed: keep it by default, with an explicit reason and the
    # unit that measured it as provenance.
    for skill_id in sorted(measured):
        if skill_id in seen:
            continue
        if skill_id not in by_id:
            raise CodeBugError(
                "TRIAL_REQUIREMENT_AGGREGATION: measured skill "
                f"{skill_id!r} is not in the bank")
        items.append(ReviewItem(
            row=TaskListRow(
                target_kind="skill",
                target_id=skill_id,
                action="keep",
                reason="no trial finding implicated this measured skill; "
                       "keep by default",
                basis_unit=(exam_units[skill_id] if exam_units is not None
                            else measured[skill_id]))))
        seen.add(skill_id)

    requirements: List[NewSkillRequirement] = []
    for entry in parsed.get("new_skill_requirements") or []:
        if not isinstance(entry, dict):
            raise ModelOutputError(
                "TRIAL_REQUIREMENT_AGGREGATION: requirements entries must "
                "be objects")
        title = entry.get("title")
        description = entry.get("description")
        trigger = entry.get("trigger") or ""
        source_ids = entry.get("source_sheet_ids") or []
        if not isinstance(title, str) or not title.strip():
            raise ModelOutputError(
                "TRIAL_REQUIREMENT_AGGREGATION: requirement title is empty")
        if not isinstance(description, str) or not description.strip():
            raise ModelOutputError(
                "TRIAL_REQUIREMENT_AGGREGATION: requirement description is "
                "empty")
        if not isinstance(trigger, str):
            raise ModelOutputError(
                "TRIAL_REQUIREMENT_AGGREGATION: requirement trigger must be "
                "a string")
        if (not isinstance(source_ids, list) or not source_ids
                or not all(isinstance(item, str) and item
                           for item in source_ids)):
            raise ModelOutputError(
                "TRIAL_REQUIREMENT_AGGREGATION: requirement source_sheet_ids "
                "must be a non-empty list of strings")
        # Requirements may only cite sheets that actually produced a
        # finding; a model sometimes names a successful/unknown sheet.
        # Such a requirement has no failure evidence and is dropped
        # rather than allowed to abort the whole aggregation.
        finding_sheets = {finding.sheet_id for finding in findings}
        source_ids = [item for item in source_ids
                      if item in finding_sheets]
        if not source_ids:
            continue
        requirement_id = requirement_id_for(title, source_ids)
        evidence = _requirement_evidence(
            requirement_id, description, source_ids, findings, current,
            context)
        requirements.append(NewSkillRequirement(
            requirement_id=requirement_id,
            title=title.strip(),
            description=description.strip(),
            trigger=trigger.strip(),
            evidence=evidence,
            source_sheet_ids=[str(item) for item in source_ids],
            source_phase_label=context.source_phase_label,
            source_steps={
                finding.sheet_id: finding.step_start
                for finding in findings if finding.sheet_id in source_ids}))
    return items, requirements


def requirement_id_for(title: str, source_sheet_ids: Sequence[str]) -> str:
    payload = title.strip() + "\0" + "\0".join(sorted(source_sheet_ids))
    return "req/" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _requirement_evidence(
    requirement_id: str,
    description: str,
    source_ids: Sequence[str],
    findings: Sequence[TrialFinding],
    current: PhaseReport,
    context: ReviewContext,
) -> EvidencePackage:
    by_sheet = {finding.sheet_id: finding for finding in findings}
    sheets = {sheet.sheet_id: sheet for sheet in current.sheets}
    packages: List[EvidencePackage] = []
    view_rules = None
    from expedistill.benchmarks.registry import get_plugin
    view_rules = get_plugin(context.benchmark).view_rules
    for index, sheet_id in enumerate(source_ids):
        sheet = sheets.get(sheet_id)
        finding = by_sheet.get(sheet_id)
        if sheet is None or finding is None:
            raise CodeBugError(
                "TRIAL_REQUIREMENT_AGGREGATION: source sheet "
                f"{sheet_id!r} has no trial finding")
        packages.append(trial_evidence.requirement_evidence(
            sheet,
            requirement_id=requirement_id,
            reason=finding.reason,
            step_start=finding.step_start,
            step_end=finding.step_end,
            view_rules=view_rules,
            index=index))
    return EvidencePackage(
        target=requirement_id,
        facts=[{"kind": "requirement", "text": description}]
              + [fact for package in packages for fact in package.facts],
        segments=[segment for package in packages for segment in package.segments],
        source_experiences=[
            experience for package in packages
            for experience in package.source_experiences])


def _exam_revision_evidence(
    skill_id: str,
    origin: Sequence[TrialFinding],
    current: PhaseReport,
    by_id: Dict[str, Skill],
) -> EvidencePackage:
    """Build the exam answer-sheet package for a trial-level revise row.

    The exam learn landing owns the exam revision writer, which expects
    the same question / weak answer / judge reason package as the old
    exam path.  ``origin`` holds the findings of the skill's own exam; the
    latest one supplies the source attempt.
    """
    sheets = {sheet.sheet_id: sheet for sheet in current.sheets}
    finding = origin[-1]
    sheet = sheets.get(finding.sheet_id)
    if sheet is None or sheet.execution is None:
        raise CodeBugError(
            "TRIAL_REQUIREMENT_AGGREGATION: exam finding names a missing "
            f"sheet {finding.sheet_id!r}")
    execution = sheet.execution
    metrics = execution.metrics if isinstance(execution.metrics, dict) else {}
    aux = execution.aux if isinstance(execution.aux, dict) else {}
    scene = metrics.get("scene_text") or finding.reason
    return EvidencePackage(
        target=skill_id,
        facts=[
            {"kind": "exam_question", "text": scene},
            {"kind": "weak_answer", "text": execution.answer or ""},
            {"kind": "judge_reason",
             "text": aux.get("judge_reason") or finding.reason},
        ],
        segments=[{
            "sheet_id": finding.sheet_id,
            "step_range": [finding.step_start, finding.step_end],
        }],
        old_skill=by_id[skill_id])


def _exam_units(current: PhaseReport) -> Dict[str, str]:
    """Examined skill id -> the unit of its highest examined version."""
    best: Dict[str, Tuple[int, str]] = {}
    for unit in current.units:
        refs = unit.injected_skills
        if not isinstance(refs, list) or len(refs) != 1 \
                or not isinstance(refs[0], dict):
            continue
        skill_id = refs[0].get("skill_id")
        version = refs[0].get("version")
        if not isinstance(skill_id, str) or not skill_id:
            continue
        version = version if isinstance(version, int) else 0
        if skill_id not in best or version > best[skill_id][0]:
            best[skill_id] = (version, unit.unit_id)
    return {skill_id: unit_id for skill_id, (_, unit_id) in best.items()}


def _measured_skills(current: PhaseReport) -> Dict[str, str]:
    """Measured skill id -> the first unit that injected it.

    The unit members are the contract's measured set; aggregation must
    decide every one of them, even when no finding mentions it.
    """
    measured: Dict[str, str] = {}
    for unit in current.units:
        refs = unit.injected_skills
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            skill_id = ref.get("skill_id")
            if isinstance(skill_id, str) and skill_id:
                measured.setdefault(skill_id, unit.unit_id)
    return measured


def _measured_text(measured: Dict[str, str]) -> str:
    return "\n".join(f"- {skill_id}" for skill_id in sorted(measured)) \
        or "(none)"


def _finding_issues(finding: TrialFinding) -> str:
    """The per-skill issue text the analysis wrote (shared by all windows)."""
    for skill_id in finding.skill_ids:
        package = finding.evidence.get(skill_id)
        if package is not None and package.windows \
                and package.windows[0].trigger_context:
            return package.windows[0].trigger_context
    return ""


def _findings_text(findings: Sequence[TrialFinding]) -> str:
    lines = []
    for finding in findings:
        lines.append(
            f"SHEET_ID: {finding.sheet_id}\n"
            f"TASK: {finding.task_ref}\n"
            f"SKILLS: {', '.join(finding.skill_ids) or '(none)'}\n"
            f"SKILL_ISSUES:\n{_finding_issues(finding) or '(none)'}\n"
            f"REASON:\n{finding.reason}\n"
            f"PROFILE_NOTE:\n{finding.profile_note or '(none)'}\n"
            f"NEW_SKILL_NOTE:\n{finding.new_skill_note or '(none)'}")
    return "\n\n".join(lines) or "(no findings)"
