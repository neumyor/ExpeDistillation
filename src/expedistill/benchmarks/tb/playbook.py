"""TB's playbook pieces: baseline facts and the update prompt.

The generic playbook evolution (``methods/evolve_text``) knows nothing about
Terminal Bench. This module is what the prompt pack hands it:

* :func:`facts_from_sheets` turns the baseline phase's trial-level answer
  sheets into the text the writer reads: per task how often each arm
  succeeded; how the failing weak trials ended; how long they ran; how often
  they repeated a command, hit an error, stalled on an unchanged screen or
  lost rounds to unparsable replies; whether they checked their work before
  declaring completion; and one compact command skeleton of a successful and
  of a failing weak trial;
* ``TB_PLAYBOOK_UPDATE`` replaces the method's ``PLAYBOOK_UPDATE`` (the
  pack's ``overrides``): the same placeholders, plus the guidance on reading
  these facts and the skill-read suffix the agent is already given, so the
  playbook adds rules instead of repeating that text.

Only train-split baseline sheets reach this code, so nothing about the test
tasks can leak into the playbook. Everything here is computed from the
recorded commands and terminal output; the heuristics are fixed before any
data is read and are named in the facts text so the writer can weigh them.
"""

import json
import re
from collections import Counter
from typing import Dict, List, Sequence

from expedistill.benchmarks.base import PlaybookRequirement
from expedistill.benchmarks.tb.skill_read import TB_TASK_PROMPT_READ
from expedistill.core.answer_sheet import AnswerSheet
from expedistill.prompts.registry import PromptSpec, register

__all__ = ["PLAYBOOK_PROMPT_NAME", "facts_from_sheets",
           "playbook_requirements"]

PLAYBOOK_PROMPT_NAME = "TB_PLAYBOOK_UPDATE"

#: The parts the writing prompt asks for, each labelled in the text so a
#: missing part is checkable instead of a judgement call.
REQUIRED_LABELS = (
    ("orientation", "## orientation",
     "say what to learn about the task and the workspace before the first "
     "real command"),
    ("working loop", "## working loop",
     "state how to proceed in small checked steps and what to read in each "
     "output before the next command"),
    ("recovery", "## recovery",
     "say what to do when a command fails, repeats, hangs or the screen "
     "stops changing"),
    ("verification", "## verification",
     "say how to check the deliverable against the task text before "
     "declaring completion"),
    ("prohibitions", "## prohibitions",
     "list the mistakes as prohibitions the reader can check at one step"),
)

#: Output a failing command prints; a fixed list, matched case-blind.
_ERROR_RE = re.compile(
    r"command not found|no such file or directory|traceback \(most recent|"
    r"permission denied|syntax error|\berror:|\bfatal:|cannot find|"
    r"modulenotfounderror|importerror|segmentation fault|"
    r"unable to locate|failed to", re.IGNORECASE)
#: A command that looks at whether the work is right.
_CHECK_RE = re.compile(
    r"\bpytest\b|\bunittest\b|\bassert\b|\bdiff\b|\bcmp\b|\bverify\b|"
    r"\bvalidate\b|\bcheck\b|\btest\b|\bsha\d*sum\b|\bmd5sum\b",
    re.IGNORECASE)
_CALL_RE = re.compile(r"^(\w+)\((.*)\)$", re.DOTALL)
_COMPLETE_NAMES = ("mark_task_complete", "task_complete")
_MAX_FACT_CHARS = 14000
_SKELETON_LINES = 22
_COMMAND_CHARS = 110
_CHECK_WINDOW = 5


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _calls(action: str) -> List[tuple]:
    """The (function name, keystrokes or raw arguments) of one step's calls.

    A step may batch several calls, one per line; a line that is not a
    ``name({...})`` call is kept whole under the name ``text``."""
    calls = []
    for line in str(action).splitlines():
        match = _CALL_RE.match(line.strip())
        if match is None:
            calls.append(("text", line.strip()))
            continue
        name, raw = match.group(1), match.group(2)
        try:
            args = json.loads(raw)
        except ValueError:
            args = None
        if isinstance(args, dict) and isinstance(args.get("keystrokes"), str):
            calls.append((name, args["keystrokes"].strip()))
        else:
            calls.append((name, raw.strip()))
    return calls


def _commands(sheet: AnswerSheet) -> List[str]:
    """Every keystroke command of a trial, in order, completion calls
    excluded."""
    commands = []
    for step in sheet.execution.steps or []:
        for name, text in _calls(step.action):
            if name not in _COMPLETE_NAMES and text:
                commands.append(text)
    return commands


def _ended_by_completion(sheet: AnswerSheet) -> bool:
    steps = sheet.execution.steps or []
    if not steps:
        return False
    return any(name in _COMPLETE_NAMES for name, _ in _calls(steps[-1].action))


def _task_name(sheet: AnswerSheet) -> str:
    ref = sheet.execution.task_ref
    return ref[3:] if ref.startswith("tb:") else ref


def _trial_stats(sheet: AnswerSheet) -> Dict[str, float]:
    steps = sheet.execution.steps or []
    commands = _commands(sheet)
    counts = Counter(commands)
    repeated = sum(count - 1 for count in counts.values() if count > 1)
    errors = sum(1 for step in steps if _ERROR_RE.search(step.observation))
    stalled = sum(1 for earlier, later in zip(steps, steps[1:])
                  if earlier.observation.strip() == later.observation.strip())
    streak = longest = 0
    for step in steps:
        if _ERROR_RE.search(step.observation):
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0
    metrics = sheet.execution.metrics or {}
    ended = _ended_by_completion(sheet)
    tail = commands[-_CHECK_WINDOW:] if ended else []
    return {
        "steps": len(steps),
        "commands": len(commands),
        "repeated": repeated,
        "repeat_share": repeated / len(commands) if commands else 0.0,
        "errors": errors,
        "error_share": errors / len(steps) if steps else 0.0,
        "error_streak": longest,
        "stalled": stalled,
        "parse_failures": float(metrics.get("skipped_parse_failures") or 0),
        "handoffs": float(metrics.get("skipped_handoffs") or 0),
        "ended": 1.0 if ended else 0.0,
        "checked": 1.0 if any(_CHECK_RE.search(c) for c in tail) else 0.0,
    }


def _skeleton(sheet: AnswerSheet) -> str:
    lines = []
    for command in _commands(sheet):
        one_line = " ".join(command.split())
        if len(one_line) > _COMMAND_CHARS:
            one_line = one_line[:_COMMAND_CHARS] + "..."
        lines.append(one_line)
    shown = lines[:_SKELETON_LINES]
    text = " | ".join(shown)
    if len(lines) > len(shown):
        text += f" | ... ({len(lines) - len(shown)} more commands)"
    return text


def _share(values: Sequence[float]) -> str:
    return (f"{sum(1 for v in values if v)} of {len(values)}"
            if values else "0 of 0")


def _arm_block(label: str, sheets: Sequence[AnswerSheet]) -> List[str]:
    if not sheets:
        return []
    stats = [_trial_stats(s) for s in sheets]

    def med(key: str) -> float:
        return _median([s[key] for s in stats])

    rows = [
        f"  {label} ({len(sheets)} trials):",
        f"    median steps {med('steps'):g}, median commands "
        f"{med('commands'):g}",
        f"    median share of commands that repeat an earlier identical "
        f"command {med('repeat_share'):.2f}",
        f"    median share of steps whose output shows an error "
        f"{med('error_share'):.2f}, median longest run of consecutive "
        f"error steps {med('error_streak'):g}",
        f"    median steps whose output is identical to the previous "
        f"step's {med('stalled'):g}",
        f"    median rounds lost to unparsable replies "
        f"{med('parse_failures'):g}, median context handoffs "
        f"{med('handoffs'):g}",
    ]
    ended = [s for s in stats if s["ended"]]
    rows.append(f"    ended by declaring completion: {len(ended)} of "
                f"{len(stats)}")
    if ended:
        rows.append(
            "    of those, ran a check-like command in the last "
            f"{_CHECK_WINDOW} commands before declaring: "
            f"{_share([s['checked'] for s in ended])}")
    return rows


def facts_from_sheets(sheets: Sequence[AnswerSheet]) -> str:
    """The baseline facts the TB playbook writer reads.

    Unscored or missing trials are skipped (a trial without an execution or
    a verdict carries no behaviour to describe). The text is deterministic
    for a given order-independent set of sheets: tasks and trials are sorted,
    examples are the shortest weak success and the longest weak failure with
    the smallest sheet id.
    """
    usable = [s for s in sheets
              if s.execution is not None and s.grading is not None
              and s.execution.steps is not None]
    if not usable:
        return "(no scored baseline trials)"
    usable.sort(key=lambda s: s.sheet_id)

    def success(sheet: AnswerSheet) -> bool:
        return sheet.grading.verdict == "success"

    weak = [s for s in usable if s.execution.role == "weak"]
    strong = [s for s in usable if s.execution.role == "strong"]
    weak_ok = [s for s in weak if success(s)]
    weak_bad = [s for s in weak if not success(s)]
    blocks: List[str] = []

    by_task: Dict[str, List[AnswerSheet]] = {}
    for sheet in usable:
        by_task.setdefault(_task_name(sheet), []).append(sheet)
    rows = []
    gap_tasks = 0
    for task in sorted(by_task):
        group = by_task[task]
        parts = []
        rates = {}
        for role in ("strong", "weak"):
            mine = [s for s in group if s.execution.role == role]
            if mine:
                ok = sum(1 for s in mine if success(s))
                rates[role] = ok / len(mine)
                parts.append(f"{role} {ok}/{len(mine)}")
        if rates.get("strong", 0) > rates.get("weak", 1):
            gap_tasks += 1
        rows.append(f"  {task}: " + ", ".join(parts))
    blocks.append(f"[outcome per task] {len(by_task)} tasks, weak "
                  f"{len(weak_ok)} success / {len(weak_bad)} failure of "
                  f"{len(weak)} trials, strong "
                  f"{sum(1 for s in strong if success(s))} success of "
                  f"{len(strong)} trials; {gap_tasks} tasks where the "
                  "strong model succeeded more often than the weak one\n"
                  + "\n".join(rows))

    behaviour = ["[behaviour, measured on the recorded commands and terminal "
                 "output]"]
    behaviour += _arm_block("weak, failing", weak_bad)
    behaviour += _arm_block("weak, successful", weak_ok)
    behaviour += _arm_block("strong, all", strong)
    blocks.append("\n".join(behaviour))

    endings: Dict[str, List[int]] = {}
    for sheet in weak_bad:
        metrics = sheet.execution.metrics or {}
        kind = metrics.get("exception_type")
        if kind:
            label = f"trial exception {kind}"
        elif metrics.get("verifier_ran") is False:
            label = "verifier did not run"
        elif _ended_by_completion(sheet):
            label = "declared completion, verifier scored it a failure"
        else:
            label = "stopped without declaring completion"
        endings.setdefault(label, []).append(len(sheet.execution.steps))
    if endings:
        blocks.append("[how failing weak trials ended]\n" + "\n".join(
            f"  {label}: {len(counts)} (median steps {_median(counts):g})"
            for label, counts in sorted(endings.items())))

    examples: List[str] = []
    if weak_ok:
        best = min(weak_ok, key=lambda s: (len(_commands(s)), s.sheet_id))
        examples.append("  shortest weak success (" + _task_name(best) + "): "
                        + _skeleton(best))
    if weak_bad:
        worst = max(weak_bad, key=lambda s: (len(_commands(s)),
                                             s.sheet_id))
        examples.append("  longest weak failure (" + _task_name(worst) + "): "
                        + _skeleton(worst))
    if examples:
        blocks.append("[command skeletons]\n" + "\n".join(examples))

    text = "\n\n".join(blocks)
    if len(text) > _MAX_FACT_CHARS:
        text = text[:_MAX_FACT_CHARS] + "\n... (facts cut)"
    return text


def playbook_requirements() -> Sequence[PlaybookRequirement]:
    """TB's machine checks on a written playbook: the five labelled parts."""
    def has_label(label: str):
        return lambda text: label in text.lower()

    return tuple(PlaybookRequirement(name, describe, has_label(label))
                 for name, label, describe in REQUIRED_LABELS)


TB_PLAYBOOK_UPDATE = register(
    PromptSpec(
        name=PLAYBOOK_PROMPT_NAME,
        template="""You maintain the GLOBAL PLAYBOOK for a weaker {agent_kind} agent that solves {benchmark_name} tasks.
The playbook is placed in the agent's first message, right after the task
description and in front of its skill-selection instructions, and stays in
its conversation for the whole task. The agent works through a terminal: it
types commands, reads the screen, and finishes by declaring the task
complete. The playbook is general working discipline that helps on every
task; it is not a skill card, never replaces a skill card that matches the
situation, and is not a list of answers to particular tasks.

ENVIRONMENT PROTOCOL (fixed; the agent already receives it, so never repeat
or contradict it):
{protocol}

CURRENT PLAYBOOK ("(empty)" before the first version):
{old_playbook}

FACTS (in the first learning round: baseline facts measured on training
tasks only; the agent ran without any skills; "strong" is a stronger
reference model, "weak" is the agent you write for. In every later round:
the trigger-evidence counts, i.e. which skill cards the agent had in its
catalog but did not open before the step where a failed trial went wrong,
and how often; "(none)" when there are none):
{facts}

NOTES FROM THIS ROUND'S FAILURE DIAGNOSES:
{playbook_notes}

How to read the facts:
- Trigger-evidence counts: the playbook should tell the agent to check
  its skill catalog at the situations those cards cover; never name the
  cards or their subtasks. The bullets below describe the baseline
  facts.
- "outcome per task" shows where the weak model loses against the strong
  one; the rules you write should be the behaviour the strong model shows
  and the weak model lacks, not task-specific knowledge.
- The "behaviour" block compares failing weak trials with successful weak
  and strong trials. A signal matters only when failing trials differ from
  the successful ones: if a number is similar in both, it is not a cause and
  deserves no rule.
- "repeat an earlier identical command", "output identical to the previous
  step's" and "consecutive error steps" are the evidence for the recovery
  rules: they measure circling, a stalled screen and retrying a failing
  command unchanged.
- "ran a check-like command ... before declaring" is a text heuristic (a
  command containing words such as test, diff, check, verify). Use it as
  evidence for the verification rules only when successful trials do it
  clearly more often than failing ones.
- "rounds lost to unparsable replies" and "context handoffs" describe
  protocol and length problems; write a rule only if the numbers are large.
- "how failing weak trials ended" tells whether the loss is giving up early,
  running out of time, or declaring completion on unfinished work.
- The command skeletons are one short and one long trial; read them for the
  shape of good and bad sequences, never copy a command from them.

THE AGENT ALREADY RECEIVES THE FOLLOWING TEXT AFTER YOUR PLAYBOOK. Do not
repeat any of it, do not contradict it, and do not restate its shell hygiene,
threshold or completion-gate rules. Add only what the facts and notes show it
lacks:

----
""" + TB_TASK_PROMPT_READ.template.replace("{", "{{").replace("}", "}}") + """
----

{profile_section}

Update the playbook: keep every rule of the current playbook that the notes
and facts do not contradict, add what they show is missing, and drop a rule
only when the evidence shows that it misleads. Write it with these parts, in
this order. Begin each part with its label on its own line, exactly as
written (the labels are checked):
## orientation
## working loop
## recovery
## verification
## prohibitions

Hard limits:
- Every rule must be checkable at the moment it fires from the terminal
  screen, the agent's own previous commands and the task text. A rule that
  needs anything else is a defect.
- Do not name files, programs, libraries, commands or values from particular
  training tasks. Name only general kinds of action. Never write a
  step-by-step recipe for one kind of task: that belongs in a skill card.
- Use short sentences, one instruction each, in plain if-then form where
  possible. Plain Markdown, no code fences.
  Aim for 1,500 to 3,500 characters; the limit is 20,000. The agent keeps the
  whole text in context for a long task, so every sentence must earn its
  place.
- The reader context above may state length targets and merging rules for
  skill cards. They do not apply to this playbook. Its rules about what the
  reader can and cannot do still apply in full.

{correction_note}End your reply with the playbook in exactly this shape, and write
nothing after the closing tag:

<playbook>
(the whole playbook)
</playbook>
""",
        description=(
            "Terminal Bench's PLAYBOOK_UPDATE: evolve the global playbook "
            "from the facts (baseline facts first, trigger-evidence counts "
            "later) and the diagnosis notes (the pack's override)."
        ),
    )
)
