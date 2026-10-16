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

Adapted from the old SkillExpand adapter (ExpeToSkill-merged
src/skillexpand/benchmarks/alfworld.py): the success semantics ("won" from the
engine, surfaced as "Task is SOLVED."), the command-format explanation in the
task prompt (reused from the old ALFWorld execution contract nearly verbatim)
and the step budget (50, the engine's own episode budget in the eval repo's
textworld.yaml) all come from there.
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
from expedistill.benchmarks.base import (
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
)
from expedistill.benchmarks.react_loop import run_react
from expedistill.infra.reliability import ConfigError
from expedistill.prompts import PromptSpec, register

__all__ = [
    "DEFAULT_MAX_STEPS",
    "MAX_STEPS_ENV",
    "TASK_PROMPT_NAME",
    "TASK_PROMPT_TEMPLATE",
    "ALFWorldPlugin",
    "clean_task_id",
    "enumerate_gamefiles",
]

#: B2 name of this plugin's task execution prompt.
TASK_PROMPT_NAME = "ALFWORLD_TASK_PROMPT"

#: The task prompt: EXACTLY the three shared variables {instruction},
#: {skills_block} (renders empty for bare runs -- bare and skills runs share
#: this one template) and {history}. The command-format section is the old
#: repo's ALFWorld execution contract, kept nearly verbatim: it is measured
#: knowledge about this engine, not style.
TASK_PROMPT_TEMPLATE = """\
You act in the ALFWorld text household simulator and must complete a household task.
Use exact object/receptacle IDs from actual observations and admissible commands.
Commands: go to <receptacle>; open/close <receptacle>; examine <object/receptacle>;
take <object> from <receptacle>; move <held object> to <receptacle>; inventory;
use <lamp>; clean <held object> with <sinkbasin>; heat <held object> with <microwave>;
cool <held object> with <fridge>. Use > action or > think: reasoning syntax.
This TextWorld setup uses move ... to ... for placement, not put ... in/on ... .
Admissible commands describe the CURRENT state; use them rather than inventing syntax.
Check possession: this setup allows one carried object. Place an irrelevant held
object before taking another; deliver two-object goals one object at a time.
Never substitute pot for pan, bowl for plate, or an imagined object for an observed ID.
An open empty container is empty: do not invent hidden contents or repeatedly examine it.
There is no camera-orientation command or abstract verify-goal action. A failed action
can reflect location, possession or prerequisites, not necessarily a syntax problem.
For lamp goals hold the requested object and use the lamp. Only environment won feedback
confirms completion; thoughts or apparent placement do not establish success.
Every new trial resets inventory, object placement, open/closed and processed state.
Prior observed locations are task-local leads to recheck, not universal facts.
Remember checked empty locations within a trial; try unvisited locations and reserve
steps for taking, processing and delivery. Consecutive identical actions terminate
the episode, even for look/examine. The step budget is limited; act efficiently.

Your task:
{instruction}
{skills_block}
Interaction so far:
{history}

Reply with your next command only, on a single line (e.g. "go to fridge 1")."""

register(PromptSpec(
    name=TASK_PROMPT_NAME,
    template=TASK_PROMPT_TEMPLATE,
    description="E1: ALFWorld stepwise task execution prompt (shared by bare "
                "and skills runs; the skills block renders empty when absent).",
))

#: The loop budget for one episode. 50 matches the engine's own episode budget
#: (dagger/rl training max_nb_steps_per_episode in the eval repo's
#: textworld.yaml), so the loop and the engine never disagree.
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
    delegates to the shared react loop.
    """

    @property
    def name(self) -> str:
        return "alfworld"

    @property
    def task_prompt_name(self) -> str:
        return TASK_PROMPT_NAME

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
            # An InfrastructureError from reset (hung adapter, dead
            # subprocess) propagates: K1 retries the unit (R2b contract).
            initial_observation = env.reset()
            # ALFWorld's task line names no receptacles; the room description
            # from reset is the rest of the instruction, exactly as the old
            # ReAct fewshots and the outer eval repo's own runner present it.
            # run_react's reset below is a no-op (fresh episode, no steps),
            # so this costs one subprocess per episode, not two.
            instruction = str(task.instruction).rstrip()
            if initial_observation:
                instruction = f"{instruction}\n\n{initial_observation}"
            effective = Task(task_id=task.task_id, instruction=instruction,
                             meta=dict(task.meta))
            return run_react(
                effective,
                env,
                handle,
                task_prompt_name=self.task_prompt_name,
                skills_block=skills_block,
                max_steps=self.max_steps(),
                # No run_dir: request dumps follow the handle's run_dir (K1
                # attaches <run>), not the per-unit workdir (README section 2).
                view_rules=self.view_rules,
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
