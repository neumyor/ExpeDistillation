"""Tests for the B1 OpenAI-compatible client. No network: the SDK call is
replaced by an in-process stub, and only env parsing and error mapping run."""

import threading
from types import SimpleNamespace

import httpx2 as httpx
import openai
import pytest

from expedistill.infra.reliability import (
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    ProviderRefusalError,
)
from expedistill.llm.client import (
    API_KEYS_ENV,
    API_KEY_ENV,
    ENDPOINTS_ENV,
    ENDPOINT_ENV,
    THINK_IN_CONTENT_ENV,
    AliasPool,
    OpenAICompatClient,
    parse_aliases,
    parse_model_overrides,
)


def _chunk(delta_content=None, delta_refusal=None, finish_reason=None,
           delta_reasoning=None):
    """One streamed chunk shaped like SDK ChatCompletionChunk."""
    delta = SimpleNamespace(content=delta_content, refusal=delta_refusal,
                            reasoning=delta_reasoning)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None)


def _usage_chunk(usage):
    """The final, choice-less chunk gateways send for include_usage."""
    return SimpleNamespace(choices=[], usage=usage)


def _content_pieces(content):
    """Split scripted content across delta chunks so tests exercise assembly.

    Short strings stay one piece; an explicitly empty string stays one
    (empty) delta so the client's none-vs-empty distinction stays observable
    (no content delta at all vs. a delta that carries "").
    """
    if len(content) <= 4:
        return [content]
    return [content[i:i + 4] for i in range(0, len(content), 4)]


def chat_response(content, *, finish_reason="stop", refusal=None, usage=None,
                  reasoning=None):
    """Script the chunk stream an endpoint emits for this final response.

    Same signature the non-streaming stub had, so existing tests only change
    through this helper: refusal and content travel as delta chunks (content
    split across several chunks), finish_reason rides the last choice chunk,
    and usage arrives on a final choice-less chunk, exactly the wire shape
    this gateway produces with ``stream_options.include_usage``.
    """
    chunks = []
    if refusal is not None:
        chunks.append(_chunk(delta_refusal=refusal))
    if reasoning is not None:
        for piece in _content_pieces(reasoning):
            chunks.append(_chunk(delta_reasoning=piece))
    if content is not None:
        for piece in _content_pieces(content):
            chunks.append(_chunk(delta_content=piece))
    chunks.append(_chunk(finish_reason=finish_reason))
    if usage is not None:
        chunks.append(_usage_chunk(usage))
    return chunks


def sdk_usage(prompt_tokens=10, completion_tokens=4, total_tokens=14,
              cached_tokens=None):
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
    if cached_tokens is not None:
        usage.prompt_tokens_details = SimpleNamespace(
            cached_tokens=cached_tokens)
    return usage


class StubSDKClient:
    """Stands in for openai.OpenAI: records init kwargs and create() calls.

    ``create()`` behaves like the SDK's streaming mode: it opens immediately
    (raising ``error`` there if scripted -- the stream-open failure path) and
    returns an iterator over the scripted chunks in ``response``; a generator
    that raises partway scripts a mid-stream failure.
    """

    last = None

    def __init__(self, *, base_url, api_key, timeout, max_retries):
        self.init_kwargs = {
            "base_url": base_url,
            "api_key": api_key,
            "timeout": timeout,
            "max_retries": max_retries,
        }
        self.create_kwargs = None
        self.response = None
        self.error = None
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )
        StubSDKClient.last = self

    def _create(self, **kwargs):
        self.create_kwargs = kwargs
        if self.error is not None:
            raise self.error
        return iter(self.response)


@pytest.fixture(autouse=True)
def endpoint_env(monkeypatch):
    """The default endpoint/key pair every client test starts from."""
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")


@pytest.fixture
def stub_sdk(monkeypatch):
    """Patch openai.OpenAI so constructing a client builds a StubSDKClient.

    ``install(response=..., error=...)`` scripts what the fake chat call
    returns or raises; the payload is captured per call.
    """

    def install(response=None, error=None):
        StubSDKClient.last = None

        def factory(**kwargs):
            client = StubSDKClient(**kwargs)
            client.response = response
            client.error = error
            return client

        monkeypatch.setattr(openai, "OpenAI", factory)
        return factory

    return install


@pytest.mark.parametrize("endpoint,key,match", [
    (None, None, r"\.env\.example"),
    ("http://endpoint/v1", "   ", API_KEY_ENV),
], ids=["missing", "blank-key"])
def test_from_env_missing_or_blank_variables_raise_config_error(
        monkeypatch, endpoint, key, match):
    for name, value in ((ENDPOINT_ENV, endpoint), (API_KEY_ENV, key)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=match):
        OpenAICompatClient.from_env()


def test_from_env_passes_timeout_sec_to_sdk(stub_sdk):
    # The client timeout is a run setting threaded from the CLI/CLI tests
    # through the pipeline into from_env; the default stays 300.
    stub_sdk()
    OpenAICompatClient.from_env(timeout_sec=120.5)
    assert StubSDKClient.last.init_kwargs["timeout"] == 120.5


def test_from_env_builds_client_with_env_values(monkeypatch, stub_sdk):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "secret-key")
    stub_sdk()
    client = OpenAICompatClient.from_env()
    assert StubSDKClient.last.init_kwargs["base_url"] == "http://endpoint/v1"
    assert StubSDKClient.last.init_kwargs["api_key"] == "secret-key"
    assert StubSDKClient.last.init_kwargs["timeout"] == 300
    # Zero client-side retries: the retry budget stays with the callers.
    assert StubSDKClient.last.init_kwargs["max_retries"] == 0
    # The wrapper itself must never hold the key.
    assert not hasattr(client, "api_key")


def test_parse_model_overrides_reads_pairs():
    assert parse_model_overrides("", ENDPOINTS_ENV) == {}
    assert parse_model_overrides(
        "qwen=http://host:8010/v1; other = http://b/v1 ",
        ENDPOINTS_ENV) == {"qwen": "http://host:8010/v1",
                           "other": "http://b/v1"}
    # The value is a URL: only the FIRST '=' splits it.
    assert parse_model_overrides(
        "qwen=http://h/v1?x=1", ENDPOINTS_ENV) == {
            "qwen": "http://h/v1?x=1"}


@pytest.mark.parametrize("raw", ["qwen", "=http://h/v1", "qwen=",
                                 "a=1;a=2"])
def test_parse_model_overrides_rejects_malformed(raw):
    with pytest.raises(ConfigError, match=ENDPOINTS_ENV):
        parse_model_overrides(raw, ENDPOINTS_ENV)


def test_from_env_model_override_replaces_endpoint_and_key(
        monkeypatch, stub_sdk):
    # A run whose roles live on two servers: the overridden model gets its
    # own base URL and key, every other model keeps the default pair.
    monkeypatch.setenv(ENDPOINT_ENV, "http://gateway/v1")
    monkeypatch.setenv(API_KEY_ENV, "gateway-key")
    monkeypatch.setenv(ENDPOINTS_ENV, "weak=http://selfhosted/v1")
    monkeypatch.setenv(API_KEYS_ENV, "weak=selfhosted-key")
    stub_sdk()
    OpenAICompatClient.from_env(model="weak")
    assert StubSDKClient.last.init_kwargs["base_url"] == "http://selfhosted/v1"
    assert StubSDKClient.last.init_kwargs["api_key"] == "selfhosted-key"
    OpenAICompatClient.from_env(model="strong")
    assert StubSDKClient.last.init_kwargs["base_url"] == "http://gateway/v1"
    assert StubSDKClient.last.init_kwargs["api_key"] == "gateway-key"
    # No key override for that model: the default key is sent.
    monkeypatch.delenv(API_KEYS_ENV)
    OpenAICompatClient.from_env(model="weak")
    assert StubSDKClient.last.init_kwargs["api_key"] == "gateway-key"


def test_reasoning_channel_is_reported_but_not_merged_by_default(
        monkeypatch, stub_sdk):
    # A server that splits the thinking out leaves it out of the text; the
    # caller still sees it on the result, and text is exactly what arrived.
    monkeypatch.delenv(THINK_IN_CONTENT_ENV, raising=False)
    stub_sdk(response=chat_response("<action>go to desk 1</action>",
                                    reasoning="I should check the desk."))
    result = OpenAICompatClient.from_env(model="weak").complete("weak", "s", "u")
    assert result.text == "<action>go to desk 1</action>"
    assert result.reasoning == "I should check the desk."


def test_think_in_content_puts_the_reasoning_back(monkeypatch, stub_sdk):
    # vLLM's qwen3 reasoning parser streams the thinking out of band; the
    # ALFWorld protocol requires <think>...</think> in the reply, so a
    # listed model gets its own thinking reunited with its answer.
    monkeypatch.setenv(THINK_IN_CONTENT_ENV, "weak, other")
    stub_sdk(response=chat_response("\n<action>go to desk 1</action>",
                                    reasoning="  I should check the desk.  "))
    result = OpenAICompatClient.from_env(model="weak").complete("weak", "s", "u")
    assert result.text == ("<think>I should check the desk.</think>"
                           "\n<action>go to desk 1</action>")


def test_think_in_content_leaves_an_unlisted_model_alone(monkeypatch, stub_sdk):
    monkeypatch.setenv(THINK_IN_CONTENT_ENV, "other")
    stub_sdk(response=chat_response("<action>x</action>", reasoning="r"))
    result = OpenAICompatClient.from_env(model="weak").complete("weak", "s", "u")
    assert result.text == "<action>x</action>"


def test_think_in_content_does_not_duplicate_an_existing_block(
        monkeypatch, stub_sdk):
    # The gate can stay on when a gateway folds the thinking into content
    # itself: the guard is what keeps that from doubling the block.
    monkeypatch.setenv(THINK_IN_CONTENT_ENV, "weak")
    stub_sdk(response=chat_response(
        "<think>already here</think><action>x</action>", reasoning="already here"))
    result = OpenAICompatClient.from_env(model="weak").complete("weak", "s", "u")
    assert result.text == "<think>already here</think><action>x</action>"


def test_complete_uses_streaming_transport(stub_sdk):
    # Regression for the 2026-10-12 incident: the campaign gateway
    # (llm.example.com) kills NON-streaming chat requests at ~300.3s
    # server-side (APIConnectionError at 300.2-300.4s, runs/
    # tb21_full_20261011_2242/llm_requests dumps 2946-2953), while a
    # reasoning-effort-max call that thinks for the full 32,768-token budget
    # needs ~414s and can therefore NEVER complete non-streaming -- the same
    # request with stream=True finished in 414s. A stub cannot wait 300s, so
    # this test pins the DECISION: every call must open a stream and ask for
    # usage in the stream options (the assembly itself is covered by the
    # other tests below).
    stub_sdk(response=chat_response("ok"))
    client = OpenAICompatClient.from_env()
    client.complete("model-a", "s", "u")
    kwargs = StubSDKClient.last.create_kwargs
    assert kwargs["stream"] is True
    assert kwargs["stream_options"] == {"include_usage": True}


def test_complete_assembles_unicode_split_across_chunks(stub_sdk):
    # The wire splits content at arbitrary token boundaries: multi-byte
    # characters and even ZWJ graphemes can straddle chunk edges. The
    # assembled text must be the exact original string.
    pieces = ["héllo ", "世", "界 ", "👨‍", "👩"]
    chunks = [_chunk(delta_content=p) for p in pieces]
    chunks.append(_chunk(finish_reason="stop"))
    stub_sdk(response=chunks)
    client = OpenAICompatClient.from_env()
    assert client.complete("model-a", "s", "u").text == "héllo 世界 👨‍👩"


def test_complete_captures_usage_and_finish_from_final_chunks(stub_sdk):
    # With stream_options include_usage the gateway sends usage on a final,
    # choice-less chunk and finish_reason on the last choice chunk (verified
    # against the 2026-10-12 capture: the 414s reasoning call reported
    # usage=(13387, 32768) with finish_reason="length"). Both must land in
    # the assembled result.
    chunks = [
        _chunk(delta_content="answer"),
        _chunk(finish_reason="length"),
        _usage_chunk(sdk_usage(13387, 32768, 46155)),
    ]
    stub_sdk(response=chunks)
    client = OpenAICompatClient.from_env()
    result = client.complete("model-a", "s", "u")
    assert result.finish_reason == "length"
    assert result.in_tokens == 13387
    assert result.out_tokens == 32768
    assert result.raw_usage["total_tokens"] == 46155


def test_complete_refusal_accumulated_across_chunks(stub_sdk):
    # delta.refusal streams exactly like content; the assembled refusal
    # string must end up in the ProviderRefusalError.
    chunks = [
        _chunk(delta_refusal="I cannot "),
        _chunk(delta_refusal="help with that."),
        _chunk(finish_reason="stop"),
    ]
    stub_sdk(response=chunks)
    client = OpenAICompatClient.from_env()
    with pytest.raises(ProviderRefusalError, match="I cannot help with that"):
        client.complete("model-a", "s", "u")


def test_complete_mid_stream_failure_maps_to_infrastructure(stub_sdk):
    # A stream that dies partway is the same endpoint failure as one that
    # never opens: the iteration runs inside the create() protection, so a
    # mid-stream APIConnectionError maps to InfrastructureError instead of
    # surfacing partial content or crashing the pipeline.
    request = httpx.Request("POST", "http://endpoint/v1/chat/completions")

    def failing_stream():
        yield _chunk(delta_content="partial answ")
        raise openai.APIConnectionError(message="died mid-stream",
                                        request=request)

    stub_sdk(response=failing_stream())
    client = OpenAICompatClient.from_env()
    with pytest.raises(InfrastructureError, match="APIConnectionError"):
        client.complete("model-a", "s", "u")


def test_complete_mid_stream_base_api_error_is_infrastructure(stub_sdk):
    # The gateway's 503 "Too many requests" arrives from the stream iterator
    # as the SDK's base APIError (no HTTP status object on it), not as
    # APIStatusError. It is still the endpoint's failure and must reach the
    # retry budget instead of stopping the batch as a code bug.
    request = httpx.Request("POST", "http://endpoint/v1/chat/completions")

    def failing_stream():
        yield _chunk(delta_content="partial")
        raise openai.APIError("upstream 503: too many requests", request,
                              body=None)

    stub_sdk(response=failing_stream())
    client = OpenAICompatClient.from_env()
    with pytest.raises(InfrastructureError, match="stream error"):
        client.complete("model-a", "s", "u")


def test_complete_sends_expected_payload(stub_sdk):
    stub_sdk(response=chat_response("ok"))
    client = OpenAICompatClient.from_env()
    client.complete(
        "model-a",
        "system text",
        "user text",
        temperature=0.7,
        max_tokens=128,
        extra={"top_p": 0.9},
    )
    kwargs = StubSDKClient.last.create_kwargs
    assert kwargs["model"] == "model-a"
    assert kwargs["messages"] == [
        {"role": "system", "content": "system text"},
        {"role": "user", "content": "user text"},
    ]
    assert kwargs["temperature"] == 0.7
    assert kwargs["max_tokens"] == 128
    assert kwargs["top_p"] == 0.9


def test_explicit_cache_marks_the_stable_prefix(stub_sdk):
    # 2026-11-04: the generic client splits the user message at the
    # configured token budget and marks the first block. 1200 tokens at the
    # repo's 4-chars/token estimate = 4800 characters.
    stub_sdk(response=chat_response("ok"))
    client = OpenAICompatClient.from_env()
    user = "A" * 10_000
    client.complete("qwen", "sys", user,
                    extra={"cache_mode": "explicit",
                           "cache_prefix_tokens": 1200})
    kwargs = StubSDKClient.last.create_kwargs
    content = kwargs["messages"][-1]["content"]
    assert isinstance(content, list) and len(content) == 2
    assert content[0]["text"] == user[:4800]
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert content[1] == {"type": "text", "text": user[4800:]}
    assert content[0]["text"] + content[1]["text"] == user
    assert "cache_mode" not in kwargs
    assert "cache_prefix_tokens" not in kwargs


def test_explicit_cache_marks_a_short_prompt_whole(stub_sdk):
    stub_sdk(response=chat_response("ok"))
    client = OpenAICompatClient.from_env()
    client.complete("qwen", "sys", "short prompt",
                    extra={"cache_mode": "explicit"})
    content = StubSDKClient.last.create_kwargs["messages"][-1]["content"]
    assert content == [{"type": "text", "text": "short prompt",
                        "cache_control": {"type": "ephemeral"}}]


def test_implicit_cache_keeps_string_content(stub_sdk):
    stub_sdk(response=chat_response("ok"))
    client = OpenAICompatClient.from_env()
    client.complete("qwen", "sys", "user text",
                    extra={"cache_mode": "implicit"})
    assert StubSDKClient.last.create_kwargs["messages"][-1]["content"] == \
        "user text"


def test_cache_options_do_not_mutate_the_handle_options(stub_sdk):
    stub_sdk(response=chat_response("ok"))
    client = OpenAICompatClient.from_env()
    extra = {"cache_mode": "explicit", "cache_prefix_tokens": 1200}
    client.complete("qwen", "sys", "user text", extra=extra)
    assert extra == {"cache_mode": "explicit", "cache_prefix_tokens": 1200}


def test_unknown_cache_mode_is_a_config_error(stub_sdk):
    stub_sdk(response=chat_response("ok"))
    client = OpenAICompatClient.from_env()
    with pytest.raises(ConfigError, match="cache_mode"):
        client.complete("qwen", "s", "u", extra={"cache_mode": "weird"})


@pytest.mark.parametrize("bad", [0, -1, True, "1200"])
def test_invalid_cache_prefix_tokens_is_a_config_error(stub_sdk, bad):
    stub_sdk(response=chat_response("ok"))
    client = OpenAICompatClient.from_env()
    with pytest.raises(ConfigError, match="cache_prefix_tokens"):
        client.complete("qwen", "s", "u",
                        extra={"cache_mode": "explicit",
                               "cache_prefix_tokens": bad})


def test_complete_omits_empty_system_message(stub_sdk):
    stub_sdk(response=chat_response("ok"))
    client = OpenAICompatClient.from_env()
    client.complete("model-a", "", "user text")
    assert StubSDKClient.last.create_kwargs["messages"] == [
        {"role": "user", "content": "user text"}
    ]


@pytest.mark.parametrize("content", ["I can't", ""], ids=["text", "empty"])
def test_complete_content_filter_maps_to_refusal(stub_sdk, content):
    # A refusal finish_reason is a ProviderRefusal even when the content is
    # empty: the refusal check stays in front of the empty-content check.
    stub_sdk(response=chat_response(content, finish_reason="content_filter"))
    client = OpenAICompatClient.from_env()
    with pytest.raises(ProviderRefusalError, match="content_filter"):
        client.complete("model-a", "s", "u")


def test_complete_refusal_field_maps_to_refusal(stub_sdk):
    stub_sdk(
        response=chat_response(
            None, refusal="I cannot help with that."
        )
    )
    client = OpenAICompatClient.from_env()
    with pytest.raises(ProviderRefusalError, match="refusal"):
        client.complete("model-a", "s", "u")


@pytest.mark.parametrize("content,why", [
    ("", "empty or whitespace-only content"),
    ("   ", "empty or whitespace-only content"),
    ("\n\t ", "empty or whitespace-only content"),
    (None, "no content"),
])
def test_complete_empty_content_maps_to_model_output_error(
        stub_sdk, content, why):
    # Smoke finding: with reasoning_effort=max the strong model burns its
    # whole output budget on reasoning and returns "" with
    # finish_reason=length; that must not be recorded as status=ok.
    stub_sdk(response=chat_response(content, finish_reason="length"))
    client = OpenAICompatClient.from_env()
    with pytest.raises(ModelOutputError) as info:
        client.complete("model-a", "s", "u")
    # The message must name the model and the finish_reason so operators can
    # tell "budget burned on reasoning" from "model chose silence".
    assert why in str(info.value)
    assert "model-a" in str(info.value)
    assert "length" in str(info.value)


def test_complete_empty_content_error_carries_usage(stub_sdk):
    # Real-data finding: with finish_reason=length the model burned ~40k
    # input + 5120 output tokens on reasoning and returned nothing; the
    # empty-content error must carry that usage so the ledger does not
    # record a real spend as null.
    stub_sdk(response=chat_response(
        "", finish_reason="length", usage=sdk_usage(40000, 5120, 45120)))
    client = OpenAICompatClient.from_env()
    with pytest.raises(ModelOutputError) as info:
        client.complete("model-a", "s", "u")
    assert info.value.usage == {
        "prompt_tokens": 40000,
        "completion_tokens": 5120,
        "total_tokens": 45120,
    }
    assert info.value.finish_reason == "length"


def test_complete_empty_content_without_usage_carries_none(stub_sdk):
    stub_sdk(response=chat_response(None, usage=None))
    client = OpenAICompatClient.from_env()
    with pytest.raises(ModelOutputError) as info:
        client.complete("model-a", "s", "u")
    assert info.value.usage is None
    # finish_reason is still carried through from the response ("stop":
    # the model chose silence rather than burning its budget).
    assert info.value.finish_reason == "stop"


def test_complete_extracts_cached_tokens_from_usage_details(stub_sdk):
    # Real observation: the gateway cache template reported a 20-token prompt
    # as 3927 prompt tokens, 3712 of them cached; without recording
    # cached_tokens the strong model's prompt costs look ~300x too high.
    stub_sdk(response=chat_response(
        "hi", usage=sdk_usage(3927, 4, 3931, cached_tokens=3712)))
    client = OpenAICompatClient.from_env()
    result = client.complete("model-a", "s", "u")
    assert result.cached_tokens == 3712
    assert result.in_tokens == 3927


def _status_error(status, message="boom"):
    request = httpx.Request("POST", "http://endpoint/v1/chat/completions")
    return openai.APIStatusError(
        message, response=httpx.Response(status, request=request), body=None
    )


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 504])
def test_complete_transient_http_status_maps_to_infrastructure_error(
    stub_sdk, status
):
    stub_sdk(error=_status_error(status))
    client = OpenAICompatClient.from_env()
    with pytest.raises(InfrastructureError, match=str(status)):
        client.complete("model-a", "s", "u")


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_complete_permanent_http_status_maps_to_refusal(
    stub_sdk, status
):
    # A wrong key / unknown model / oversized context cannot succeed on
    # resend: it must stop the phase, not burn call- and unit-level retries.
    stub_sdk(error=_status_error(status))
    client = OpenAICompatClient.from_env()
    with pytest.raises(ProviderRefusalError, match=f"status={status}") as info:
        client.complete("model-a", "s", "u")
    assert not isinstance(info.value, InfrastructureError)
    assert "APIStatusError" in str(info.value)
    assert isinstance(info.value.__cause__, openai.APIStatusError)


def test_http_error_message_never_echoes_key_or_request_body(
    monkeypatch, stub_sdk
):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "sk-secret-value")
    # SDK messages carry the provider's error body, which may echo the
    # request (and, behind some gateways, the key).
    leaky = "invalid key sk-secret-value for prompt SECRET-USER-TEXT"
    for status in (401, 503):
        stub_sdk(error=_status_error(status, message=leaky))
        client = OpenAICompatClient.from_env()
        with pytest.raises((ProviderRefusalError, InfrastructureError)) as info:
            client.complete("model-a", "s", "SECRET-USER-TEXT")
        assert "sk-secret-value" not in str(info.value)
        assert "SECRET-USER-TEXT" not in str(info.value)


def test_complete_timeout_and_connection_map_to_infrastructure_error(
    stub_sdk
):
    request = httpx.Request("POST", "http://endpoint/v1/chat/completions")

    stub_sdk(error=openai.APITimeoutError(request=request))
    client = OpenAICompatClient.from_env()
    with pytest.raises(InfrastructureError, match="APITimeoutError"):
        client.complete("model-a", "s", "u")

    stub_sdk(error=openai.APIConnectionError(message="down", request=request))
    client = OpenAICompatClient.from_env()
    with pytest.raises(InfrastructureError, match="APIConnectionError"):
        client.complete("model-a", "s", "u")


def test_complete_unexpected_error_propagates_unwrapped(stub_sdk):
    stub_sdk(error=ValueError("not an SDK error"))
    client = OpenAICompatClient.from_env()
    with pytest.raises(ValueError):
        client.complete("model-a", "s", "u")


def test_complete_no_usage_reports_none_tokens(stub_sdk):
    stub_sdk(response=chat_response("text", usage=None))
    client = OpenAICompatClient.from_env()
    result = client.complete("model-a", "s", "u")
    assert result.in_tokens is None
    assert result.out_tokens is None
    assert result.raw_usage is None


def test_malformed_endpoint_body_is_infrastructure(stub_sdk):
    # 2026-10-12 campaign crash: the gateway returned an empty/invalid body on
    # a 200 and the openai SDK surfaced a bare JSONDecodeError from inside
    # create(), which slipped past the APIStatusError/APIConnectionError
    # mapping and killed the pipeline. The client boundary must translate
    # protocol-level failures into InfrastructureError so the retry budget
    # (not the process) absorbs them.
    import json as _json
    stub_sdk(error=_json.JSONDecodeError("Expecting value", "", 0))
    client = OpenAICompatClient.from_env()
    with pytest.raises(InfrastructureError, match="non-JSON body"):
        client.complete("model-a", "s", "u")


def test_schema_invalid_response_is_infrastructure(stub_sdk):
    # APIResponseValidationError (the SDK's own wrapper for responses that
    # fail its schema) is also an endpoint-protocol failure, not our bug.
    import openai as _openai
    import httpx2 as _httpx  # openai>=3 depends on httpx2
    _req = _httpx.Request("POST", "http://endpoint/v1/chat/completions")
    _resp = _httpx.Response(200, request=_req)
    stub_sdk(error=_openai.APIResponseValidationError(
        _resp, body=None, message="invalid response"))
    client = OpenAICompatClient.from_env()
    with pytest.raises(InfrastructureError, match="invalid response"):
        client.complete("model-a", "s", "u")


def test_aliases_change_only_the_wire_model_and_rotate(monkeypatch, stub_sdk):
    from expedistill.llm.client import ALIASES_ENV

    monkeypatch.setenv(ALIASES_ENV, "model-a=wire-1,wire-2")
    stub_sdk(response=chat_response("ok"))
    client = OpenAICompatClient.from_env()

    client.complete("model-a", "s", "u")
    first = StubSDKClient.last.create_kwargs["model"]
    stub_sdk(response=chat_response("ok"))
    client2 = OpenAICompatClient.from_env()
    client2.complete("model-a", "s", "u")
    client2.complete("model-a", "s", "u")
    wires = {first, StubSDKClient.last.create_kwargs["model"]}

    assert wires == {"wire-1", "wire-2"}


def test_a_429_rests_that_wire_id_and_the_error_names_the_logical_model(
        monkeypatch, stub_sdk):
    from expedistill.llm.client import ALIASES_ENV

    monkeypatch.setenv(ALIASES_ENV, "model-a=wire-1,wire-2")
    stub_sdk(error=_status_error(429))
    client = OpenAICompatClient.from_env()

    with pytest.raises(InfrastructureError) as info:
        client.complete("model-a", "s", "u")
    limited = StubSDKClient.last.create_kwargs["model"]
    other = {"wire-1", "wire-2"} - {limited}
    picks = {client._aliases.pick("model-a") for _ in range(4)}

    assert "model-a" in str(info.value) and "wire-" not in str(info.value)
    assert picks == other


# ---------------------------------------------------------------------------
# Wire-id rotation for a gateway that rate-limits per request id
# ---------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_parse_reads_several_models_and_drops_duplicates():
    table = parse_aliases("a=x,y,x; b=z")

    assert table == {"a": ["x", "y"], "b": ["z"]}


@pytest.mark.parametrize("raw", ["a", "a=", "=x", "a=x;a=y"])
def test_a_malformed_table_is_a_config_error_without_echoing_values(raw):
    with pytest.raises(ConfigError) as excinfo:
        parse_aliases(raw)

    assert "EXPEDISTILL_LLM_MODEL_ALIASES" in str(excinfo.value)
    assert "=x" not in str(excinfo.value)


def test_an_empty_table_is_no_aliases():
    assert parse_aliases("") == {} and parse_aliases(" ; ") == {}


def test_a_model_without_aliases_goes_out_unchanged():
    pool = AliasPool({"a": ["x"]})

    assert pool.pick("other") == "other"
    assert AliasPool().pick("a") == "a"


def test_requests_rotate_evenly_over_the_aliases():
    pool = AliasPool({"a": ["x", "y", "z"]})

    picks = [pool.pick("a") for _ in range(9)]

    assert picks == ["x", "y", "z"] * 3


def test_a_rate_limited_alias_rests_until_its_cooldown_ends():
    clock = Clock()
    pool = AliasPool({"a": ["x", "y", "z"]}, cooldown_seconds=20, clock=clock)
    pool.rate_limited("y")

    during = [pool.pick("a") for _ in range(6)]
    clock.now += 21
    after = [pool.pick("a") for _ in range(6)]

    assert "y" not in during and {"x", "z"} <= set(during)
    assert set(after) == {"x", "y", "z"}


def test_when_every_alias_rests_the_earliest_to_recover_is_used():
    clock = Clock()
    pool = AliasPool({"a": ["x", "y"]}, cooldown_seconds=20, clock=clock)
    pool.rate_limited("x")
    clock.now += 5
    pool.rate_limited("y")

    assert pool.pick("a") == "x"


def test_rotation_is_per_logical_model():
    pool = AliasPool({"a": ["a1", "a2"], "b": ["b1", "b2"]})

    assert [pool.pick("a"), pool.pick("b"), pool.pick("a"), pool.pick("b")] \
        == ["a1", "b1", "a2", "b2"]


def test_concurrent_picks_stay_balanced():
    pool = AliasPool({"a": ["x", "y", "z", "w"]})
    seen = []
    lock = threading.Lock()

    def work():
        for _ in range(100):
            alias = pool.pick("a")
            with lock:
                seen.append(alias)

    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert {alias: seen.count(alias) for alias in "xyzw"} == {
        "x": 200, "y": 200, "z": 200, "w": 200}
