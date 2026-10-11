"""SearchQA benchmark plugin (layer 3): E1's local stepwise runner.

The plugin is a thin adapter over the outer benchmark/searchqa-eval repo
(README 1.3: environment, task table and scoring come from that repo; the
runner only adapts). Task data, the pre-retrieved context passages and the
EM/F1/sub_EM scoring are all delegated at run time through the
``env.import_eval_repo`` seam; this module owns only loading, the prompt and
loop plumbing.

Design choices pinned here:

- ``Task.instruction`` is the bare question. The eval repo's own protocol is
  single-turn (it stuffs the truncated context into one prompt); this runner
  is stepwise instead (README 1.3: SearchQA runs locally, step by step), so
  the context enters through the env's ``Search`` actions rather than the
  instruction.
- ``load_tasks(None)`` loads the full materialized task set: the train, val
  and test splits of the eval repo concatenated in that order (which split
  an item belongs to is a D2/experiment concern, not the plugin's). It
  refuses when a split file is missing rather than silently returning a
  subset; explicit id selection instead works with whatever is materialized.
- Step budget is ``SEARCHQA_MAX_STEPS`` (6, the old QAEnv default) by
  default, overridable at call time via ``EXPEDISTILL_SEARCHQA_MAX_STEPS``
  (same contract as the ALFWorld plugin's ``max_steps()``): a model that
  only searches never gets scored, which is the intended pressure toward
  committing to an answer.
"""

import os
from pathlib import Path
from typing import Any, List, Optional, Union

from expedistill.benchmarks.base import (
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
)
from expedistill.benchmarks.react_loop import run_react
from expedistill.core.models import Trajectory
from expedistill.infra.reliability import CodeBugError, ConfigError
from expedistill.prompts import PromptSpec, register

# Same-package import through the module object (not ``from .env import``)
# so tests can monkeypatch the seam in one place and both files see it.
from expedistill.benchmarks.searchqa import env as _env

__all__ = ["SEARCHQA_MAX_STEPS", "MAX_STEPS_ENV", "SearchQAPlugin"]

#: Episode budget for one task run; the old QAEnv used the same default.
SEARCHQA_MAX_STEPS = _env.DEFAULT_MAX_STEPS

#: Env var overriding the episode step budget (read at call time).
MAX_STEPS_ENV = "EXPEDISTILL_SEARCHQA_MAX_STEPS"

SEARCHQA_TASK_PROMPT = PromptSpec(
    name="SEARCHQA_TASK_PROMPT",
    template=(
        "You are answering a trivia question with a stepwise search "
        "environment.\n"
        "\n"
        "{skills_block}"
        "## Question\n"
        "{instruction}\n"
        "\n"
        "## Actions\n"
        "Every reply must be exactly one action, in one of these formats:\n"
        "- Search[<query>]: search the passage collection for <query>; the "
        "observation is the passage that best matches the query.\n"
        "- Finish[<answer>]: submit <answer> as your final answer and end "
        "the episode.\n"
        "\n"
        "Search with the question's key terms to gather evidence, then reply "
        "with Finish[<answer>]. Keep the final answer concise: typically a "
        "few words or a short phrase.\n"
        "\n"
        "## Transcript so far\n"
        "{history}\n"
        "\n"
        "Reply with exactly one action."
    ),
    description=(
        "E1 SearchQA runner: stepwise search-environment execution prompt "
        "(actions Search[<query>] / Finish[<answer>]); variables "
        "{instruction}, {skills_block}, {history} per the plugin contract."
    ),
)

register(SEARCHQA_TASK_PROMPT)


class SearchQAPlugin(BenchmarkPlugin):
    """Adapter plugin for the outer benchmark/searchqa-eval repo."""

    #: Pre-registered AUXILIARY metric (the plugin contract in
    #: benchmarks/base.py): the eval repo's sub_EM (substring match), which
    #: scores e.g. answer "lumbar" against gold "lumbar vertebrae" where
    #: the strict-EM primary scores 0. Reported always beside the primary,
    #: never chosen between after seeing results.
    aux_metric_name = "sub_em"

    @property
    def name(self) -> str:
        return "searchqa"

    @property
    def task_prompt_name(self) -> str:
        return "SEARCHQA_TASK_PROMPT"

    @property
    def view_rules(self) -> ViewRules:
        """Truncation rules tuned for search-passage observations.

        ``max_obs_chars=6000`` mirrors the eval repo's own context budget
        (``searchqa_eval.data.MAX_CONTEXT_CHARS``): a rendered episode should
        never spend more of the prompt on passages than the single-turn
        protocol it is compared against. The env already caps each live
        search result at 2000 chars, so this is a backstop for D5 rendering
        of recorded steps; head/tail 1000 keep a passage's leading title and
        closing sentences, which carry most of the retrieval signal.
        """
        return ViewRules(max_obs_chars=6000, head_keep=1000, tail_keep=1000)

    # ------------------------------------------------------------------
    # task loading (D1 delegates here)
    # ------------------------------------------------------------------

    def load_tasks(self, tasks_spec: Union[None, str, List[str]]) -> List[Task]:
        """Resolve a tasks spec into Task objects (eval-repo items).

        ``None`` loads the full materialized task set (train+val+test) and
        refuses if any split file is missing -- a "full set" that silently
        omits a split would mislead D2 downstream; a list selects those item
        ids (works with whatever splits are materialized); a ``str`` is a
        path to a file with one id per line. Unknown ids are a ConfigError.
        All eval-repo imports happen here (lazy), so importing the plugin
        package stays safe without the checkout.
        """
        eval_repo = _env.import_eval_repo()
        items, missing_files = self._load_available_items(eval_repo)
        by_id = {str(item["id"]): item for item in items}

        if tasks_spec is None:
            if missing_files:
                raise ConfigError(
                    "searchqa-eval task data is not materialized; missing: "
                    + ", ".join(missing_files))
            wanted = list(by_id)
        elif isinstance(tasks_spec, str):
            wanted = _read_id_file(tasks_spec)
        elif isinstance(tasks_spec, (list, tuple)):
            wanted = [str(spec) for spec in tasks_spec]
        else:
            raise ConfigError(
                "tasks_spec must be None, an id list or a path to an id "
                f"file, got {type(tasks_spec).__name__}")

        if not by_id:
            raise ConfigError(
                "no SearchQA items found under "
                f"{_env.eval_data_dir()}")
        missing = [task_id for task_id in wanted if task_id not in by_id]
        if missing:
            shown = ", ".join(missing[:5])
            if len(missing) > 5:
                shown += ", ..."
            raise ConfigError(
                f"unknown SearchQA task id(s): {shown} "
                f"({len(missing)} of {len(wanted)} requested ids are not in "
                f"the {len(by_id)} tasks provided by the eval repo)")
        return [self._to_task(by_id[task_id]) for task_id in wanted]

    @staticmethod
    def _load_available_items(eval_repo: Any) -> Any:
        """Load every split the eval repo materialized.

        Returns ``(items, missing_files)``: the caller decides whether
        missing split files are fatal (full-set loading) or merely shrink
        the pool for explicit id selection.
        """
        data_dir = _env.eval_data_dir()
        items: List[dict] = []
        missing_files: List[str] = []
        for split in _env.SPLITS:
            path = data_dir / split / "items.json"
            if not path.is_file():
                missing_files.append(str(path))
                continue
            items.extend(eval_repo.data.load_items(path))
        return items, missing_files

    @staticmethod
    def _to_task(item: dict) -> Task:
        """Map one eval-repo item to a Task; the runner reads meta at run time."""
        return Task(
            task_id=str(item["id"]),
            instruction=str(item["question"]),
            meta={
                "context": str(item.get("context", "")),
                "answers": [str(a) for a in item.get("answers", [])],
            },
        )

    # ------------------------------------------------------------------
    # execution (E1 delegates here)
    # ------------------------------------------------------------------

    def run(self, task: Task, handle: Any, skills_block: str,
            workdir: Optional[Union[str, Path]], usage: Any = None) -> RunOutcome:
        """Run one task once, stepwise, through the shared ReAct loop."""
        max_steps = self.max_steps()
        episode_env = _env.SearchQAEnv(
            question=task.instruction,
            answers=task.meta.get("answers", []),
            context=task.meta.get("context", ""),
            max_steps=max_steps,
        )
        outcome = run_react(
            task,
            episode_env,
            handle,
            task_prompt_name=self.task_prompt_name,
            skills_block=skills_block,
            max_steps=max_steps,
            usage=usage,
            # No run_dir: request dumps follow the handle's run_dir (K1
            # attaches <run>), not the per-unit workdir (README section 2).
            view_rules=self.view_rules,
        )
        if episode_env.eval_result is not None:
            outcome.meta["searchqa"] = dict(episode_env.eval_result)
        return outcome

    @staticmethod
    def max_steps() -> int:
        """The episode step budget; overridable for experiments."""
        raw = os.environ.get(MAX_STEPS_ENV, "")
        if not str(raw).strip():
            return SEARCHQA_MAX_STEPS
        try:
            value = int(raw)
        except ValueError:
            raise ConfigError(
                f"{MAX_STEPS_ENV} must be an integer, got {raw!r}") from None
        if value < 1:
            raise ConfigError(f"{MAX_STEPS_ENV} must be >= 1, got {value}")
        return value

    # ------------------------------------------------------------------
    # auxiliary metric (aux_metric_name = "sub_em")
    # ------------------------------------------------------------------

    def aux_success(self, traj: Trajectory) -> bool:
        """sub_EM of the submitted answer (1.0 = auxiliary success).

        Strict about genuinely broken data, honest about a real zero:

        - a scored episode carries ``meta["searchqa"]`` (the evaluator's
          em/f1/sub_em dict, copied verbatim by ``run``); sub_em >= 1 is
          the auxiliary success;
        - a VALID trajectory without a submitted answer (step-budget
          exhaustion / model output error) has no eval meta, and no
          answer can be a substring of gold: sub_EM is 0, a real
          measurement;
        - a trajectory whose status or termination REQUIRES a score
          (success, or an episode the env terminated via Finish) but has
          no eval meta is broken data, not a zero: CodeBugError, so the
          run stops loudly instead of quietly under-reporting.
        """
        meta = traj.meta.get("searchqa") if isinstance(traj.meta, dict) else None
        if isinstance(meta, dict) and "sub_em" in meta:
            return float(meta["sub_em"]) >= 1.0
        if traj.status == "success" or traj.meta.get("termination") \
                == "terminated":
            raise CodeBugError(
                f"trajectory {traj.trajectory_id} has no searchqa eval "
                "result, but a scored Finish always records one; the "
                "stored record is broken")
        return False


def _read_id_file(path: str) -> List[str]:
    """Read a task-id file: one id per line, blank lines ignored."""
    file_path = Path(path).expanduser()
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"cannot read SearchQA task id file {file_path}: {exc}") from exc
    ids = [line.strip() for line in text.splitlines() if line.strip()]
    if not ids:
        raise ConfigError(f"SearchQA task id file {file_path} contains no ids")
    return ids
