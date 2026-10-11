"""B1 test double: a scripted client used across the whole repo's tests.

No network, deterministic, and assertable: every call is recorded in
``.calls``. Blocks and composites test against FakeClient so their unit tests
never touch a real endpoint (AGENTS.md section 8).
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Union

from expedistill.llm.call import ModelHandle

__all__ = ["FakeClient", "json_script", "make_fake_handle"]


class FakeClient:
    """Scripted stand-in for OpenAICompatClient (same ``complete`` signature).

    ``script`` may be:
    - a single string: returned for every call;
    - a list: consumed in order; an exhausted script or a scripted exception
      instance is raised (to test error paths and retry behavior);
    - a callable ``(system, user) -> response``: its return value is passed
      through, so it can also yield CompletionResult-like objects or raise.

    Every call is appended to ``.calls`` as a dict for assertions.
    """

    def __init__(self, script: Union[str, List[Any], Callable[[str, str], Any]]):
        self.script = script
        self.calls: List[Dict[str, Any]] = []

    def complete(
        self,
        model: str,
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
        max_tokens: Any = None,
        extra: Any = None,
    ) -> Any:
        self.calls.append(
            {
                "model": model,
                "system": system,
                "user": user,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "extra": extra,
            }
        )
        if isinstance(self.script, str):
            return self.script
        if isinstance(self.script, list):
            if not self.script:
                raise AssertionError("FakeClient script exhausted")
            item = self.script.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        return self.script(system, user)


def make_fake_handle(
    role: str = "test",
    script: Union[str, List[Any], Callable[[str, str], Any]] = "",
    **kw: Any,
) -> ModelHandle:
    """A ModelHandle wired to a FakeClient, for block/composite tests."""
    return ModelHandle(
        name=kw.pop("name", "fake-model"),
        role=role,
        client=FakeClient(script),
        **kw,
    )


def json_script(payload: Union[dict, list]) -> str:
    """Render ``payload`` as a fenced JSON block, the way prompts ask for it."""
    return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
