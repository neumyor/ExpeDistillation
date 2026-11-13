"""The method table: (family, name) -> driver, params.

Plain data on purpose (the plan's "explicit list, no auto-discovery"
discipline). The table is keyed by the four CONTENT families --
``delivery``, ``execution``, ``review``, ``author``; a learn PHASE is
never a registry entry -- its content methods (review/author) are. Resolving a family imports only what that
resolution needs:

* ``resolve_params`` imports the family's light params package (never a
  driver) and builds the params with loud validation -- stage 0 does
  this for every family of every phase before anything freezes, so a
  typo in the config stops the run before a single task loads.
* ``resolve`` additionally imports the driver module on demand. The
  frozen prompt-name surface is the import-time snapshot
  (``METHOD_PROMPT_NAMES``), so the guarded invariant is the name set,
  not the module graph.
* ``resolve_fixed`` resolves a name that is not a config choice (the
  bare control arm's ``none`` delivery) with an empty raw mapping
  through the same table and validation.

The raw mapping is the PHASE'S OWN nested mapping (v4): it carries the
method's ``name`` plus that method's parameters, e.g. a learn phase's
``author`` value or an execute phase's ``delivery`` value. A family
name asked for by the config that is not in the table is a ConfigError
listing what the family offers -- same contract as the named-rule
registries (blocks/rules.py).

Driver-less entries (execution, review, author) are not a gap: the
phase driver consumes their params directly (there is no uniform driver
surface to dispatch to, and inventing one ahead of the first consumer
is the over-design the repair plan forbids). Cross-constraint checks
live where their inputs live: the phase-shape checks in core.config's
load-time checklist, the rule-name validation in stage 0.
"""

import importlib
from typing import Any, Dict, Mapping, NamedTuple, Optional, Tuple

from expedistill.core.config import ConfigError
from expedistill.methods.params import build_params

__all__ = ["resolve", "resolve_fixed", "resolve_params"]

#: One table entry: the driver module (None while the phase driver
#: consumes the params directly), the light module holding the params
#: class.
class _Entry(NamedTuple):
    driver: Optional[str]
    params: str
    params_class: str


#: Adding a method = one entry here + its driver file (plan section 8).
_ENTRIES: Dict[Tuple[str, str], _Entry] = {
    ("delivery", "whole_bank"):
        _Entry("expedistill.methods.delivery.whole_bank",
               "expedistill.methods.delivery.params", "WholeBankParams"),
    ("delivery", "none"):
        _Entry("expedistill.methods.delivery.none",
               "expedistill.methods.delivery.params", "NoneParams"),
    # The skill-folder play: practice and test phases (core.config's
    # load-time checklist holds that boundary and the pairing with the
    # real_folder execution below).
    ("delivery", "folder"):
        _Entry("expedistill.methods.delivery.folder_delivery",
               "expedistill.methods.delivery.params", "FolderParams"),
    # What kind of run an execute phase makes. Driver-less -- the phase
    # driver consumes the params directly.
    ("execution", "real"):
        _Entry(None, "expedistill.methods.execution",
               "RealExecutionParams"),
    # The skill-folder play: real's folder twin, paired with the folder
    # delivery by core.config's load-time checklist.
    ("execution", "real_folder"):
        _Entry(None, "expedistill.methods.execution",
               "RealFolderExecutionParams"),
    # Every learn phase's card writer: segments are typed into the run's
    # subtask type table and each type gets at most one card. Driver-less:
    # stages/phase_learn orchestrates methods/cards.
    ("author", "subtask_cards"):
        _Entry(None, "expedistill.methods.cards", "CardAuthoringParams"),
    # The learn phase's parameters; stages/phase_learn runs the flow.
    ("review", "review"):
        _Entry(None, "expedistill.methods.review", "ReviewParams"),
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

    For a driver-less family (execution, review, author)
    this is a construction bug in the caller: the phase driver owns those params
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
