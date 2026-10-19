"""Pure mappings from older config schemas to schema v4.

``upgrade_v2_config`` maps a v2 config dict to v3 (the ``methods`` slots)
and ``upgrade_v3_config`` maps v3 to v4 (``sequence`` + ``phases``). Both
take a raw YAML mapping or a frozen manifest's ``config`` entry and return
a new dict; neither loads anything. The users are the one-time converter
``scripts/run/convert_v3_configs.py`` and the reuse assembler reading an
old source run's manifest; ``core.config`` re-exports both names.
"""

import copy
from typing import Any, Dict

from expedistill.core.config import (
    BASELINE_ARMS,
    DEFAULT_INVALID_REDISPATCH_LIMIT,
    MODES,
    PHASE_ROLE_BASELINE,
    PHASE_ROLE_EXAM,
    PHASE_ROLE_LEARN,
    PHASE_ROLE_LIVE,
    PHASE_ROLE_TEST,
    ConfigError,
)

__all__ = ["upgrade_v2_config", "upgrade_v3_config"]

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
    defaults = _V3_METHOD_DEFAULTS
    methods: Dict[str, Dict[str, Any]] = {
        "segmentation": {
            "K": v2.pop("K", defaults["segmentation"]["K"]),
            "retry_limit": v2.pop("segment_retry_limit",
                                  defaults["segmentation"]["retry_limit"]),
        },
        "authoring": dict(defaults["authoring"]),
        "curation": {
            "name": defaults["curation"]["name"],
            "token_budget": v2.pop("bank_token_budget",
                                   defaults["curation"]["token_budget"]),
            "rounds": v2.pop("curation_rounds",
                             defaults["curation"]["rounds"]),
        },
    }
    acceptance = v2.pop("acceptance", None)
    if acceptance is None or not acceptance or acceptance.get(
            "mode", "exam") == "exam":
        # All three v2 spellings of the exam protocol map to one value.
        methods["acceptance"] = {
            "name": "exam",
            "retry_limit": v2.pop("retry_limit",
                                  defaults["acceptance"]["retry_limit"]),
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

    The result carries ``schema_version: 4``; the converter writes it
    under new file names (v3 files are frozen experiment records).

    The emitted shapes are exactly what schema v4's ``parse_phases``
    accepts: every phase declares its R8 role and is named after it
    (baseline, exam, live, test, learn), and the v4-only global
    ``invalid_redispatch_limit`` is emitted at its default.

    Deliberate identity changes, disclosed here and in the converter's
    diff output:

    * the acceptance method dissolves into the SEQUENCE -- exam with
      ``retry_limit=R`` becomes
      ``[baseline, learn] + [exam, learn] x (R+1) + [test]`` (the K4
      answer rounds), live_run becomes
      ``[baseline, learn, live, learn, live, learn, test]`` -- and
      live_run's ``n_attempts`` / ``improvement`` /
      ``max_injection_tokens`` move into the live phase;
    * a live_run config's merges are no longer proven on regression
      exams: ``learn.curate.merge_check = none`` (the live sequence has no
      exam phase to check against; merges are proven by the round-1
      group re-execution instead). An explicit ``merge_check`` in the
      curation slot is preserved -- except ``exam`` under live_run,
      which has no v4 image and is refused;
    * the authoring ``exams`` switch is deleted: true is implied by
      every emitted sequence; false converts only as the one legal
      ablation pair (live_run acceptance + curation ``none`` -- the
      same boundary v3's own cross-slot check draws) and is refused
      otherwise. A live sequence has no exam phase either way, so the
      ablation pair upgrades to the identical shape;
    * the five ``methods`` slots are deleted; delivery and curation
      params move key-for-key into the test and learn phases.

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

    # -- segmentation -> learn.evidence (the add path's cutting budget) -----
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

    # -- authoring -> learn.author (the exams switch dies with the slot) ----
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

    # -- acceptance -> the sequence (and the live phase for live_run) ------
    acceptance = methods["acceptance"]
    kind = acceptance.get("name")
    if kind is None:
        raise ConfigError(
            "methods.acceptance needs a name; known: ['exam', 'live_run']")
    if kind not in ("exam", "live_run"):
        raise ConfigError(
            f"methods.acceptance name {kind!r} has no v4 mapping; the "
            "sequence derives from 'exam' or 'live_run'")

    # -- curation -> learn.curate (merge_check follows the acceptance) ------
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

    # -- delivery -> test.delivery, verbatim (same name, same params) ------
    delivery = methods["delivery"]
    if "name" not in delivery:
        raise ConfigError(
            "methods.delivery needs a name; known: ['whole_bank', "
            "'retrieval', 'none', 'per_skill', 'group_by_task']")

    # -- the phases (section 4's shapes, fixed construction order) ------
    # Each phase is NAMED after its role (2026-10-19: the old letters
    # D / L / A / B / C read as noise; the name is free, the role is what
    # the drivers dispatch on, so one vocabulary serves both).
    baseline, learn, exam, live, test = (
        PHASE_ROLE_BASELINE, PHASE_ROLE_LEARN, PHASE_ROLE_EXAM,
        PHASE_ROLE_LIVE, PHASE_ROLE_TEST)
    phases: Dict[str, Any] = {
        baseline: {
            "kind": "execute",
            "role": PHASE_ROLE_BASELINE,
            "tasks": "train",
            "delivery": {"name": "none"},
            "execution": {"name": "real",
                          "arms": list(BASELINE_ARMS)},
        },
        learn: {
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
                f"(the sequence carries retry_limit + 1 exam phases), got "
                f"{retry_limit!r}")
        phases[exam] = {
            "kind": "execute",
            "role": PHASE_ROLE_EXAM,
            "delivery": {"name": "per_skill"},
            "execution": {"name": "exam"},
        }
        sequence = ([baseline, learn] + [exam, learn] * (retry_limit + 1)
                    + [test])
    else:
        _refuse_unmapped_keys("acceptance", acceptance,
                              ("name", "n_attempts", "improvement",
                               "max_injection_tokens"))
        for key in ("n_attempts", "improvement"):
            if key not in acceptance:
                raise ConfigError(
                    "methods.acceptance method 'live_run' is missing "
                    f"required parameter {key!r}")
        phases[live] = {
            "kind": "execute",
            "role": PHASE_ROLE_LIVE,
            "delivery": {"name": "group_by_task"},
            "execution": {
                "name": "real",
                "n_attempts": acceptance["n_attempts"],
                "max_injection_tokens": acceptance.get(
                    "max_injection_tokens"),
            },
            "compare": {"against": baseline,
                        "rule": acceptance["improvement"]},
        }
        sequence = [baseline, learn, live, learn, live, learn, test]
    phases[test] = {
        "kind": "execute",
        "role": PHASE_ROLE_TEST,
        "tasks": "test",
        "delivery": delivery,
        "execution": {"name": "real"},
        # Split mode runs its own fresh bare control arm on the test set;
        # full mode reuses the baseline's weak arm (and its accepted holes).
        "compare": ({"against": baseline} if mode == "full"
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
