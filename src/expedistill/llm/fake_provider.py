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
  block, or a skill card the model opened with ``ReadSkill[<location>]``
  (round 0 keeps);
* ``revised_skills`` -- correct only once the skills in the prompt carry
  the revision marker (round 0 misses, the G10/G11 revision flips round
  1);
* ``never`` -- never correct (both rounds miss, the last learn phase
  deletes the group).

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

Everything else is dispatched on the prompt text by the same anchor
style as the golden matrix's ``_script`` (tests/golden/harness.py):
one correct-contract response per named method prompt the phase world
calls, loud refusal on anything else -- an unexpected prompt in a smoke
is a finding, never improvised. The paper method's prompts (cluster
authoring, the generalization exam, trial-level review, the requirement
skill, the profile update, the two skill-use judges) answer
deterministically: choices that need variety (which family, which skill
the exam reader opens, which exam plan fails) are a stable function of
the prompt text, so a rerun reproduces a run byte for byte and a smoke
still sees some failures to review. The two live prompts get the revision
marker treatment the golden matrix pioneered: G10 marks every listed
skill for revision with a concrete problem, G11 returns the original
skill with the marker line appended, so the weak-executor policy can
key the round on it exactly like the golden live plugin does. REWRITE
reads its ``{scope_section}``: in a ``body`` or ``trigger`` scope the
marker lands in the part the scope allows, and the part it forbids is
edited anyway, so a smoke can see ``blocks.author.enforce_scope`` restore
it.

The client is deliberately benchmark-agnostic: it never imports a
benchmark package, it only matches question text the plan provides.
It records every call in ``.calls`` for the smoke report's call census.
"""

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from expedistill.core.models import SKILL_MARKERS
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

#: Default marker the G11 fake appends to a revised skill; the
#: ``revised_skills`` policy keys on it. Overridable in the plan.
REVISION_MARKER = "REVISED-BY-G11"

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

#: One line of an exam catalog: ``<skill id> | <dir>/SKILL.md | <text>``.
_CATALOG_LINE_RE = re.compile(
    r"^(?P<id>\S.*?) \| (?P<file>[a-z0-9][a-z0-9-]*/SKILL\.md) \| "
    r"(?P<description>.*)$", re.M)


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


def _ids_of(block: str) -> List[str]:
    """``- <id>`` lines of a listing; ``(none)`` / ``(empty bank)`` is
    the empty list."""
    return [line[2:].strip() for line in block.splitlines()
            if line.startswith("- ")]


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
        for anchor, handler in self._method_prompts():
            if user.startswith(anchor):
                return handler(user)
        if "CLUSTER THEME: " in user:
            return self._cluster_skill(user)
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
            ("You classify ONE segment of a ", self._trigger_family),
            ("You group subtasks of ONE trigger-timing family",
             self._cluster_subtasks),
            ("Reconstruct one exam scene", self._exam_scene),
            ("Choose which skills from the library catalog",
             self._exam_trigger),
            ("Write an action plan for the situation below",
             self._exam_plan),
            ("Judge whether the plan below handles the scene",
             self._exam_judge),
            ("You analyze ONE failed trial of a weaker",
             self._trial_failure_analysis),
            ("You analyze ONE failed paper exam of a weaker",
             self._trial_exam_analysis),
            ("You merge per-trial failure diagnoses into ONE phase review",
             self._requirement_aggregation),
            ("You maintain the accumulated profile of a weaker",
             self._profile_update),
            ("You write ONE reusable three-section skill for a weaker",
             self._requirement_skill),
            ("An agent worked on one task. Before starting it was shown",
             self._applicability_judge),
            ("An agent opened one skill card during a trial.",
             self._adherence_judge),
        )

    # -- G1 / G2 ----------------------------------------------------------

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
        note = self.revision_marker + ": name the concrete state before acting."
        if "SCOPE OF THIS REWRITE - the trigger only" in user:
            text = self._edit_section(original, "[When to use]", note,
                                      "[Do not use when]")
        elif "SCOPE OF THIS REWRITE - the body only" in user:
            text = self._edit_section(original, "[Do not use when]", note,
                                      "[When to use]")
        else:
            text = original + "\n" + note
        return _fenced({
            "failure_category": "wrong_trigger",
            "skill_text": text,
            "rewrite_reason": "made the trigger condition concrete",
            "summary": "search before submitting the final answer",
        })

    @staticmethod
    def _edit_section(text: str, allowed: str, note: str,
                      forbidden: str) -> str:
        """``text`` with ``note`` ending the ``allowed`` section and a
        stray edit ending the ``forbidden`` one (a rewriter that ignores
        its scope; the splice must undo it)."""
        pending = {allowed: note,
                   forbidden: "OUT-OF-SCOPE-EDIT: unrelated wording."}
        out: List[str] = []
        current = None

        def flush() -> None:
            if current in pending:
                out.append(pending.pop(current))

        for line in text.split("\n"):
            marker = next((m for m in SKILL_MARKERS if line.startswith(m)),
                          None)
            if marker is not None:
                flush()
                current = marker
            out.append(line)
        flush()
        out.extend(pending.values())  # sections the text lacks: nothing to splice
        return "\n".join(out)

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

    # -- cluster authoring --------------------------------------------------

    @staticmethod
    def _trigger_family(user: str) -> str:
        """A failed segment is a ``when_command_fails`` moment; otherwise
        the segment's place in its trajectory names the family."""
        keys = re.findall(r'^- "([a-z_]+)":', user, re.M)
        if not keys:
            raise AssertionError(
                "CLUSTER_TRIGGER_FAMILY listed no family keys")
        if "SEGMENT SUCCESS: False" in user and "when_command_fails" in keys:
            family = "when_command_fails"
        else:
            place = re.search(r"segment (\d+) of (\d+)", user)
            first, total = ((int(place.group(1)), int(place.group(2)))
                            if place else (1, 1))
            if first == total and "before_submitting" in keys:
                family = "before_submitting"
            elif first == 1 and "at_task_start" in keys:
                family = "at_task_start"
            else:
                family = keys[_pick(user, len(keys))]
        return _fenced({"family": family,
                        "reason": "the steps show this moment"})

    @staticmethod
    def _cluster_subtasks(user: str) -> str:
        """Pairs of consecutive ids (a leftover joins the last pair), so a
        smoke sees both multi-member and single-member clusters."""
        ids = sorted(re.findall(r"^SUBTASK_ID: (.+)$", user, re.M))
        if not ids:
            raise AssertionError("CLUSTER_SUBTASKS listed no subtask cards")
        groups = [ids[index:index + 2] for index in range(0, len(ids), 2)]
        if len(groups) > 1 and len(groups[-1]) == 1:
            groups[-2].extend(groups.pop())
        family = re.search(r"^FAMILY: (\S+)", user, re.M)
        label = family.group(1).replace("_", " ") if family else "search"
        return _fenced({"clusters": [
            {"theme": f"search the passages for the evidence ({label}, "
                      f"group {number})",
             "subtask_ids": group,
             "reason": "the members call for the same search discipline"}
            for number, group in enumerate(groups, start=1)]})

    @staticmethod
    def _cluster_skill(user: str) -> str:
        phrase = re.search(r"^OPENING PHRASE: (.+)$", user, re.M)
        theme = re.search(r"^CLUSTER THEME: (.+)$", user, re.M)
        if phrase is None or theme is None:
            raise AssertionError(
                "CLUSTER_SKILL lacks its OPENING PHRASE / CLUSTER THEME")
        return _fenced({
            "when_to_use": phrase.group(1).strip() + " the question names "
                           "an entity and asks for a short exact answer.",
            "steps": [
                "When the question names an entity, do Search[<query>] "
                "with its key terms; verify the passage names the entity; "
                "if not, reword <query> and search again.",
                "When a passage answers the question, copy the shortest "
                "phrase that does; verify it matches the asked form; if "
                "unsure, search once more.",
                "When the answer is ready, do Finish[<answer>]; verify "
                "it is a short phrase, not a sentence; if it is long, cut "
                "it down first.",
            ],
            "not_applicable_when": "the answer was already submitted.",
            "summary": "Teach the reader to "
                       + _one_line(theme.group(1)) + ".",
        })

    # -- the generalization exam ---------------------------------------------

    @staticmethod
    def _exam_scene(user: str) -> str:
        instruction = _between(
            user, "that produced the trajectory was asked to do):\n",
            "\n\nSTEPS BEFORE THE CUT")
        question = re.search(r"^## Question\n(.+)$", instruction, re.M)
        topic = _one_line(question.group(1) if question else instruction)
        prior = _between(user, "the reader sees everything up to here):\n",
                         "\n\nSTEPS AFTER THE CUT")
        observed = re.findall(r"^observation: (.+)$", prior, re.M)
        latest = (f" The latest observation reads: {_one_line(observed[-1])}"
                  if observed else "")
        return _fenced({
            "scene_text": f"A reader is answering the question "
                          f"\"{topic}\" with a stepwise search.{latest}",
            "expected_behavior": "Search with the question's key terms, "
                                 "check the passage against the question "
                                 "and submit a short answer only after "
                                 "checking.",
            "reference_answer": "Search[<key terms>], read the passage, "
                                "then Finish[<short answer>].",
        })

    def _exam_trigger(self, user: str) -> str:
        scene = _between(user, "SCENE:\n", "\n\nSKILL CATALOG")
        catalog = _between(user, "SKILL CATALOG", "\n\nRules:")
        entries = _CATALOG_LINE_RE.findall(catalog)
        allowed = re.search(r"may open at most (\d+) skill", user)
        limit = int(allowed.group(1)) if allowed else 1
        chosen: List[int] = []
        if entries and limit > 0:
            index = _pick(scene, len(entries) + 1, "trigger")
            if index < len(entries):
                chosen.append(index)
                if limit > 1 and len(entries) > 1 \
                        and _pick(scene, 3, "second") == 0:
                    chosen.append((index + 1) % len(entries))
        names = []
        for position in chosen:
            skill_id, filename, _description = entries[position]
            # Both spellings the catalog shows are legal names.
            names.append(filename.split("/", 1)[0]
                         if _pick(skill_id, 2) else skill_id)
        reason = ("the catalog line matches the scene" if names
                  else "no catalog line describes this scene")
        return _fenced({"skills": names, "reason": reason})

    @staticmethod
    def _exam_plan(user: str) -> str:
        scene = _between(user, "SCENE:\n", "\n\nWHY THESE SKILLS")
        opened = _between(user, "OPENED SKILLS:\n",
                          "\n\nWrite a plan the reader would follow now:")
        steps = ["Search with the question's key terms.",
                 "Check the returned passage against the question.",
                 "Reply with Finish[<short answer>]."]
        trigger = re.search(r"^\[When to use\] (.+)$", opened, re.M)
        if trigger:
            steps.insert(0, "Apply the opened skill: "
                         + _one_line(trigger.group(1), 120))
        if _pick(scene, 2, "plan-shape"):
            return _fenced({"plan": steps})
        return _fenced({"plan": " ".join(
            f"{number}. {step}" for number, step in enumerate(steps, 1))})

    @staticmethod
    def _exam_judge(user: str) -> str:
        scene = _between(user, "SCENE:\n", "\n\nEXPECTED BEHAVIOR")
        opened = _between(user, "SKILLS THE MODEL OPENED:\n",
                          "\n\nWHY IT OPENED THEM:").strip()
        plan = _between(user, "THE MODEL'S PLAN:\n",
                        "\n\nThe verdict is")
        if not opened:
            return _fenced({
                "verdict": "failure",
                "reason": "The plan was written without opening any skill "
                          "and never checks the passage against the "
                          "question."})
        if _pick(scene + "\0" + plan, 3, "judge") == 0:
            return _fenced({
                "verdict": "failure",
                "reason": "The plan submits an answer without checking the "
                          "passage against the question."})
        return _fenced({
            "verdict": "success",
            "reason": "The plan searches, checks the passage and answers "
                      "briefly."})

    # -- trial-level review ----------------------------------------------------

    @staticmethod
    def _trial_failure_analysis(user: str) -> str:
        steps = _between(user, "TRIAL STEPS:\n",
                         "\n\nSKILLS ACTUALLY DELIVERED")
        delivered = _ids_of(_between(
            user, "SKILLS ACTUALLY DELIVERED TO THIS TRIAL:\n",
            "\n\nALL SKILLS IN THE CURRENT BANK"))
        count = len(re.findall(r"^step \d+$", steps, re.M))
        opened_at = None
        for match in re.finditer(
                r"^step (\d+)\naction: (?:> )?(?:ReadSkill\[|cat )",
                steps, re.M):
            opened_at = int(match.group(1))
            break
        entries = []
        if delivered:
            if opened_at is None:
                issue = ("the card matched the situation but the model "
                         "never opened it")
                start = 0
            else:
                issue = ("the model opened the card and did not follow "
                         "its verification step")
                start = opened_at
            entries.append({"skill_id": delivered[0], "issue": issue,
                            "step_start": start,
                            "step_end": max(count, start + 1)})
        flavour = _pick(steps, 3, "trial")
        return _fenced({
            "reason": "The trial submitted an answer that does not match "
                      "the passage it had retrieved.",
            "profile_note": ("The weak reader answers from memory instead "
                             "of the retrieved passage."
                             if flavour == 0 else ""),
            "skills": entries,
            "new_skill_note": ("No skill covers checking the passage "
                               "against the question before answering."
                               if flavour != 1 else ""),
        })

    @staticmethod
    def _trial_exam_analysis(user: str) -> str:
        judge_reason = _between(user, "JUDGE'S REASON FOR THE FAILURE:\n",
                                "\n\nSKILL UNDER EXAMINATION").strip()
        exam_skill = re.search(
            r"\(this exam was written for it\):\nSKILL_ID: (.+)", user)
        if exam_skill is None:
            raise AssertionError(
                "TRIAL_EXAM_ANALYSIS names no skill under examination")
        exam_id = exam_skill.group(1).strip()
        opened = re.findall(
            r"^SKILL_ID: (.+)$",
            _between(user, "SKILLS THE MODEL OPENED:\n",
                     "\n\nTHE MODEL'S PLAN"), re.M)
        opened = [skill_id.strip() for skill_id in opened]
        flavour = _pick(judge_reason + exam_id, 3, "exam-analysis")
        if exam_id not in opened:
            issue = ("it was not opened, and its catalog line should have "
                     "matched this scene: its trigger is defective"
                     if flavour else
                     "it was not opened, and its catalog line rightly does "
                     "not match this scene: the model chose wrongly")
        elif flavour:
            issue = ("it was opened, and it does not cover the defect the "
                     "judge names: its content is defective")
        else:
            issue = ("it was opened, and it covers what the judge says is "
                     "wrong: the model did not follow a correct skill")
        others = [skill_id for skill_id in opened if skill_id != exam_id]
        return _fenced({
            "reason": "The judge says: " + (judge_reason
                                            or "the plan is wrong."),
            "exam_skill_issue": issue,
            "profile_note": ("The weak reader skips verification unless "
                             "told explicitly." if flavour != 2 else ""),
            "skills": ([{"skill_id": others[0],
                         "issue": "opened and misleading for this scene"}]
                       if others else []),
            "new_skill_note": ("No skill covers verifying the passage "
                               "against the question before answering."
                               if flavour == 0 else ""),
        })

    def _requirement_aggregation(self, user: str) -> str:
        findings = re.split(r"^SHEET_ID: ", _between(
            user, "TRIAL FINDINGS:\n", "\n\nCURRENT SKILL BANK:"),
            flags=re.M)[1:]
        skills_of: Dict[str, List[str]] = {}
        noted: List[str] = []
        for finding in findings:
            sheet_id = finding.split("\n", 1)[0].strip()
            named = re.search(r"^SKILLS: (.*)$", finding, re.M)
            names = [name.strip() for name in
                     (named.group(1).split(", ") if named else [])
                     if name.strip() and name.strip() != "(none)"]
            for name in names:
                skills_of.setdefault(name, []).append(sheet_id)
            note = finding.split("NEW_SKILL_NOTE:\n", 1)[-1].strip()
            if note and note != "(none)":
                noted.append(sheet_id)
        measured = _ids_of(_between(
            user, "MEASURED SKILLS (one action is required for every id "
                  "below):\n", "\n\nDo two jobs."))
        actions = []
        for skill_id in measured:
            sheets = skills_of.get(skill_id)
            if not sheets:
                actions.append({
                    "skill_id": skill_id, "action": "keep",
                    "reason": "No finding implicated this skill.",
                    "source_sheet_ids": []})
                continue
            action = "delete" if _pick(skill_id, 4, "delete") == 0 \
                else "revise"
            actions.append({
                "skill_id": skill_id, "action": action,
                "reason": "The findings show the skill's trigger or "
                          "content did not prevent the failure.",
                "source_sheet_ids": sheets})
        half = len(noted) // 2 if len(noted) > 2 else len(noted)
        requirements = []
        for title, sheets in (
                ("Verify the passage against the question", noted[:half]),
                ("Shorten the final answer to the asked form", noted[half:])):
            if sheets:
                requirements.append({
                    "title": title,
                    "description": title + " before replying with "
                                   "Finish[<answer>].",
                    "trigger": "Before answering, when a passage was "
                               "retrieved.",
                    "source_sheet_ids": sheets})
        return _fenced({"skill_actions": actions,
                        "new_skill_requirements": requirements})

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
    def _requirement_skill(user: str) -> str:
        title = re.search(r"^REQUIREMENT TITLE: (.+)$", user, re.M)
        trigger = re.search(r"^REQUIREMENT TRIGGER: (.+)$", user, re.M)
        if title is None:
            raise AssertionError(
                "REQUIREMENT_SKILL lacks its REQUIREMENT TITLE")
        label = _one_line(title.group(1))
        return _fenced({
            "summary": f"Teach the reader to {label.lower()}.",
            "when_to_use": _one_line(trigger.group(1)) if trigger
                           else "Before answering.",
            "steps": [
                f"When a passage was retrieved, {label.lower()}; verify "
                "the passage names the entity the question asks about; "
                "if not, search again with <query>.",
                "When the check passes, reply with Finish[<answer>]; "
                "verify the answer is short.",
            ],
            "not_applicable_when": "no passage was retrieved yet.",
        })

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
