"""The registry's ``fake`` provider: a plan-scripted client for smokes.

A checked-in counterpart of ``llm/fake.py``'s test double that the
PROVIDERS table can name: setting ``EXPEDISTILL_LLM_PROVIDER=fake``
builds this client instead of the real endpoint, so a whole phase
sequence (freeze -> D -> L -> B -> ... -> C) runs against REAL benchmark
plugins with zero network. The golden matrix pins the phase machinery on
a stub benchmark; this provider exists for the other direction -- the
real plugins (SearchQA/ALFWorld/TB's local seam) under the fake model.

The script is a JSON PLAN loaded from the path named by
``EXPEDISTILL_FAKE_PROVIDER_PLAN`` (operational, like every endpoint
knob: never frozen into the manifest). The plan carries what the client
cannot derive from the prompt text alone -- the benchmark's gold
answers and, per question, which way the weak executor should go:

    {
      "strong_model": "fake-strong",      # answers every plan question
      "weak_model":   "fake-weak",        # follows the policy
      "revision_marker": "REVISED-BY-G11",
      "questions": {
        "<the full question text>": {
          "answer": "<gold answer>",      # what Finish[] submits
          "policy": "always | skills | revised_skills | never"
        }
      }
    }

Policies (the weak model only; the strong model always answers every
plan question correctly, whatever the policy says):

* ``always`` -- correct bare or injected;
* ``skills`` -- correct iff a skills block is injected (round 0 keeps);
* ``revised_skills`` -- correct only once the injected block carries the
  revision marker (round 0 misses, the G10/G11 revision flips round 1);
* ``never`` -- never correct (both rounds miss, the last learn phase
  deletes the group).

Everything else is dispatched on the prompt text by the same anchor
style as the golden matrix's ``_script`` (tests/golden/harness.py):
one correct-contract response per named method prompt the phase world
calls, loud refusal on anything else -- an unexpected prompt in a smoke
is a finding, never improvised. The two live prompts get the revision
marker treatment the golden matrix pioneered: G10 marks every listed
skill for revision with a concrete problem, G11 returns the original
skill with the marker line appended, so the weak-executor policy can
key the round on it exactly like the golden live plugin does.

The client is deliberately benchmark-agnostic: it never imports a
benchmark package, it only matches question text the plan provides.
It records every call in ``.calls`` for the smoke report's call census.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

from expedistill.infra.reliability import ConfigError
from expedistill.inject.skills import BLOCK_HEADER

__all__ = [
    "PLAN_ENV",
    "POLICIES",
    "REVISION_MARKER",
    "FakeProviderClient",
    "from_env",
    "load_plan",
]

#: Environment variable naming the plan JSON (the provider's only knob).
PLAN_ENV = "EXPEDISTILL_FAKE_PROVIDER_PLAN"

#: Default marker the G11 fake appends to a revised skill; the
#: ``revised_skills`` policy keys on it. Overridable in the plan.
REVISION_MARKER = "REVISED-BY-G11"

#: The weak executor's closed policy vocabulary (module docstring).
POLICIES = ("always", "skills", "revised_skills", "never")

#: What a wrong weak answer submits: never the gold phrase, never a
#: substring of it, so both EM and sub_EM score it 0.
_WRONG_ANSWER = "not the answer the grader expects"


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
    return plan


def from_env(timeout_sec: float = 300.0) -> "FakeProviderClient":
    """The PROVIDERS entry's builder (the table's documented shape).

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
        # Method prompts first: the G10 attribution prompt embeds the
        # RENDERED task instruction (question included), so the execution
        # fallback must never see it.
        if user.startswith("You are given the view"):
            return self._segment(user)
        if "The subtask SUCCEEDED" in user:
            return self._distill()
        if "WHAT THE PRECEDING PART OF THE TRAJECTORY WAS DOING" in user:
            return self._diagnose()
        if user.startswith("You are attributing ONE window"):
            return self._attempt_attribution(user)
        if user.startswith("A skill was active in real attempts"):
            return self._attempt_revision(user)
        if user.startswith("A skill written for a weak model failed"):
            return self._rewrite(user)
        if user.startswith("You are curating a bank of skills written "
                          "for a weak model. Find"):
            return self._similarity(user)
        if user.startswith("You are curating a bank of skills written "
                          "for a weak model. Merge"):
            return self._merge(user)
        if user.startswith("You are curating a bank of skills written "
                          "for a weak model. Assign"):
            return self._group(user)
        question = self._question_of(user)
        if question is not None:
            return self._action(model, question, user)
        raise AssertionError(
            "the fake provider was asked a prompt its plan does not "
            f"cover (model {model!r}):\n{user[:300]}")

    # -- G1 / G2 ----------------------------------------------------------

    @staticmethod
    def _segment(user: str) -> str:
        steps = len(re.findall(r"^step \d+$", user, re.M))
        if steps < 1:
            raise AssertionError(
                "SEGMENT was handed a view with no numbered steps")
        success = "(final status: success)" in user
        return _fenced([{
            "start": 0, "end": steps,
            "label": "answer the question",
            "description": "Gather the evidence, then submit the final "
                           "answer.",
            "success": success,
        }])

    @staticmethod
    def _distill() -> str:
        return _fenced({
            "when_to_use": "when a question needs an exact short answer",
            "steps": ["search the question's key terms",
                      "reply with Finish[<the short answer>]"],
            "not_applicable_when": "the answer is already known",
            "summary": "search before submitting the final answer",
        })

    @staticmethod
    def _diagnose() -> str:
        return _fenced({
            "when_to_use": "about to submit an answer without searching "
                           "first: the guess misses the gold phrasing",
            "steps": ["search the question's key terms",
                      "reply with Finish[<the short answer>]"],
            "not_applicable_when": "a search was already done",
            "summary": "never submit an answer before searching for it",
        })

    # -- G10 / G11 / G6 ---------------------------------------------------

    def _attempt_attribution(self, user: str) -> str:
        ids = [line.split("skill_id: ", 1)[1].strip()
               for line in user.splitlines()
               if line.startswith("skill_id: ")]
        if not ids:
            raise AssertionError(
                "the SKILL_ATTEMPT_ATTRIBUTION prompt listed no skills")
        return _fenced({"skills": [
            {"skill_id": skill_id,
             "trigger_context": "the window shows the task context",
             "behavior_after_trigger": "the model followed the observed "
                                       "procedure",
             "improvement": "name the concrete state before acting"}
            for skill_id in ids]})

    def _attempt_revision(self, user: str) -> str:
        original = user.split("SKILL (current version):\n", 1)[1].split(
            "\n\nATTEMPT EVIDENCE:", 1)[0]
        return _fenced({
            "skill_text": (original + "\n" + self.revision_marker +
                           ": name the concrete state before acting."),
            "revision_reason": "made the trigger condition concrete",
            "summary": "a sharper capability line",
        })

    def _rewrite(self, user: str) -> str:
        original = user.split("SKILL (current version):\n", 1)[1].split(
            "\n\nEXAM QUESTION:", 1)[0]
        return _fenced({
            "failure_category": "wrong_trigger",
            "skill_text": original + "\n" + self.revision_marker +
                          ": name the concrete state before acting.",
            "rewrite_reason": "made the trigger condition concrete",
            "summary": "search before submitting the final answer",
        })

    # -- G7 / G8 / G9 -------------------------------------------------------

    @staticmethod
    def _listing_ids(user: str) -> List[str]:
        """The skill ids of a G7/G9 listing: the paragraph after the
        prompt's ``SKILLS:`` header, ``- <skill_id>: <content>`` per line.
        TWO cuts matter: the header (the template's own bullets like
        ``- similar_groups: ...`` must never parse as skills) and the
        listing's end (the first blank line -- the renderer emits one
        contiguous paragraph, the template prose follows after a gap)."""
        listing = user.split("SKILLS:\n", 1)[1].split("\n\n", 1)[0]
        return [line[2:].split(": ", 1)[0]
                for line in listing.splitlines()
                if line.startswith("- ")]

    def _similarity(self, user: str) -> str:
        ids = self._listing_ids(user)
        groups = [ids[:2]] if len(ids) >= 2 else []
        return _fenced({"similar_groups": groups, "conflicts": [],
                        "low_value": []})

    @staticmethod
    def _merge(user: str) -> str:
        sources = []
        for block in re.split(r"^### ", user, flags=re.M)[1:]:
            _skill_id, _, content = block.partition("\n")
            if content.strip():
                sources.append(content.strip())
        if not sources:
            raise AssertionError("the MERGE prompt listed no skills")
        return _fenced({
            "content": (sources[0] + "\nMerged from " +
                        str(max(2, len(sources))) + " near-duplicate skills."),
            "summary": "search before submitting the final answer",
        })

    def _group(self, user: str) -> str:
        ids = self._listing_ids(user)
        return _fenced({"groups": {skill_id: "search answer discipline"
                                   for skill_id in ids}})

    # -- the benchmark execution prompt ------------------------------------

    def _question_of(self, user: str) -> Optional[str]:
        """The plan question this prompt renders, if any (substring
        match: the task templates embed the question verbatim)."""
        for question in self.questions:
            if question in user:
                return question
        return None

    def _action(self, model: str, question: str, user: str) -> str:
        """One Finish action for one execution prompt (module docstring:
        the strong model always answers, the weak follows the policy)."""
        spec = self.questions[question]
        if model == self.strong_model:
            return f"Finish[{spec['answer']}]"
        has_skills = BLOCK_HEADER in user
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
