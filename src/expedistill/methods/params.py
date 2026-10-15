"""The generic params builder: every method's params are one dataclass.

A slot's params class declares its fields and their types (the minimal
annotation set: int / float / str / bool and Optional of those); this
module turns the config's raw mapping into an instance with loud
failures: unknown keys, missing required keys, wrong types, then the
class's own ``__post_init__`` constraints. Deliberately tiny -- the v2
validators this replaces were hand-written per method inside
core.config, where the methods layer could not own them.
"""

import dataclasses
import typing
from typing import Any, Dict, Type

from expedistill.core.config import ConfigError

__all__ = ["build_params"]


def build_params(cls: Type, raw: Dict[str, Any], slot: str, name: str) -> Any:
    """``cls(**raw)`` with the loud validation described in the module
    docstring. ``raw`` must already have the method ``name`` removed."""
    hints = typing.get_type_hints(cls)
    fields = {field.name: field for field in dataclasses.fields(cls)}
    unknown = sorted(set(raw) - set(fields))
    if unknown:
        raise ConfigError(
            f"methods.{slot} method {name!r} got unknown parameter(s) "
            f"{unknown}; accepted: {sorted(fields)}")
    for fname, field in sorted(fields.items()):
        if fname in raw:
            continue
        if (field.default is dataclasses.MISSING
                and field.default_factory is dataclasses.MISSING):
            raise ConfigError(
                f"methods.{slot} method {name!r} is missing required "
                f"parameter {fname!r}")
    for fname, value in sorted(raw.items()):
        if not _matches(value, hints[fname]):
            raise ConfigError(
                f"methods.{slot} method {name!r}: parameter {fname!r} "
                f"must be {hints[fname]}, got {value!r}")
    return cls(**raw)


def _matches(value: Any, annotation: Any) -> bool:
    """True when ``value`` satisfies the annotation (the minimal set)."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union:
        return any(_matches(value, arg) for arg in typing.get_args(annotation))
    if annotation is type(None):
        return value is None
    if annotation is int:
        # bool is an int subclass but never a budget or a depth.
        return isinstance(value, int) and not isinstance(value, bool)
    if annotation is float:
        # YAML writes 0 as int; an int is a legal score (the v2 contract).
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if annotation is str:
        return isinstance(value, str)
    if annotation is bool:
        return isinstance(value, bool)
    raise ConfigError(
        f"unsupported params annotation {annotation!r}; the minimal set "
        "is int / float / str / bool and Optional of those")
