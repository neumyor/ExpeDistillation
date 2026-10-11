"""Experiment configuration (stage 0) and its YAML (de)serialisation.

One YAML file under ``configs/experiments/`` describes one experiment. The
frozen copy of this config (``to_dict``) goes into the run manifest; changing
any field after the run started is a new experiment (B4 enforces that).

The role table is a plain dict on purpose: ``models[role] -> model name``. Two
entries are the anchors -- ``executor_strong`` and ``executor_weak`` name the
strong and weak executor models -- and every other role defaults to the strong
model except ``answerer``, which defaults to the weak one (the exam is meant
to probe what the weak model can do). Users override any role by giving it
explicitly; ``__post_init__`` fills in the defaults, so a partially specified
table is completed, never rejected.

The named-rule selections get the same treatment: ``DEFAULT_RULES`` names
each stage's rule, ``__post_init__`` fills every missing key, so after
construction ``config.rules`` always has all three ``RULE_KEYS`` and the
frozen config carries the effective names (not a raw, possibly empty,
mapping). Stage 0 validates the names against the rule registries before
anything runs; later stages read ``config.rules[key]`` directly.

``tasks`` names the experiment's task set: ``None`` (the plugin's full set;
a plugin may reject it, TB does), a list of raw task ids, or a path to a
task file whose format the plugin defines. ``load_config`` resolves a
relative path against the config file's directory.

Deviation from the layer rule "core depends on stdlib only": ``load_config`` /
``save_config`` need YAML, and the wave spec places them here. The ``yaml``
import is function-local so that importing the data model never pulls the
dependency in; moving YAML I/O into ``infra`` later is a drop-in change if the
team prefers a strictly stdlib core.
"""

import json
from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Optional, Union

#: Experiment modes (README stage 0). ``split`` divides the task set into
#: train/test; ``full`` distills on everything and re-runs with the bank.
MODES = ("split", "full")

#: Every role a stage may request from the role table.
MODEL_ROLES = (
    "executor_strong", "executor_weak", "segmenter", "author", "examiner",
    "answerer", "judge", "rewriter", "curator",
)

#: Keys of the named-rule selections.
RULE_KEYS = ("task_select", "segment_select", "trajectory_select")

#: Rule name per key when the config names none: the README definitions of
#: stage 1 (D4), stage 3 (D6) and stage 2's frozen default (all trajectories).
#: Each must be registered in ``blocks/rules.py`` under exactly this name.
DEFAULT_RULES = {
    "task_select": "all_valid_failed",
    "segment_select": "strong_all_weak_failed",
    "trajectory_select": "all",
}

#: Keys accepted in the ``split`` field: either explicit task-id lists, or a
#: ratio + seed from which stage 0 derives the lists deterministically.
_SPLIT_LIST_KEYS = ("train", "test")
_SPLIT_RATIO_KEYS = ("ratio", "seed")

CONFIG_SCHEMA_VERSION = 1


def _normalise_tasks(tasks: Any) -> Union[None, str, List[str]]:
    """Validate the ``tasks`` field; a tuple of ids becomes a list (the
    frozen manifest is JSON, where a tuple would not compare equal)."""
    if tasks is None:
        return None
    if isinstance(tasks, str):
        if not tasks.strip():
            raise ValueError("tasks path must be a non-empty string")
        return tasks
    if isinstance(tasks, (list, tuple)):
        if not tasks or not all(isinstance(t, str) and t for t in tasks):
            raise ValueError(
                "tasks must be a non-empty list of task-id strings, "
                f"got {tasks!r}")
        seen: set = set()
        duplicates = set()
        for task_id in tasks:
            (duplicates if task_id in seen else seen).add(task_id)
        if duplicates:
            raise ValueError(f"tasks lists duplicate task ids: {sorted(duplicates)}")
        return list(tasks)
    raise ValueError(
        "tasks must be None, a task-file path or a list of task ids, "
        f"got {type(tasks).__name__}")


def _validate_split(split: Dict[str, Any]) -> None:
    keys = set(split)
    if keys <= set(_SPLIT_LIST_KEYS) and keys:
        for name in _SPLIT_LIST_KEYS:
            value = split.get(name)
            if value is None:
                continue
            if (not isinstance(value, list)
                    or not all(isinstance(t, str) for t in value)):
                raise ValueError(
                    f"split[{name!r}] must be a list of task-id strings, "
                    f"got {value!r}")
    elif _SPLIT_RATIO_KEYS[0] in keys:
        ratio = split[_SPLIT_RATIO_KEYS[0]]
        if not isinstance(ratio, (int, float)) or not 0.0 < ratio < 1.0:
            raise ValueError(f"split ratio must be in (0, 1), got {ratio!r}")
        seed = split.get("seed", 0)
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise ValueError(f"split seed must be an int, got {seed!r}")
        if keys - set(_SPLIT_RATIO_KEYS):
            raise ValueError(
                f"split mixes ratio and explicit lists: {sorted(keys)}")
    else:
        raise ValueError(
            "split must give either {'train': [...], 'test': [...]} or "
            f"{{'ratio': float, 'seed': int}}, got keys {sorted(keys)}")


@dataclass
class ExperimentConfig:
    """All method parameters of one experiment (frozen into the manifest)."""

    benchmark: str
    mode: str
    models: Dict[str, str]
    weak_profile: str = ""
    n_attempts_strong: int = 1
    n_attempts_weak: int = 1
    #: Max number of segments per trajectory (K).
    K: int = 5
    #: None in ``full`` mode; task-id lists or ratio+seed in ``split`` mode.
    split: Optional[Dict[str, Any]] = None
    #: The experiment's task set: None (plugin's full set), a list of raw
    #: task ids, or a task-file path (plugin-interpreted). Stage 0 loads
    #: exactly this; explicit split lists must then cover it exactly.
    tasks: Union[None, str, List[str]] = None
    segment_retry_limit: int = 2
    retry_limit: int = 2
    bank_token_budget: int = 4096
    curation_rounds: int = 3
    #: Literal-overlap threshold for the leak audit (D8), in [0, 1].
    leak_threshold: float = 0.3
    #: Named-rule selections; missing keys are filled from DEFAULT_RULES,
    #: and stage 0 validates every name against the rule registries.
    rules: Dict[str, str] = field(default_factory=dict)
    #: Prompt name -> digest; filled by the prompt registry (B2) when the
    #: stages freeze the manifest, empty in the source config.
    prompts_digests: Dict[str, str] = field(default_factory=dict)
    #: Model NAME -> extra chat-call parameters (e.g. ``reasoning_effort``)
    #: forwarded to the endpoint for every call of that model, whichever
    #: role makes it. Changes what the model produces, so it is part of the
    #: experiment's frozen identity, not an operational setting.
    model_options: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    schema_version: int = CONFIG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        if not self.benchmark:
            raise ValueError("benchmark is required")
        if not 0.0 <= self.leak_threshold <= 1.0:
            raise ValueError(
                f"leak_threshold must be in [0, 1], got {self.leak_threshold!r}")
        if self.n_attempts_strong < 1 or self.n_attempts_weak < 1:
            raise ValueError("n_attempts_strong and n_attempts_weak must be >= 1")
        if self.K < 1:
            raise ValueError(f"K must be >= 1, got {self.K}")
        if self.split is not None:
            _validate_split(self.split)
        self.tasks = _normalise_tasks(self.tasks)
        unknown_rules = set(self.rules) - set(RULE_KEYS)
        if unknown_rules:
            raise ValueError(
                f"unknown rule keys {sorted(unknown_rules)}, "
                f"expected subset of {list(RULE_KEYS)}")
        for key, name in self.rules.items():
            if not isinstance(name, str) or not name:
                raise ValueError(
                    f"rules[{key!r}] must be a non-empty rule name, "
                    f"got {name!r}")
        # Complete the rule table (a fresh dict: never alias DEFAULT_RULES
        # or the caller's mapping), keys in RULE_KEYS order.
        self.rules = {key: self.rules.get(key, DEFAULT_RULES[key])
                      for key in RULE_KEYS}
        self.models = dict(self.models)
        unknown = set(self.models) - set(MODEL_ROLES)
        if unknown:
            raise ValueError(
                f"unknown model roles {sorted(unknown)}, "
                f"expected subset of {list(MODEL_ROLES)}")
        strong = self.models.get("executor_strong")
        weak = self.models.get("executor_weak")
        if not strong or not weak:
            raise ValueError(
                "models must define 'executor_strong' and 'executor_weak'")
        # Fill the role table: everything answers with the strong model except
        # the exam answerer, which uses the weak model by design.
        for role in MODEL_ROLES:
            self.models.setdefault(role, weak if role == "answerer" else strong)
        # Extra per-model chat-call parameters: keyed by model NAME, so they
        # are validated against the completed role table (any model a role
        # names is acceptable, not just the two anchors). Checked AFTER the
        # table is filled, and copies replace the caller's mapping so a later
        # mutation there cannot change the frozen value.
        known_models = set(self.models.values())
        unknown_models = set(self.model_options) - known_models
        if unknown_models:
            raise ValueError(
                f"model_options names unknown model(s) "
                f"{sorted(unknown_models)}; models in the role table: "
                f"{sorted(known_models)}")
        for name, options in self.model_options.items():
            if not isinstance(options, dict) or not options:
                raise ValueError(
                    f"model_options[{name!r}] must be a non-empty dict of "
                    f"chat-call parameters, got {options!r}")
            try:
                json.dumps(options)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"model_options[{name!r}] must be JSON-serialisable "
                    f"(it freezes into the manifest): {exc}") from None
        self.model_options = {name: dict(options) for name, options
                              in self.model_options.items()}

    def model_for(self, role: str) -> str:
        """Model name for a role (KeyError with the known roles on a typo)."""
        try:
            return self.models[role]
        except KeyError:
            raise KeyError(
                f"unknown model role {role!r}; known: {sorted(self.models)}") from None

    def to_dict(self) -> Dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExperimentConfig":
        if not isinstance(data, dict):
            raise TypeError(
                f"config must be a mapping, got {type(data).__name__}")
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def load_config(path: str) -> ExperimentConfig:
    """Load one experiment config from a YAML file.

    ``weak_profile`` may be given inline, or as ``weak_profile_file``: a path
    (relative to the config file's directory if not absolute) whose text is
    read into ``weak_profile``. "The config file's directory" is that of the
    real file: ``path`` is resolved first, so a symlink to a config behaves
    exactly like the config itself.

    A ``tasks`` string is a task-file path; a relative one is resolved
    against the config file's directory and stored absolute (``~`` is
    expanded), so the frozen value does not depend on the cwd a run or a
    resume was started from. A ``tasks`` list is kept as is.
    """
    import yaml  # function-local: see the module docstring

    from pathlib import Path

    config_path = Path(path)
    # Relative paths inside the config are relative to the REAL config
    # file: loading it through a symlink must not change what they name.
    config_dir = config_path.resolve().parent
    with open(config_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a YAML mapping")
    profile_file = data.pop("weak_profile_file", None)
    if profile_file is not None:
        if data.get("weak_profile"):
            raise ValueError(
                "config gives both weak_profile and weak_profile_file")
        profile_path = Path(profile_file)
        if not profile_path.is_absolute():
            profile_path = config_dir / profile_path
        with open(profile_path, "r", encoding="utf-8") as fh:
            data["weak_profile"] = fh.read()
    tasks = data.get("tasks")
    if isinstance(tasks, str) and tasks.strip():
        tasks_path = Path(tasks).expanduser()
        if not tasks_path.is_absolute():
            tasks_path = config_dir / tasks_path
        data["tasks"] = str(tasks_path.resolve())
    return ExperimentConfig.from_dict(data)


def save_config(config: ExperimentConfig, path: str) -> None:
    """Write a config back to YAML (round-trips through ``to_dict``)."""
    import yaml  # function-local: see the module docstring

    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(config.to_dict(), fh, sort_keys=True, allow_unicode=True)
