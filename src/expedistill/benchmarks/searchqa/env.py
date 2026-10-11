"""SearchQA stepwise environment (layer 3): local adaptation of the outer
benchmark/searchqa-eval repo for E1's SearchQA runner.

Adapted from the SkillExpand adapter (ExpeToSkill-merged @ TB-eval 4f436af,
``benchmarks/searchqa.py``). What was kept from the old ``QAEnv``:

- The ReAct action grammar and the ``parse_action`` regex
  (``Search[<query>]`` / ``Finish[<answer>]``, ``Type[argument]``,
  non-empty single-line argument).
- The finish observations ``Answer is CORRECT.`` / ``Answer is INCORRECT.``
  and the invalid-action rule: an unrecognized action is answered with an
  observation listing the valid formats, never raised.
- The default step budget of 6.

What changed, and why:

- The old adapter searched a live docstore (langchain Wikipedia via
  ``DocstoreExplorer``). The outer eval repo has no live search: each item
  carries pre-retrieved passages in a ``[DOC]``-separated ``context``
  (searchqa_eval.data). ``Search`` therefore ranks those passages by
  query-term overlap -- the old ``ContextExplorer.search`` mechanism pointed
  at the item's own context -- and returns the best match, hard-truncated to
  ``SEARCH_RESULT_CHAR_BUDGET``. ``Lookup`` is not migrated: it existed to
  drill into a live docstore page, which this data does not have.
- The gym 5-tuple return collapses to a plain ``str`` observation
  (foundation-wave decision, benchmarks/base.py): the loop polls
  ``is_terminated`` / ``is_truncated`` and reads ``success_fn()`` once.
- Scoring is delegated to the eval repo's evaluator (SQuAD-normalized
  EM / F1 / sub_EM) through the ``import_eval_repo`` seam; ``success_fn`` is
  EM == 1, the headline metric of the SkillOpt protocol the eval repo ports.

The eval repo is imported lazily: importing this module must succeed even
when the outer checkout or its dependencies are absent (AGENTS.md section 9).
No ``environment_call`` deadline is used: the env is in-process string
ranking with no subprocess to lose (the pipe-death lesson does not apply).
"""

import importlib
import os
import re
import sys
from pathlib import Path
from typing import Any, List, Optional, Tuple

from expedistill.benchmarks.base import BaseEnv
from expedistill.infra.reliability import ConfigError

__all__ = [
    "EVAL_DIR_ENV",
    "SPLITS",
    "SEARCH_RESULT_CHAR_BUDGET",
    "DEFAULT_MAX_STEPS",
    "import_eval_repo",
    "eval_data_dir",
    "parse_action",
    "SearchQAEnv",
]

#: Env var pointing at the outer benchmark/searchqa-eval checkout
#: (AGENTS.md section 9; listed in .env.example).
EVAL_DIR_ENV = "EXPEDISTILL_SEARCHQA_EVAL_DIR"

#: The eval repo materializes items as {id, question, context, answers} JSON
#: arrays under data/searchqa_split/<split>/items.json (its split_manifest.json);
#: loading the "full task set" means all three splits concatenated in this order.
SPLITS = ("train", "val", "test")

#: Per-observation budget for one Search result. The eval repo's own protocol
#: spends MAX_CONTEXT_CHARS = 6000 on the whole context; a stepwise env shows
#: one passage at a time, so a third of that keeps several searches inside the
#: same prompt budget the single-turn protocol used.
SEARCH_RESULT_CHAR_BUDGET = 2000

#: Episode budget; the old QAEnv used the same default.
DEFAULT_MAX_STEPS = 6

#: Old-adapter wording, minus the Lookup action that was not migrated.
INVALID_ACTION_MESSAGE = (
    "Invalid action. Valid actions are Search[<query>] and Finish[<answer>]."
)

_ACTION_RE = re.compile(r"^(\w+)\[(.+)\]$")

_EVAL_REPO_CACHE: Any = None


def import_eval_repo() -> Any:
    """Import the outer searchqa-eval package and return it (the test seam).

    The returned object exposes the submodules this plugin uses:
    ``data.load_items`` (task loading) and ``evaluator.evaluate`` (scoring).
    Both are stdlib-only in the eval repo, so no venv switch is needed.

    Failure taxonomy: a missing env var or a directory that is not the eval
    checkout is a ConfigError pointing at .env.example; anything else from the
    import is wrapped the same way because the eval repo's data/evaluator
    modules have no third-party deps -- an ImportError here means the path is
    wrong, not the machine. The successful package is cached: the path
    resolution and ``sys.path`` insertion run once per process.
    """
    global _EVAL_REPO_CACHE
    if _EVAL_REPO_CACHE is not None:
        return _EVAL_REPO_CACHE
    raw = os.environ.get(EVAL_DIR_ENV, "").strip()
    if not raw:
        raise ConfigError(
            f"{EVAL_DIR_ENV} is not set; copy .env.example to .env and point "
            f"{EVAL_DIR_ENV} at the outer benchmark/searchqa-eval checkout.")
    src_dir = Path(raw).expanduser() / "src"
    if not src_dir.is_dir():
        raise ConfigError(
            f"{EVAL_DIR_ENV}={raw!r} does not point at the searchqa-eval "
            "checkout (no src/ directory inside it); see .env.example.")
    src_str = str(src_dir)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)
    try:
        package = importlib.import_module("searchqa_eval")
        importlib.import_module("searchqa_eval.data")
        importlib.import_module("searchqa_eval.evaluator")
    except ImportError as exc:
        raise ConfigError(
            f"cannot import the searchqa-eval package from "
            f"{EVAL_DIR_ENV}={raw!r}: {exc}") from exc
    _EVAL_REPO_CACHE = package
    return package


def eval_data_dir() -> Path:
    """Directory of the eval repo's materialized split data (task items)."""
    raw = os.environ.get(EVAL_DIR_ENV, "").strip()
    if not raw:
        raise ConfigError(
            f"{EVAL_DIR_ENV} is not set; copy .env.example to .env and point "
            f"{EVAL_DIR_ENV} at the outer benchmark/searchqa-eval checkout.")
    return Path(raw).expanduser() / "data" / "searchqa_split"


def parse_action(action: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse ``Type[argument]``; anything else is (None, None).

    Kept verbatim from the old adapter (HotpotQA/Fever action grammar):
    anchored, non-empty, single-line argument -- a ``Finish[]`` with no
    argument parses as invalid, and a multi-line answer cannot be smuggled
    through the bracket.
    """
    match = _ACTION_RE.match(action)
    if match:
        return match.group(1), match.group(2)
    return None, None


def _truncate_search_result(text: str) -> str:
    """Hard-cut one search result to the budget, marking the cut (never silent)."""
    if len(text) <= SEARCH_RESULT_CHAR_BUDGET:
        return text
    omitted = len(text) - SEARCH_RESULT_CHAR_BUDGET
    return text[:SEARCH_RESULT_CHAR_BUDGET] + (
        f"\n[... {omitted} characters truncated ...]")


class SearchQAEnv(BaseEnv):
    """Stepwise SearchQA episode over one item's question and context.

    ``reset()`` starts a fresh episode (the passages are fixed per item, so
    only the per-episode state resets). ``step`` never raises on model output:
    unknown actions get the invalid-format observation. ``Finish[<answer>]``
    scores through the eval repo's evaluator, flags termination and reports
    the full result (em/f1/sub_em/predicted/gold) in ``self.eval_result`` for
    the runner's ``RunOutcome.meta``.
    """

    def __init__(self, question: str, answers: List[str], context: str = "",
                 max_steps: int = DEFAULT_MAX_STEPS):
        self.question = str(question)
        self.answers = [str(a) for a in answers]
        self.context = str(context or "")
        self.max_steps = int(max_steps)
        self.docs: List[str] = [
            doc.strip() for doc in self.context.split("[DOC]") if doc.strip()]
        self._lowered_docs = [doc.lower() for doc in self.docs]
        self.reset()

    def reset(self) -> None:
        self.curr_step = 0
        self.terminated = False
        self.answer: Optional[str] = None
        self.eval_result: Optional[dict] = None

    def step(self, action: str) -> str:
        action = str(action).strip()
        action_type, argument = parse_action(action)
        if action_type == "Finish":
            observation = self._finish(argument)
        elif action_type == "Search":
            observation = self._search(argument)
        else:
            observation = INVALID_ACTION_MESSAGE
        self.curr_step += 1
        return observation

    def success_fn(self) -> bool:
        if self.eval_result is None:
            return False
        return float(self.eval_result.get("em", 0.0)) >= 1.0

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _search(self, query: str) -> str:
        """Return the passage that best matches the query terms.

        Old-adapter semantics (ContextExplorer.search): rank the passages by
        query-term overlap and return the top one -- ties keep file order,
        and a query with no matching terms still returns the first passage
        rather than an error, so the episode can continue.
        """
        if not self.docs:
            return ("No passages are available for this question; reply with "
                    "Finish[<answer>].")
        terms = [t for t in re.split(r"\W+", str(query).lower()) if t]

        def score(index: int) -> int:
            return sum(1 for term in terms if term in self._lowered_docs[index])

        best = max(range(len(self.docs)), key=score)
        return _truncate_search_result(self.docs[best])

    def _finish(self, answer: str) -> str:
        """Score the submitted answer and end the episode."""
        self.answer = str(answer).strip()
        evaluator = import_eval_repo().evaluator
        self.eval_result = dict(evaluator.evaluate(self.answer, list(self.answers)))
        self.terminated = True
        if self.success_fn():
            return "Answer is CORRECT."
        return "Answer is INCORRECT."
