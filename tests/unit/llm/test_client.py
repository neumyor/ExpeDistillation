"""Tests for the B1 OpenAI-compatible client. No network: the SDK call is
replaced by an in-process stub, and only env parsing and error mapping run."""

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
    API_KEY_ENV,
    ENDPOINT_ENV,
    OpenAICompatClient,
)


def _chunk(delta_content=None, delta_refusal=None, finish_reason=None):
    """One streamed chunk shaped like SDK ChatCompletionChunk."""
    delta = SimpleNamespace(content=delta_content, refusal=delta_refusal)
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


def chat_response(content, *, finish_reason="stop", refusal=None, usage=None):
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


def test_from_env_missing_variables_raise_config_error(monkeypatch):
    monkeypatch.delenv(ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(ConfigError, match=r"\.env\.example"):
        OpenAICompatClient.from_env()


def test_from_env_passes_timeout_sec_to_sdk(monkeypatch, stub_sdk):
    # The client timeout is a run setting threaded from the CLI/CLI tests
    # through the pipeline into from_env; the default stays 300.
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk()
    OpenAICompatClient.from_env(timeout_sec=120.5)
    assert StubSDKClient.last.init_kwargs["timeout"] == 120.5


def test_from_env_blank_value_counts_as_missing(monkeypatch):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "   ")
    with pytest.raises(ConfigError, match=API_KEY_ENV):
        OpenAICompatClient.from_env()


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


def test_complete_uses_streaming_transport(monkeypatch, stub_sdk):
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
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(response=chat_response("ok"))
    client = OpenAICompatClient.from_env()
    client.complete("model-a", "s", "u")
    kwargs = StubSDKClient.last.create_kwargs
    assert kwargs["stream"] is True
    assert kwargs["stream_options"] == {"include_usage": True}


def test_complete_assembles_unicode_split_across_chunks(monkeypatch, stub_sdk):
    # The wire splits content at arbitrary token boundaries: multi-byte
    # characters and even ZWJ graphemes can straddle chunk edges. The
    # assembled text must be the exact original string.
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    pieces = ["héllo ", "世", "界 ", "👨‍", "👩"]
    chunks = [_chunk(delta_content=p) for p in pieces]
    chunks.append(_chunk(finish_reason="stop"))
    stub_sdk(response=chunks)
    client = OpenAICompatClient.from_env()
    assert client.complete("model-a", "s", "u").text == "héllo 世界 👨‍👩"


def test_complete_captures_usage_and_finish_from_final_chunks(
        monkeypatch, stub_sdk):
    # With stream_options include_usage the gateway sends usage on a final,
    # choice-less chunk and finish_reason on the last choice chunk (verified
    # against the 2026-10-12 capture: the 414s reasoning call reported
    # usage=(13387, 32768) with finish_reason="length"). Both must land in
    # the assembled result.
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
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


def test_complete_refusal_accumulated_across_chunks(monkeypatch, stub_sdk):
    # delta.refusal streams exactly like content; the assembled refusal
    # string must end up in the ProviderRefusalError.
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    chunks = [
        _chunk(delta_refusal="I cannot "),
        _chunk(delta_refusal="help with that."),
        _chunk(finish_reason="stop"),
    ]
    stub_sdk(response=chunks)
    client = OpenAICompatClient.from_env()
    with pytest.raises(ProviderRefusalError, match="I cannot help with that"):
        client.complete("model-a", "s", "u")


def test_complete_mid_stream_failure_maps_to_infrastructure(
        monkeypatch, stub_sdk):
    # A stream that dies partway is the same endpoint failure as one that
    # never opens: the iteration runs inside the create() protection, so a
    # mid-stream APIConnectionError maps to InfrastructureError instead of
    # surfacing partial content or crashing the pipeline.
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    request = httpx.Request("POST", "http://endpoint/v1/chat/completions")

    def failing_stream():
        yield _chunk(delta_content="partial answ")
        raise openai.APIConnectionError(message="died mid-stream",
                                        request=request)

    stub_sdk(response=failing_stream())
    client = OpenAICompatClient.from_env()
    with pytest.raises(InfrastructureError, match="APIConnectionError"):
        client.complete("model-a", "s", "u")


def test_complete_roundtrip_with_usage(monkeypatch, stub_sdk):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(response=chat_response("hello", usage=sdk_usage(10, 4, 14)))
    client = OpenAICompatClient.from_env()
    result = client.complete("model-a", "be brief", "hi")
    assert result.text == "hello"
    assert result.in_tokens == 10
    assert result.out_tokens == 4
    assert result.raw_usage["total_tokens"] == 14


def test_complete_sends_expected_payload(monkeypatch, stub_sdk):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
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


def test_complete_merges_string_valued_extra(monkeypatch, stub_sdk):
    # model_options entries (e.g. reasoning_effort) are merged into the
    # top-level payload, exactly like the numeric case above.
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(response=chat_response("ok"))
    client = OpenAICompatClient.from_env()
    client.complete("deepseek", "s", "u", extra={"reasoning_effort": "max"})
    kwargs = StubSDKClient.last.create_kwargs
    assert kwargs["reasoning_effort"] == "max"


def test_complete_extra_body_max_tokens_overrides_explicit_value(
        monkeypatch, stub_sdk):
    # The per-model override path (model_options -> extra_body): the extra
    # merge runs after the handle-level max_tokens, so extra wins.
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(response=chat_response("ok"))
    client = OpenAICompatClient.from_env()
    client.complete("model-a", "s", "u", max_tokens=5120,
                    extra={"max_tokens": 128})
    assert StubSDKClient.last.create_kwargs["max_tokens"] == 128


def test_complete_omits_empty_system_message(monkeypatch, stub_sdk):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(response=chat_response("ok"))
    client = OpenAICompatClient.from_env()
    client.complete("model-a", "", "user text")
    assert StubSDKClient.last.create_kwargs["messages"] == [
        {"role": "user", "content": "user text"}
    ]


def test_complete_content_filter_maps_to_refusal(monkeypatch, stub_sdk):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(response=chat_response("I can't", finish_reason="content_filter"))
    client = OpenAICompatClient.from_env()
    with pytest.raises(ProviderRefusalError, match="content_filter"):
        client.complete("model-a", "s", "u")


def test_complete_refusal_field_maps_to_refusal(monkeypatch, stub_sdk):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(
        response=chat_response(
            None, refusal="I cannot help with that."
        )
    )
    client = OpenAICompatClient.from_env()
    with pytest.raises(ProviderRefusalError, match="refusal"):
        client.complete("model-a", "s", "u")


def test_complete_none_content_maps_to_model_output_error(monkeypatch, stub_sdk):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(response=chat_response(None, finish_reason="length"))
    client = OpenAICompatClient.from_env()
    with pytest.raises(ModelOutputError, match="no content"):
        client.complete("model-a", "s", "u")


@pytest.mark.parametrize("empty", ["", "   ", "\n\t "])
def test_complete_empty_content_maps_to_model_output_error(
        monkeypatch, stub_sdk, empty):
    # Smoke finding: with reasoning_effort=max the strong model burns its
    # whole output budget on reasoning and returns "" with
    # finish_reason=length; that must not be recorded as status=ok.
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(response=chat_response(empty, finish_reason="length"))
    client = OpenAICompatClient.from_env()
    with pytest.raises(ModelOutputError) as info:
        client.complete("model-a", "s", "u")
    # The message must name the model and the finish_reason so operators can
    # tell "budget burned on reasoning" from "model chose silence".
    assert "model-a" in str(info.value)
    assert "length" in str(info.value)


def test_complete_empty_content_error_carries_usage(monkeypatch, stub_sdk):
    # Real-data finding: with finish_reason=length the model burned ~40k
    # input + 5120 output tokens on reasoning and returned nothing; the
    # empty-content error must carry that usage so the ledger does not
    # record a real spend as null.
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
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


def test_complete_empty_content_without_usage_carries_none(
        monkeypatch, stub_sdk):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(response=chat_response(None, usage=None))
    client = OpenAICompatClient.from_env()
    with pytest.raises(ModelOutputError) as info:
        client.complete("model-a", "s", "u")
    assert info.value.usage is None
    # finish_reason is still carried through from the response ("stop":
    # the model chose silence rather than burning its budget).
    assert info.value.finish_reason == "stop"


def test_model_output_error_defaults_carry_no_usage():
    # Backward compatibility: the single-message constructor keeps working
    # and other raise sites (e.g. extract_json) carry no usage attribute
    # value; a successful response must not leave attributes behind either.
    exc = ModelOutputError("response did not contain JSON")
    assert exc.usage is None
    assert exc.finish_reason is None


def test_complete_content_filter_check_precedes_empty_check(monkeypatch, stub_sdk):
    # A refusal finish_reason is a ProviderRefusal even when content is
    # empty: the existing refusal check must stay in front, unchanged.
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(response=chat_response("", finish_reason="content_filter"))
    client = OpenAICompatClient.from_env()
    with pytest.raises(ProviderRefusalError, match="content_filter"):
        client.complete("model-a", "s", "u")


def test_complete_result_carries_finish_reason(monkeypatch, stub_sdk):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(response=chat_response("hi", finish_reason="stop"))
    client = OpenAICompatClient.from_env()
    result = client.complete("model-a", "s", "u")
    assert result.finish_reason == "stop"


def test_complete_result_finish_reason_defaults_to_none(monkeypatch, stub_sdk):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(response=chat_response("hi", finish_reason=None))
    client = OpenAICompatClient.from_env()
    assert client.complete("model-a", "s", "u").finish_reason is None


def test_complete_extracts_cached_tokens_from_usage_details(monkeypatch, stub_sdk):
    # Real observation: the gateway cache template reported a 20-token prompt
    # as 3927 prompt tokens, 3712 of them cached; without recording
    # cached_tokens the strong model's prompt costs look ~300x too high.
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(response=chat_response(
        "hi", usage=sdk_usage(3927, 4, 3931, cached_tokens=3712)))
    client = OpenAICompatClient.from_env()
    result = client.complete("model-a", "s", "u")
    assert result.cached_tokens == 3712
    assert result.in_tokens == 3927


def test_complete_cached_tokens_none_without_usage_details(monkeypatch, stub_sdk):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(response=chat_response("hi", usage=sdk_usage()))
    client = OpenAICompatClient.from_env()
    assert client.complete("model-a", "s", "u").cached_tokens is None


def test_complete_http_error_maps_to_infrastructure_error(monkeypatch, stub_sdk):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    request = httpx.Request("POST", "http://endpoint/v1/chat/completions")
    error = openai.APIStatusError(
        "boom", response=httpx.Response(500, request=request), body=None
    )
    stub_sdk(error=error)
    client = OpenAICompatClient.from_env()
    with pytest.raises(InfrastructureError, match="500"):
        client.complete("model-a", "s", "u")


def _status_error(status, message="boom"):
    request = httpx.Request("POST", "http://endpoint/v1/chat/completions")
    return openai.APIStatusError(
        message, response=httpx.Response(status, request=request), body=None
    )


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 504])
def test_complete_transient_http_status_maps_to_infrastructure_error(
    monkeypatch, stub_sdk, status
):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(error=_status_error(status))
    client = OpenAICompatClient.from_env()
    with pytest.raises(InfrastructureError, match=str(status)):
        client.complete("model-a", "s", "u")


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_complete_permanent_http_status_maps_to_refusal(
    monkeypatch, stub_sdk, status
):
    # A wrong key / unknown model / oversized context cannot succeed on
    # resend: it must stop the phase, not burn call- and unit-level retries.
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
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
    monkeypatch, stub_sdk
):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    request = httpx.Request("POST", "http://endpoint/v1/chat/completions")

    stub_sdk(error=openai.APITimeoutError(request=request))
    client = OpenAICompatClient.from_env()
    with pytest.raises(InfrastructureError, match="APITimeoutError"):
        client.complete("model-a", "s", "u")

    stub_sdk(error=openai.APIConnectionError(message="down", request=request))
    client = OpenAICompatClient.from_env()
    with pytest.raises(InfrastructureError, match="APIConnectionError"):
        client.complete("model-a", "s", "u")


def test_complete_unexpected_error_propagates_unwrapped(monkeypatch, stub_sdk):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(error=ValueError("not an SDK error"))
    client = OpenAICompatClient.from_env()
    with pytest.raises(ValueError):
        client.complete("model-a", "s", "u")


def test_complete_no_usage_reports_none_tokens(monkeypatch, stub_sdk):
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(response=chat_response("text", usage=None))
    client = OpenAICompatClient.from_env()
    result = client.complete("model-a", "s", "u")
    assert result.in_tokens is None
    assert result.out_tokens is None
    assert result.raw_usage is None


def test_malformed_endpoint_body_is_infrastructure(monkeypatch, stub_sdk):
    # 2026-10-12 campaign crash: the gateway returned an empty/invalid body on
    # a 200 and the openai SDK surfaced a bare JSONDecodeError from inside
    # create(), which slipped past the APIStatusError/APIConnectionError
    # mapping and killed the pipeline. The client boundary must translate
    # protocol-level failures into InfrastructureError so the retry budget
    # (not the process) absorbs them.
    import json as _json
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    stub_sdk(error=_json.JSONDecodeError("Expecting value", "", 0))
    client = OpenAICompatClient.from_env()
    with pytest.raises(InfrastructureError, match="non-JSON body"):
        client.complete("model-a", "s", "u")


def test_schema_invalid_response_is_infrastructure(monkeypatch, stub_sdk):
    # APIResponseValidationError (the SDK's own wrapper for responses that
    # fail its schema) is also an endpoint-protocol failure, not our bug.
    import openai as _openai
    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
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

    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
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


def test_a_model_not_in_the_alias_table_is_sent_unchanged(
        monkeypatch, stub_sdk):
    from expedistill.llm.client import ALIASES_ENV

    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    monkeypatch.setenv(ALIASES_ENV, "model-a=wire-1")
    stub_sdk(response=chat_response("ok"))

    OpenAICompatClient.from_env().complete("model-b", "s", "u")

    assert StubSDKClient.last.create_kwargs["model"] == "model-b"


def test_a_429_rests_that_wire_id_and_the_error_names_the_logical_model(
        monkeypatch, stub_sdk):
    from expedistill.llm.client import ALIASES_ENV

    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
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


def test_a_malformed_alias_table_stops_the_run_at_client_build(
        monkeypatch, stub_sdk):
    from expedistill.llm.client import ALIASES_ENV

    monkeypatch.setenv(ENDPOINT_ENV, "http://endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "k")
    monkeypatch.setenv(ALIASES_ENV, "model-a")
    stub_sdk()

    with pytest.raises(ConfigError, match=ALIASES_ENV):
        OpenAICompatClient.from_env()
