"""B1 low-level client: one thin OpenAI-compatible chat call (layer 2).

Adapted from the SkillExpand GPTWrapper (runtime/models/llm.py @ TB-eval
4f436af). Behavioral contract kept from the old wrapper: talk to any
OpenAI-compatible endpoint, keep client-side retries at zero so the retry
budget stays with the callers (a dead endpoint must fail loudly instead of
hanging), and map transport failures onto the B6 exception taxonomy. Rewritten
on the ``openai>=1`` SDK instead of the old langchain-0.0.x/0.27 stack; see
docs/decisions for the migration note.

The API key is read from the environment, held only inside the SDK client,
and never logged, stored, or returned.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from expedistill.infra.reliability import (
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    ProviderRefusalError,
)

__all__ = [
    "API_KEY_ENV",
    "ENDPOINT_ENV",
    "TIMEOUT_ENV",
    "CompletionResult",
    "OpenAICompatClient",
]

ENDPOINT_ENV = "EXPEDISTILL_LLM_ENDPOINT"
API_KEY_ENV = "EXPEDISTILL_LLM_API_KEY"
TIMEOUT_ENV = "EXPEDISTILL_LLM_TIMEOUT_SECONDS"

#: Default per-request timeout (seconds); a weak-model ANSWER has been
#: observed to hit exactly 300s, hence the env override.
DEFAULT_TIMEOUT_SEC = 300.0

#: finish_reason values that mean the provider refused the content policy.
_REFUSAL_FINISH_REASONS = frozenset({"content_filter"})

#: 4xx statuses that mean "try again later" rather than "this request is
#: wrong" (SkillExpand runtime/models/llm.py TRANSIENT_HTTP_STATUSES).
TRANSIENT_HTTP_STATUSES = frozenset({408, 409, 429})


def _status_is_permanent(status: Any) -> bool:
    """Any other 4xx (bad key, unknown model, oversized context) cannot
    succeed on resend; 5xx and unknown statuses are treated as transient,
    as the SkillExpand classifier did."""
    return isinstance(status, int) and 400 <= status < 500 \
        and status not in TRANSIENT_HTTP_STATUSES


def _env_timeout() -> float:
    """Read EXPEDISTILL_LLM_TIMEOUT_SECONDS, same validation style as the
    other optional env knobs (e.g. the TB plugin's _env_float): a malformed
    or non-positive value is a ConfigError, an unset/blank one keeps the
    default. Read here in from_env (config resolution point), not per call,
    so one run holds one timeout."""
    raw = os.environ.get(TIMEOUT_ENV, "")
    if not raw.strip():
        return DEFAULT_TIMEOUT_SEC
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(
            f"{TIMEOUT_ENV} must be a number, got {raw!r}"
        ) from None
    if value <= 0:
        raise ConfigError(
            f"{TIMEOUT_ENV} must be positive, got {value!r}"
        ) from None
    return value


@dataclass(frozen=True)
class CompletionResult:
    """One completed chat call: text plus provider-reported token usage.

    Token counts are ``None`` when the provider did not report usage; callers
    must record unknown as unknown, never as zero (AGENTS.md section 6).
    ``finish_reason`` is the provider's stop cause (e.g. "stop", "length")
    and ``cached_tokens`` the prompt tokens served from the gateway cache
    (``usage.prompt_tokens_details.cached_tokens``), when reported.
    """

    text: str
    in_tokens: Optional[int] = None
    out_tokens: Optional[int] = None
    finish_reason: Optional[str] = None
    cached_tokens: Optional[int] = None
    raw_usage: Optional[Dict[str, Any]] = None


def _usage_dict(usage: Any) -> Optional[Dict[str, Any]]:
    """Normalize an SDK usage object into a plain dict, tolerating test doubles."""
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        try:
            dumped = usage.model_dump()
        except Exception:  # pragma: no cover - defensive, SDK internals
            dumped = None
        if isinstance(dumped, dict):
            return dumped
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _cached_tokens(usage: Any) -> Optional[int]:
    """Extract usage.prompt_tokens_details.cached_tokens, tolerating absence.

    The gateway-side cache template otherwise inflates the prompt-token cost
    of the strong model ~300x (real observation: a 20-token prompt reported
    as 3927, of which 3712 cached), which would mislead role cost
    comparisons; callers record the value next to the raw counts.
    """
    details = getattr(usage, "prompt_tokens_details", None)
    value = getattr(details, "cached_tokens", None)
    return value if isinstance(value, int) else None


def _refusal_reason(response: Any) -> Optional[str]:
    """Return why the provider refused the request, or None if it did not."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        return None
    choice = choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason in _REFUSAL_FINISH_REASONS:
        return f"finish_reason={finish_reason!r}"
    message = getattr(choice, "message", None)
    refusal = getattr(message, "refusal", None)
    if refusal:
        return f"message.refusal={refusal!r}"
    return None


class OpenAICompatClient:
    """Thin synchronous chat client over any OpenAI-compatible endpoint.

    One instance serves every model role; the model id is passed per call so
    the same endpoint can serve both the strong and the weak model.
    """

    def __init__(self, endpoint: str, api_key: str,
                 timeout_sec: float = DEFAULT_TIMEOUT_SEC):
        # Imported lazily so test doubles can patch this attribute without
        # importing the SDK for registry-only use.
        from openai import OpenAI

        # max_retries=0 keeps the retry budget with the callers: a dead
        # endpoint must fail loudly instead of silently multiplying timeouts.
        self._client = OpenAI(
            base_url=endpoint,
            api_key=api_key,
            timeout=timeout_sec,
            max_retries=0,
        )

    @classmethod
    def from_env(cls) -> "OpenAICompatClient":
        """Build a client from EXPEDISTILL_LLM_ENDPOINT / EXPEDISTILL_LLM_API_KEY.

        A missing variable is a configuration error, not an infrastructure
        error: it stops the run immediately. The message points at
        .env.example but never echoes any value that was set.
        """
        endpoint = os.environ.get(ENDPOINT_ENV, "").strip()
        api_key = os.environ.get(API_KEY_ENV, "").strip()
        missing = [
            name
            for name, value in ((ENDPOINT_ENV, endpoint), (API_KEY_ENV, api_key))
            if not value
        ]
        if missing:
            raise ConfigError(
                "missing required environment variable(s): "
                + ", ".join(missing)
                + "; copy .env.example to .env and fill them in (never commit .env)"
            )
        return cls(endpoint=endpoint, api_key=api_key,
                   timeout_sec=_env_timeout())

    def complete(
        self,
        model: str,
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> CompletionResult:
        """One non-streaming chat call; raises on transport, refusal, or empty output."""
        messages: list = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if extra:
            payload.update(extra)
        try:
            response = self._client.chat.completions.create(**payload)
        except Exception as exc:  # openai exceptions below; keep the cause chained
            from openai import APIConnectionError, APIStatusError

            if isinstance(exc, APIStatusError):
                # Status and error type only: the SDK message carries the
                # provider's error body, which may echo the request.
                status = getattr(exc, "status_code", None)
                detail = (f"LLM endpoint HTTP error for model {model}: "
                          f"status={status} type={type(exc).__name__}")
                if _status_is_permanent(status):
                    raise ProviderRefusalError(detail) from exc
                raise InfrastructureError(detail) from exc
            # APIConnectionError covers APITimeoutError in the openai>=1 SDK.
            if isinstance(exc, APIConnectionError):
                raise InfrastructureError(
                    f"LLM endpoint unreachable for model {model}: "
                    f"type={type(exc).__name__}"
                ) from exc
            raise

        refusal = _refusal_reason(response)
        if refusal is not None:
            raise ProviderRefusalError(
                f"model {model} refused the request ({refusal})"
            )
        choices = getattr(response, "choices", None) or []
        content = None
        finish_reason = None
        if choices:
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None)
            finish_reason = getattr(choices[0], "finish_reason", None)
        # Usage is read before the empty-content check so a response that
        # consumed tokens (empty content with finish_reason="length" burned
        # the whole budget on reasoning) carries its real spend into the
        # usage ledger instead of null.
        usage_obj = getattr(response, "usage", None)
        usage = _usage_dict(usage_obj)
        if content is None or not str(content).strip():
            # Empty with finish_reason="length" is the reasoning-effort=max
            # failure mode: the model burned its whole output budget on
            # reasoning and produced no answer; recording it as ok would
            # waste the step's budget silently. The finish_reason is in the
            # message so operators can tell "budget burned on reasoning"
            # from "model chose silence" (finish_reason="stop").
            why = "no content" if content is None \
                else "empty or whitespace-only content"
            raise ModelOutputError(
                f"model {model} returned {why} "
                f"(finish_reason={finish_reason!r})",
                usage=usage,
                finish_reason=finish_reason,
            )
        return CompletionResult(
            text=content,
            in_tokens=(usage or {}).get("prompt_tokens"),
            out_tokens=(usage or {}).get("completion_tokens"),
            finish_reason=finish_reason,
            cached_tokens=_cached_tokens(usage_obj),
            raw_usage=usage,
        )
