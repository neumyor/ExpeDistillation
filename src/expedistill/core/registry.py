"""Generic named registry for prompts (B2), named rules and benchmark plugins.

README 1.1 rule 7: replaceable behaviour is selected by *name* -- the config
names a rule, the manifest freezes the name, and swapping behaviour means
registering a new name, never editing an existing entry. This module provides
the one mechanism all three registries share; each subsystem calls
:func:`make_registry` with its own ``kind`` label so error messages say
"unknown rule 'x'" rather than "unknown entry 'x'".

Entries are plain values (usually functions or prompt constants); the registry
knows nothing about what they do.
"""

from typing import Any, Callable, Dict, List


class Registry:
    """A name -> entry mapping with loud failures on misuse."""

    def __init__(self, kind: str = "entry") -> None:
        self._kind = kind
        self._entries: Dict[str, Any] = {}

    def register(self, name: str) -> Callable[[Any], Any]:
        """Decorator that files the decorated object under ``name``.

        A duplicate name is a programming error (two rules claiming the same
        identity), so it raises immediately rather than overwriting.
        """
        if name in self._entries:
            raise ValueError(
                f"{self._kind} {name!r} is already registered; "
                "register the new behaviour under a new name")
        # Bound in a closure per call so a loop-decorated definition cannot
        # accidentally capture a shared `name` variable.
        def deco(obj: Any) -> Any:
            self._entries[name] = obj
            return obj
        return deco

    def get(self, name: str) -> Any:
        try:
            return self._entries[name]
        except KeyError:
            known = ", ".join(sorted(self._entries)) or "(nothing registered)"
            raise KeyError(
                f"unknown {self._kind} {name!r}; known: {known}") from None

    def __contains__(self, name: str) -> bool:
        return name in self._entries

    def names(self) -> List[str]:
        return sorted(self._entries)


def make_registry(kind: str = "entry") -> Registry:
    return Registry(kind=kind)
