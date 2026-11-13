"""The registry's ``fake`` provider: a plan-scripted client for smokes.

A checked-in counterpart of ``llm/fake.py``'s test double that the
PROVIDERS table can name: setting ``EXPEDISTILL_LLM_PROVIDER=fake``
builds this client instead of the real endpoint, so a whole phase
sequence (freeze -> D -> L -> A -> ... -> C) runs against REAL benchmark
plugins with zero network. The golden matrix drives a stub benchmark
with it; smokes drive the real plugins (SearchQA/ALFWorld/TB's local
seam) under the fake model.

The script is a JSON PLAN loaded from the path named by
``EXPEDISTILL_FAKE_PROVIDER_PLAN`` (operational, like every endpoint
knob: never frozen into the manifest). The plan carries what the client
cannot derive from the prompt text alone -- the benchmark's gold
answers and, per question, which way the weak executor should go:

    {
      "strong_model": "fake-strong",      # answers every plan question
      "weak_model":   "fake-weak",        # follows the policy
      "revision_marker": "REVISED",
      "segment_steps": 1,                 # optional: steps per G1 segment
      "questions": {
        "<the full question text>": {
          "answer": "<gold answer>",      # what Finish[] submits
          "policy": "always | skills | revised_skills | never",
          "read":   "auto | always | never"   # optional, see below
        }
      }
    }

Policies (the weak model only; the strong model always answers every
plan question correctly, whatever the policy says):

* ``always`` -- correct bare or injected;
* ``skills`` -- correct iff skills reach the prompt: a ``## Skill rules``
  block, or a skill card the model opened with ``ReadSkill[<location>]``;
* ``revised_skills`` -- correct only once the skills in the prompt carry
  the revision marker the two rewrite fakes append;
* ``never`` -- never correct.

The executors speak the SearchQA grammar when the prompt offers
``Search[<query>]``: one ``Search[<the question>]``, then
``Finish[<answer>]``. A prompt that also lists ``<available_skills>``
(the progressive-disclosure folder, benchmarks/skill_folder.py) first gets
a ``ReadSkill[<first listed location>]`` -- every time with ``read:
always``, never with ``read: never``, for a stable half of the questions
with ``auto`` -- so the folder reads, the usage facts and the trial
analysis are exercised. A prompt without ``Search[<query>]`` (the stub
tasks of the unit suites) gets the single ``Finish[<answer>]`` of the
original script.

Everything else is dispatched on the prompt text by anchor: one
correct-contract response per named method prompt the phase world
calls, loud refusal on anything else -- an unexpected prompt in a smoke
is a finding, never improvised. The paper method's prompts (the trial
diagnosis, the subtask typing, the card writer, the profile and playbook
updates, the two rewrites, the two skill-use judges) answer
deterministically: choices that need variety (whether a failure is
``general``, which note is empty) are a stable function of the prompt
text, so a rerun reproduces a run byte for byte and a smoke still sees
every kind of evidence. The diagnosis blames the trial's last step; the
typing puts segments with the same label into one type (the table's type
of that name when there is one); the card writer copies the type's entry
state into ``when_to_use``. REVISE_TRIGGER appends the revision marker the
``revised_skills`` policy keys on to ``when_to_use``, REVISE_CONTENT to
``not_applicable_when``; REVISE_CONTENT reads the current fields back from
the rendered card in the prompt. The playbook fake writes every part label
the prompt demands, so a benchmark's playbook checks pass.

The client is deliberately benchmark-agnostic: it never imports a
benchmark package, it only matches question text the plan provides.
It records every call in ``.calls`` for the smoke report's call census.
"""

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from expedistill.infra.reliability import ConfigError
from expedistill.inject.skills import BLOCK_HEADER

__all__ = [
    "PLAN_ENV",
    "POLICIES",
    "READ_MODES",
    "REVISION_MARKER",
    "FakeProviderClient",
    "from_env",
    "load_plan",
]

#: Environment variable naming the plan JSON (the provider's only knob).
PLAN_ENV = "EXPEDISTILL_FAKE_PROVIDER_PLAN"

#: Default marker the rewrite fakes append to a revised skill; the
#: ``revised_skills`` policy keys on it. Overridable in the plan.
REVISION_MARKER = "REVISED"

#: The weak executor's closed policy vocabulary (module docstring).
POLICIES = ("always", "skills", "revised_skills", "never")

#: Whether an executor opens the first listed skill card before it
#: searches (module docstring).
READ_MODES = ("auto", "always", "never")

#: What a wrong weak answer submits: never the gold phrase, never a
#: substring of it, so both EM and sub_EM score it 0.
_WRONG_ANSWER = "not the answer the grader expects"

#: The skill-folder prompt's card location, one per listed card.
_LOCATION_RE = re.compile(r"<location>\s*(.*?)\s*</location>", re.S)


def load_plan(path: str) -> Dict[str, Any]:
    """Read and validate the plan JSON (the builder's loader).

    Fails as ConfigError on every problem a smoke can hit: a missing
    file, unparsable JSON, a wrong shape. The questions are matched by
    substring, so the plan is validated to answer exactly what it is
    asked about -- an empty question set would silently make every
    execution an unexpected prompt.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            plan = json.load(handle)
    except OSError as exc:
        raise ConfigError(
            f"{PLAN_ENV}={path!r} cannot be read: {exc}") from exc
    except ValueError as exc:
        raise ConfigError(
            f"{PLAN_ENV}={path!r} is not valid JSON: {exc}") from exc
    if not isinstance(plan, dict):
        raise ConfigError(
            f"{PLAN_ENV}={path!r} must be a JSON object, got "
            f"{type(plan).__name__}")
    questions = plan.get("questions")
    if not isinstance(questions, dict) or not questions:
        raise ConfigError(
            f"{PLAN_ENV}={path!r} carries no 'questions' object; the "
            "fake provider needs at least one question to answer")
    segment_steps = plan.get("segment_steps")
    if segment_steps is not None and (
            isinstance(segment_steps, bool)
            or not isinstance(segment_steps, int) or segment_steps < 1):
        raise ConfigError(
            f"{PLAN_ENV}={path!r}: 'segment_steps' must be an integer "
            f">= 1, got {segment_steps!r}")
    for question, spec in questions.items():
        if not isinstance(question, str) or not question.strip():
            raise ConfigError(
                f"{PLAN_ENV}={path!r}: every question key must be a "
                f"non-empty string, got {question!r}")
        if not isinstance(spec, dict) \
                or not isinstance(spec.get("answer"), str) \
                or not spec["answer"].strip():
            raise ConfigError(
                f"{PLAN_ENV}={path!r}: question {question[:60]!r} needs "
                "a non-empty string field 'answer'")
        policy = spec.get("policy", "always")
        if policy not in POLICIES:
            raise ConfigError(
                f"{PLAN_ENV}={path!r}: question {question[:60]!r} names "
                f"policy {policy!r}; the vocabulary is {list(POLICIES)}")
        read = spec.get("read", "auto")
        if read not in READ_MODES:
            raise ConfigError(
                f"{PLAN_ENV}={path!r}: question {question[:60]!r} names "
                f"read mode {read!r}; the vocabulary is {list(READ_MODES)}")
    return plan


def from_env(timeout_sec: float = 300.0,
             model: Optional[str] = None) -> "FakeProviderClient":
    """The PROVIDERS entry's builder (the table's documented shape).

    ``model`` is the model the client will serve; the plan answers every
    model alike, so it is accepted and ignored.

    Reads and validates the plan EAGERLY: a broken plan is a ConfigError
    at handle-build time, before the freeze step locks anything and
    before a single task loads -- the same early-failure contract the
    openai_compat builder gives a missing endpoint variable.
    """
    path = os.environ.get(PLAN_ENV, "").strip()
    if not path:
        raise ConfigError(
            f"{PLAN_ENV} is not set; the 'fake' provider needs a plan "
            "file (see llm/fake_provider.py's module docstring for the "
            "schema)")
    return FakeProviderClient(load_plan(path))


def _fenced(payload: Any) -> str:
    """Render ``payload`` as a fenced JSON block, the prompts' contract."""
    return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


def _pick(text: str, modulus: int, salt: str = "") -> int:
    """A stable number in ``range(modulus)`` for ``text``: the fake's only
    source of variety, so a rerun reproduces every choice."""
    digest = hashlib.sha256((salt + "\0" + text).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % modulus


def _between(text: str, start: str, end: Optional[str]) -> str:
    """The text after the first ``start`` up to the next ``end`` (or the
    end of the text). A missing anchor is a template drift: loud."""
    if start not in text:
        raise AssertionError(
            f"the prompt lacks the anchor {start!r}:\n{text[:300]}")
    rest = text.split(start, 1)[1]
    if end is None or end not in rest:
        return rest
    return rest.split(end, 1)[0]


def _one_line(text: str, limit: int = 200) -> str:
    return " ".join(text.split())[:limit]


def _card_fields(card: str) -> Dict[str, Any]:
    """The three body fields of a rendered skill card (the inverse of
    ``inject.skill_text.render_card`` for the cards this fake writes)."""
    head, _, rest = card.partition("\n[Steps]\n")
    steps_text, _, tail = rest.partition("\n[Do not use when] ")
    steps: List[str] = []
    for line in steps_text.split("\n"):
        match = re.match(rf"{len(steps) + 1}\. (.*)", line)
        if match:
            steps.append(match.group(1))
        elif steps:
            steps[-1] += "\n" + line
    return {"when_to_use": head.split("[When to use] ", 1)[1],
            "steps": steps, "not_applicable_when": tail}


class FakeProviderClient:
    """The scripted client: one deterministic response per prompt.

    ``complete`` has the OpenAICompatClient signature and returns plain
    strings (call_model's unpacker accepts them); every call is recorded
    in ``.calls`` for the smoke's call census. The plan is read once by
    the builder; the client itself is stateless besides the log.
    """

    def __init__(self, plan: Dict[str, Any]):
        self.plan = plan
        self.strong_model = plan.get("strong_model", "fake-strong")
        self.weak_model = plan.get("weak_model", "fake-weak")
        self.revision_marker = plan.get("revision_marker", REVISION_MARKER)
        self.segment_steps: Optional[int] = plan.get("segment_steps")
        self.questions: Dict[str, Dict[str, str]] = plan["questions"]
        self.calls: List[Dict[str, Any]] = []

    # -- the shared ``complete`` contract ------------------------------

    def complete(
        self,
        model: str,
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
        max_tokens: Any = None,
        extra: Any = None,
    ) -> str:
        reply = self._reply(model, user)
        self.calls.append({"model": model, "user": user, "reply": reply})
        return reply

    # -- dispatch ---------------------------------------------------------

    def _reply(self, model: str, user: str) -> str:
        # Method prompts first: a method prompt may embed the RENDERED
        # task instruction (question included), so the execution fallback
        # must never see it.
        if user.startswith("You are given the view"):
            return self._segment(user)
        if user.startswith("A skill card written for a weaker"):
            return self._revise(user)
        for anchor, handler in self._method_prompts():
            if user.startswith(anchor):
                return handler(user)
        question = self._question_of(user)
        if question is not None:
            return self._action(model, question, user)
        raise AssertionError(
            "the fake provider was asked a prompt its plan does not "
            f"cover (model {model!r}):\n{user[:300]}")

    def _method_prompts(self) -> Sequence[Tuple[str, Any]]:
        """The paper method's prompts, by the first words of the template
        (text no vocabulary slot touches)."""
        return (
            ("You analyze ONE failed trial of a weaker",
             self._trial_analysis),
            ("You sort segments of ", self._subtask_typing),
            ("You are writing ONE skill card", self._card_write),
            ("You maintain the accumulated profile of a weaker",
             self._profile_update),
            ("You maintain the GLOBAL PLAYBOOK",
             self._playbook_update),
            ("An agent worked on one task. Before starting it was shown",
             self._applicability_judge),
            ("An agent opened one skill card during a trial.",
             self._adherence_judge),
        )

    # -- G1 ---------------------------------------------------------------

    def _segment(self, user: str) -> str:
        steps = len(re.findall(r"^step \d+$", user, re.M))
        if steps < 1:
            raise AssertionError(
                "SEGMENT was handed a view with no numbered steps")
        success = "(final status: success)" in user
        limit = re.search(r"at most (\d+) such subtasks", user)
        budget = int(limit.group(1)) if limit else 1
        count = 1
        if self.segment_steps is not None:
            count = max(1, min(budget, -(-steps // self.segment_steps)))
        size = -(-steps // count)
        segments = []
        for index in range(count):
            start = index * size
            end = min(steps, (index + 1) * size)
            if start >= end:
                break
            last = end == steps
            segments.append({
                "start": start, "end": end,
                "label": "answer the question" if last
                         else "search for the evidence",
                "description": "Gather the evidence, then submit the final "
                               "answer." if last else
                               "Search the passage collection for the "
                               "entity the question names.",
                "success": success if last else True,
            })
        return _fenced(segments)

    # -- the learn phase: diagnosis, typing, card writing ------------------------

    @staticmethod
    def _trial_analysis(user: str) -> str:
        """The last step fails; one trial in four is ``general``; which
        note is empty varies with the trial, so the evolving texts see both
        empty and non-empty notes."""
        last = re.search(r"an integer from 0 to (\d+)", user)
        if last is None:
            raise AssertionError(
                "TRIAL_FAILURE_ANALYSIS names no step range")
        failing = int(last.group(1))
        opened = _between(user, "when it opened\nnone):\n",
                          "\n\nTHE TRIAL").strip()
        flavour = _pick(user, 3, "trial-analysis")
        return _fenced({
            "reason": f"The trial went wrong at step {failing}: the agent "
                      "acted without checking the evidence against the "
                      "task" + (" although it had opened a card."
                                if opened != "(none)" else "."),
            "failing_step": failing,
            "scope": ("general" if _pick(user, 4, "trial-scope") == 0
                      else "subtask"),
            "model_note": ("The weak reader skips verification unless told "
                           "explicitly." if flavour != 1 else ""),
            "playbook_note": ("Check the result against the task text "
                              "before finishing." if flavour != 2 else ""),
        })

    @staticmethod
    def _subtask_typing(user: str) -> str:
        """Segments with the same label share a type: an existing type of
        that name, else one new type per distinct label."""
        table = _between(user, "table is empty):\n",
                         "\n\nSEGMENTS TO SORT").strip()
        existing = {}
        if table != "(none)":
            for line in table.splitlines():
                type_id, name, _entry = line.split(" | ", 2)
                existing.setdefault(name, type_id)
        segments: List[Tuple[str, str]] = []
        current = None
        for line in _between(user, "reached its own goal):\n",
                             "\n\nRules:").splitlines():
            if line.startswith("SUBTASK_ID: "):
                current = line[len("SUBTASK_ID: "):]
            elif line.startswith("LABEL: ") and current is not None:
                segments.append((current, line[len("LABEL: "):]))
                current = None
        if not segments:
            raise AssertionError("SUBTASK_TYPING listed no segments")
        new_types: List[Dict[str, str]] = []
        keys: Dict[str, str] = {}
        assignments = []
        for subtask_id, label in segments:
            label = label.strip()
            if label in existing:
                ref = existing[label]
            else:
                if label not in keys:
                    keys[label] = f"new{len(keys) + 1}"
                    new_types.append({
                        "key": keys[label], "name": label,
                        "entry_state": f"The agent is about to {label}."})
                ref = keys[label]
            assignments.append({"subtask_id": subtask_id.strip(),
                                "type": ref})
        return _fenced({"new_types": new_types, "assignments": assignments})

    @staticmethod
    def _card_write(user: str) -> str:
        name = re.search(r"^SUBTASK TYPE: (.+)$", user, re.M)
        entry = _between(user, "when this subtask starts):\n",
                         "\n\nHOW THE STRONG MODEL").strip()
        if name is None or not entry:
            raise AssertionError(
                "CARD_WRITE lacks its SUBTASK TYPE / ENTRY STATE")
        return _fenced({
            "when_to_use": entry,
            "steps": [
                "When the task names what to look for, do the first "
                "action toward it; verify the feedback names it; if not, "
                "try the next place.",
                "When the feedback shows the target, act on it; verify the "
                "state changed; if not, read the feedback and retry once.",
                "When the target state is reached, finish; verify it "
                "matches the task text; if not, go back one step.",
            ],
            "not_applicable_when": "the subtask is already done.",
            "summary": "Teach the reader to " + _one_line(name.group(1))
                       + ".",
        })

    # -- the evolving texts -------------------------------------------------

    @staticmethod
    def _profile_update(user: str) -> str:
        old = _between(user, "CURRENT PROFILE:\n",
                       "\n\nNEW PROFILE NOTES FROM THIS PHASE:").strip()
        notes = _between(user, "NEW PROFILE NOTES FROM THIS PHASE:\n",
                         "\n\nMerge the notes").strip()
        lines: List[str] = []
        for text in ([] if old == "(empty)" else old.splitlines()) \
                + notes.split("\n\n"):
            line = "- " + _one_line(text.lstrip("- "), 160)
            if len(line) > 2 and line not in lines:
                lines.append(line)
        return _fenced({"profile_text": "\n".join(lines[:8])})

    @staticmethod
    def _playbook_update(user: str) -> str:
        """The old rules plus one line per note, under every part label the
        prompt demands, padded past the playbook's minimum length."""
        old = _between(user, "before the first version):\n",
                       "\n\n").strip()
        notes = _between(user, "NOTES FROM THIS ROUND'S FAILURE "
                               "DIAGNOSES:\n", "\n\n").strip()
        labels: List[str] = []
        for label in re.findall(r"(?:^|`)(## [a-z ]+?)(?:`|$)", user, re.M):
            if label not in labels:
                labels.append(label)
        rules = [] if old == "(empty)" else [
            line for line in old.splitlines()
            if line.startswith("- ") and line not in labels]
        for note in ([] if notes == "(none)" else notes.split("\n\n")):
            rule = "- " + _one_line(note, 160)
            if rule not in rules:
                rules.append(rule)
        rules = rules or ["- Read the task text before the first action."]
        body = ["When the situation matches a skill card's when-to-use "
                "line, open that card before acting.",
                "If the task says A, an object named B is not it; if the "
                "task says B, an object named A is not it."]
        parts = [label + "\n" + "\n".join(rules) for label in labels] \
            or ["\n".join(rules)]
        text = "\n\n".join(body + parts)
        while len(text) < 320:
            text += ("\n- Check the result against the task text before "
                     "finishing.")
        return "<playbook>\n" + text + "\n</playbook>"

    # -- the two rewrite prompts ------------------------------------------

    def _revise(self, user: str) -> str:
        note = self.revision_marker + ": name the concrete state before acting."
        words = " ".join(user.split())
        if "Rewrite ONLY the trigger" in words:
            summary = re.search(r"^CURRENT SUMMARY: (.+)$", user, re.M)
            trigger = re.search(r"^CURRENT WHEN TO USE: (.+)$", user, re.M)
            if summary is None or trigger is None:
                raise AssertionError("REVISE_TRIGGER lacks its current "
                                     "trigger")
            return _fenced({
                "summary": summary.group(1).strip(),
                "when_to_use": trigger.group(1).strip() + " " + note,
                "revision_reason": "made the trigger name the entry "
                                   "state's observable signs"})
        if "Rewrite ONLY the content" in words:
            card = _between(user, "THE CARD (current version):\n",
                            "\n\nTHE PLAYBOOK THE AGENT SEES")
            fields = _card_fields(card)
            return _fenced({
                "steps": fields["steps"],
                "not_applicable_when": fields["not_applicable_when"]
                                       + "\n" + note,
                "revision_reason": "added the missing check"})
        raise AssertionError("a rewrite prompt names neither side")

    # -- the skill-use judges --------------------------------------------------

    @staticmethod
    def _applicability_judge(user: str) -> str:
        listing = _between(user, "CARD LIST, exactly as the agent saw it:\n",
                           "\n\nTHE TRIAL (")
        cards = re.findall(r"^- skill_id: (.+)$", listing, re.M)
        trial = _between(user, "what each action returned, step by step):\n",
                         "\n\nHow to judge:")
        steps = len(re.findall(r"^step \d+$", trial, re.M))
        return _fenced({"skills": [
            {"skill_id": card.strip(),
             "applicable": steps > 0 and index % 2 == 0,
             "first_step": 0 if steps > 0 and index % 2 == 0 else None,
             "reason": "the situation the card names came up at the "
                       "start" if steps > 0 and index % 2 == 0
                       else "the situation the card names never arose"}
            for index, card in enumerate(cards)]})

    @staticmethod
    def _adherence_judge(user: str) -> str:
        count = re.search(r"has (\d+) numbered item", user)
        if count is None:
            raise AssertionError(
                "SKILL_ADHERENCE_JUDGE does not state its step count")
        cycle = ("followed", "partial", "skipped", "not_reached")
        return _fenced({"steps": [
            {"number": number, "status": cycle[(number - 1) % len(cycle)],
             "evidence": "" if cycle[(number - 1) % len(cycle)]
             == "not_reached" else "the action after the card"}
            for number in range(1, int(count.group(1)) + 1)]})

    # -- the benchmark execution prompt ------------------------------------

    def _question_of(self, user: str) -> Optional[str]:
        """The plan question this prompt renders, if any (substring
        match: the task templates embed the question verbatim)."""
        for question in self.questions:
            if question in user:
                return question
        return None

    def _wants_read(self, spec: Dict[str, str], question: str) -> bool:
        mode = spec.get("read", "auto")
        if mode == "always":
            return True
        return mode == "auto" and _pick(question, 2, "read") == 0

    def _action(self, model: str, question: str, user: str) -> str:
        """One action for one execution prompt (module docstring: the
        strong model always answers, the weak follows the policy)."""
        spec = self.questions[question]
        history = ""
        if "## Transcript so far\n" in user:
            history = _between(user, "## Transcript so far\n",
                               "\n\nReply with exactly one action.")
        taken = re.findall(r"^action: (.*)$", history, re.M)
        reads = [action for action in taken if "ReadSkill[" in action]
        searched = [action for action in taken
                    if "ReadSkill[" not in action]
        locations = _LOCATION_RE.findall(user)
        if "Search[<query>]" in user:
            if (locations and not reads and not searched
                    and self._wants_read(spec, question)):
                return f"ReadSkill[{locations[0]}]"
            if not searched:
                return f"Search[{_one_line(question)}]"
        if model == self.strong_model:
            return f"Finish[{spec['answer']}]"
        has_skills = BLOCK_HEADER in user or bool(reads)
        policy = spec.get("policy", "always")
        if policy == "always":
            correct = True
        elif policy == "never":
            correct = False
        elif policy == "skills":
            correct = has_skills
        elif policy == "revised_skills":
            correct = has_skills and self.revision_marker in user
        else:  # load_plan validated the vocabulary; a stranger is a bug
            raise AssertionError(f"unknown policy {policy!r}")
        answer = spec["answer"] if correct else _WRONG_ANSWER
        return f"Finish[{answer}]"
