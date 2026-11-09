"""ALFWorld's learned-playbook pieces: baseline facts and the writing prompt.

The generic pipeline (``blocks/playbook.py``, ``stages/learn_playbook.py``)
knows nothing about ALFWorld. This module is what the plugin hands it:

* :func:`facts_from_sheets` turns the baseline phase's trial-level answer
  sheets into the text the writer reads. Per task type: how often each arm
  failed; how the failing trials looped; whether they moved an object whose
  name is not the task's; where successful trials found the object; whether
  a failing trial placed the object and then took it back; how many actions
  followed the transform; and one compact action skeleton of a successful
  and of a failing trial. Across all trials: how often a ``go to`` returned
  to a place already visited and how far apart those visits were, which is
  what tells the writer whether a rule keyed on memory can be followed;
* ``ALFWORLD_PLAYBOOK_WRITE`` is the prompt that turns those facts into the
  global playbook.

Only train-split baseline sheets reach this code, so nothing about the test
tasks can leak into the playbook.
"""

import re
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

from expedistill.benchmarks.base import PlaybookRequirement
from expedistill.core.answer_sheet import AnswerSheet
from expedistill.prompts.registry import PromptSpec, register

__all__ = ["PLAYBOOK_PROMPT_NAME", "facts_from_sheets",
           "playbook_requirements"]

PLAYBOOK_PROMPT_NAME = "ALFWORLD_PLAYBOOK_WRITE"

#: The six parts the writing prompt asks for, each labelled in the text so
#: a missing part is checkable instead of a judgement call.
REQUIRED_LABELS = (
    ("format", "## format",
     "state the reply format and that the action comes from the admissible "
     "list"),
    ("recipes", "## recipes",
     "give one recipe per task type, from finding the object to the final "
     "placement"),
    ("search order", "## search order",
     "give the place-kind order per task type and the rule for the next "
     "go-to"),
    ("object identity", "## object identity",
     "state the whole-noun rule symmetrically, with a related-name example "
     "in both directions"),
    ("delivering", "## delivering",
     "say how delivering and stopping work and which noun decides whether "
     "an object is taken back"),
    ("prohibitions", "## prohibitions",
     "list the mistakes as prohibitions the reader can check at one step"),
)


def playbook_requirements() -> Sequence[PlaybookRequirement]:
    """ALFWorld's machine checks on a written playbook.

    Five of the six are the labelled parts the prompt demands; the identity
    part also has to be symmetric, because that is the whole point of the
    rule (a one-directional pair is a pair of examples, not a rule).
    """
    def has_label(label: str):
        return lambda text: label in text.lower()

    requirements = [
        PlaybookRequirement(name, describe, has_label(label))
        for name, label, describe in REQUIRED_LABELS]
    # The identity part is the one substantive check: forbid-the-example
    # phrasing ("if the task says A ... if the task says B") is what makes
    # the rule transfer to a pair the writer never saw.
    identity = requirements[3]
    symmetric = identity.matches

    def identity_ok(text: str) -> bool:
        return (symmetric(text)
                and text.lower().count("if the task says") >= 2)

    requirements[3] = PlaybookRequirement(
        identity.name,
        identity.describe + "; write the rule in both directions "
                            "(the task says A, and the task says B)",
        identity_ok)
    return tuple(requirements)

#: The six ALFWorld task families, as they appear in the game path.
_TASK_TYPES = (
    "pick_and_place_simple",
    "pick_two_obj_and_place",
    "look_at_obj_in_light",
    "pick_clean_then_place_in_recep",
    "pick_heat_then_place_in_recep",
    "pick_cool_then_place_in_recep",
)
_TYPE_RE = re.compile("(" + "|".join(_TASK_TYPES) + ")")
_TASK_LINE_RE = re.compile(r"Your task is to:\s*(.+)")
_PRODUCTIVE = ("take ", "put ", "move ", "clean ", "heat ", "cool ", "use ",
               "examine ")
#: The task line names the object (and, for the look tasks, the tool); the
#: destination is the place the closing "in/on/to <place>." names.
_GOAL_RE = re.compile(
    r"(?:put|find|clean|heat|cool|examine|look at)\s+"
    r"(?:(?:a|an|two|some|the)\s+)?"
    r"(?:(?:hot|cool|cold|clean|heated|cooled|cleaned|dirty)\s+)?([a-z]+)")
_DEST_RE = re.compile(r"\b(?:in|on|to)\s+(?:a|an|the)?\s*([a-z]+)\.?$")
_TRANSFORMS = ("clean ", "heat ", "cool ")
#: A place is "of a kind" by its name without the instance number, which is
#: how the priors and the class rules are stated.
_PLACE_NUMBER_RE = re.compile(r"\s+\d+$")

#: Each skeleton shows at most this many actions; a failing trial's first
#: actions already show the loop and the rest is repetition.
_SKELETON_ACTIONS = 24
#: Facts above this size are cut with a marker; far above the typical size
#: of the six task types, so the cut only guards against a corrupt input.
_MAX_FACT_CHARS = 60000


def _task_type(task_ref: str) -> str:
    match = _TYPE_RE.search(task_ref)
    return match.group(1) if match else "other"


def _actions(sheet: AnswerSheet) -> List[str]:
    steps = sheet.execution.steps or []
    return [str(step.action) for step in steps
            if not str(step.action).startswith("ReadSkill")]


def _task_line(sheet: AnswerSheet) -> str:
    metrics = sheet.execution.metrics or {}
    match = _TASK_LINE_RE.search(str(metrics.get("initial_observation", "")))
    return match.group(1).strip() if match else ""


def _loop_stats(actions: Sequence[str]) -> Dict[str, object]:
    gos = [action for action in actions if action.startswith("go to ")]
    counts = Counter(gos)
    top: Optional[Tuple[str, int]] = counts.most_common(1)[0] \
        if counts else None
    return {
        "actions": len(actions),
        "go_to": len(gos),
        "distinct_places": len(counts),
        "most_repeated_go_to": top,
        # go-tos beyond the first visit of each place: the size of the circle
        "repeats": len(gos) - len(counts),
        "productive": sum(action.startswith(_PRODUCTIVE)
                          for action in actions),
    }


def _skeleton(actions: Sequence[str]) -> str:
    shown = list(actions[:_SKELETON_ACTIONS])
    tail = "" if len(actions) <= _SKELETON_ACTIONS else \
        f" ... ({len(actions) - _SKELETON_ACTIONS} more)"
    return " | ".join(shown) + tail


def _median(values: Sequence[int]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _goal(sheet: AnswerSheet) -> Tuple[str, str]:
    """(object, destination) of the task line; empty strings when unparsed."""
    line = _task_line(sheet).lower()
    obj = _GOAL_RE.search(line)
    dest = _DEST_RE.search(line)
    return (obj.group(1) if obj else "",
            dest.group(1) if dest else "")


def _place_kind(place: str) -> str:
    """``cabinet 3`` -> ``cabinet``: the class a prior is stated over."""
    return _PLACE_NUMBER_RE.sub("", str(place).strip())


def _interactions(actions: Sequence[str]) -> List[Tuple[str, Optional[str],
                                                        Optional[str]]]:
    """(verb, object, place) per action; a field the action does not name is
    None. Object names carry an instance number ("take mug 3 from ..."),
    places too ("cabinet 1"); the object's number is dropped here and the
    place's number is dropped by :func:`_place_kind`."""
    out = []
    for action in actions:
        parts = str(action).split()
        if not parts:
            continue
        verb = parts[0]
        obj = place = None
        if verb in ("take", "move", "put") and len(parts) >= 3:
            obj = parts[1]
            index = 2
            if parts[index].isdigit():
                index += 1
            if index < len(parts) and parts[index] in ("from", "to", "in",
                                                       "on"):
                place = " ".join(parts[index + 1:])
        elif verb == "go" and len(parts) >= 3 and parts[1] == "to":
            place = " ".join(parts[2:])
        elif verb in ("clean", "heat", "cool", "use") and len(parts) >= 2:
            obj = parts[1]
        out.append((verb, obj, place))
    return out


def _delivered_nouns(sheet: AnswerSheet) -> List[str]:
    """The noun of every object the trial moved to a receptacle, in order."""
    return [obj for verb, obj, _ in _interactions(_actions(sheet))
            if verb in ("move", "put") and obj]


def _object_identity(failing: Sequence[AnswerSheet]) -> Tuple[int, List]:
    """How many failing trials handled an object whose name is not the
    task's, and the most common (task object -> handled object) pairs."""
    wrong = 0
    pairs = Counter()
    for sheet in failing:
        obj, _ = _goal(sheet)
        if not obj:
            continue
        handled = [other for verb, other, _ in _interactions(_actions(sheet))
                   if verb in ("take", "move", "put") and other]
        if handled and obj not in handled:
            wrong += 1
            for other in handled:
                if other != obj:
                    pairs[(obj, other)] += 1
                    break
    return wrong, sorted(pairs.items(),
                         key=lambda pair: (-pair[1], pair[0]))[:3]


def _identity_outcome(group: Sequence[AnswerSheet]) -> Optional[str]:
    """What happened to trials that delivered an object, split by whether
    the LAST delivered noun was the task's own.

    The last delivery is what the scorer sees, so a trial that first moved
    a wrong object and later moved the right one counts as correct, and is
    reported separately: that correction is the behaviour the playbook
    should teach.
    """
    own = Counter()
    other = Counter()
    corrected = 0
    for sheet in group:
        obj, _ = _goal(sheet)
        nouns = _delivered_nouns(sheet)
        if not obj or not nouns:
            continue
        ok = sheet.grading.verdict == "success"
        if nouns[-1] == obj:
            own["ok" if ok else "bad"] += 1
            if nouns[0] != obj:
                corrected += 1
        else:
            other["ok" if ok else "bad"] += 1
    if not (own or other):
        return None
    text = (f"the last object delivered carried the task's own noun: "
            f"{own['ok']} success / {own['bad']} failure; carried a "
            f"different noun: {other['ok']} success / {other['bad']} failure")
    if corrected:
        text += (f"; {corrected} trial(s) first delivered a different noun "
                 "and then delivered the right one")
    return text


def _name_families(sheets: Sequence[AnswerSheet]) -> List[Tuple[str, str]]:
    """Object nouns that share a scene and share a word: one name is
    contained in the other (knife / butterknife) or both end in the same
    word (saltshaker / peppershaker). Read from the observations only, so a
    pair is reported whether or not a trial ever confused it."""
    seen: Dict[str, set] = {}
    for sheet in sheets:
        if sheet.execution is None or sheet.execution.steps is None:
            continue
        nouns = seen.setdefault(sheet.execution.task_ref, set())
        for step in sheet.execution.steps:
            match = re.search(r"you see (.*?)\.(?:\s|$)",
                              str(step.observation))
            if match:
                nouns.update(re.findall(r"(?:a|an) ([a-z]+) \d+",
                                        match.group(1)))
    related = set()
    for nouns in seen.values():
        ordered = sorted(nouns)
        for index, first in enumerate(ordered):
            for second in ordered[index + 1:]:
                short, long_ = sorted((first, second), key=len)
                shares_word = (len(short) >= 3 and short in long_
                               and short != long_)
                same_tail = (len(first) >= 6 and len(second) >= 6
                             and first[-6:] == second[-6:])
                if shares_word or same_tail:
                    related.add((first, second))
    return sorted(related)


def _success_sources(successes: Sequence[AnswerSheet]) -> List[Tuple[str, int]]:
    """Where successful trials first took the task object, by place class.

    A take from the destination's KIND stays in: the destination is one
    instance and the source is often another instance of the same kind
    (measured: 5 of 9 successful cool trials took the object from the same
    kind of place they delivered it to). Only the exact instance the reader
    has chosen as its destination is off limits, which is a rule for the
    playbook, not a filter on these counts.
    """
    counts = Counter()
    for sheet in successes:
        obj, _ = _goal(sheet)
        for verb, taken, place in _interactions(_actions(sheet)):
            if verb == "take" and place and (not obj or taken == obj):
                counts[_place_kind(place)] += 1
                break
    # Sorted by count then name: the facts must not depend on input order.
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:5]


def _replace_loops(failing: Sequence[AnswerSheet]) -> int:
    """Failing trials that moved an object to the destination and later
    took the same object back out of it."""
    loops = 0
    for sheet in failing:
        moved = set()
        for verb, obj, place in _interactions(_actions(sheet)):
            if verb in ("move", "put") and obj:
                moved.add(obj)
            elif verb == "take" and obj in moved:
                loops += 1
                break
    return loops


def _after_transform(actions: Sequence[str]) -> Optional[int]:
    """Actions between the transform and the first delivery, or None when
    the trial never transformed anything."""
    items = _interactions(actions)
    start = next((i for i, (verb, _, _) in enumerate(items)
                  if str(actions[i]).startswith(_TRANSFORMS)), None)
    if start is None:
        return None
    end = next((i for i in range(start, len(items))
                if items[i][0] in ("move", "put")), None)
    return None if end is None else end - start - 1


def _look_handling(failing: Sequence[AnswerSheet]) -> Tuple[int, int]:
    """Look-task failures: (never used the lamp, moved the object away)."""
    never_used = moved_away = 0
    for sheet in failing:
        actions = _actions(sheet)
        if not any(action.startswith("use ") for action in actions):
            never_used += 1
        if any(action.startswith(("move ", "put ")) for action in actions):
            moved_away += 1
    return never_used, moved_away


def _second_source_share(successes: Sequence[AnswerSheet]) -> Optional[str]:
    """Of the successful two-object trials that took both objects, how many
    found the second where the first came from."""
    same = total = 0
    for sheet in successes:
        takes = [(verb, obj, place) for verb, obj, place
                 in _interactions(_actions(sheet))
                 if verb == "take" and place]
        if len(takes) < 2:
            continue
        total += 1
        if _place_kind(takes[1][2]) == _place_kind(takes[0][2]):
            same += 1
    if not total:
        return None
    return (f"{same} of {total} successful two-object trials took the "
            "second object from the same place kind as the first")


def _container_reopens(failing: Sequence[AnswerSheet]) -> Optional[str]:
    """How many failing transform trials opened the same container again."""
    reused = total = 0
    for sheet in failing:
        actions = _actions(sheet)
        opened = [action.split(" ", 2)[2] for action in actions
                  if action.startswith("open ") and len(action.split()) > 2]
        if not opened:
            continue
        total += 1
        if len(opened) != len(set(opened)):
            reused += 1
    if not total:
        return None
    return (f"{reused} of {total} failing trials opened the same container "
            "more than once")


def _destination_instance_visits(group: Sequence[AnswerSheet]
                                 ) -> Optional[str]:
    """How often trials went to the numbered place they later delivered to
    while not holding anything, split by outcome. Instance level on
    purpose: the kind is often a real source."""
    stats = {"ok": [0, 0], "bad": [0, 0]}
    for sheet in group:
        items = _interactions(_actions(sheet))
        placed = [place for verb, _, place in items
                  if verb in ("move", "put") and place]
        if not placed:
            continue
        instance = placed[-1]
        holding = False
        visits = 0
        for verb, _, place in items:
            if verb == "take":
                holding = True
            elif verb in ("move", "put"):
                holding = False
            elif verb == "go" and place == instance and not holding:
                visits += 1
        key = "ok" if sheet.grading.verdict == "success" else "bad"
        stats[key][0] += 1
        stats[key][1] += visits >= 2
    if not (stats["ok"][0] or stats["bad"][0]):
        return None
    ok_n, bad_n = stats["ok"][0], stats["bad"][0]
    ok_hits, bad_hits = stats["ok"][1], stats["bad"][1]
    # The verdict is computed here, not left to the writer: comparing two
    # rates in prose is where a spurious "clearly higher" came from, and a
    # rule against visiting the destination is only justified by a clearly
    # higher failing rate. Fixed before any result was read: at least five
    # failing trials, a failing rate at least double the successful one,
    # and above a quarter.
    if bad_n < 5 or ok_n == 0:
        verdict = "too few trials on one side to support any rule"
    elif bad_hits >= 0.25 * bad_n and bad_hits * ok_n >= 2 * ok_hits * bad_n:
        verdict = "clearly higher"
    else:
        verdict = "similar or lower"
    return (f"visited the numbered place they finally delivered to, empty-"
            f"handed, two or more times: {ok_hits} of "
            f"{ok_n} successful trials, {bad_hits} of "
            f"{bad_n} failing trials; failing rate vs successful rate: "
            f"{verdict}")


def _search_behaviour(groups: Sequence[Sequence[AnswerSheet]]) -> str:
    """How failing trials wander, across every task type."""
    gaps = Counter()
    gos = revisits = 0
    for group in groups:
        for sheet in group:
            if sheet.grading.verdict == "success":
                continue
            last: Dict[str, int] = {}
            for index, action in enumerate(_actions(sheet)):
                if not action.startswith("go to "):
                    continue
                gos += 1
                if action in last:
                    revisits += 1
                    gap = index - last[action]
                    gaps["<=2" if gap <= 2 else "3-5" if gap <= 5
                         else "6-10" if gap <= 10 else ">10"] += 1
                last[action] = index
    if not gos:
        return ""
    total = sum(gaps.values()) or 1
    share = (revisits / gos) * 100
    parts = ", ".join(f"{key} {gaps[key] / total * 100:.0f}%"
                      for key in ("<=2", "3-5", "6-10", ">10"))
    return (f"  {share:.0f}% of all go-to actions in failing trials returned "
            f"to a place already visited in the same trial.\n"
            f"  gap between two visits of the same place: {parts} "
            f"(n={total}).\n"
            "  The reader remembers only the current observation and the "
            "last two interactions.")


def facts_from_sheets(sheets: Sequence[AnswerSheet]) -> str:
    """The baseline facts the ALFWorld playbook writer reads.

    Unscored or missing trials are skipped (a trial without an execution or a
    verdict carries no behaviour to describe). The text is deterministic for
    a given input order-independent set of sheets: groups are keyed by task
    type and sorted, examples are the shortest success and the longest
    failure with the smallest sheet id.
    """
    by_type: Dict[str, List[AnswerSheet]] = {}
    for sheet in sheets:
        if sheet.execution is None or sheet.grading is None \
                or sheet.execution.steps is None:
            continue
        by_type.setdefault(_task_type(sheet.execution.task_ref),
                           []).append(sheet)
    if not by_type:
        return "(no scored baseline trials)"

    blocks: List[str] = []
    for task_type in sorted(by_type):
        group = sorted(by_type[task_type], key=lambda s: s.sheet_id)
        rows = []
        for role in ("strong", "weak"):
            mine = [s for s in group if s.execution.role == role]
            if not mine:
                continue
            ok = [s for s in mine if s.grading.verdict == "success"]
            bad = [s for s in mine if s.grading.verdict != "success"]
            line = (f"  {role}: {len(ok)} success / {len(bad)} failure "
                    f"of {len(mine)}")
            if ok:
                line += (", median actions on success "
                         f"{_median([len(_actions(s)) for s in ok]):g}")
            rows.append(line)
        failing = [s for s in group if s.grading.verdict != "success"]
        loops = [_loop_stats(_actions(s)) for s in failing]
        if loops:
            rows.append(
                "  failing trials: median go-to "
                f"{_median([l['go_to'] for l in loops]):g}, median distinct "
                f"places {_median([l['distinct_places'] for l in loops]):g}, "
                f"median visits per place "
                f"{_median([l['go_to'] / max(l['distinct_places'], 1) for l in loops]):.1f}, "
                "median productive actions "
                f"{_median([l['productive'] for l in loops]):g}, "
                f"{sum(l['productive'] == 0 for l in loops)} of "
                f"{len(loops)} never took, used or put anything")
            repeats = [l["most_repeated_go_to"] for l in loops
                       if l["most_repeated_go_to"]]
            if repeats:
                worst = max(repeats, key=lambda pair: pair[1])
                rows.append(
                    f"  most repeated single go-to: '{worst[0]}' "
                    f"x{worst[1]}")
        successes = [s for s in group if s.grading.verdict == "success"]
        outcome = _identity_outcome(group)
        if outcome:
            rows.append(f"  object identity outcome: {outcome}")
        instance = _destination_instance_visits(group)
        if instance:
            rows.append(f"  destination instance: {instance}")
        if failing:
            wrong, pairs = _object_identity(failing)
            if wrong:
                detail = ", ".join(f"{task}->{other} x{count}"
                                   for (task, other), count in pairs)
                rows.append(
                    f"  object identity: {wrong} of {len(failing)} failing "
                    f"trials handled an object whose name is not the task's "
                    f"(most common: {detail})")
            replays = _replace_loops(failing)
            if replays:
                rows.append(
                    f"  re-place loops: {replays} of {len(failing)} failing "
                    "trials moved the object to the destination and later "
                    "took the same object back out")
            gaps = [_after_transform(_actions(s)) for s in failing]
            gaps = [gap for gap in gaps if gap is not None]
            if gaps:
                rows.append(
                    "  actions between the transform and the first "
                    f"delivery, failing trials: median {_median(gaps):g}")
        if successes:
            sources = _success_sources(successes)
            if sources:
                rows.append(
                    "  where successful trials first took the object: "
                    + ", ".join(f"{kind} {count}"
                                for kind, count in sources))
            gaps = [_after_transform(_actions(s)) for s in successes]
            gaps = [gap for gap in gaps if gap is not None]
            if gaps:
                rows.append(
                    "  actions between the transform and the first "
                    f"delivery, successful trials: median {_median(gaps):g}")
        if task_type == "pick_two_obj_and_place" and successes:
            share = _second_source_share(successes)
            if share:
                rows.append(f"  second object: {share}")
        if failing:
            reopens = _container_reopens(failing)
            if reopens:
                rows.append(f"  repeated opening: {reopens}")
        if failing and task_type == "look_at_obj_in_light":
            never_used, moved_away = _look_handling(failing)
            rows.append(
                f"  look tasks: {never_used} of {len(failing)} failing trials "
                f"never used a lamp; {moved_away} moved the object to a "
                "receptacle instead of using a lamp on it")
        if successes:
            best = min(successes, key=lambda s: (len(_actions(s)),
                                                 s.sheet_id))
            rows.append(f"  example success ({_task_line(best)}): "
                        + _skeleton(_actions(best)))
        if failing:
            worst_trial = max(failing, key=lambda s: (len(_actions(s)),
                                                      s.sheet_id))
            rows.append(f"  example failure ({_task_line(worst_trial)}): "
                        + _skeleton(_actions(worst_trial)))
        blocks.append(f"[{task_type}] {len(group)} trials\n"
                      + "\n".join(rows))
    families = _name_families([sheet for key in sorted(by_type)
                               for sheet in by_type[key]])
    if families:
        shown = ", ".join(f"{a}/{b}" for a, b in families)
        blocks.append(
            "[object names that share a scene and share a word]\n"
            f"  {shown}\n"
            "  Each pair names two different objects that can be in the same "
            "room. A task asks for one name; the other is not it.")
    behaviour = _search_behaviour([by_type[key] for key in sorted(by_type)])
    if behaviour:
        blocks.append("[search behaviour over all failing trials]\n"
                      + behaviour)
    text = "\n\n".join(blocks)
    if len(text) > _MAX_FACT_CHARS:
        text = text[:_MAX_FACT_CHARS] + "\n... (facts cut)"
    return text


PLAYBOOK_WRITE = register(
    PromptSpec(
        name=PLAYBOOK_PROMPT_NAME,
        template="""You write the GLOBAL PLAYBOOK for a weaker {agent_kind} agent that plays {benchmark_name}.
The playbook is shown to the agent at EVERY step, above its list of skill
cards, for the whole episode. It is general strategy, not a skill card and
not a list of answers to particular tasks.

BASELINE FACTS (measured on training tasks only; the agent ran without any
skills; "strong" is a stronger reference model, "weak" is the agent you
write for):
{facts}

How to read the facts:
- "where successful trials first took the object" is the evidence for
  search order: name the place kinds in that order, per task type.
- "object identity" counts failing trials that handled an object whose name
  is not the one the task asks for; the pairs named are the confusions that
  actually happened. A warm/hot/cool/clean variant of the same noun is the
  same object; a different noun is not.
- "object identity outcome" is the plain rule behind that count: when the
  last object delivered carried a different noun it was never accepted, and
  when it carried the task's own noun it usually was. A trial that first
  delivered a wrong noun and then delivered the right one still succeeded,
  so a wrong delivery is recoverable and the playbook must say how.
- "object names that share a scene and share a word" lists pairs of
  different objects that can be in one room. The training trials only
  confused a few pairs, but the same situation arises for ANY pair of
  objects whose names are related (one name inside the other, or the same
  last word), and the pairs in the next rooms will differ. State the rule
  for the whole class, not for the listed pairs.
- "re-place loops" counts failing trials that put the object at the
  destination and then took it back out. That happens when the object or
  the destination is wrong: the agent keeps re-delivering instead of
  re-checking the task names.
- "median visits per place" is how many times a failing trial went back
  to a place it had already stood in: a number above 1.5 means the failing
  trials spend their budget in a circle of a few places while the
  admissible list names many more. That is the evidence for the instance
  sweep and for class exhaustion.
- "actions between the transform and the first delivery" is the cost of
  extra checking after cleaning, heating or cooling; a low number is the
  target.
- "destination instance" counts trials that went to the exact numbered
  place they finally delivered to while holding nothing, split by outcome.
  The last clause is the verdict, computed from those counts: "clearly
  higher", "similar or lower", or "too few trials". Act on it literally.
  Only "clearly higher" supports a rule against visiting the destination;
  on the other two, a rule would remove a real search step, so write no
  prohibition at all -- not even a softened one.
- "second object" is the evidence for the two-object tasks: how often the
  second object came from the same place kind as the first.
- "repeated opening" counts failing trials that opened the same container
  more than once; it is the evidence for the container rule below.
- "gap between two visits of the same place" and the reader-memory line
  tell you which rules the reader can actually follow. Any rule that needs
  a record of the places visited so far is impossible for this reader: it
  remembers only the current observation and the last two interactions.
  Give an ORDER instead: pick from the admissible-action list by a fixed
  rule (for example: always take the first listed place of the most likely
  kind that the current observation does not show you are already at), so
  that following the order cannot revisit a place. The same limit applies
  to containers: "the container you are standing at" is visible in the
  current observation, so a rule that says what to do at the container in
  front of the reader is executable, while "containers opened so far" is
  not.

{profile_section}

The agent's reply format is fixed by the protocol:
<think>brief reason</think><action>one admissible action</action>
Do not change it and do not tell the agent to skip the <think> part.

Write the playbook with these parts, in this order. Begin each part with its
label on a line of its own, exactly as written here (`## format`,
`## recipes`, `## search order`, `## object identity`, `## delivering`,
`## prohibitions`): the labels are checked, and a playbook without them is
rejected and written again.
1. One line stating the reply format and that the action must come from the
   admissible-action list.
2. One recipe per task type that appears in the facts: the ordered actions
   from finding the object to the final placement. Every recipe's search
   step must say, in the recipe itself and not only in the search-order
   part, that arriving at a closed container means opening it before
   moving on: the observation says "X is closed", and the very next action
   is `open X`. The facts show failing trials arriving at a closed
   container and walking away from it. For the two-object task
   the recipe must say what to do after the first delivery: check the
   destination the reader just used for a second copy (it is in the last
   interactions), and only then continue the walk forward; the facts give
   how often the second object came from the same place kind as the first,
   and that share justifies the rule ("usually", not "always").
3. Search order, per task type, using the "where successful trials first
   took the object" evidence. State it as an order over place kinds, plus
   the rule for choosing the next place (see the order advice above). The
   search-order part must also tell the reader that the admissible-action
   list keeps naming every place for the whole episode: the room
   description leaves the observation after the first step, and a reader
   that searches only what the current observation happens to show walks
   the same handful of places until the step budget ends. These rules
   belong here:
   - the walk is one-way: when it reaches the end it does not restart from
     the beginning in the same search; the reader continues from where the
     observation shows it is;
   - container rule: after the reader opens a container and the
     observation does not show the object inside, that container is done --
     the next action is another container or another place kind, never the
     same opener again. The facts give how often failing trials re-opened a
     container, so the rule can be stated as a measured problem.
   - instance sweep within a kind: while the current kind still has an
     instance the reader has not been to in this search, the next go-to is
     the next numbered instance of that kind, never a different kind. Take
     it from the admissible list: after "countertop 1" comes
     "countertop 2", then "countertop 3"; the list names every instance of
     every kind. The order is the NUMBER, not the list position: the list
     sorts names as text, so "shelf 10" sits next to "shelf 1" and
     "shelf 2" comes after "shelf 19". Say it as a numbered walk ("1, then
     2, then 3", "9, then 10"), not as "the first listed place": picking
     the first listed place of a kind after leaving it returns the reader
     to instance 1 forever, and the facts show failing trials spending
     their whole budget in that circle (tens of go-to actions over a
     handful of places).
   - class exhaustion: a kind is done only when every instance the
     admissible list names for it has been visited. Only then move to the
     next kind in the order, and never come back to a finished kind in the
     same search. Do not alternate between kinds: alternation is what
     turns a six-instance kind into a two-instance ping-pong.
   - destination instance: apply the "destination instance" fact and its
     verdict. The destination is ONE numbered place (for example
     "cabinet 1"). Only when the verdict is "clearly higher", write: while
     not holding the object, do not reach that exact numbered place before
     the sweep would; on "similar or lower" or "too few trials", write no
     prohibition at all, and do not invent a task type to mark. Whenever
     the rule is written, it applies to the exact numbered place, never to
     its kind, and it never breaks the sweep: the reader visits that place
     last among its kind, not never.
   - how the rules fit together: the search order gives the kinds, the
     instance sweep walks each kind end to end, class exhaustion decides
     when to switch, and the destination note only changes where one
     numbered place sits inside its kind. Write them as one short
     procedure in that order, so the reader never has to weigh one rule
     against another.
4. Object identity, written as a procedure the reader can run. The task
   sentence names an object with one noun, and the scene may hold another
   object whose name is related (a name inside it, or the same last word).
   The playbook must contain all of these, in plain if-then sentences:
   - match by the whole noun: remove the trailing number from an object
     name in the observation; it is the task's object only if the rest is
     exactly the task's noun. Say it symmetrically: if the task says A, an
     object named B is not it, and if the task says B, an object named A
     is not it. Illustrate with one pair from the facts' list and one pair
     the facts' list does not contain, and say that the rule applies to any
     two related names;
   - never reason that a related name "serves as" or "counts as" the task's
     noun: the environment accepts only the exact noun, and the facts show
     that every delivery of a different noun failed;
   - if the observation shows only a related name and not the task's noun,
     the task's object is somewhere else: continue the search and do not
     pick the related one up;
   - recovery: if the reader has delivered an object and the environment
     did not report success, compare the noun it delivered with the task's
     noun. Different noun: take it back out, and continue the search for
     the exact noun. Same noun: do not take it back (see part 5).
5. Delivering and stopping: put the object at the named destination. If the
   delivered noun is the task's own noun, stop there; do not take it back
   and re-place it, and check the destination's name against the task
   sentence instead. This rule and the recovery rule in part 4 do not
   conflict, because they apply to different nouns: write one sentence that
   says which applies to which, so the reader never has to choose. After a
   clean, heat or cool, go straight to the destination.
6. The mistakes the failing examples show, each as one short prohibition.
   Every prohibition must be a statement the reader can check against what
   it sees now; drop any that needs a memory of earlier steps.

Hard limits:
- Every rule must be checkable at the moment it fires from the current
  observation, the last two interactions, the inventory and the
  admissible-action list. A rule that needs anything else is a defect.
- Do not name objects, rooms or numbered receptacles from particular
  training tasks. Name only general kinds (fridge, microwave, sinkbasin,
  desklamp) that the task types themselves require.
- Use short sentences, one instruction each. Plain Markdown, no code fences.
  Aim for 2,500 to 5,000 characters; the limit is 20,000.
- The reader context above may state length targets and merging rules for
  skill cards. They do not apply to this playbook. Its rules about what the
  reader can and cannot remember still apply in full.

{correction_note}End your reply with the playbook in exactly this shape, and write
nothing after the closing tag:

<playbook>
(the whole playbook)
</playbook>
""",
        description=(
            "Learned playbook: write ALFWorld's global playbook from the "
            "baseline trial facts (the plugin's playbook_prompt_name)."
        ),
    )
)
