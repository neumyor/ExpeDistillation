"""The authoring slot's contract (stage 3, G2/G3).

One method today (distill/diagnose, dispatched per subtask source);
the params carry only ``exams`` for now -- stage 3 keeps generating
exam pairs unconditionally, and P4 opens ``exams: false`` together with
``curation: none`` (the pair lands as one pre-registered protocol
change, decision doc section 7).
"""

from dataclasses import dataclass

__all__ = ["AuthoringParams"]


@dataclass(frozen=True)
class AuthoringParams:
    """Whether the authoring stage generates exam questions."""

    exams: bool = True
