"""ALFWorld benchmark plugin (layer 3): task loading, prompt, run, view rules.

Task ids are game-file paths relative to ``$ALFWORLD_DATA`` (the convention of
the eval repo's items.json manifests), e.g.::

    json_2.1.1/valid_seen/pick_and_place_simple-Cloth-None-BathtubBasin-405/
    trial-20170907-123456/game.tw-pddl

The task instruction is the human annotation that ships next to every game
(``traj_data.json`` -> ``turk_annotations.anns[0].task_desc``), which is the
same text the engine puts behind "Your task is to:" in the initial
observation.

The ``___N`` bookkeeping suffix: old ExpeL-style task entries carried a
trailing ``___<index>`` that was an index into ExpeL's own task list, not part
of the task. The old react loop stripped it from the task text
(``BaseAgent.remove_task_suffix``); with the react loop now benchmark-blind
that knowledge moved here -- ``load_tasks`` strips the suffix from every
incoming id (and from instruction text) before anything else sees it. Only a
trailing ``___<digits>`` is stripped, so SearchQA-style legitimate blanks such
as "____ Jesus" are unaffected.

Execution runs under the outer eval repo's unified protocol
(``benchmarks/alfworld/protocol.py``): the same system message, the same
step templates (no history at step 1, the last two interactions afterwards),
the engine's admissible action list, ``<think>...</think><action>...</action>``
replies, one correction request for an invalid reply, temperature 0,
``max_tokens`` 4096 and the engine's own 50-step budget. The earlier local
prompt repeated the room description every step and accepted free-form
commands; it measured a materially easier protocol and its numbers are not
comparable with the unified runner's.

Progressive skill disclosure is kept: a folder unit shows the card list
(``<available_skills>``) in the prompt's skill slot and the model opens a
card with ``<action>ReadSkill[<location>]</action>``, which the loop answers
without touching the environment or spending a game step.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, List, Union

from expedistill.benchmarks.alfworld.env import (
    DATA_ENV,
    DATA_ROOT_SUBDIR,
    GAME_SUFFIX,
    ALFWorldEnv,
    resolve_gamefile,
)
from expedistill.benchmarks.alfworld.learning import ALFWORLD_CONTEXT
from expedistill.benchmarks.alfworld.playbook import (
    PLAYBOOK_PROMPT_NAME, facts_from_sheets, playbook_requirements)
from expedistill.benchmarks.alfworld.protocol import (
    CORRECTION_PROMPT_NAME,
    FOLDER_PROMPT_NAME,
    FOLDER_PROMPT_NO_HISTORY_NAME,
    PROMPT_NAME,
    PROMPT_NO_HISTORY_NAME,
    SYSTEM_PROMPT_NAME,
    protocol_handle,
    run_unified,
)
from expedistill.benchmarks.base import (
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
)
from expedistill.benchmarks.skill_folder import SkillFolder
from expedistill.infra.reliability import ConfigError

__all__ = [
    "DEFAULT_MAX_STEPS",
    "MAX_STEPS_ENV",
    "ALFWorldPlugin",
    "clean_task_id",
    "enumerate_gamefiles",
]

#: The loop budget for one episode. 50 matches the engine's own episode budget
#: (dagger/rl training max_nb_steps_per_episode in the eval repo's
#: textworld.yaml) and the unified runner's, so all three agree.
DEFAULT_MAX_STEPS = 50
MAX_STEPS_ENV = "EXPEDISTILL_ALFWORLD_MAX_STEPS"

#: Old ExpeL task entries carry a trailing ``___<index>`` bookkeeping suffix.
#: Digits + end anchor only: SearchQA clues legitimately contain blanks like
#: "____ Jesus" (four underscores, no digits), which must survive.
_TASK_SUFFIX_RE = re.compile(r"___\d+$")


def clean_task_id(task_id: str) -> str:
    """Strip the old ``___N`` bookkeeping suffix from a task id or text."""
    return _TASK_SUFFIX_RE.sub("", str(task_id).strip())


def enumerate_gamefiles(data_root: Union[str, Path]) -> List[str]:
    """Every game file under the data root, as sorted data-root-relative ids.

    Enumerates all splits (train / valid_seen / valid_unseen / ...) so the
    full universe of tasks is visible to D1; which subset an experiment uses
    is configuration (an explicit id list frozen into the manifest), not
    something load_tasks decides.
    """
    root = Path(data_root) / DATA_ROOT_SUBDIR
    if not root.is_dir():
        raise ConfigError(
            f"{DATA_ENV} has no {DATA_ROOT_SUBDIR!r} directory under "
            f"{str(data_root)!r}; is it the ALFWorld data root?")
    return sorted(
        path.relative_to(data_root).as_posix()
        for path in root.rglob(GAME_SUFFIX)
    )


def _instruction_for(gamefile: str) -> str:
    """The task's human-annotated instruction, from the game's traj_data.json."""
    traj = Path(gamefile).parent / "traj_data.json"
    if not traj.is_file():
        raise ConfigError(
            f"game file {gamefile!r} has no traj_data.json next to it; the "
            "ALFWorld data tree looks incomplete")
    try:
        data = json.loads(traj.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read {str(traj)!r}: {exc}") from None
    anns = (data.get("turk_annotations") or {}).get("anns") or []
    if not anns:
        raise ConfigError(f"no turk annotations in {str(traj)!r}")
    description = str(anns[0].get("task_desc") or "").strip()
    if not description:
        raise ConfigError(f"empty task description in {str(traj)!r}")
    return clean_task_id(description)


def _relative_id(task_id: str, gamefile: str, data_root: Path) -> str:
    """``task_id`` as a data-root-relative posix path when the game lies under
    the data root; an absolute id outside it is kept as given."""
    if not Path(task_id).expanduser().is_absolute():
        return task_id
    try:
        return Path(gamefile).resolve().relative_to(
            data_root.resolve()).as_posix()
    except ValueError:
        return task_id


class ALFWorldPlugin(BenchmarkPlugin):
    """The ALFWorld benchmark: local stepwise execution through the eval venv.

    Importing this class (or the plugin package) needs no environment
    variables; every variable is resolved when a task is loaded or a run
    starts, and a missing one is a ConfigError. ``run`` builds an
    ``ALFWorldEnv`` (the subprocess bridge to the eval repo's venv) and
    delegates to the unified-protocol loop.
    """

    #: Skill-folder capability: progressive disclosure through the shared
    #: folder contract (``benchmarks/skill_folder.py``).
    skill_folder_prompt_name = FOLDER_PROMPT_NAME
    learning_context = ALFWORLD_CONTEXT
    #: Learned-playbook capability: the writing prompt lives in
    #: ``alfworld/playbook.py``; ``run_unified`` renders the manifest's
    #: ``playbook_text`` above the card list at every step.
    playbook_prompt_name = PLAYBOOK_PROMPT_NAME
    #: The protocol's other frozen prompt names: the step-1 variant of each
    #: template, the system message and the one-shot correction request.
    #: Stage 0 freezes these too, so a protocol edit cannot slip past a
    #: resume.
    protocol_prompt_names = (
        SYSTEM_PROMPT_NAME,
        PROMPT_NO_HISTORY_NAME,
        FOLDER_PROMPT_NO_HISTORY_NAME,
        CORRECTION_PROMPT_NAME,
    )

    @property
    def name(self) -> str:
        return "alfworld"

    @property
    def task_prompt_name(self) -> str:
        return PROMPT_NAME

    def playbook_facts(self, sheets):
        return facts_from_sheets(sheets)

    def playbook_requirements(self):
        return playbook_requirements()

    @property
    def view_rules(self) -> ViewRules:
        # ALFWorld observations are short (room descriptions and one-line
        # command results, typically well under 500 characters), so the class
        # defaults are already lenient and never bite in practice; they are
        # restated here so the intent survives a default change in base.py.
        return ViewRules(max_obs_chars=4000, head_keep=200, tail_keep=200)

    # -- task loading (D1) ---------------------------------------------------

    def load_tasks(
        self, tasks_spec: Union[None, str, List[str]]
    ) -> List[Task]:
        raw = os.environ.get(DATA_ENV, "")
        if not str(raw).strip():
            raise ConfigError(
                f"{DATA_ENV} is not set; the ALFWorld plugin needs the outer "
                "benchmark/alfworld-eval environment (see .env.example)")
        data_root = Path(raw).expanduser()
        if not data_root.is_dir():
            raise ConfigError(f"{DATA_ENV} is not a directory: {raw!r}")

        if tasks_spec is None:
            ids = enumerate_gamefiles(data_root)
        elif isinstance(tasks_spec, str):
            ids = self._ids_from_file(tasks_spec)
        else:
            ids = [clean_task_id(task_id) for task_id in tasks_spec]
        if not ids:
            return []

        tasks: List[Task] = []
        for task_id in ids:
            gamefile = resolve_gamefile(task_id, data_root)
            # Ids and meta stay relative to $ALFWORLD_DATA: stage 0 freezes
            # them, and the data root is a machine-specific location (moving
            # an identical tree must not break a resume). run() re-resolves
            # the id against the current data root.
            task_id = _relative_id(task_id, gamefile, data_root)
            tasks.append(Task(
                task_id=task_id,
                instruction=_instruction_for(gamefile),
                meta={
                    "gamefile": task_id,
                    "split": Path(task_id).relative_to(DATA_ROOT_SUBDIR).parts[0]
                    if task_id.startswith(DATA_ROOT_SUBDIR + "/") else None,
                },
            ))
        return tasks

    @staticmethod
    def _ids_from_file(path: str) -> List[str]:
        file_path = Path(path).expanduser()
        if not file_path.is_file():
            raise ConfigError(f"ALFWorld task file {path!r} does not exist")
        ids = []
        for line in file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ids.append(clean_task_id(line))
        return ids

    # -- execution (E1's local runner) ---------------------------------------

    def run(
        self,
        task: Task,
        handle: Any,
        skills_block: str,
        workdir: Any,
    ) -> RunOutcome:
        env = ALFWorldEnv(task.task_id)
        try:
            # A folder unit shows only the card list; the delivery's catalog
            # text in ``skills_block`` is the TB fallback and is not used.
            folder = SkillFolder.from_task(task)
            return run_unified(
                task,
                env,
                protocol_handle(handle),
                skill_folder=folder,
                skills_block=skills_block,
                max_steps=self.max_steps(),
            )
        finally:
            env.close()

    @staticmethod
    def max_steps() -> int:
        """The episode step budget; overridable for experiments."""
        raw = os.environ.get(MAX_STEPS_ENV, "")
        if not str(raw).strip():
            return DEFAULT_MAX_STEPS
        try:
            value = int(raw)
        except ValueError:
            raise ConfigError(
                f"{MAX_STEPS_ENV} must be an integer, got {raw!r}") from None
        if value < 1:
            raise ConfigError(f"{MAX_STEPS_ENV} must be >= 1, got {value}")
        return value
