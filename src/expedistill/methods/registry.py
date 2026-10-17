"""The method table: (family, name) -> driver, params.

Plain data on purpose (the plan's "explicit list, no auto-discovery"
discipline). R8 re-keyed the table to the five CONTENT families --
``delivery``, ``execution``, ``evidence``, ``author``, ``curation`` --
and retired the four v3 slot keys (segmentation.llm,
authoring.distill_diagnose, acceptance.exam, acceptance.live_run): the
v4 config declares phases, not slots, and a learn PHASE is never a
registry entry -- its content methods (evidence/author/curation) are.
Resolving a family imports only what that resolution needs:

* ``resolve_params`` imports the family's light params package (never a
  driver) and builds the params with loud validation -- stage 0 does
  this for every family of every phase before anything freezes, so a
  typo in the config stops the run before a single task loads.
* ``resolve`` additionally imports the driver module on demand, so the
  exam dispatch never loads the live driver and vice versa. The frozen
  prompt-name surface is the import-time snapshot (``METHOD_PROMPT_
  NAMES``); importing ``composites`` as a package still pulls the live
  blocks in through ``composites/__init__`` (baseline behaviour), so the
  guarded invariant is the name set, not the module graph.
* ``resolve_fixed`` resolves a name that is not a config choice (the
  bare control arm's ``none`` delivery) with an empty raw mapping
  through the same table and validation.

The raw mapping is the PHASE'S OWN nested mapping (v4): it carries the
method's ``name`` plus that method's parameters, e.g. a learn phase's
``curate`` value or an execute phase's ``delivery`` value. A family
name asked for by the config that is not in the table is a ConfigError
listing what the family offers -- same contract as the named-rule
registries (blocks/rules.py).

Driver-less entries (execution, evidence, author) are not a gap: the
phase driver consumes their params directly (there is no uniform driver
surface to dispatch to, and inventing one ahead of the first consumer
is the over-design the repair plan forbids). Cross-constraint checks
that used to live on entries (v3's check_authoring, check_live_run)
moved where their inputs live: the phase-shape checks into
core.config's load-time checklist, the rule-name validation into
stage 0.
"""

import importlib
from typing import Any, Dict, Mapping, NamedTuple, Optional, Tuple

from expedistill.core.config import ConfigError
from expedistill.methods.params import build_params

__all__ = ["FAMILIES", "resolve", "resolve_fixed", "resolve_params"]

#: The five content families (R8). The v4 phase fields name four of them
#: verbatim (delivery, execution, evidence, author); the learn phase's
#: ``curate`` field names the curation family.
FAMILIES = ("delivery", "execution", "evidence", "author", "curation")

#: One table entry: the driver module (None while the phase driver
#: consumes the params directly), the light module holding the params
#: class. The v3 ``check`` hook is gone: its two users retired with the
#: v3 slots, and their constraints moved to the config loader (load-time
#: checklist) and stage 0 (rule-name validation).
class _Entry(NamedTuple):
    driver: Optional[str]
    params: str
    params_class: str


#: Adding a method = one entry here + its driver file (plan section 8).
_ENTRIES: Dict[Tuple[str, str], _Entry] = {
    ("delivery", "whole_bank"):
        _Entry("expedistill.methods.delivery.whole_bank",
               "expedistill.methods.delivery", "WholeBankParams"),
    ("delivery", "retrieval"):
        _Entry("expedistill.methods.delivery.retrieval",
               "expedistill.methods.delivery", "RetrievalParams"),
    ("delivery", "none"):
        _Entry("expedistill.methods.delivery.none",
               "expedistill.methods.delivery", "NoneParams"),
    ("delivery", "per_skill"):
        _Entry("expedistill.methods.delivery.per_skill",
               "expedistill.methods.delivery", "PerSkillParams"),
    ("delivery", "group_by_task"):
        _Entry("expedistill.methods.delivery.group_by_task",
               "expedistill.methods.delivery", "GroupByTaskParams"),
    # The skill-folder play (M2): a catalog injection, test role only
    # (core.config's load-time checklist holds that boundary; the M3
    # execution version that pairs with it is not in the table yet).
    ("delivery", "folder"):
        _Entry("expedistill.methods.delivery.folder",
               "expedistill.methods.delivery", "FolderParams"),
    # R8's new family: what kind of run an execute phase makes. Driver-
    # less -- the phase driver consumes the params directly.
    ("execution", "real"):
        _Entry(None, "expedistill.methods.execution",
               "RealExecutionParams"),
    ("execution", "exam"):
        _Entry(None, "expedistill.methods.execution",
               "ExamExecutionParams"),
    ("evidence", "segment"):
        _Entry(None, "expedistill.methods.evidence",
               "SegmentationParams"),
    ("author", "distill_diagnose"):
        _Entry(None, "expedistill.methods.author", "AuthoringParams"),
    ("curation", "agentbrew"):
        _Entry("expedistill.methods.curation.agentbrew",
               "expedistill.methods.curation", "AgentBrewParams"),
    ("curation", "none"):
        _Entry("expedistill.methods.curation.none",
               "expedistill.methods.curation", "NoneParams"),
}


def _entry_named(family: str, name: str) -> _Entry:
    """The entry a KNOWN-name lookup selects; the one owner of the
    unknown-name ConfigError. Both resolution paths go through here --
    the config's choice and a fixed name -- so the message can never
    drift between them (M2 review F2/T-1)."""
    try:
        return _ENTRIES[(family, name)]
    except KeyError:
        known = [entry_name for (f, entry_name) in _ENTRIES
                 if f == family]
        raise ConfigError(
            f"unknown methods.{family} name {name!r}; known: {known}"
        ) from None


def _entry_for(family: str, raw: Mapping[str, Any]) -> Tuple[str, _Entry]:
    """The (name, entry) a phase's raw family mapping selects."""
    known = [name for (f, name) in _ENTRIES if f == family]
    name = raw.get("name")
    if name is None:
        if len(known) != 1:
            raise ConfigError(
                f"methods.{family} needs a name; known: {known}")
        name = known[0]
    return name, _entry_named(family, name)


def _load_params(family: str, name: str, entry: _Entry,
                 raw: Mapping[str, Any]) -> Any:
    """Build the entry's params from ``raw`` with loud validation."""
    module = importlib.import_module(entry.params)
    return build_params(getattr(module, entry.params_class),
                        dict(raw), family, name)


def _load_driver(family: str, name: str, entry: _Entry) -> Any:
    """Import the entry's driver module (driver-less entries are a bug
    in the CALLER: the phase driver consumes those params directly)."""
    if entry.driver is None:
        raise ConfigError(
            f"methods.{family} name {name!r} has no driver; the phase "
            "driver consumes its params directly")
    return importlib.import_module(entry.driver)


def _resolve_entry(family: str, raw: Mapping[str, Any],
                   ) -> Tuple[str, _Entry, Any]:
    """Validate the family's params; returns (name, entry, params).

    Never imports a driver: the params module is the family's light
    package, so stage 0 can validate every phase with zero driver loads.
    """
    if not isinstance(raw, Mapping):
        raise ConfigError(
            f"methods.{family} must be a mapping of the method's "
            f"parameter names to values, got {type(raw).__name__}")
    raw = dict(raw)
    name, entry = _entry_for(family, raw)
    raw.pop("name", None)
    return name, entry, _load_params(family, name, entry, raw)


def resolve_params(family: str, raw: Mapping[str, Any]) -> Any:
    """The family's validated params object (no driver is loaded)."""
    return _resolve_entry(family, raw)[2]


def resolve(family: str, raw: Mapping[str, Any]) -> Tuple[Any, Any]:
    """(driver module, params) for the family; imports the driver now.

    For a driver-less family (execution, evidence, author) this is a
    construction bug in the caller: the phase driver owns those params
    and must not ask for a driver that does not exist.
    """
    name, entry, params = _resolve_entry(family, raw)
    return _load_driver(family, name, entry), params


def resolve_fixed(family: str, name: str) -> Tuple[Any, Any]:
    """(driver module, params) for a FIXED name -- no phase mapping.

    Some call sites' semantics are not a config choice: a bare control
    arm always runs the ``none`` delivery, whatever ``delivery`` the
    experiment picked for its with-bank arm. It goes through the same
    table and the same validation as ``resolve`` (plan section 3): the
    raw mapping is empty, so a params class with required fields still
    fails loudly here.
    """
    entry = _entry_named(family, name)
    params = _load_params(family, name, entry, {})
    return _load_driver(family, name, entry), params
