"""P04/P06 attribution: one bounded window per attempt (or invocation).

The writer's evidence is built here from the trial-level AnswerSheets: each
present ``ExecutionRecord`` supplies its steps, attempt index, raw
correctness and the folder manifest.  The driver is decision-agnostic: it
receives the skills already chosen for revision and returns one
``SkillAttemptEvidence`` per skill.

Folder deliveries use every actual ``cat`` event and take a fixed
before/after window around it. Group/whole-bank deliveries have no
per-skill read event, so they take the attempt's start window and end
window; the middle is intentionally ignored.

One attribution call covers all target skills in a window, so a window's
trajectory text is sent once, not once per skill.
"""

from collections import defaultdict
from typing import Any, Dict, List, Mapping, Optional, Sequence

from expedistill.blocks.attribution import attribute_attempts
from expedistill.blocks.view import render_steps_view
from expedistill.composites import skill_usage
from expedistill.composites.trace import trace_of_execution
from expedistill.composites.skill_attempt_evidence import (
    SkillAttemptEvidence,
    WindowEvidence,
)
from expedistill.composites.skill_use import (
    FOLDER_DELIVERIES,
    skill_used_in_attempt,
    window_bounds,
)
from expedistill.core.answer_sheet import AnswerSheet, ExecutionRecord
from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError
from expedistill.methods.review import ReviewParams

__all__ = ["collect_skill_evidence", "task_instruction_for"]


def task_instruction_for(benchmark: str, task_id: str) -> str:
    """The benchmark task's execution prompt for one task id."""
    from expedistill.benchmarks.registry import get_plugin
    from expedistill.blocks import tasks as tasks_block
    from expedistill.core import ids
    from expedistill.prompts import get as get_prompt

    plugin = get_plugin(benchmark)
    raw_id = ids.raw_task_id(task_id, benchmark)
    loaded = tasks_block.load_tasks(benchmark, [raw_id])
    match = [task for task in loaded if task.task_id == raw_id]
    if not match:
        raise CodeBugError(
            f"benchmark {benchmark!r} did not load task {raw_id!r} for "
            f"group {task_id!r}: the arms must never run a different "
            "set than the groups")
    return get_prompt(plugin.task_prompt_name).format(
        instruction=match[0].instruction, skills_block="", history="")


def collect_skill_evidence(
    *,
    task_id: str,
    skills: Sequence[Skill],
    sheets: Sequence[AnswerSheet],
    round_: int,
    task_instruction: str,
    delivery: str,
    usage_rows: Optional[Sequence[Dict[str, Any]]] = None,
    weak_profile: str,
    author_handle: Any,
    view_rules: Any = None,
    params: Optional[ReviewParams] = None,
    usage: Any = None,
    run_dir: Any = None,
) -> Dict[str, SkillAttemptEvidence]:
    """Collect every window for every target skill of one task.

    Input is the trial-level AnswerSheet set: each present execution
    supplies its steps, attempt index, raw correctness and the folder
    manifest (``artifacts["skill_folder"]``).  Nothing is read from the
    trajectory store.
    """
    if not skills:
        return {}
    params = params or ReviewParams()
    executions = _executions_for_task(task_id, sheets)
    records = {execution.sample_index: execution
               for execution in executions}
    if usage_rows is None:
        usage_rows = skill_usage.build_usage_rows_from_sheets(
            list(sheets), round_=round_)
    usage_by_attempt = {
        row.get("attempt"): row for row in usage_rows}
    evidence = {
        skill.skill_id: SkillAttemptEvidence(
            skill_id=skill.skill_id, skill_version=skill.version)
        for skill in skills}

    for attempt in sorted(records):
        execution = records[attempt]
        execution_folder = execution.artifacts.get("skill_folder")
        usage_row = usage_by_attempt.get(attempt)
        if delivery in FOLDER_DELIVERIES:
            _collect_folder_attempt(
                task_id=task_id, attempt=attempt, execution=execution,
                usage_row=usage_row, skills=skills, folder=execution_folder,
                task_instruction=task_instruction,
                weak_profile=weak_profile, handle=author_handle,
                view_rules=view_rules, params=params, usage=usage,
                run_dir=run_dir, evidence=evidence)
        else:
            _collect_injected_attempt(
                task_id=task_id, attempt=attempt, execution=execution,
                usage_row=usage_row, skills=skills,
                task_instruction=task_instruction,
                weak_profile=weak_profile, handle=author_handle,
                view_rules=view_rules, params=params, usage=usage,
                run_dir=run_dir, evidence=evidence)
    return evidence


def _executions_for_task(task_id: str,
                         sheets: Sequence[AnswerSheet]
                         ) -> List[ExecutionRecord]:
    return [
        sheet.execution for sheet in sheets
        if sheet.execution is not None
        and sheet.execution.kind == "trajectory"
        and sheet.execution.task_ref == task_id]


def _folder_aliases(folder: Optional[Mapping[str, Any]]
                    ) -> Dict[str, str]:
    """The model-visible folder names for this task's skills.

    The attribution task instruction carries the folder's
    ``<available_skills>`` block, whose names are directory names (and
    file paths), while ``SKILLS UNDER REVIEW`` uses full skill ids.
    Models mix the two; the alias map lets the block resolve either form.
    """
    aliases: Dict[str, str] = {}
    for entry in (folder or {}).get("files") or []:
        if not isinstance(entry, Mapping):
            continue
        skill_id = entry.get("skill_id")
        filename = entry.get("filename")
        if not isinstance(skill_id, str) or not isinstance(filename, str):
            continue
        aliases[skill_id] = skill_id
        aliases[filename] = skill_id
        aliases[filename.replace("/", "-")] = skill_id
        dirname = filename.split("/", 1)[0]
        aliases[dirname] = skill_id
        aliases[dirname + "-SKILL.md"] = skill_id
    return aliases


def _collect_folder_attempt(
    *, task_id: str, attempt: int, execution: ExecutionRecord,
    usage_row: Optional[Dict[str, Any]], skills: Sequence[Skill],
    folder: Optional[Dict[str, Any]], task_instruction: str,
    weak_profile: str, handle: Any, view_rules: Any,
    params: ReviewParams, usage: Any, run_dir: Any,
    evidence: Dict[str, SkillAttemptEvidence],
) -> None:
    if usage_row is None:
        return
    aliases = _folder_aliases(folder)
    steps = execution.steps or []
    groups: Dict[tuple, Dict[str, Any]] = defaultdict(
        lambda: {"skills": [], "trigger_steps": set()})
    for skill in skills:
        use = skill_used_in_attempt(
            skill_id=skill.skill_id, attempt=attempt, delivery="folder",
            usage_row=usage_row, trajectory=execution, folder=folder)
        if not use.used:
            continue
        invocations = list(use.invocations)
        if not invocations:
            invocations = _fallback_invocations(usage_row, skill.skill_id)
        for invocation in invocations:
            if invocation.step_index is None:
                continue
            start, end = window_bounds(
                invocation.step_index, len(steps),
                before=params.before_steps, after=params.after_steps)
            group = groups[(start, end)]
            if skill.skill_id not in group["skills"]:
                group["skills"].append(skill.skill_id)
            group["trigger_steps"].add(invocation.step_index)
    for (start, end), group in sorted(groups.items()):
        selected = [skill for skill in skills
                    if skill.skill_id in group["skills"]]
        window = _attribute_window(
            task_id=task_id, attempt=attempt, execution=execution,
            usage_row=usage_row, skills=selected, kind="invocation",
            step_start=start, step_end=end,
            trigger_steps=sorted(group["trigger_steps"]),
            task_instruction=task_instruction, weak_profile=weak_profile,
            handle=handle, view_rules=view_rules, usage=usage,
            run_dir=run_dir, skill_aliases=aliases)
        for skill in selected:
            evidence[skill.skill_id].windows.append(window[skill.skill_id])


def _collect_injected_attempt(
    *, task_id: str, attempt: int, execution: ExecutionRecord,
    usage_row: Optional[Dict[str, Any]], skills: Sequence[Skill],
    task_instruction: str, weak_profile: str, handle: Any,
    view_rules: Any, params: ReviewParams, usage: Any, run_dir: Any,
    evidence: Dict[str, SkillAttemptEvidence],
) -> None:
    windows = _start_end_windows(
        len(execution.steps or []), params.before_steps,
        params.after_steps)
    for kind, start, end in windows:
        result = _attribute_window(
            task_id=task_id, attempt=attempt, execution=execution,
            usage_row=usage_row, skills=list(skills), kind=kind,
            step_start=start, step_end=end, trigger_steps=[],
            task_instruction=task_instruction, weak_profile=weak_profile,
            handle=handle, view_rules=view_rules, usage=usage,
            run_dir=run_dir)
        for skill in skills:
            evidence[skill.skill_id].windows.append(result[skill.skill_id])


def _start_end_windows(n_steps: int, start_size: int,
                       end_size: int) -> List[tuple]:
    """The injected-delivery windows, with empty windows never emitted.

    A zero size contributes nothing. When the end window reaches the
    start of the trajectory it subsumes the start window, so the result
    is one whole-trajectory window rather than a start prefix that drops
    the tail.
    """
    if n_steps <= 0:
        return []
    windows: List[tuple] = []
    start_end = min(start_size, n_steps) if start_size > 0 else 0
    if start_end > 0:
        windows.append(("start", 0, start_end))
    end_start = max(0, n_steps - end_size) if end_size > 0 else n_steps
    if end_size > 0 and end_start < n_steps:
        if not windows or end_start >= windows[-1][2]:
            windows.append(("end", end_start, n_steps))
        else:
            windows[-1] = ("start", 0, n_steps)
    return windows


def _task_description(initial_prompt: Optional[str],
                      fallback: str) -> str:
    """The task's own description, without the terminal agent's contract.

    TB's raw first message embeds the task description inside the
    Terminus agent prompt, which also tells the model to answer with its
    own ``analysis/plan/commands`` JSON. Sending that whole message to
    the attribution model makes it occasionally answer as the terminal
    agent (``{"skills": null}`` or the agent's JSON). The attribution
    block needs the task, not the executor's output format.
    """
    if not isinstance(initial_prompt, str):
        return fallback
    marker = "Task Description:"
    start = initial_prompt.find(marker)
    if start < 0:
        return fallback
    text = initial_prompt[start + len(marker):]
    stops = ("\nBefore running any task command:",
             "\n<available_skills>",
             "\nTASK:",
             "\nTERMINAL HISTORY")
    ends = [text.find(stop) for stop in stops if text.find(stop) >= 0]
    end = min(ends) if ends else len(text)
    description = text[:end].strip()
    return description or fallback


def _attribute_window(
    *, task_id: str, attempt: int, execution: ExecutionRecord,
    usage_row: Optional[Dict[str, Any]], skills: Sequence[Skill],
    kind: str, step_start: int, step_end: int,
    trigger_steps: Sequence[int], task_instruction: str,
    weak_profile: str, handle: Any, view_rules: Any,
    usage: Any, run_dir: Any,
    skill_aliases: Optional[Mapping[str, str]] = None,
) -> Dict[str, WindowEvidence]:
    text = render_steps_view(
        (execution.steps or [])[step_start:step_end], view_rules,
        start_index=step_start)
    meta = execution.metrics if isinstance(execution.metrics, dict) else {}
    initial_prompt = meta.get("initial_prompt")
    instruction = _task_description(initial_prompt, task_instruction)
    outcome = (usage_row or {}).get("outcome") or _outcome(execution)
    success = {"success": True, "failure": False}.get(outcome)
    judged = attribute_attempts(
        task_instruction=instruction,
        attempt=attempt,
        outcome=outcome,
        window_kind=kind,
        step_start=step_start,
        step_end=step_end,
        trajectory_window=text,
        skills=list(skills),
        weak_profile=weak_profile,
        handle=handle,
        usage=usage,
        run_dir=run_dir,
        skill_aliases=skill_aliases)
    ref = trace_of_execution(
        execution, min(trigger_steps) if trigger_steps else step_start)
    return {
        skill_id: WindowEvidence(
            task_id=task_id, attempt=attempt, success=success, kind=kind,
            step_start=step_start, step_end=step_end, ref=ref,
            trigger_steps=list(trigger_steps),
            trigger_context=values["trigger_context"],
            behavior_after_trigger=values["behavior_after_trigger"],
            improvement=values["improvement"])
        for skill_id, values in judged.items()}


def _fallback_invocations(usage_row: Dict[str, Any],
                          skill_id: str) -> List[Any]:
    from expedistill.composites.skill_use import Invocation
    return [
        Invocation(skill_id, event.get("step_index"),
                   event.get("command", ""), event.get("filename", ""))
        for event in usage_row.get("reads") or []
        if event.get("skill_id") == skill_id]


def _outcome(execution: ExecutionRecord) -> str:
    return {True: "success", False: "failure"}.get(
        execution.raw_correctness, "unknown")
