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

``skill_delivery`` (protocol v2 / retrieval split) follows the same
complete-don't-reject discipline: absent means the default ``whole_bank``
and is completed to ``{"mode": "whole_bank"}``, so the frozen config names
the effective delivery the way it names the effective rule selections.
``mode: retrieval`` requires its three parameters (``top_k``,
``max_categories``, ``min_score``) together; under ``whole_bank`` they are
rejected rather than ignored.

``tasks`` names the experiment's task set: ``None`` (the plugin's full set;
a plugin may reject it, TB does), a list of raw task ids, or a path to a
task file whose format the plugin defines. ``load_config`` resolves a
relative path against the config file's directory.

``token_budgets`` (schema v2) is the single source of every call's token
caps: a required ``default_max_output_tokens`` whenever the mapping is
present, an optional global ``max_input_tokens`` (null = measure only),
and optional per-role entries under ``roles``.
``max_output_tokens_for`` resolves a role's effective cap and fails closed
when no budgets are configured. ``model_options`` must not carry
``max_tokens`` (or any request-shape key): a second budget source silently
won or lost against the real one depending on merge order.

Schema v2 also makes config files fail loud: unknown top-level keys and
schema versions from the future are ConfigErrors instead of being silently
dropped. The trap this closes: a new config run by old code dropped
``token_budgets`` without a trace and fell back to the dead 5120 default
(5120 was burned end-to-end by reasoning-max models on 2026-10-11; 65536
is the endpoint-verified safe ceiling for that family; a cap is a ceiling,
not a spend).

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
    "answerer", "judge", "rewriter", "curator", "auditor",
)

#: Keys of the named-rule selections.
RULE_KEYS = ("task_select", "segment_select", "trajectory_select")

#: Rule name per key when the config names none: the stage 2/3 protocol v2
#: definitions of stage 1 (D4) and stage 3 (D6), plus stage 2's frozen
#: default (all trajectories). The defaults ARE the method canon (every
#: formal series config names them explicitly anyway), so switching them
#: declares v2 rather than migrating anything; the v1 names stay registered
#: in ``blocks/rules.py`` (old configs are historical records).
DEFAULT_RULES = {
    "task_select": "any_weak_failed",
    "segment_select": "strong_success_weak_failed",
    "trajectory_select": "all",
}

#: Delivery modes for the skill bank at test time (stage 6, protocol v2 /
#: retrieval split): inject the whole bank (the frozen v1 behaviour) or
#: retrieve a per-task top-k from it.
SKILL_DELIVERY_MODES = ("retrieval", "whole_bank")

#: Keys accepted inside ``skill_delivery``.
_SKILL_DELIVERY_KEYS = ("mode", "top_k", "max_categories", "min_score")

#: Retrieval-mode parameters: all three are required together under
#: ``mode: retrieval`` and all three are meaningless under ``whole_bank``.
_SKILL_DELIVERY_RETRIEVAL_PARAMS = ("top_k", "max_categories", "min_score")

#: Key sets accepted in the ``split`` field with explicit task-id lists:
#: exactly the two-key or the three-key form (``reserved`` is the frozen
#: partition held back from both arms). Same contract as D2 in
#: ``blocks/tasks.py`` but a different layer: this validates the SHAPE at
#: config load, D2 validates the lists against the loaded task set.
_SPLIT_LIST_KEY_SETS = (
    frozenset({"train", "test"}),
    frozenset({"train", "test", "reserved"}),
)
#: Every list-shaped ``split`` key that can carry task ids.
_SPLIT_LIST_KEYS = ("train", "test", "reserved")
#: The ratio + seed shorthand, from which stage 0 derives the lists
#: deterministically before D2 sees them.
_SPLIT_RATIO_KEYS = ("ratio", "seed")

#: Chat-call keys ``model_options`` must never carry (schema v2):
#: ``max_tokens`` is owned by ``token_budgets`` -- a second source silently
#: won or lost against the real one depending on merge order -- and the rest
#: are not provider knobs: ``model``/``messages``/``stream`` ARE the request,
#: and ``temperature`` belongs to the client, not the experiment identity.
#: model_options only forwards per-model sampling knobs (reasoning_effort).
MODEL_OPTIONS_RESERVED_KEYS = ("max_tokens", "temperature", "model",
                               "messages", "stream")

#: Keys accepted inside ``token_budgets``.
_TOKEN_BUDGET_KEYS = ("default_max_output_tokens", "max_input_tokens", "roles")

#: Keys accepted inside one ``token_budgets.roles`` entry.
_ROLE_BUDGET_KEYS = ("max_output_tokens", "max_input_tokens")

#: v2: ``token_budgets`` added; ``model_options`` reserved keys; config
#: files fail loud on unknown top-level keys and future schema versions.
CONFIG_SCHEMA_VERSION = 2


class ConfigError(ValueError):
    """Invalid experiment config: bad shape, unknown key or bad value.

    Defined in core (not imported from infra.reliability) because core is
    stdlib-only. Subclassing ValueError keeps every existing contract:
    pipeline.py wraps load_config's ValueErrors into infra's ConfigError for
    the CLI, and callers matching ValueError keep matching.
    """


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
    keys = frozenset(split)
    if keys in _SPLIT_LIST_KEY_SETS:
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
        if keys - frozenset(_SPLIT_RATIO_KEYS):
            raise ValueError(
                f"split mixes ratio and explicit lists: {sorted(keys)}")
    else:
        raise ValueError(
            "split must give exactly {'train': [...], 'test': [...]}, "
            "{'train': [...], 'test': [...], 'reserved': [...]}, or "
            f"{{'ratio': float, 'seed': int}}, got keys {sorted(keys)}")


def _validate_skill_delivery(delivery: Any) -> Dict[str, Any]:
    """Validate ``skill_delivery``; returns a copy with ``mode`` always
    present, so the frozen config names the effective delivery like it
    names the effective rule selections (never a raw, possibly empty,
    mapping). A copy, not the caller's mapping: the frozen value must not
    change through a later mutation there (same contract as
    token_budgets)."""
    if not isinstance(delivery, dict):
        raise ConfigError(
            f"skill_delivery must be a mapping, got {type(delivery).__name__}")
    unknown = set(delivery) - set(_SKILL_DELIVERY_KEYS)
    if unknown:
        raise ConfigError(
            f"skill_delivery has unknown keys {sorted(unknown, key=str)}, "
            f"expected a subset of {list(_SKILL_DELIVERY_KEYS)}")
    if not delivery:
        # Absent/empty: the default delivery, spelled out so the frozen
        # config names it (module docstring, "complete-don't-reject").
        return {"mode": "whole_bank"}
    mode = delivery.get("mode", "whole_bank")
    if mode not in SKILL_DELIVERY_MODES:
        raise ConfigError(
            f"skill_delivery.mode must be one of {SKILL_DELIVERY_MODES}, "
            f"got {mode!r}")
    if mode == "whole_bank":
        # An ineffective retrieval key under whole_bank would mislead about
        # what the run injects (the reader would assume per-task selection
        # is on); the config is the experiment's identity, so fail loud.
        stray = sorted(set(delivery) - {"mode"}, key=str)
        if stray:
            raise ConfigError(
                "skill_delivery carries retrieval parameter(s) "
                + ", ".join(stray) + " under mode 'whole_bank'; they only "
                "apply to mode 'retrieval'")
        return {"mode": "whole_bank"}
    # mode == "retrieval": the three parameters are required together.
    missing = [key for key in _SKILL_DELIVERY_RETRIEVAL_PARAMS
               if key not in delivery]
    if missing:
        raise ConfigError(
            "skill_delivery mode 'retrieval' requires "
            f"{list(_SKILL_DELIVERY_RETRIEVAL_PARAMS)}; missing {missing}")
    if not _is_positive_int(delivery["top_k"]):
        raise ConfigError(
            f"skill_delivery.top_k must be an int >= 1, "
            f"got {delivery['top_k']!r}")
    if not _is_positive_int(delivery["max_categories"]):
        raise ConfigError(
            f"skill_delivery.max_categories must be an int >= 1, "
            f"got {delivery['max_categories']!r}")
    min_score = delivery["min_score"]
    # int is accepted beside float: a YAML "0" parses as int, and the grid's
    # lower bound is a legal value. bool is an int subclass but never a
    # score. NaN and inf fail the range check, as does anything >= 1.
    if (isinstance(min_score, bool)
            or not isinstance(min_score, (int, float))
            or not 0.0 <= min_score < 1.0):
        raise ConfigError(
            f"skill_delivery.min_score must be a number in [0, 1), "
            f"got {min_score!r}")
    return dict(delivery)


def _is_positive_int(value: Any) -> bool:
    """True for a positive int; bool is an int subclass but never a budget."""
    return (isinstance(value, int) and not isinstance(value, bool)
            and value > 0)


def _validate_input_cap(value: Any, where: str) -> None:
    """Check a ``max_input_tokens`` value: positive int, or null.

    Null defers input enforcement until a full run's calibration data
    exists (converged v1 decision): the threshold only flags, never aborts.
    The flagging side lives in the call pipeline; here only the type is
    checked.
    """
    if value is None or _is_positive_int(value):
        return
    raise ConfigError(
        f"{where} must be a positive int or null (null = measure only), "
        f"got {value!r}")


def _validate_token_budgets(budgets: Any) -> Dict[str, Any]:
    """Validate ``token_budgets``; returns a deep copy so the frozen config
    is never aliased to the caller's mapping (same contract as
    model_options)."""
    if not isinstance(budgets, dict):
        raise ConfigError(
            f"token_budgets must be a mapping, got {type(budgets).__name__}")
    unknown = set(budgets) - set(_TOKEN_BUDGET_KEYS)
    if unknown:
        raise ConfigError(
            f"token_budgets has unknown keys {sorted(unknown, key=str)}, "
            f"expected a subset of {list(_TOKEN_BUDGET_KEYS)}")
    if not budgets:
        # Absent/empty: no budgets configured (the helper then fails closed).
        return {}
    default = budgets.get("default_max_output_tokens")
    if not _is_positive_int(default):
        raise ConfigError(
            "token_budgets is present but default_max_output_tokens is "
            "missing or not a positive int; it is required whenever "
            "token_budgets is given and is the fallback cap for every role "
            f"without its own entry, got {default!r}")
    if "max_input_tokens" in budgets:
        _validate_input_cap(budgets["max_input_tokens"],
                            "token_budgets.max_input_tokens")
    roles = budgets.get("roles", {})
    if not isinstance(roles, dict):
        raise ConfigError(
            "token_budgets.roles must be a mapping of role -> budget entry, "
            f"got {type(roles).__name__}")
    unknown_roles = set(roles) - set(MODEL_ROLES)
    if unknown_roles:
        raise ConfigError(
            f"token_budgets.roles has unknown roles "
            f"{sorted(unknown_roles, key=str)}, expected a subset of "
            f"{list(MODEL_ROLES)}")
    for role, entry in roles.items():
        if not isinstance(entry, dict):
            raise ConfigError(
                f"token_budgets.roles[{role!r}] must be a mapping with "
                f"max_output_tokens (and optionally max_input_tokens), "
                f"got {entry!r}")
        unknown_keys = set(entry) - set(_ROLE_BUDGET_KEYS)
        if unknown_keys:
            raise ConfigError(
                f"token_budgets.roles[{role!r}] has unknown keys "
                f"{sorted(unknown_keys, key=str)}, expected a subset of "
                f"{list(_ROLE_BUDGET_KEYS)}")
        if not _is_positive_int(entry.get("max_output_tokens")):
            raise ConfigError(
                f"token_budgets.roles[{role!r}].max_output_tokens is "
                "required and must be a positive int; omit the role entry "
                "to fall back to default_max_output_tokens")
        if "max_input_tokens" in entry:
            _validate_input_cap(
                entry["max_input_tokens"],
                f"token_budgets.roles[{role!r}].max_input_tokens")
    frozen = dict(budgets)
    if "roles" in frozen:
        frozen["roles"] = {role: dict(entry)
                           for role, entry in frozen["roles"].items()}
    return frozen


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
    #: Named-rule selections; missing keys are filled from DEFAULT_RULES,
    #: and stage 0 validates every name against the rule registries.
    rules: Dict[str, str] = field(default_factory=dict)
    #: Stage-6 skill delivery (protocol v2 / retrieval split): ``mode`` is
    #: ``whole_bank`` (the frozen v1 behaviour: inject the entire bank) or
    #: ``retrieval`` (per-task top-k selection). Absent/empty means
    #: whole_bank and is COMPLETED to ``{"mode": "whole_bank"}`` so the
    #: frozen config always names the effective delivery. Under
    #: ``retrieval`` the three parameters are required together:
    #: ``top_k`` (int >= 1), ``max_categories`` (int >= 1) and ``min_score``
    #: (number in [0, 1), the top-1 gate below which nothing is injected);
    #: under ``whole_bank`` they are rejected (an ineffective key would
    #: mislead about what the run injects). Delivery changes what the test
    #: arm sees, so it is part of the experiment's frozen identity.
    skill_delivery: Dict[str, Any] = field(default_factory=dict)
    #: Prompt name -> digest; filled by the prompt registry (B2) when the
    #: stages freeze the manifest, empty in the source config.
    prompts_digests: Dict[str, str] = field(default_factory=dict)
    #: Model NAME -> extra chat-call parameters (e.g. ``reasoning_effort``)
    #: forwarded to the endpoint for every call of that model, whichever
    #: role makes it. Changes what the model produces, so it is part of the
    #: experiment's frozen identity, not an operational setting.
    model_options: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: Per-role token budgets (schema v2), the single source of every call's
    #: caps: ``default_max_output_tokens`` (positive int, required when this
    #: mapping is non-empty) is the fallback for roles without an entry;
    #: ``max_input_tokens`` (positive int or null = measure only) is global;
    #: ``roles`` maps MODEL_ROLES names to entries carrying a required
    #: ``max_output_tokens`` and an optional ``max_input_tokens``. Why the
    #: defaults are what they are: reasoning-max models burned 5120 entirely
    #: on thinking (2026-10-11, recorded in configs/experiments/
    #: tb21_full_v2.yaml), while 65536 is the endpoint-verified safe ceiling
    #: for that family -- and a cap is a ceiling, not a spend, so capping
    #: high costs nothing when the answer is short.
    token_budgets: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = CONFIG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        if not self.benchmark:
            raise ValueError("benchmark is required")
        if self.n_attempts_strong < 1 or self.n_attempts_weak < 1:
            raise ValueError("n_attempts_strong and n_attempts_weak must be >= 1")
        if self.K < 1:
            raise ValueError(f"K must be >= 1, got {self.K}")
        if self.split is not None:
            _validate_split(self.split)
        self.tasks = _normalise_tasks(self.tasks)
        # Skill delivery: validated and completed here (the frozen config
        # always carries the effective mode), copied so a later mutation of
        # the caller's mapping cannot change the frozen value.
        self.skill_delivery = _validate_skill_delivery(self.skill_delivery)
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
            reserved = sorted(set(options) & set(MODEL_OPTIONS_RESERVED_KEYS),
                               key=str)
            if reserved:
                guidance = []
                if "max_tokens" in reserved:
                    guidance.append(
                        "max_tokens lives in token_budgets (schema v2): give "
                        "token_budgets.default_max_output_tokens, and "
                        "token_budgets.roles.<role>.max_output_tokens for "
                        "per-role caps")
                others = [key for key in reserved if key != "max_tokens"]
                if others:
                    guidance.append(
                        "not provider knobs (the client owns the request "
                        "shape): " + ", ".join(others) + "; model_options "
                        "only forwards sampling knobs such as "
                        "reasoning_effort")
                raise ConfigError(
                    f"model_options[{name!r}] carries reserved key(s) "
                    f"{reserved}: " + "; ".join(guidance))
            try:
                json.dumps(options)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"model_options[{name!r}] must be JSON-serialisable "
                    f"(it freezes into the manifest): {exc}") from None
        self.model_options = {name: dict(options) for name, options
                              in self.model_options.items()}
        # Token budgets: validated and deep-copied here, so the frozen config
        # cannot change through the caller's mapping and stage 0's replace()
        # re-validation is a no-op on an already-valid structure.
        self.token_budgets = _validate_token_budgets(self.token_budgets)

    def model_for(self, role: str) -> str:
        """Model name for a role (KeyError with the known roles on a typo)."""
        try:
            return self.models[role]
        except KeyError:
            raise KeyError(
                f"unknown model role {role!r}; known: {sorted(self.models)}") from None

    def max_output_tokens_for(self, role: str) -> int:
        """Effective output cap for a role.

        ``roles[role].max_output_tokens`` when the role has an entry, else
        ``default_max_output_tokens``. Fails closed when no budgets are
        configured: returning any number here would resurrect the dead 5120
        default this schema removed, so the pipeline treats it as a hard
        config error (every experiment config carries token_budgets).
        """
        if not self.token_budgets:
            raise ConfigError(
                "token_budgets not configured: add "
                "token_budgets.default_max_output_tokens to the experiment "
                "config")
        if role not in MODEL_ROLES:
            raise KeyError(
                f"unknown model role {role!r}; known: {list(MODEL_ROLES)}")
        entry = self.token_budgets.get("roles", {}).get(role)
        if entry is not None:
            return entry["max_output_tokens"]
        return self.token_budgets["default_max_output_tokens"]

    def to_dict(self) -> Dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExperimentConfig":
        if not isinstance(data, dict):
            raise TypeError(
                f"config must be a mapping, got {type(data).__name__}")
        known = {f.name for f in fields(cls)}
        # Schema version first, so a record from newer code says "needs
        # newer code" instead of drowning in unknown-key errors its own
        # schema would explain. Unlike the data model's from_dict (lenient
        # for stored records), the config IS the experiment's identity: an
        # unknown key is a typo or a version mismatch, and dropping it
        # silently once ran a new config on old code with dead defaults.
        version = data.get("schema_version", CONFIG_SCHEMA_VERSION)
        if not isinstance(version, int) or isinstance(version, bool):
            raise ConfigError(
                f"schema_version must be an int, got {version!r}")
        if version > CONFIG_SCHEMA_VERSION:
            raise ConfigError(
                f"config schema_version is {version}, newer than this code's "
                f"{CONFIG_SCHEMA_VERSION}; this config needs newer code -- "
                "update the repository and retry")
        unknown = [key for key in data if key not in known]
        if unknown:
            raise ConfigError(
                f"unknown config keys {sorted(unknown, key=str)}; known keys: "
                f"{sorted(known)}")
        # The field filter stays: construction only ever receives dataclass
        # fields, whatever the checks above grow to allow.
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

    Schema v2 fails loud on the RAW mapping: unknown top-level keys and a
    schema version from the future are ConfigErrors naming the offender.
    from_dict re-checks both (defense in depth); the checks here run first
    so the file's own schema -- the dataclass fields plus the file-only
    ``weak_profile_file`` key -- is what the error message lists.
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
    version = data.get("schema_version", CONFIG_SCHEMA_VERSION)
    if not isinstance(version, int) or isinstance(version, bool):
        raise ConfigError(
            f"{path}: schema_version must be an int, got {version!r}")
    if version > CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"{path} has schema_version {version}, newer than this code's "
            f"{CONFIG_SCHEMA_VERSION}; this config needs newer code -- "
            "update the repository and retry")
    known_file_keys = ({f.name for f in fields(ExperimentConfig)}
                       | {"weak_profile_file"})
    unknown = [key for key in data if key not in known_file_keys]
    if unknown:
        raise ConfigError(
            f"{path} has unknown top-level key(s) {sorted(unknown, key=str)}; "
            f"known keys: {sorted(known_file_keys)}")
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
