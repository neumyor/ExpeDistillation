"""Prompt-template helpers for the prompt and benchmark-vocabulary tests."""

import string
from typing import Set

__all__ = ["placeholders"]


def placeholders(template: str) -> Set[str]:
    """The ``{field}`` names a template formats."""
    return {field for _, field, _, _ in string.Formatter().parse(template)
            if field is not None}
