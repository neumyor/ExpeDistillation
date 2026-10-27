"""Merge trial findings into final skill actions and new requirements."""

import hashlib
from typing import Any, Dict, List, Sequence, Tuple

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
    parsed = call_model(
        handle, "TRIAL_REQUIREMENT_AGGREGATION",
        {
            "findings": _findings_text(findings),
            "skills": _skill_cards(bank),
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
        origin = findings_by_skill.get(skill_id) or []
        if not origin:
            raise ModelOutputError(
                "TRIAL_REQUIREMENT_AGGREGATION: no finding supports "
                f"{skill_id!r}")
        evidence = None
        if action == "revise":
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
                basis_unit=origin[0].unit_id),
            evidence=evidence))
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
            source_sheet_ids=[str(item) for item in source_ids]))
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


def _findings_text(findings: Sequence[TrialFinding]) -> str:
    lines = []
    for finding in findings:
        lines.append(
            f"SHEET_ID: {finding.sheet_id}\n"
            f"TASK: {finding.task_ref}\n"
            f"SKILLS: {', '.join(finding.skill_ids) or '(none)'}\n"
            f"REASON:\n{finding.reason}\n"
            f"PROFILE_NOTE:\n{finding.profile_note or '(none)'}\n"
            f"NEW_SKILL_NOTE:\n{finding.new_skill_note or '(none)'}")
    return "\n\n".join(lines) or "(no findings)"
