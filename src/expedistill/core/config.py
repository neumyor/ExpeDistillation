"""Experiment configuration (stage 0) and its YAML (de)serialisation.

One YAML file under ``configs/experiments/`` describes one experiment. The
frozen copy of this config (``to_dict``) goes into the run manifest; changing
any field after the run started is a new experiment (B4 enforces that).

SCHEMA v4 (the execute-learn repair, R9): the method is a ``sequence``
of phases -- local names looked up under ``phases``, an execute or a
learn phase each (see the schema-v4 section below for the parsed specs,
the R8 role vocabulary and the load-time checklist). A phase family's
params are validated by the FAMILY'S OWN contract (a dataclass in the
methods layer) at resolution time, NOT here: core is layer 0 and cannot
know a method's parameters. Core owns only the shape, and nothing is
completed by default -- an absent family is an error, because an
experiment's identity includes every phase it runs. v3 and older are
refused with a pointer at the pure upgrade mappings
(``upgrade_v3_config``, chaining ``upgrade_v2_config`` for v2) and the
converter script (``scripts/run/convert_v3_configs.py``); the v3 files
are frozen experiment records, so the converter writes new names.

The role table is a plain dict on purpose: ``models[role] -> model name``.
Two entries are the anchors -- ``executor_strong`` and ``executor_weak`` --
and every other role defaults to the anchor ``ROLE_DEFAULTS`` names (the
answerer probes the weak model by design; everything else answers with the
strong one). ``ROLE_DEFAULTS`` is a dict, not the old ``MODEL_ROLES`` tuple,
so the defaults are data and no longer a namesake of ``core.models``'
trajectory roles.

``token_budgets`` (schema v2) remains the single source of every call's token
caps: a required ``default_max_output_tokens`` whenever the mapping is
present, an optional global ``max_input_tokens`` (null = measure only), and
optional per-role entries under ``roles``. ``max_output_tokens_for``
resolves a role's effective cap and fails closed when no budgets are
configured. ``model_options`` must not carry ``max_tokens`` (or any
request-shape key): a second budget source silently won or lost against
the real one depending on merge order.

Config files fail loud: unknown top-level keys and schema versions other
than this code's are ConfigErrors instead of being silently dropped. The
trap this closes: a new config run by old code dropped ``token_budgets``
without a trace and fell back to the dead 5120 default. ``upgrade_v2_config``
and ``upgrade_v3_config`` are the pure stdlib mappings the one-time
converters and the reuse assembler (reading an old source run's manifest)
share.

Deviation from the layer rule "core depends on stdlib only": ``load_config`` /
``save_config`` need YAML, and the wave spec places them here. The ``yaml``
import is function-local so that importing the data model never pulls the
dependency in.
"""

import copy
import dataclasses
import json
import re
from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Optional, Tuple, Union

#: Experiment modes (README stage 0). ``split`` divides the task set into
#: train/test; ``full`` distills on everything and re-runs with the bank.
MODES = ("split", "full")

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

#: The v3 slot defaults -- schema-v3 knowledge, kept ONLY for
#: ``upgrade_v3_config`` (the v4 loader has no slots; an absent phase
#: family is a load-time error, not a default). These must match what
#: the v3 loader froze for an absent slot; the upgrade's tests pin that.
_V3_METHOD_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "segmentation": {"K": 5, "retry_limit": 2},
    "authoring": {"exams": True},
    "acceptance": {"name": "exam", "retry_limit": 2},
    "curation": {"name": "agentbrew", "token_budget": 4096,
                "rounds": 3},
    "delivery": {"name": "whole_bank"},
}

#: Job role -> the executor anchor it defaults to: "strong", "weak", or
#: None -- None means the config MUST name the model explicitly (the
#: pipeline's handle builder refuses a missing role; nothing here
#: guesses). The answerer uses the weak model by design (the exam probes
#: what the weak model can do); everything else answers with the strong
#: one. A dict on purpose -- the defaults are data, and the old name
#: (MODEL_ROLES, a plain tuple) was a permanent namesake of
#: core.models' trajectory roles.
ROLE_DEFAULTS: Dict[str, Optional[str]] = {
    "executor_strong": "strong",
    "executor_weak": "weak",
    "segmenter": "strong",
    "author": "strong",
    "examiner": "strong",
    "judge": "strong",
    "rewriter": "strong",
    "curator": "strong",
    "auditor": "strong",
    "answerer": "weak",
}

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

#: v4: the method is a ``sequence`` of phases; every method parameter
#: lives in the phase definitions under ``phases`` (R9). v3 and older
#: are refused with a pointer at the upgrade mapping and the converter.
CONFIG_SCHEMA_VERSION = 4


class ConfigError(ValueError):
    """Invalid experiment config: bad shape, unknown key or bad value.

    Defined in core (not imported from infra.reliability) because core is
    stdlib-only. Subclassing ValueError keeps every existing contract:
    pipeline.py wraps load_config's ValueErrors into infra's ConfigError for
    the CLI, and callers matching ValueError keep matching.
    """


# ---------------------------------------------------------------------------
# SCHEMA v4: the phase vocabulary and the parsed phase specs (R9/R8)
# ---------------------------------------------------------------------------
#
# One method is a SEQUENCE of phases (repair plan R9): the config names a
# list of phase LOCAL names and, for each name, a phase definition under
# ``phases``; the same definition may occupy several positions in the
# sequence (the exam method's ``[D, L] + [A, L] x 3 + [C]``). Local names
# are restricted to a colon-free charset because the pipeline_log stage
# value and every per-phase path key off the label ``pNN:<local name>``
# (composites.decision.parse_unit_id and composites.phase_bank both
# refuse anything else, and a colon would break their parsing).
#
# Phase specs are DATACLASSES, not TypedDicts, for the same reason the
# rest of core's records are: ``__post_init__`` is where the load-time
# checklist lives (a TypedDict validates nothing at runtime), the frozen
# manifest serialises them through ``dataclasses.fields`` exactly like
# every other ExperimentConfig field, and frozen=True keeps a parsed
# config from being mutated after the manifest comparison.

#: R8's explicit phase roles. The decision entry (composites.decision)
#: receives exactly these words as ``current_kind`` / ``control_kind``;
# the cross-layer equality is pinned in tests/unit/test_w2c_config_v4.py.
PHASE_ROLE_BASELINE = "baseline"  # D: the two-arm bare baseline
PHASE_ROLE_EXAM = "exam"          # A: paper acceptance, one skill per exam
PHASE_ROLE_LIVE = "live"         # B: live acceptance, a task's whole group
PHASE_ROLE_TEST = "test"         # C: the final test -- never reaches decision
PHASE_ROLE_LEARN = "learn"       # L: a learn phase

#: The roles an execute phase may declare (a learn phase is always
#: ``learn`` -- its content methods are the evidence/author/curate keys).
EXECUTE_PHASE_ROLES = (PHASE_ROLE_BASELINE, PHASE_ROLE_EXAM,
                       PHASE_ROLE_LIVE, PHASE_ROLE_TEST)

#: What an execute phase runs over: the frozen train or test id list.
PHASE_TASK_SETS = ("train", "test")

#: The execution family's names: ``real`` (a benchmark run) or ``exam``
#: (questions are generated at execution time; grading belongs to
#: acceptance). The registry's execution entries resolve these.
EXECUTION_NAMES = ("real", "exam")

#: The delivery family's ``folder`` name: the skill-folder delivery
#: (skill-folder M2) is registered for the TEST role (C) only -- the
#: alignment ruling keeps the A (per-skill), B (whole-group) and D
#: (bare) roles on the existing injection methods. The name lives here
#: because core may not import the methods registry; it is the same
#: cross-layer literal pattern EXECUTION_NAMES already uses.
FOLDER_DELIVERY_NAME = "folder"

#: The execution family's ``real_folder`` name: the skill-folder
#: execution (skill-folder M3), ``real``'s folder twin. Legal on a
#: test-role execute phase only, and paired one-to-one with the folder
#: delivery (checklist item 8). Kept out of EXECUTION_NAMES on purpose:
#: that pair names the executions the OTHER roles may run, and
#: ``real_folder`` never may.
FOLDER_EXECUTION_NAME = "real_folder"

#: The control a ``compare`` may name: an earlier phase's local name, or
#: a bare arm run fresh inside this phase.
FRESH_BARE = "fresh_bare"

#: How many times the phase driver may re-dispatch one unit the verdict
#: ruled invalid (R9/R1's redispatch loop; after the limit the unit
#: stays a hole with cause ``invalid_redispatch_exhausted``). A method
#: parameter, not an operational knob: accepting holes changes what is
#: measured. The default is one redispatch; upgrade_v3_config emits it
#: so a converted config's identity never depends on this default.
DEFAULT_INVALID_REDISPATCH_LIMIT = 1

#: Phase local names: colon-free and directory-safe. The colon is the
#: label separator, so it is the one character a name can never carry
#: (decision.parse_unit_id splits on it; phase_bank maps it to ``_``).
PHASE_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")

#: The pipeline_log stage value of the freeze step: freeze is always
#: ``p00`` and every sequence phase's number is its 0-based sequence
#: index plus one (the frozen derivation in tests/golden/semantics.py:
#: ``phases/p{index + 1:02d}_<name>``; D, the first sequence entry, is
#: ``p01_D``).
FREEZE_LABEL = "p00:freeze"


def phase_label(index: int, name: str) -> str:
    """The pipeline_log stage value of sequence entry ``index`` (0-based):
    ``pNN:<local name>`` with ``NN = index + 1`` (p00 is the freeze step)."""
    return f"p{index + 1:02d}:{name}"


@dataclass(frozen=True)
class CompareSpec:
    """An execute phase's control: which earlier measurement the decision
    compares against, and the named comparison rule (blocks/rules.py's
    acceptance-improve registry; ``None`` is the rule family's default)."""

    against: str
    rule: Optional[str] = None


@dataclass(frozen=True)
class ExecutePhaseSpec:
    """One execute phase (delivery -> execution -> acceptance).

    ``delivery`` / ``execution`` keep the RAW method mappings (a
    ``name`` key plus that method's parameters): core owns the shape,
    the methods layer's contracts validate the parameters at resolution
    (stage 0 resolves every family of every phase before anything
    freezes). ``tasks`` picks the frozen train/test id list; ``compare``
    is optional; ``acceptance`` names the verdict rule (C11) and
    ``None`` is the benchmark's default (composites.verdicts.
    BENCHMARK_VERDICT_RULES).
    """

    role: str
    tasks: str = "train"
    delivery: Dict[str, Any] = field(default_factory=dict)
    execution: Dict[str, Any] = field(default_factory=dict)
    compare: Optional[CompareSpec] = None
    acceptance: Optional[str] = None
    kind: str = "execute"

    def __post_init__(self) -> None:
        if self.kind != "execute":
            raise ConfigError(
                f"an execute phase's kind must be 'execute', got "
                f"{self.kind!r}")
        if self.role not in EXECUTE_PHASE_ROLES:
            raise ConfigError(
                f"an execute phase's role must be one of "
                f"{list(EXECUTE_PHASE_ROLES)}, got {self.role!r}")
        if self.tasks not in PHASE_TASK_SETS:
            raise ConfigError(
                f"an execute phase's tasks must be one of "
                f"{list(PHASE_TASK_SETS)}, got {self.tasks!r}")


@dataclass(frozen=True)
class LearnPhaseSpec:
    """One learn phase (decision -> evidence -> author -> curate).

    Learn phases are not registry entries (plan R8); their three content
    families resolve through the registry like any other: ``evidence``
    (the add path's cutting budget), ``author`` (the writing method) and
    ``curate`` (agentbrew with merge_check, or none). Curation runs only
    at the sequence's LAST learn phase -- the driver branches on the
    sequence position, not on this spec.
    """

    evidence: Dict[str, Any] = field(default_factory=dict)
    author: Dict[str, Any] = field(default_factory=dict)
    curate: Dict[str, Any] = field(default_factory=dict)
    kind: str = "learn"
    role: str = PHASE_ROLE_LEARN

    def __post_init__(self) -> None:
        if self.kind != "learn":
            raise ConfigError(
                f"a learn phase's kind must be 'learn', got {self.kind!r}")
        if self.role != PHASE_ROLE_LEARN:
            raise ConfigError(
                f"a learn phase's role is always {PHASE_ROLE_LEARN!r}, "
                f"got {self.role!r}")


#: A phase definition: what ``config.phases`` maps a local name to.
PhaseSpec = Union[ExecutePhaseSpec, LearnPhaseSpec]


@dataclass(frozen=True)
class PhaseInstance:
    """One POSITION of the sequence, as the phase driver receives it.

    Built by the CLI's phase loop (cli.py, R9's skeleton): ``index`` is
    the 0-based sequence position, ``label`` is the pipeline_log stage
    value ``pNN:<name>`` and every per-phase path key, ``spec`` is the
    parsed definition, and ``task_ids`` is the frozen id list this phase
    runs over (the train or test list for an execute phase, empty for a
    learn phase). This is the frozen hand-off contract between the
    CLI's phase loop and the two phase drivers' ``run`` entry points.
    """

    index: int
    name: str
    label: str
    spec: PhaseSpec
    task_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PhaseResult:
    """What a phase driver returns to the loop: the phase's JSON-safe
    summary (the ledger line's ``summary``). Drivers may carry more in
    the summary dict; ``label`` is echoed so the loop can assert the
    driver answered for the phase it was asked about."""

    label: str
    summary: Dict[str, Any]


#: The field sets the two kinds accept (checked by parse_phases: a key
#: outside the kind's set is a typo or a version mismatch, never a
#: silent drop -- the same discipline as the unknown top-level key).
_EXECUTE_FIELDS = frozenset({"kind", "role", "tasks", "delivery", "execution",
                             "compare", "acceptance"})
_LEARN_FIELDS = frozenset({"kind", "role", "evidence", "author", "curate"})

#: The baseline phase's two arms: fixed by design (the strong arm feeds
#: the add path, the weak arm is every later comparison's control).
_BASELINE_ARMS = ("executor_strong", "executor_weak")


def _method_mapping(raw: Any, where: str) -> Dict[str, Any]:
    """One nested method mapping: a dict naming its method. The deep
    validation belongs to the family's contract in the methods layer
    (resolution, stage 0); here only the shape and the ``name`` are
    checked, the same split as v3's ``_validate_methods``."""
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{where} must be a mapping of the method's parameter names "
            f"to values, got {type(raw).__name__}")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError(f"{where} needs a non-empty 'name'")
    return dict(raw)


def _parse_execute_phase(name: str, raw: Dict[str, Any]) -> ExecutePhaseSpec:
    """One execute phase definition: shape, role consistency and the
    execution mapping's R9 rules (the plan's field table)."""
    unknown = sorted(set(raw) - _EXECUTE_FIELDS)
    if unknown:
        raise ConfigError(
            f"phases.{name} is kind 'execute' but carries key(s) {unknown}; "
            f"an execute phase accepts {sorted(_EXECUTE_FIELDS)}")
    role = raw.get("role")
    if role not in EXECUTE_PHASE_ROLES:
        raise ConfigError(
            f"phases.{name}.role must be one of {list(EXECUTE_PHASE_ROLES)}"
            f" (R8: the role is declared, never inferred), got {role!r}")
    tasks = raw.get("tasks", "train")
    if tasks not in PHASE_TASK_SETS:
        raise ConfigError(
            f"phases.{name}.tasks must be one of {list(PHASE_TASK_SETS)}, "
            f"got {tasks!r}")
    # The test role and the test task set are one fact, not two: a
    # final-test phase on the train set would leak test results into
    # learning (boundary doc section 3 rule 9), and a train-measuring
    # phase on the test set would measure on the held-out tasks.
    if (role == PHASE_ROLE_TEST) != (tasks == "test"):
        raise ConfigError(
            f"phases.{name} mixes role {role!r} with tasks {tasks!r}: the "
            "test role runs the test set and only the test role does")
    delivery = _method_mapping(raw.get("delivery"),
                               f"phases.{name}.delivery")
    # Checklist item 8: the folder delivery is a C-phase delivery. The
    # alignment ruling pins the play to the final test: A injects one
    # skill, B a task's whole group, D nothing. Its real_folder
    # execution is C-phase-only with it (the pairing check below).
    if delivery["name"] == FOLDER_DELIVERY_NAME and \
            role != PHASE_ROLE_TEST:
        raise ConfigError(
            f"phases.{name} declares delivery name "
            f"{FOLDER_DELIVERY_NAME!r} with role {role!r}: the "
            "skill-folder delivery is a final-test delivery, legal only "
            f"for role {PHASE_ROLE_TEST!r} (the C phase; the other "
            f"roles use the existing delivery methods)")
    execution = _method_mapping(raw.get("execution"),
                                f"phases.{name}.execution")
    exec_name = execution["name"]
    if exec_name not in EXECUTION_NAMES + (FOLDER_EXECUTION_NAME,):
        raise ConfigError(
            f"phases.{name}.execution name {exec_name!r} is not one of "
            f"{list(EXECUTION_NAMES + (FOLDER_EXECUTION_NAME,))}")
    # Checklist item 8, second half: the folder delivery and the
    # real_folder execution are one play, so they pair one-to-one on a
    # test-role phase -- a folder delivery under the plain real
    # execution would inject the catalog as a skills block (silently the
    # wrong experiment), and a real_folder execution over another
    # delivery would expect a folder no delivery produced.
    if (delivery["name"] == FOLDER_DELIVERY_NAME) != \
            (exec_name == FOLDER_EXECUTION_NAME):
        raise ConfigError(
            f"phases.{name} declares delivery name {delivery['name']!r} "
            f"with execution name {exec_name!r}: the skill-folder play "
            f"pairs delivery {FOLDER_DELIVERY_NAME!r} with execution "
            f"{FOLDER_EXECUTION_NAME!r}, and nothing else (the other "
            "deliveries run the plain real execution)")
    # The exam execution is what the exam ROLE measures with; every
    # non-test role runs the benchmark for real; a test role may also
    # run the folder execution (checklist item 8 keeps the folder
    # delivery -- and with the pairing above, real_folder -- on the
    # test role alone).
    if role == PHASE_ROLE_EXAM:
        expected_exec = EXECUTION_NAMES[1]
    elif role == PHASE_ROLE_TEST:
        expected_exec = None  # real or real_folder, the pairing above
    else:
        expected_exec = EXECUTION_NAMES[0]
    if expected_exec is not None and exec_name != expected_exec:
        raise ConfigError(
            f"phases.{name} declares role {role!r} but execution name "
            f"{exec_name!r}; that role runs {expected_exec!r}")
    if "n_attempts" in execution and role == PHASE_ROLE_EXAM:
        raise ConfigError(
            f"phases.{name}.execution carries n_attempts but an exam phase "
            "has no sampling depth -- the weak model answers once")
    if role == PHASE_ROLE_BASELINE:
        # The two-arm phase: both arms, no per-arm depth (the global
        # n_attempts_strong / n_attempts_weak decide the two depths).
        arms = execution.get("arms")
        if not (isinstance(arms, list) and tuple(arms) == _BASELINE_ARMS):
            raise ConfigError(
                f"phases.{name}.execution.arms must be exactly "
                f"{list(_BASELINE_ARMS)} (a baseline phase runs both arms; "
                "their depths come from the global n_attempts_strong / "
                "n_attempts_weak)")
        if "n_attempts" in execution:
            raise ConfigError(
                f"phases.{name}.execution carries n_attempts but a "
                "baseline phase's depths come from the global "
                "n_attempts_strong / n_attempts_weak")
    elif "arms" in execution:
        raise ConfigError(
            f"phases.{name}.execution carries arms but only a baseline "
            "phase is two-armed; every other execute phase runs the weak "
            "model")
    compare_raw = raw.get("compare")
    compare: Optional[CompareSpec] = None
    if compare_raw is not None:
        if not isinstance(compare_raw, dict):
            raise ConfigError(
                f"phases.{name}.compare must be a mapping with 'against' "
                f"(and optionally 'rule'), got {compare_raw!r}")
        unknown = sorted(set(compare_raw) - {"against", "rule"})
        if unknown:
            raise ConfigError(
                f"phases.{name}.compare has unknown key(s) {unknown}")
        against = compare_raw.get("against")
        if not isinstance(against, str) or not against:
            raise ConfigError(
                f"phases.{name}.compare.against must be a phase name or "
                f"{FRESH_BARE!r}, got {against!r}")
        rule = compare_raw.get("rule")
        if rule is not None and (not isinstance(rule, str) or not rule):
            raise ConfigError(
                f"phases.{name}.compare.rule must be a named rule or null, "
                f"got {rule!r}")
        compare = CompareSpec(against=against, rule=rule)
    acceptance = raw.get("acceptance")
    if acceptance is not None and (not isinstance(acceptance, str)
                                    or not acceptance):
        raise ConfigError(
            f"phases.{name}.acceptance must name a verdict rule or be "
            f"absent (the benchmark's default), got {acceptance!r}")
    return ExecutePhaseSpec(
        role=role, tasks=tasks, delivery=delivery, execution=execution,
        compare=compare, acceptance=acceptance, kind="execute")


def _parse_learn_phase(name: str, raw: Dict[str, Any]) -> LearnPhaseSpec:
    """One learn phase definition: shape only -- the content families'
    parameters validate at resolution (the methods layer's contracts)."""
    unknown = sorted(set(raw) - _LEARN_FIELDS)
    if unknown:
        raise ConfigError(
            f"phases.{name} is kind 'learn' but carries key(s) {unknown}; "
            f"a learn phase accepts {sorted(_LEARN_FIELDS)}")
    evidence = _method_mapping(raw.get("evidence"), f"phases.{name}.evidence")
    author = _method_mapping(raw.get("author"), f"phases.{name}.author")
    if "exams" in author:
        # The v3 authoring switch has no v4 image: upgrade_v3_config
        # refuses every combination but the one legal ablation pair, so
        # a v4 config that names it is a hand edit gone wrong.
        raise ConfigError(
            f"phases.{name}.author carries 'exams', a v3 authoring switch "
            "with no v4 image: exam generation is implied by every "
            "sequence's A phases (upgrade_v3_config maps the one legal "
            "ablation pair to a sequence without any)")
    curate = _method_mapping(raw.get("curate"), f"phases.{name}.curate")
    role = raw.get("role", PHASE_ROLE_LEARN)
    if role != PHASE_ROLE_LEARN:
        raise ConfigError(
            f"phases.{name}.role must be {PHASE_ROLE_LEARN!r} for a learn "
            f"phase, got {role!r}")
    return LearnPhaseSpec(evidence=evidence, author=author, curate=curate,
                         kind="learn", role=PHASE_ROLE_LEARN)


def parse_phases(raw_phases: Any, raw_sequence: Any, *,
                 n_attempts_strong: int,
                 n_attempts_weak: int) -> Dict[str, PhaseSpec]:
    """Validate the v4 ``sequence`` / ``phases`` pair and return the
    parsed specs, keyed by local name (the sequence may repeat a name;
    the definitions are one per name).

    The load-time checklist (plan R9 item 4), each check a ConfigError:

    1. the sequence is a non-empty list of local names over the
       colon-free charset, and every name has a definition (a definition
       the sequence never uses is refused too -- it would freeze method
       identity no phase ever runs);
    2. each definition's field set matches its ``kind``;
    3. the declared role is one of the five (R8) and consistent with
       the field values (test role <-> test tasks, exam role <-> exam
       execution, baseline <-> both arms, single-arm depth limits);
    4. ``compare.against`` names a phase that appears EARLIER in the
       sequence, or is exactly ``fresh_bare``; the named control is a
       baseline phase; a single-arm real phase's ``n_attempts`` never
       exceeds its control's weak-model depth;
    5. ``curate.merge_check: exam`` requires an exam-role execute phase
       somewhere in the sequence (the merges' regression exams come from
       the A phases);
    6. the sequence starts with an execute phase (a learn phase first
       would have nothing to learn from);
    7. a live phase carries ``compare`` naming an earlier baseline phase
       (its own report rows are all with-skill rows, so it has no
       in-report control);
    8. the ``folder`` delivery appears only on a test-role execute
       phase (the skill-folder delivery is a C-phase delivery; the A, B
       and D roles keep the existing injection methods), and pairs
       one-to-one with the ``real_folder`` execution: a folder delivery
       under the plain ``real`` execution, or a ``real_folder``
       execution over any other delivery, is refused (checklist item 8,
       skill-folder M3).

    Model roles and ``invalid_redispatch_limit`` are ExperimentConfig's
    own checks, not the sequence's.
    """
    if not isinstance(raw_sequence, list) or not raw_sequence:
        raise ConfigError(
            "sequence must be a non-empty list of phase names, got "
            f"{raw_sequence!r}")
    for position, entry in enumerate(raw_sequence):
        if not isinstance(entry, str) or not PHASE_NAME_RE.fullmatch(entry):
            raise ConfigError(
                f"sequence entry {position} is {entry!r}: a phase name is "
                "1+ characters over letters, digits, '.', '_', '-' "
                "(never a colon -- the label 'pNN:<name>' and the phase "
                "directories parse on it)")
    if not isinstance(raw_phases, dict) or not raw_phases:
        raise ConfigError(
            "phases must be a non-empty mapping of phase name -> "
            f"definition, got {raw_phases!r}")
    for phase_name in raw_phases:
        if not isinstance(phase_name, str) or not PHASE_NAME_RE.fullmatch(
                phase_name):
            raise ConfigError(
                f"phases key {phase_name!r} is not a legal phase name "
                "(1+ characters over letters, digits, '.', '_', '-')")
    unknown = [entry for entry in raw_sequence if entry not in raw_phases]
    if unknown:
        raise ConfigError(
            f"sequence names phase(s) {unknown} with no definition under "
            f"'phases'; defined: {sorted(raw_phases)}")
    unused = sorted(set(raw_phases) - set(raw_sequence))
    if unused:
        raise ConfigError(
            f"phases defines {unused} but the sequence never runs "
            "them; a definition no phase runs would freeze method "
            "identity nothing measures")
    specs: Dict[str, PhaseSpec] = {}
    for phase_name, raw in raw_phases.items():
        if not isinstance(raw, dict):
            raise ConfigError(
                f"phases.{phase_name} must be a mapping, got "
                f"{type(raw).__name__}")
        kind = raw.get("kind")
        if kind == "execute":
            specs[phase_name] = _parse_execute_phase(phase_name, raw)
        elif kind == "learn":
            specs[phase_name] = _parse_learn_phase(phase_name, raw)
        else:
            raise ConfigError(
                f"phases.{phase_name}.kind must be 'execute' or 'learn', "
                f"got {kind!r}")
    _check_first_phase_is_execute(raw_sequence, specs)
    _check_compare_targets(raw_sequence, specs)
    _check_live_needs_a_control(raw_sequence, specs)
    _check_attempt_depths(raw_sequence, specs,
                          n_attempts_weak=n_attempts_weak)
    _check_merge_check_needs_exams(raw_sequence, specs)
    return specs


def _check_first_phase_is_execute(raw_sequence: List[str],
                                  specs: Dict[str, PhaseSpec]) -> None:
    """Checklist item 6: the sequence starts with an execute phase. A
    learn phase first would have no measurement to learn from -- a
    failure the learn driver previously raised only at run time."""
    first = specs[raw_sequence[0]]
    if not isinstance(first, ExecutePhaseSpec):
        raise ConfigError(
            f"the sequence starts with {raw_sequence[0]!r} of kind "
            f"{getattr(first, 'kind', None)!r}: a learn phase needs a "
            "preceding execute phase to learn from, so the sequence must "
            "start with an execute phase")


def _check_compare_targets(raw_sequence: List[str],
                           specs: Dict[str, PhaseSpec]) -> None:
    """Checklist item 4, first half: ``against`` is an earlier sequence
    name or exactly ``fresh_bare``, and the named control is a BASELINE
    phase. A baseline's weak bare arm is the only report whose rows are
    bare measurements; a live phase's report rows all carry the
    injected group, and comparing against one would read a with-skill
    arm as the control."""
    for position, name in enumerate(raw_sequence):
        spec = specs[name]
        if not isinstance(spec, ExecutePhaseSpec) or spec.compare is None:
            continue
        against = spec.compare.against
        if against == FRESH_BARE:
            continue
        if against not in raw_sequence[:position]:
            raise ConfigError(
                f"phases.{name}.compare.against is {against!r}, which is "
                "not 'fresh_bare' and does not appear earlier in the "
                f"sequence; earlier phases: {raw_sequence[:position]}")
        target = specs[against]
        if not isinstance(target, ExecutePhaseSpec) or \
                target.role != PHASE_ROLE_BASELINE:
            raise ConfigError(
                f"phases.{name}.compare.against is {against!r} with role "
                f"{getattr(target, 'role', None)!r}: a control is a "
                "baseline phase's bare measurement, never an exam, live "
                "or final-test phase")


def _check_live_needs_a_control(raw_sequence: List[str],
                                specs: Dict[str, PhaseSpec]) -> None:
    """Checklist item 7: a live phase's report rows are all with-skill
    rows, so its control must be a named earlier baseline phase. Without
    ``compare`` (or with ``fresh_bare``) the decision would have no
    control row to compare against -- a failure the learn driver
    previously raised only after the live phase's executions were
    spent."""
    for name in raw_sequence:
        spec = specs[name]
        if not isinstance(spec, ExecutePhaseSpec) or \
                spec.role != PHASE_ROLE_LIVE:
            continue
        if spec.compare is None or spec.compare.against == FRESH_BARE:
            raise ConfigError(
                f"phases.{name} is a live phase without a control: its "
                "report rows all carry the injected group, so "
                "compare.against must name an earlier baseline phase "
                "(not fresh_bare and not nothing)")


def _check_attempt_depths(raw_sequence: List[str],
                          specs: Dict[str, PhaseSpec],
                          *, n_attempts_weak: int) -> None:
    """Checklist item 4, second half: a single-arm real phase's explicit
    ``n_attempts`` never exceeds its control's weak-model depth (the
    same bound v3's check_live_run drew against the stage-1 weak
    baseline: executions beyond the control's depth have no control to
    compare against). The control is ``fresh_bare`` or a baseline phase
    -- both run the weak model ``n_attempts_weak`` times."""
    for name in raw_sequence:
        spec = specs[name]
        if not isinstance(spec, ExecutePhaseSpec):
            continue
        n_attempts = spec.execution.get("n_attempts")
        if n_attempts is None:
            continue
        if not _is_positive_int(n_attempts):
            raise ConfigError(
                f"phases.{name}.execution.n_attempts must be a positive "
                f"int, got {n_attempts!r}")
        if n_attempts > n_attempts_weak:
            raise ConfigError(
                f"phases.{name}.execution.n_attempts ({n_attempts}) "
                f"exceeds its control's weak-model depth "
                f"({n_attempts_weak}); the comparison would ask for "
                "executions the control never ran")


def _check_merge_check_needs_exams(raw_sequence: List[str],
                                   specs: Dict[str, PhaseSpec]) -> None:
    """Checklist item 5: ``curate.merge_check: exam`` proves every kept
    merge on regression exams, and those exams only exist when the
    sequence has an exam-role execute phase (the upgrade maps live_run
    configs to merge_check 'none' for exactly this reason)."""
    has_exam_phase = any(
        isinstance(specs[name], ExecutePhaseSpec)
        and specs[name].role == PHASE_ROLE_EXAM for name in raw_sequence)
    for name in raw_sequence:
        spec = specs[name]
        if not isinstance(spec, LearnPhaseSpec):
            continue
        if spec.curate.get("name") == "agentbrew" and \
                spec.curate.get("merge_check") == "exam" and \
                not has_exam_phase:
            raise ConfigError(
                f"phases.{name}.curate asks merge_check 'exam' but the "
                "sequence has no exam execute phase: the merges would be "
                "proved on regression exams nothing generates (a "
                "live-only sequence uses merge_check 'none' -- merges "
                "are proven by the round-1 group re-execution)")


def phase_spec_to_raw(spec: PhaseSpec) -> Dict[str, Any]:
    """The freezable form of one parsed phase spec: the same shape
    ``parse_phases`` accepts, so ``to_dict`` -> ``from_dict`` round-trips
    (the manifest comparison depends on that)."""
    return dataclasses.asdict(spec)


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
    if keys in (frozenset({"train", "test"}),
                frozenset({"train", "test", "reserved"})):
        for name in ("train", "test", "reserved"):
            value = split.get(name)
            if value is None:
                continue
            if (not isinstance(value, list)
                    or not all(isinstance(t, str) for t in value)):
                raise ValueError(
                    f"split[{name!r}] must be a list of task-id strings, "
                    f"got {value!r}")
    elif "ratio" in keys:
        ratio = split["ratio"]
        if not isinstance(ratio, (int, float)) or not 0.0 < ratio < 1.0:
            raise ValueError(f"split ratio must be in (0, 1), got {ratio!r}")
        seed = split.get("seed", 0)
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise ValueError(f"split seed must be an int, got {seed!r}")
        if keys - frozenset(("ratio", "seed")):
            raise ValueError(
                f"split mixes ratio and explicit lists: {sorted(keys)}")
    else:
        raise ValueError(
            "split must give {'train': [...], 'test': [...]}, "
            "{'train': [...], 'test': [...], 'reserved': [...]}, or "
            f"{{'ratio': float, 'seed': int}}, got keys {sorted(keys)}")


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
    unknown_roles = set(roles) - set(ROLE_DEFAULTS)
    if unknown_roles:
        raise ConfigError(
            f"token_budgets.roles has unknown roles "
            f"{sorted(unknown_roles, key=str)}, expected a subset of "
            f"{list(ROLE_DEFAULTS)}")
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
    #: None in ``full`` mode; task-id lists or ratio+seed in ``split`` mode.
    split: Optional[Dict[str, Any]] = None
    #: The experiment's task set: None (plugin's full set), a list of raw
    #: task ids, or a task-file path (plugin-interpreted). Stage 0 loads
    #: exactly this; explicit split lists must then cover it exactly.
    tasks: Union[None, str, List[str]] = None
    #: The method, as a sequence of phase LOCAL names (R9). Position i
    #: (0-based) is the phase the ledger and the per-phase directories
    #: label ``p{i+1:02d}:<name>``; a name may repeat (the exam method's
    #: ``[D, L] + [A, L] x 3 + [C]``).
    sequence: List[str] = field(default_factory=list)
    #: The parsed phase definitions, keyed by local name: raw mappings on
    #: the way in (YAML / a frozen manifest), ExecutePhaseSpec /
    #: LearnPhaseSpec afterwards (parse_phases runs the load-time
    #: checklist in __post_init__).
    phases: Dict[str, PhaseSpec] = field(default_factory=dict)
    #: How many times the phase driver may re-dispatch one unit the
    #: verdict ruled invalid (R9); after the limit the unit stays a hole
    #: with cause ``invalid_redispatch_exhausted``. A method parameter:
    #: accepting holes changes what is measured, so it freezes.
    invalid_redispatch_limit: int = DEFAULT_INVALID_REDISPATCH_LIMIT
    #: Named-rule selections; missing keys are filled from DEFAULT_RULES,
    #: and stage 0 validates every name against the rule registries.
    rules: Dict[str, str] = field(default_factory=dict)
    #: Prompt name -> digest; filled by the prompt registry (B2) when the
    #: stages freeze the manifest, empty in the source config.
    prompts_digests: Dict[str, str] = field(default_factory=dict)
    #: Model NAME -> extra chat-call parameters (e.g. ``reasoning_effort``)
    #: forwarded to the endpoint for every call of that model, whichever
    #: role makes it. Changes what the model produces, so it is part of
    #: the experiment's frozen identity, not an operational setting.
    model_options: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: Per-role token budgets (schema v2), the single source of every call's
    #: caps: ``default_max_output_tokens`` (positive int, required when this
    #: mapping is non-empty) is the fallback for roles without an entry;
    #: ``max_input_tokens`` (positive int or null = measure only) is global;
    #: ``roles`` maps role names to entries carrying a required
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
        if self.split is not None:
            _validate_split(self.split)
        self.tasks = _normalise_tasks(self.tasks)
        # The phase sequence: parsed here (the load-time checklist runs
        # before anything freezes), whether the caller handed raw
        # mappings (YAML / a frozen manifest) or already-parsed specs
        # (dataclasses.replace on a loaded config). Deep-copied inside,
        # so the frozen value never aliases the caller's mapping.
        self.sequence = list(self.sequence)
        raw_phases = {
            name: (phase_spec_to_raw(spec)
                   if isinstance(spec, (ExecutePhaseSpec, LearnPhaseSpec))
                   else spec)
            for name, spec in self.phases.items()}
        self.phases = parse_phases(
            raw_phases, self.sequence,
            n_attempts_strong=self.n_attempts_strong,
            n_attempts_weak=self.n_attempts_weak)
        if not _is_positive_int(self.invalid_redispatch_limit):
            raise ConfigError(
                "invalid_redispatch_limit must be a positive int, got "
                f"{self.invalid_redispatch_limit!r}")
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
        unknown = set(self.models) - set(ROLE_DEFAULTS)
        if unknown:
            raise ValueError(
                f"unknown model roles {sorted(unknown)}, "
                f"expected subset of {list(ROLE_DEFAULTS)}")
        strong = self.models.get("executor_strong")
        weak = self.models.get("executor_weak")
        if not strong or not weak:
            raise ValueError(
                "models must define 'executor_strong' and 'executor_weak'")
        # Fill the role table from the data defaults: the answerer uses
        # the weak model by design, everything else answers with the
        # strong one; a None anchor stays absent -- the pipeline's
        # handle builder refuses a missing role, so a must-configure
        # role cannot silently fall back.
        for role, anchor in ROLE_DEFAULTS.items():
            if anchor is None:
                continue
            self.models.setdefault(role, weak if anchor == "weak" else strong)
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
        if role not in ROLE_DEFAULTS:
            raise KeyError(
                f"unknown model role {role!r}; known: {list(ROLE_DEFAULTS)}")
        entry = self.token_budgets.get("roles", {}).get(role)
        if entry is not None:
            return entry["max_output_tokens"]
        return self.token_budgets["default_max_output_tokens"]

    def to_dict(self) -> Dict[str, Any]:
        """The freezable payload: every field, values included as they are,
        with the parsed phase specs back in their raw (JSON-safe) form so
        ``from_dict(to_dict(x))`` round-trips -- B4's resume comparison
        depends on that equality."""
        payload = {f.name: getattr(self, f.name) for f in fields(self)}
        payload["phases"] = {name: phase_spec_to_raw(spec)
                             for name, spec in self.phases.items()}
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExperimentConfig":
        if not isinstance(data, dict):
            raise TypeError(
                f"config must be a mapping, got {type(data).__name__}")
        known = {f.name for f in fields(cls)}
        # Schema version first, so a record from newer code says "needs
        # newer code" and one from older code says "convert it" instead of
        # drowning in unknown-key errors its own schema would explain. The
        # config IS the experiment's identity: an unknown key is a typo or
        # a version mismatch, and dropping it silently once ran a new config
        # on old code with dead defaults.
        version = data.get("schema_version", CONFIG_SCHEMA_VERSION)
        if not isinstance(version, int) or isinstance(version, bool):
            raise ConfigError(
                f"schema_version must be an int, got {version!r}")
        if version > CONFIG_SCHEMA_VERSION:
            raise ConfigError(
                f"config schema_version is {version}, newer than this code's "
                f"{CONFIG_SCHEMA_VERSION}; this config needs newer code -- "
                "update the repository and retry")
        if version < CONFIG_SCHEMA_VERSION:
            raise ConfigError(
                f"config schema_version is {version}; this code speaks "
                f"schema {CONFIG_SCHEMA_VERSION} (the phase sequence) -- "
                "convert the config with upgrade_v3_config (the converter "
                "script scripts/run/convert_v3_configs.py writes NEW file "
                "names; v3 files are frozen experiment records; a v2 file "
                "chains through upgrade_v2_config first), and resume runs "
                "frozen under an older schema on their maintenance branch "
                "(AGENTS section 6)")
        unknown = [key for key in data if key not in known]
        if unknown:
            raise ConfigError(
                f"unknown config keys {sorted(unknown, key=str)}; known keys: "
                f"{sorted(known)}")
        return cls(**{k: v for k, v in data.items() if k in known})


def upgrade_v2_config(data: Dict[str, Any]) -> Dict[str, Any]:
    """Map a schema-v2 CONFIG dict (a raw YAML mapping, or a frozen
    manifest's ``config`` entry) to the v3 shape. Pure stdlib, copy in /
    copy out; the two users are the one-time converter script and
    ``assemble_reuse`` reading a v2 source run's manifest for the identity
    walk.

    Deliberate identity changes, disclosed here and in the converter's
    diff output: (1) the v2 top-level ``retry_limit`` was the exam
    protocol's rewrite budget, unused by live_run -- a v2 live_run config's
    value is DROPPED; (2) the three v2 spellings of "exam" (the key
    absent, ``{}``, ``{mode: exam}``) map to the same v3 value; (3) v2's
    ``{mode: whole_bank}`` becomes ``{name: whole_bank}``. Everything
    else moves key-for-key into the slot that owns it.
    """
    v2 = copy.deepcopy(data)
    methods: Dict[str, Dict[str, Any]] = {
        "segmentation": {
            "K": v2.pop("K", 5),
            "retry_limit": v2.pop("segment_retry_limit", 2),
        },
        "authoring": {"exams": True},
        "curation": {
            "name": "agentbrew",
            "token_budget": v2.pop("bank_token_budget", 4096),
            "rounds": v2.pop("curation_rounds", 3),
        },
    }
    acceptance = v2.pop("acceptance", None)
    if acceptance is None or not acceptance or acceptance.get(
            "mode", "exam") == "exam":
        # All three v2 spellings of the exam protocol map to one value.
        methods["acceptance"] = {
            "name": "exam",
            "retry_limit": v2.pop("retry_limit", 2),
        }
    else:
        # live_run never read the top-level retry_limit; dropping it is a
        # disclosed identity change, not a loss.
        v2.pop("retry_limit", None)
        methods["acceptance"] = {
            "name": "live_run",
            **{key: acceptance[key] for key in
               ("n_attempts", "improvement", "max_injection_tokens")
               if key in acceptance},
        }
    delivery = v2.pop("skill_delivery", None) or {"mode": "whole_bank"}
    if delivery.get("mode", "whole_bank") == "whole_bank":
        methods["delivery"] = {"name": "whole_bank"}
    else:
        methods["delivery"] = {
            "name": delivery["mode"],
            **{key: delivery[key] for key in
               ("top_k", "max_categories", "min_score") if key in delivery},
        }
    v2["methods"] = methods
    v2["schema_version"] = 3
    return v2


def _refuse_unmapped_keys(slot: str, given: Dict[str, Any],
                          mapped: Any) -> None:
    """Refuse slot keys the v3 -> v4 mapping has no destination for.

    A key that neither moves into a phase nor is copied verbatim would
    be silently dropped, and a dropped config key is a changed
    experiment identity -- the same trap as the unknown top-level key.
    """
    unknown = sorted(set(given) - set(mapped), key=str)
    if unknown:
        raise ConfigError(
            f"methods.{slot} has key(s) {unknown} with no v4 destination; "
            f"the mapping moves {list(mapped)} -- refusing rather than "
            "dropping them")


def _v3_methods(methods: Any) -> Dict[str, Dict[str, Any]]:
    """Validate and complete a v3 ``methods`` mapping (upgrade_v3_config's
    input side; the v4 loader has no slots). Same contract the v3 loader's
    ``_validate_methods`` had: five known slots, an absent/null/empty slot
    completes from ``_V3_METHOD_DEFAULTS``, a non-empty mapping freezes
    verbatim."""
    if not isinstance(methods, dict):
        raise ConfigError(
            f"methods must be a mapping, got {type(methods).__name__}")
    unknown = sorted(set(methods) - set(_V3_METHOD_DEFAULTS), key=str)
    if unknown:
        raise ConfigError(
            f"methods has unknown slot(s) {unknown}; the slots are "
            f"{list(_V3_METHOD_DEFAULTS)}")
    completed: Dict[str, Dict[str, Any]] = {}
    for slot, default in _V3_METHOD_DEFAULTS.items():
        given = methods.get(slot)
        if given is not None and not isinstance(given, dict):
            raise ConfigError(
                f"methods.{slot} must be a mapping of parameter names to "
                f"values, got {type(given).__name__}")
        completed[slot] = dict(given) if given else dict(default)
    return completed


def upgrade_v3_config(data: Dict[str, Any]) -> Dict[str, Any]:
    """Map a schema-v3 CONFIG dict (a raw YAML mapping, or a frozen v3
    manifest's ``config`` entry) to the v4 phase-sequence shape (docs/
    decisions/execute_learn_implementation_20261015.md section 4). Pure
    stdlib, copy in / copy out; the consumers are the one-time v3 -> v4
    converter and the reuse assembler's identity walk, both of which
    chain v1/v2 sources through ``upgrade_v2_config`` first.

    The result carries ``schema_version: 4`` as DATA only: this code
    still speaks v3 (``CONFIG_SCHEMA_VERSION`` flips with the M4.3
    core, whose loader learns ``sequence``/``phases``), so the output
    does not load yet and the converter writes it under new file names.

    The emitted shapes are exactly what schema v4's ``parse_phases``
    accepts: every phase declares its R8 role (D baseline, A exam, B
    live, C test, L learn), and the v4-only global
    ``invalid_redispatch_limit`` is emitted at its default.

    Deliberate identity changes, disclosed here and in the converter's
    diff output:

    * the acceptance method dissolves into the SEQUENCE -- exam with
      ``retry_limit=R`` becomes ``[D, L] + [A, L] x (R+1) + [C]`` (the
      K4 answer rounds), live_run becomes ``[D, L, B, L, B, L, C]`` --
      and live_run's ``n_attempts`` / ``improvement`` /
      ``max_injection_tokens`` move into the B phase;
    * a live_run config's merges are no longer proven on regression
      exams: ``L.curate.merge_check = none`` (the live sequence has no
      exam phase to check against; merges are proven by the round-1
      group re-execution instead). An explicit ``merge_check`` in the
      curation slot is preserved -- except ``exam`` under live_run,
      which has no v4 image and is refused;
    * the authoring ``exams`` switch is deleted: true is implied by
      every emitted sequence; false converts only as the one legal
      ablation pair (live_run acceptance + curation ``none`` -- the
      same boundary v3's own cross-slot check draws) and is refused
      otherwise. A live sequence has no A phase either way, so the
      ablation pair upgrades to the identical shape;
    * the five ``methods`` slots are deleted; delivery and curation
      params move key-for-key into the C and L phases.

    Absent, null or empty slots complete from the v3 defaults
    (``_V3_METHOD_DEFAULTS``) exactly as the v3 loader froze them, so the output is the v4
    identity of what the same file loads as. Everything else -- rules,
    budgets, the role table, weak_profile, mode, split, tasks,
    model_options, prompts_digests -- passes through untouched. Inputs
    that are not v3-shaped fail loudly: a non-mapping (TypeError, the
    ``from_dict`` precedent), a config already carrying v4 keys, and a
    config without a ``methods`` mapping (a v1/v2 config: chain
    ``upgrade_v2_config`` first).
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"config must be a mapping, got {type(data).__name__}")
    carried = [key for key in ("sequence", "phases") if key in data]
    if carried:
        raise ConfigError(
            f"the config already carries {carried} -- v4 keys naming the "
            "phases; upgrade_v3_config maps v3 -> v4 and an already-v4 "
            "config has nothing to upgrade")
    methods_given = data.get("methods")
    if not isinstance(methods_given, dict):
        raise ConfigError(
            "upgrade_v3_config needs a schema-v3 config (the method "
            "parameters live under 'methods', got "
            f"{type(methods_given).__name__}); a v1 or v2 config chains "
            "through upgrade_v2_config first, and a v4 config is already "
            "upgraded")
    mode = data.get("mode")
    if mode not in MODES:
        raise ConfigError(f"mode must be one of {MODES}, got {mode!r}")
    v4 = copy.deepcopy(data)
    methods = _v3_methods(v4.pop("methods"))

    # -- segmentation -> L.evidence (the add path's cutting budget) -----
    segmentation = methods["segmentation"]
    seg_name = segmentation.get("name", "llm")
    if seg_name != "llm":
        raise ConfigError(
            f"methods.segmentation name {seg_name!r} has no v4 mapping; "
            "the v3 LLM splitter is the evidence method 'segment'")
    _refuse_unmapped_keys("segmentation", segmentation,
                          ("name", "K", "retry_limit"))
    if "K" not in segmentation:
        raise ConfigError(
            "methods.segmentation method 'llm' is missing required "
            "parameter 'K'")
    evidence = {
        "name": "segment",
        "K": segmentation["K"],
        "retry_limit": segmentation.get(
            "retry_limit", _V3_METHOD_DEFAULTS["segmentation"]["retry_limit"]),
    }

    # -- authoring -> L.author (the exams switch dies with the slot) ----
    authoring = methods["authoring"]
    auth_name = authoring.get("name", "distill_diagnose")
    if auth_name != "distill_diagnose":
        raise ConfigError(
            f"methods.authoring name {auth_name!r} has no v4 mapping; the "
            "v3 authoring method is the author family's "
            "'distill_diagnose'")
    _refuse_unmapped_keys("authoring", authoring, ("name", "exams"))
    exams = authoring.get("exams", _V3_METHOD_DEFAULTS["authoring"]["exams"])
    if not isinstance(exams, bool):
        raise ConfigError(
            f"methods.authoring.exams must be a bool, got {exams!r}")

    # -- acceptance -> the sequence (and the B phase for live_run) ------
    acceptance = methods["acceptance"]
    kind = acceptance.get("name")
    if kind is None:
        raise ConfigError(
            "methods.acceptance needs a name; known: ['exam', 'live_run']")
    if kind not in ("exam", "live_run"):
        raise ConfigError(
            f"methods.acceptance name {kind!r} has no v4 mapping; the "
            "sequence derives from 'exam' or 'live_run'")

    # -- curation -> L.curate (merge_check follows the acceptance) ------
    curation = methods["curation"]
    curate_name = curation.get("name")
    if curate_name is None:
        raise ConfigError(
            "methods.curation needs a name; known: ['agentbrew', 'none']")
    if curate_name == "agentbrew":
        _refuse_unmapped_keys("curation", curation,
                              ("name", "token_budget", "rounds",
                               "merge_check"))
        if "token_budget" not in curation:
            raise ConfigError(
                "methods.curation method 'agentbrew' is missing required "
                "parameter 'token_budget'")
        merge_check = curation.get("merge_check")
        if merge_check is None:
            # The mapping's derivation: exam acceptance proves merges on
            # the generated exams; live_run has no exam phase (merges are
            # proven by the round-1 group re-execution instead).
            merge_check = "exam" if kind == "exam" else "none"
        elif kind == "live_run" and merge_check == "exam":
            raise ConfigError(
                "methods.curation.agentbrew merge_check is 'exam' but the "
                "acceptance method is 'live_run': the v4 live sequence "
                "generates no exams to check merges against (drop the "
                "explicit merge_check or use exam acceptance)")
        curate = {
            "name": "agentbrew",
            "merge_check": merge_check,
            "token_budget": curation["token_budget"],
            "rounds": curation.get(
                "rounds", _V3_METHOD_DEFAULTS["curation"]["rounds"]),
        }
    elif curate_name == "none":
        _refuse_unmapped_keys("curation", curation,
                              ("name", "token_budget"))
        if "token_budget" not in curation:
            raise ConfigError(
                "methods.curation method 'none' is missing required "
                "parameter 'token_budget'")
        curate = {"name": "none", "token_budget": curation["token_budget"]}
    else:
        raise ConfigError(
            f"methods.curation name {curate_name!r} has no v4 mapping; "
            "the curate family offers 'agentbrew' and 'none'")

    if not exams and not (kind == "live_run" and curate_name == "none"):
        raise ConfigError(
            "methods.authoring.exams is false but the combination "
            f"(acceptance {kind!r}, curation {curate_name!r}) has no v4 "
            "image: exam generation off converts only with live_run "
            "acceptance and curation 'none' -- the one legal ablation "
            "pair, the same boundary v3's own cross-slot check draws")

    # -- delivery -> C.delivery, verbatim (same name, same params) ------
    delivery = methods["delivery"]
    if "name" not in delivery:
        raise ConfigError(
            "methods.delivery needs a name; known: ['whole_bank', "
            "'retrieval', 'none', 'per_skill', 'group_by_task']")

    # -- the phases (section 4's shapes, fixed construction order) ------
    phases: Dict[str, Any] = {
        "D": {
            "kind": "execute",
            "role": PHASE_ROLE_BASELINE,
            "tasks": "train",
            "delivery": {"name": "none"},
            "execution": {"name": "real",
                          "arms": ["executor_strong", "executor_weak"]},
        },
        "L": {
            "kind": "learn",
            "role": PHASE_ROLE_LEARN,
            "evidence": evidence,
            "author": {"name": "distill_diagnose"},
            "curate": curate,
        },
    }
    if kind == "exam":
        _refuse_unmapped_keys("acceptance", acceptance,
                              ("name", "retry_limit"))
        retry_limit = acceptance.get(
            "retry_limit", _V3_METHOD_DEFAULTS["acceptance"]["retry_limit"])
        if (not isinstance(retry_limit, int) or isinstance(retry_limit, bool)
                or retry_limit < 0):
            raise ConfigError(
                "methods.acceptance.exam retry_limit must be an int >= 0 "
                f"(the sequence carries retry_limit + 1 A phases), got "
                f"{retry_limit!r}")
        phases["A"] = {
            "kind": "execute",
            "role": PHASE_ROLE_EXAM,
            "delivery": {"name": "per_skill"},
            "execution": {"name": "exam"},
        }
        sequence = ["D", "L"] + ["A", "L"] * (retry_limit + 1) + ["C"]
    else:
        _refuse_unmapped_keys("acceptance", acceptance,
                              ("name", "n_attempts", "improvement",
                               "max_injection_tokens"))
        for key in ("n_attempts", "improvement"):
            if key not in acceptance:
                raise ConfigError(
                    "methods.acceptance method 'live_run' is missing "
                    f"required parameter {key!r}")
        phases["B"] = {
            "kind": "execute",
            "role": PHASE_ROLE_LIVE,
            "delivery": {"name": "group_by_task"},
            "execution": {
                "name": "real",
                "n_attempts": acceptance["n_attempts"],
                "max_injection_tokens": acceptance.get(
                    "max_injection_tokens"),
            },
            "compare": {"against": "D", "rule": acceptance["improvement"]},
        }
        sequence = ["D", "L", "B", "L", "B", "L", "C"]
    phases["C"] = {
        "kind": "execute",
        "role": PHASE_ROLE_TEST,
        "tasks": "test",
        "delivery": delivery,
        "execution": {"name": "real"},
        # Split mode runs its own fresh bare control arm on the test set;
        # full mode reuses D's weak arm (and its accepted holes).
        "compare": ({"against": "D"} if mode == "full"
                    else {"against": "fresh_bare"}),
    }
    v4["sequence"] = sequence
    v4["phases"] = phases
    # The v4-only global the v3 file cannot name: emit the default so the
    # converted identity is complete (complete-don't-reject) and a later
    # default change never silently re-identifies a converted config.
    v4["invalid_redispatch_limit"] = DEFAULT_INVALID_REDISPATCH_LIMIT
    v4["schema_version"] = 4
    return v4


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

    v4 fails loud on the RAW mapping: unknown top-level keys, future
    schema versions, and older-schema files (convert them first -- the
    error names the upgrade mapping and the converter) are ConfigErrors
    naming the offender.
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
    version = data.get("schema_version", 3)
    if not isinstance(version, int) or isinstance(version, bool):
        raise ConfigError(
            f"{path}: schema_version must be an int, got {version!r}")
    if version > CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"{path} has schema_version {version}, newer than this code's "
            f"{CONFIG_SCHEMA_VERSION}; this config needs newer code -- "
            "update the repository and retry")
    if version < CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"{path} has schema_version {version}; this code speaks "
            f"schema {CONFIG_SCHEMA_VERSION} (the phase sequence) -- "
            "convert it with upgrade_v3_config (the converter script "
            "scripts/run/convert_v3_configs.py writes NEW file names; "
            "v3 files are frozen experiment records; a v2 file chains "
            "through upgrade_v2_config first), and resume runs frozen "
            "under an older schema on their maintenance branch "
            "(AGENTS section 6)")
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
