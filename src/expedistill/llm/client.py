"""B1 low-level client: one thin OpenAI-compatible chat call (layer 2).

Adapted from the SkillExpand GPTWrapper (runtime/models/llm.py @ TB-eval
4f436af). Behavioral contract kept from the old wrapper: talk to any
OpenAI-compatible endpoint, keep client-side retries at zero so the retry
budget stays with the callers (a dead endpoint must fail loudly instead of
hanging), and map transport failures onto the B6 exception taxonomy. Rewritten
on the ``openai>=1`` SDK instead of the old langchain-0.0.x/0.27 stack; see
docs/decisions for the migration note.

Transport: every call is streamed on the wire and assembled before it is
returned (see ``OpenAICompatClient.complete`` for the 300s gateway cutoff
that forced this, and for why the public contract is unchanged by it).

The API key is read from the environment, held only inside the SDK client,
and never logged, stored, or returned.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from expedistill.infra.reliability import (
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    ProviderRefusalError,
)

__all__ = [
    "ALIASES_ENV",
    "API_KEYS_ENV",
    "API_KEY_ENV",
    "ENDPOINTS_ENV",
    "ENDPOINT_ENV",
    "THINK_IN_CONTENT_ENV",
    "AliasPool",
    "CompletionResult",
    "OpenAICompatClient",
    "parse_aliases",
    "parse_model_overrides",
]

ENDPOINT_ENV = "EXPEDISTILL_LLM_ENDPOINT"
API_KEY_ENV = "EXPEDISTILL_LLM_API_KEY"

#: ``model=base_url;other=base_url``: a per-model endpoint override, for a
#: run whose roles are split across two servers (e.g. a self-hosted model
#: next to a gateway serving the rest). Both are transports, not identity:
#: the manifest keeps recording the logical model NAME, so switching a
#: model's server never changes what a run says it ran.
ENDPOINTS_ENV = "EXPEDISTILL_LLM_ENDPOINTS"
#: ``model=key;other=key``: the key for an overridden endpoint. A model
#: without an entry sends the default key.
API_KEYS_ENV = "EXPEDISTILL_LLM_API_KEYS"

#: Comma-separated model names whose server splits the thinking into a
#: separate ``reasoning`` channel (vLLM with ``--reasoning-parser``). For
#: those, the client puts the thinking back into the text as
#: ``<think>...</think>`` -- which is what the ALFWorld protocol requires
#: and what the same model returns through a gateway that does not split
#: it. Unset (the default) leaves every response exactly as received.
THINK_IN_CONTENT_ENV = "EXPEDISTILL_LLM_THINK_IN_CONTENT"

#: ``logical=alias1,alias2;other=aliasA,aliasB``. A gateway that rate-limits
#: per request id (not per model) serves one model under several ids; the
#: pool spreads requests over them. Gateway ids are access details, so they
#: live in the environment and never in a config, manifest or log.
ALIASES_ENV = "EXPEDISTILL_LLM_MODEL_ALIASES"

#: Seconds an alias rests after the gateway rate-limited it.
ALIAS_COOLDOWN_SECONDS = 20.0

#: finish_reason values that mean the provider refused the content policy.
_REFUSAL_FINISH_REASONS = frozenset({"content_filter"})

#: 4xx statuses that mean "try again later" rather than "this request is
#: wrong" (SkillExpand runtime/models/llm.py TRANSIENT_HTTP_STATUSES).
TRANSIENT_HTTP_STATUSES = frozenset({408, 409, 429})

#: Per-model explicit prompt-cache knobs, carried inside ``model_options``:
#: ``cache_mode: explicit`` turns on Qwen/DashScope-compatible
#: ``cache_control`` markers; the default ``implicit`` leaves the request
#: byte-identical to the pre-cache shape. ``cache_prefix_tokens`` names how
#: many tokens of the user message form the stable, marked prefix. The
#: markers are inserted in this generic request path (not in a benchmark
#: or prompt template), so ALFWorld, TB and every other workload get the
#: same treatment.
CACHE_MODE_KEY = "cache_mode"
CACHE_PREFIX_TOKENS_KEY = "cache_prefix_tokens"
CACHE_MODES = ("implicit", "explicit")
DEFAULT_CACHE_PREFIX_TOKENS = 1200
MIN_CACHE_PREFIX_TOKENS = 1024
_CHARS_PER_TOKEN = 4


def _explicit_cache_blocks(user: str, prefix_tokens: int) -> List[dict]:
    """Split one user message into a marked stable prefix plus remainder.

    Qwen's explicit cache marker lives on a content block
    (``cache_control: {"type": "ephemeral"}``), covers everything from the
    request start to that block, and needs at least 1024 tokens. The split
    is deliberately benchmark-agnostic: the first ``prefix_tokens`` tokens
    (at the repo's 4-chars/token estimate) are the stable head each prompt
    puts first, and the rest is the per-call payload. A prompt shorter
    than the budget is marked whole; the provider then decides whether it
    is long enough to cache.
    """
    limit = max(prefix_tokens, MIN_CACHE_PREFIX_TOKENS) * _CHARS_PER_TOKEN
    if len(user) <= limit:
        return [{"type": "text", "text": user,
                 "cache_control": {"type": "ephemeral"}}]
    return [
        {"type": "text", "text": user[:limit],
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": user[limit:]},
    ]


def _status_is_permanent(status: Any) -> bool:
    """Any other 4xx (bad key, unknown model, oversized context) cannot
    succeed on resend; 5xx and unknown statuses are treated as transient,
    as the SkillExpand classifier did."""
    return isinstance(status, int) and 400 <= status < 500 \
        and status not in TRANSIENT_HTTP_STATUSES



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
    #: The provider's separate reasoning channel, when one exists and the
    #: response was returned unmerged. ``text`` already carries it when the
    #: client was built with THINK_IN_CONTENT_ENV listing the model.
    reasoning: Optional[str] = None


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


def _refusal_detail(finish_reason: Any, refusal: Any) -> Optional[str]:
    """Return why the provider refused the request, or None if it did not.

    Same rule as the non-streaming wrapper: a refusal finish_reason wins, a
    populated refusal channel (here assembled from ``delta.refusal`` chunks)
    is checked after it.
    """
    if finish_reason in _REFUSAL_FINISH_REASONS:
        return f"finish_reason={finish_reason!r}"
    if refusal:
        return f"message.refusal={refusal!r}"
    return None


@dataclass(frozen=True)
class _StreamedReply:
    """One drained stream: the assembled message plus final-chunk metadata.

    ``content`` is None when the stream never opened the content channel
    (no ``delta.content`` chunk arrived at all) and the joined string
    otherwise -- the streaming counterpart of ``message.content is None``.
    """

    content: Optional[str]
    refusal: Optional[str]
    finish_reason: Optional[str]
    #: The raw SDK usage object (kept as-is so _usage_dict/_cached_tokens see
    #: the same object shape the non-streaming path saw), from the last chunk
    #: that carried one.
    usage: Any
    #: The streamed ``delta.reasoning`` channel (vLLM's qwen3 reasoning
    #: parser fills it and leaves the thinking out of ``content``); None
    #: when the endpoint never opened that channel.
    reasoning: Optional[str] = None


def _drain_stream(stream: Iterable[Any]) -> _StreamedReply:
    """Assemble one streamed chat completion into its final response parts.

    Reuses the old repo's assembly discipline (SkillExpand
    runtime/llm_relay.py ``coalesce_sse``, built for the same gateway
    family): accumulate ``delta.content`` pieces in order, accumulate
    ``delta.refusal`` the same way, let the last non-None ``finish_reason``
    win, and take usage from the last chunk that carries one -- with
    ``stream_options.include_usage`` that is the final, choice-less chunk
    this gateway sends. Tolerates test doubles: every field is read with
    getattr, so SimpleNamespace chunks and SDK chunks behave identically.
    """
    content_parts = []
    reasoning_parts = []
    refusal_parts = []
    finish_reason: Optional[str] = None
    usage_obj: Any = None
    for chunk in stream:
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            usage_obj = usage
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        choice = choices[0]
        reason = getattr(choice, "finish_reason", None)
        if reason is not None:
            finish_reason = reason
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue
        piece = getattr(delta, "content", None)
        if piece is not None:
            content_parts.append(piece)
        # vLLM's qwen3 parser streams the thinking out of band; the gateways
        # we otherwise use fold it into ``content`` and never open this
        # channel, so collecting it here is a no-op for them.
        thinking = (getattr(delta, "reasoning", None)
                    or getattr(delta, "reasoning_content", None))
        if thinking:
            reasoning_parts.append(thinking)
        refusal_piece = getattr(delta, "refusal", None)
        if refusal_piece:
            refusal_parts.append(refusal_piece)
    return _StreamedReply(
        content="".join(content_parts) if content_parts else None,
        refusal="".join(refusal_parts) if refusal_parts else None,
        finish_reason=finish_reason,
        usage=usage_obj,
        reasoning="".join(reasoning_parts) if reasoning_parts else None,
    )


def parse_aliases(raw: str) -> Dict[str, List[str]]:
    """``logical=a,b;other=c`` -> ``{"logical": ["a", "b"], "other": ["c"]}``.

    An empty string means no aliases. A malformed entry is a configuration
    error that names the variable, not the value (the ids are access
    details)."""
    table: Dict[str, List[str]] = {}
    for entry in (part.strip() for part in raw.split(";")):
        if not entry:
            continue
        logical, sep, tail = entry.partition("=")
        names = [name.strip() for name in tail.split(",") if name.strip()]
        if not sep or not logical.strip() or not names:
            raise ConfigError(
                f"{ALIASES_ENV} entries must look like "
                "'logical=alias1,alias2'; one entry is malformed")
        if logical.strip() in table:
            raise ConfigError(
                f"{ALIASES_ENV} names one logical model twice")
        table[logical.strip()] = list(dict.fromkeys(names))
    return table


def parse_model_overrides(raw: str, env_name: str) -> Dict[str, str]:
    """``model=value;other=value`` -> ``{"model": "value", ...}``.

    The value is a base URL or an API key, so the split is on the FIRST
    ``=`` and everything after it is kept verbatim. An empty string means
    no overrides. A malformed entry is a configuration error that names
    the variable, never the value (a URL and a key are both access
    details)."""
    table: Dict[str, str] = {}
    for entry in (part.strip() for part in raw.split(";")):
        if not entry:
            continue
        model, sep, value = entry.partition("=")
        model, value = model.strip(), value.strip()
        if not sep or not model or not value:
            raise ConfigError(
                f"{env_name} entries must look like 'model=value'; one "
                "entry is malformed")
        if model in table:
            raise ConfigError(f"{env_name} names one model twice")
        table[model] = value
    return table


class AliasPool:
    """Which wire id a request for a logical model goes out under.

    Callers keep addressing the logical name (it is the model's identity in
    the manifest, the usage ledger and the stored trajectories); only the
    ``model`` field on the wire changes. Requests rotate over a model's
    aliases; an alias the gateway just rate-limited is skipped until its
    cooldown ends, and when every alias is cooling the one that recovers
    first is used. A logical name without aliases goes out unchanged.
    """

    def __init__(self, table: Optional[Dict[str, List[str]]] = None,
                 cooldown_seconds: float = ALIAS_COOLDOWN_SECONDS,
                 clock: Any = time.monotonic):
        self._table = {name: list(ids) for name, ids in (table or {}).items()}
        self._cooldown = float(cooldown_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._next: Dict[str, int] = {}
        self._rested_until: Dict[str, float] = {}

    def pick(self, logical: str) -> str:
        ids = self._table.get(logical)
        if not ids:
            return logical
        with self._lock:
            now = self._clock()
            start = self._next.get(logical, 0)
            self._next[logical] = (start + 1) % len(ids)
            ordered = ids[start:] + ids[:start]
            for alias in ordered:
                if self._rested_until.get(alias, 0.0) <= now:
                    return alias
            return min(ordered, key=lambda alias: self._rested_until[alias])

    def rate_limited(self, alias: str) -> None:
        with self._lock:
            self._rested_until[alias] = self._clock() + self._cooldown


class OpenAICompatClient:
    """Thin synchronous chat client over any OpenAI-compatible endpoint.

    One instance serves every model role; the model id is passed per call so
    the same endpoint can serve both the strong and the weak model.
    """

    def __init__(self, endpoint: str, api_key: str,
                 timeout_sec: float = 300.0,
                 aliases: Optional[AliasPool] = None,
                 think_in_content: bool = False):
        self._aliases = aliases or AliasPool()
        self._think_in_content = think_in_content
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
    def from_env(cls, timeout_sec: float = 300,
                 model: Optional[str] = None) -> "OpenAICompatClient":
        """Build a client from EXPEDISTILL_LLM_ENDPOINT / EXPEDISTILL_LLM_API_KEY.

        ``timeout_sec`` is the per-request client timeout, a run setting
        threaded in from the pipeline (an unstable endpoint that hangs
        otherwise burns the full default per request); it is NOT frozen into
        the manifest.

        ``model`` is the logical model this client will serve. When it
        carries an entry in EXPEDISTILL_LLM_ENDPOINTS, that endpoint (and,
        if given, its EXPEDISTILL_LLM_API_KEYS key) replaces the default
        pair -- the pipeline builds one client per unique model, so a run
        can spread its roles over several servers.

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
        if model:
            overrides = parse_model_overrides(
                os.environ.get(ENDPOINTS_ENV, ""), ENDPOINTS_ENV)
            if model in overrides:
                endpoint = overrides[model]
                keys = parse_model_overrides(
                    os.environ.get(API_KEYS_ENV, ""), API_KEYS_ENV)
                api_key = keys.get(model, api_key)
        aliases = AliasPool(parse_aliases(os.environ.get(ALIASES_ENV, "")))
        think = {name.strip() for name in
                 os.environ.get(THINK_IN_CONTENT_ENV, "").split(",")
                 if name.strip()}
        return cls(endpoint=endpoint, api_key=api_key, timeout_sec=timeout_sec,
                   aliases=aliases,
                   think_in_content=bool(model) and model in think)

    def complete(
        self,
        model: str,
        system: str,
        user: str,
        *,
        temperature: float = 1.0,
        max_tokens: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> CompletionResult:
        """One chat call, streamed on the wire and fully assembled on return.

        Streaming is the default transport because the campaign gateway
        (the LLM gateway) kills NON-streaming chat requests at
        ~300.3s server-side (measured 2026-10-12: APIConnectionError at
        300.2-300.4s on every attempt, runs/tb21_full_20261011_2242/
        llm_requests dumps 2946-2953), while a reasoning-effort-max call
        that thinks for the full 32,768-token budget needs ~414s and can
        therefore NEVER complete non-streaming; the same request with
        stream=True finished in 414s. The old repo was forced onto the same
        transport by the same gateway family (SkillExpand
        runtime/models/llm.py GPTWrapper ``streaming=True``; the manual
        assembly discipline lives in runtime/llm_relay.py ``coalesce_sse``).

        To callers the contract is unchanged: this method blocks until the
        stream ends, then returns one CompletionResult with the same fields
        and raises the same taxonomy on transport failure, refusal, or empty
        output. ``stream``/``stream_options`` are set after the ``extra``
        merge on purpose: the transport choice is not a per-call option
        anymore, and a stray ``stream: false`` in model options must not
        reintroduce the 300s cutoff.

        The constructor timeout bounds stream reads, not total think time:
        a stream that stops sending bytes dies at the timeout (httpx read
        timeout), while the 414s call above survives a 300s timeout because
        its chunks keep arriving.

        Prompt caching is per model through ``extra``: ``cache_mode:
        explicit`` turns the user message into a content-block list whose
        first block carries ``cache_control`` (Qwen/DashScope-compatible);
        the default ``implicit`` leaves it a plain string. The two knobs
        are popped from ``extra`` and never reach the wire.
        """
        # Cache knobs are request metadata, not payload fields: pop them
        # from a copy so the handle's model_options stay reusable across
        # calls and nothing unknown reaches the wire.
        extra = dict(extra) if extra else {}
        cache_mode = extra.pop(CACHE_MODE_KEY, "implicit")
        prefix_tokens = extra.pop(CACHE_PREFIX_TOKENS_KEY,
                                  DEFAULT_CACHE_PREFIX_TOKENS)
        if cache_mode not in CACHE_MODES:
            raise ConfigError(
                f"{CACHE_MODE_KEY} must be one of {list(CACHE_MODES)}, "
                f"got {cache_mode!r}")
        if (isinstance(prefix_tokens, bool)
                or not isinstance(prefix_tokens, int)
                or prefix_tokens < 1):
            raise ConfigError(
                f"{CACHE_PREFIX_TOKENS_KEY} must be a positive int, "
                f"got {prefix_tokens!r}")
        messages: list = []
        if system:
            messages.append({"role": "system", "content": system})
        user_content: Any = user
        if cache_mode == "explicit" and user:
            user_content = _explicit_cache_blocks(user, prefix_tokens)
        messages.append({"role": "user", "content": user_content})
        wire_model = self._aliases.pick(model)
        payload: Dict[str, Any] = {
            "model": wire_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if extra:
            payload.update(extra)
        # Forced last so `extra` cannot turn the transport back into the
        # mode the gateway kills at ~300s.
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        try:
            # The try covers the stream open AND the iteration: a stream that
            # dies partway is the same endpoint failure as one that never
            # opens, and must reach the same mapping below.
            stream = self._client.chat.completions.create(**payload)
            reply = _drain_stream(stream)
        except Exception as exc:  # openai exceptions below; keep the cause chained
            import json as _json
            from openai import (
                APIConnectionError,
                APIError,
                APIResponseValidationError,
                APIStatusError,
            )

            # Protocol-level failures from inside the SDK call: the endpoint
            # answered with something the SDK could not turn into a response
            # (an empty/non-JSON body on a 200 raised a bare JSONDecodeError
            # during the 2026-10-12 campaign and killed the process). The
            # client boundary owns the whole translation -- anything the SDK
            # raises about the exchange itself is an infrastructure failure
            # for the retry budget to absorb, never a crash.
            if isinstance(exc, _json.JSONDecodeError):
                raise InfrastructureError(
                    f"LLM endpoint returned a non-JSON body for model "
                    f"{model}: {exc}"
                ) from exc
            if isinstance(exc, APIResponseValidationError):
                raise InfrastructureError(
                    f"LLM endpoint returned a schema-invalid response for "
                    f"model {model}: {exc}"
                ) from exc
            if isinstance(exc, APIStatusError):
                # Status and error type only: the SDK message carries the
                # provider's error body, which may echo the request.
                status = getattr(exc, "status_code", None)
                if status == 429:
                    self._aliases.rate_limited(wire_model)
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
            # A streaming response can fail with the base APIError (a 503
            # "Too many requests" from the gateway does this): the SDK raises
            # it from the stream iterator without an HTTP status object, so
            # it misses APIStatusError above. It is the endpoint's failure,
            # not a code bug -- send it through the retry budget.
            if isinstance(exc, APIError):
                status = getattr(exc, "status_code", None)
                raise InfrastructureError(
                    f"LLM endpoint stream error for model {model}: "
                    f"status={status} type={type(exc).__name__}"
                ) from exc
            raise

        refusal = _refusal_detail(reply.finish_reason, reply.refusal)
        if refusal is not None:
            raise ProviderRefusalError(
                f"model {model} refused the request ({refusal})"
            )
        content = reply.content
        finish_reason = reply.finish_reason
        # Usage is read before the empty-content check so a response that
        # consumed tokens (empty content with finish_reason="length" burned
        # the whole budget on reasoning) carries its real spend into the
        # usage ledger instead of null.
        usage_obj = reply.usage
        usage = _usage_dict(usage_obj)
        if content is None or not str(content).strip():
            # Empty with finish_reason="length" is the reasoning-effort=max
            # failure mode: the model burned its whole output budget on
            # reasoning and produced no answer; recording it as ok would
            # waste the step's budget silently. The finish_reason is in the
            # message so operators can tell "budget burned on reasoning"
            # from "model chose silence" (finish_reason="stop"), and travels
            # as an attribute so call_model records it in the usage row and
            # request dump instead of None.
            why = "no content" if content is None \
                else "empty or whitespace-only content"
            error = ModelOutputError(
                f"model {model} returned {why} "
                f"(finish_reason={finish_reason!r})",
                usage=usage,
                finish_reason=finish_reason,
            )
            raise error
        # A server that splits the thinking out (vLLM's reasoning parser)
        # hands back an answer stripped of it. Reunite the two exactly as
        # the model produced them, so the caller sees the same text the
        # gateway form gives. The guard keeps an endpoint that already
        # carries the thinking in ``content`` from getting it twice.
        reasoning = reply.reasoning
        if (self._think_in_content and reasoning
                and "<think>" not in content.lower()):
            content = f"<think>{reasoning.strip()}</think>{content}"
        return CompletionResult(
            text=content,
            in_tokens=(usage or {}).get("prompt_tokens"),
            out_tokens=(usage or {}).get("completion_tokens"),
            finish_reason=finish_reason,
            cached_tokens=_cached_tokens(usage_obj),
            raw_usage=usage,
            reasoning=reasoning,
        )
