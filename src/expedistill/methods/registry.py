"""The method table: (slot, name) -> driver, params, check.

Plain data on purpose (the plan's "explicit list, no auto-discovery"
discipline). Resolving a slot imports only what that resolution needs:

* ``resolve_params`` imports the slot's light params package (never a
  driver), builds the params with loud validation, and runs the entry's
  ``check`` -- stage 0 does this for every slot before anything freezes,
  so a typo in the config stops the run before a single task loads.
* ``resolve`` additionally imports the driver module on demand, so the
  exam dispatch never loads the live driver and vice versa. The frozen
  prompt-name surface is the import-time snapshot (``METHOD_PROMPT_
  NAMES``); importing ``composites`` as a package still pulls the live
  blocks in through ``composites/__init__`` (baseline behaviour), so the
  guarded invariant is the name set, not the module graph.

A slot name asked for by the config that is not in the table is a
ConfigError listing what the slot offers -- same contract as the
named-rule registries (blocks/rules.py). Single-method slots may be
named explicitly (matching their one registered name) or left unnamed.
"""

import importlib
from typing import Any, Dict, NamedTuple, Optional, Tuple

from expedistill.core.config import ConfigError
from expedistill.methods.params import build_params

__all__ = ["resolve", "resolve_params"]

#: One table entry: the driver module (None until the slot gains one --
#: the stage consumes those params directly), the light module holding
#: the params class, and the optional cross-constraint check inside it.
class _Entry(NamedTuple):
    driver: Optional[str]
    params: str
    params_class: str
    check: Optional[str]


#: Adding a method = one entry here + its driver file (plan section 8).
_ENTRIES: Dict[Tuple[str, str], _Entry] = {
    ("segmentation", "llm"):
        _Entry(None, "expedistill.methods.segmentation",
               "SegmentationParams", None),
    ("authoring", "distill_diagnose"):
        _Entry(None, "expedistill.methods.authoring", "AuthoringParams",
               "check_authoring"),
    ("acceptance", "exam"):
        _Entry("expedistill.methods.acceptance.exam",
               "expedistill.methods.acceptance", "ExamParams", None),
    ("acceptance", "live_run"):
        _Entry("expedistill.methods.acceptance.live_run",
               "expedistill.methods.acceptance", "LiveRunParams",
               "check_live_run"),
    ("curation", "agentbrew"):
        _Entry("expedistill.methods.curation.agentbrew",
               "expedistill.methods.curation", "AgentBrewParams", None),
    ("curation", "none"):
        _Entry("expedistill.methods.curation.none",
               "expedistill.methods.curation", "NoneParams", None),
    ("delivery", "whole_bank"):
        _Entry("expedistill.methods.delivery.whole_bank",
               "expedistill.methods.delivery", "WholeBankParams", None),
    ("delivery", "retrieval"):
        _Entry("expedistill.methods.delivery.retrieval",
               "expedistill.methods.delivery", "RetrievalParams", None),
}


def _entry_for(slot: str, raw: Dict[str, Any]) -> Tuple[str, _Entry]:
    """The (name, entry) the config's raw slot mapping selects."""
    known = [name for (s, name) in _ENTRIES if s == slot]
    name = raw.get("name")
    if name is None:
        if len(known) != 1:
            raise ConfigError(
                f"methods.{slot} needs a name; known: {known}")
        name = known[0]
    try:
        return name, _ENTRIES[(slot, name)]
    except KeyError:
        raise ConfigError(
            f"unknown methods.{slot} name {name!r}; known: {known}") from None


def _resolve_entry(slot: str, config: Any) -> Tuple[str, _Entry, Any]:
    """Validate the slot's params; returns (name, entry, params).

    Never imports a driver: the params module is the slot's light
    package, so stage 0 can validate every slot with zero driver loads.
    """
    raw = config.methods.get(slot)
    if raw is None:
        raise ConfigError(
            f"methods.{slot} is missing from the config; the config "
            "loader completes every slot, so this is a construction bug")
    raw = dict(raw)
    name, entry = _entry_for(slot, raw)
    raw.pop("name", None)
    module = importlib.import_module(entry.params)
    params = build_params(getattr(module, entry.params_class), raw, slot, name)
    if entry.check is not None:
        getattr(module, entry.check)(params, config)
    return name, entry, params


def resolve_params(slot: str, config: Any) -> Any:
    """The slot's validated params object (no driver is loaded)."""
    return _resolve_entry(slot, config)[2]


def resolve(slot: str, config: Any) -> Tuple[Any, Any]:
    """(driver module, params) for the slot; imports the driver now.

    For a driver-less slot this is a construction bug: the stage owns
    those params and must not ask for a driver that does not exist.
    """
    name, entry, params = _resolve_entry(slot, config)
    if entry.driver is None:
        raise ConfigError(
            f"methods.{slot} name {name!r} has no driver yet; the stage "
            "consumes its params directly")
    return importlib.import_module(entry.driver), params
