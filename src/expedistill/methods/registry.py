"""The method table: (slot, name) -> driver module path.

Plain data on purpose (the plan's "explicit import list, no plugin
auto-discovery" discipline): four entries today, each named in one
place, and resolving a name IMPORTS the driver module on demand -- the
table itself never imports anything, which is what keeps the exam
process from ever loading the live modules (the frozen prompt-name
surface stays the import-time snapshot, exactly as before).

The driver MODULE is the unit (a driver may carry more than one
function: the retrieval driver resolves AND writes its audit), so a
slot name asked for by the config that is not in the table is a
ConfigError listing what the slot offers -- same contract as the
named-rule registries (blocks/rules.py).
"""

import importlib
from typing import Dict, Tuple

from expedistill.infra.reliability import ConfigError

__all__ = ["get_method"]

#: One entry per (slot, name): the driver module's path. Adding a method
#: = one entry here + the driver file (plan section 8).
_ENTRIES: Dict[Tuple[str, str], str] = {
    ("acceptance", "exam"): "expedistill.methods.acceptance.exam",
    ("acceptance", "live_run"): "expedistill.methods.acceptance.live_run",
    ("delivery", "whole_bank"): "expedistill.methods.delivery.whole_bank",
    ("delivery", "retrieval"): "expedistill.methods.delivery.retrieval",
}


def get_method(slot: str, name: str):
    """The driver module registered under ``(slot, name)``, imported on
    demand."""
    try:
        module_path = _ENTRIES[(slot, name)]
    except KeyError:
        known = ", ".join(
            sorted(method for (s, method) in _ENTRIES if s == slot)
        ) or "(nothing registered)"
        raise ConfigError(
            f"unknown {slot} method {name!r}; known: {known}") from None
    return importlib.import_module(module_path)
