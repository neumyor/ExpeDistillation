"""B1 call_model: the single model-call entry every block uses (layer 2).

Pipeline per AGENTS.md block rule 4: look the prompt up by name in the B2
registry, call the client behind a ModelHandle (retrying transient endpoint
failures when the handle opts in), account usage through the B7
UsageRecorder, dump every request and response under the run directory, and
repair JSON output by re-asking with a repair instruction. Blocks never touch
the client or prompt strings directly.
"""

from __future__ import annotations

import ast
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from expedistill import prompts
from expedistill.infra.reliability import (
    ConfigError,
    InfrastructureError,
    ModelOutputError,
)
from expedistill.inject import estimate_tokens

__all__ = [
    "DEFAULT_SYSTEM",
    "MAX_INFRA_BACKOFF_SECONDS",
    "REPAIR_INSTRUCTION",
    "ModelHandle",
    "call_model",
    "extract_json",
    "make_handle",
]

# Registered in the B2 prompt registry (prompts/llm.py) so their digests are
# frozen into the manifest; kept as module-level names for callers/tests.
DEFAULT_SYSTEM = prompts.get("DEFAULT_SYSTEM")
REPAIR_INSTRUCTION = prompts.get("REPAIR_INSTRUCTION")

#: Appended to the original prompt when a PLAIN-TEXT reply arrives with
#: finish_reason="length": the reply was cut off at the output limit, so the
#: re-ask asks for the complete answer again, more concisely. Same
#: bounded-re-ask pattern as the empty-content repair; REPAIR_INSTRUCTION is
#: JSON-specific and must never reach plain-text callers. Method-level
#: plain-text callers since protocol v2: MERGE alone (SKILL_DISTILL, like
#: REWRITE, calls with output_json=True).
_TRUNCATION_NOTE = (
    "Your previous reply was cut off at the output limit before it was "
    "complete. Reply again with the complete answer, but more concisely, "
    "so that it fits within the output limit."
)

_FENCED_RE = re.compile(r"```(?:json|javascript|js)?\s*(.*?)\s*```", re.S | re.I)

LOGGER = logging.getLogger(__name__)

#: Ceiling of one infra-retry wait, the last step of SkillExpand's PROVIDER
#: schedule: past a minute, waiting longer only delays noticing an outage.
MAX_INFRA_BACKOFF_SECONDS = 60.0

# Indirection so tests capture backoff waits instead of sleeping.
_sleep = time.sleep


@dataclass
class ModelHandle:
    """One (model, role) pair wired to a client.

    ``name`` is the model id sent to the endpoint; ``role`` is a free string
    like "judge" / "executor_weak" used for usage accounting. ``client`` is
    anything with the ``complete`` signature (OpenAICompatClient, FakeClient,
    or a benchmark runner's adapter).

    ``max_tokens`` is the output cap call_model sends with every request.
    It defaults to 5120 for hand-built handles (reasoning models with
    reasoning_effort=max need several thousand output tokens, and an
    uncapped call lets a runaway reasoning trace burn the whole step
    budget); the stage pipeline does NOT rely on that default -- it wires
    the config's token budgets here via ``cfg.max_output_tokens_for(role)``
    (see cli._build_handles). A per-model override goes through
    ``extra_body`` (wired from the config's ``model_options``): the client
    merges ``extra`` into the payload after the handle-level
    ``max_tokens``, so ``extra_body={"max_tokens": 128}`` wins over this
    field.

    ``max_input_tokens`` (default None) never aborts a call: when the
    pre-request estimate of system+user exceeds it, the usage row's meta
    carries ``input_violation: true`` -- v1 measures the overflow instead of
    rejecting the prompt, so real data can calibrate the limit first.
    """

    name: str
    role: str
    client: Any
    temperature: float = 1.0
    max_tokens: Optional[int] = 5120
    # Measure-only input ceiling; None = measure nothing (see class doc).
    max_input_tokens: Optional[int] = None
    # Accounting attachments for callers that thread a handle through layers
    # that do not carry usage/run_dir parameters (E1 -> plugin.run ->
    # run_react -> call_model). call_model falls back to these when its own
    # usage/run_dir arguments are not given.
    usage: Any = None
    run_dir: Optional[Union[str, Path]] = None
    # Per-request retries of InfrastructureError (HTTP 5xx/408/409/429,
    # connection, timeout -- the client's mapping) inside call_model, with exponential backoff
    # ``infra_backoff_seconds * 2**retry`` capped at MAX_INFRA_BACKOFF_SECONDS.
    # Default 0: a handle that does not opt in fails on the first error.
    infra_retries: int = 0
    infra_backoff_seconds: float = 0.0
    # Extra chat-call parameters (e.g. reasoning_effort) forwarded to the
    # client for every call of this handle; per-model, wired by the pipeline
    # from the config's ``model_options``. Empty means: send nothing extra.
    extra_body: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Validated here so dataclasses.replace() re-checks as well.
        if isinstance(self.infra_retries, bool) or not isinstance(
                self.infra_retries, int) or self.infra_retries < 0:
            raise ConfigError(
                f"infra_retries must be an int >= 0, got {self.infra_retries!r}")
        backoff = self.infra_backoff_seconds
        if isinstance(backoff, bool) or not isinstance(backoff, (int, float)) \
                or not math.isfinite(backoff) or backoff < 0:
            raise ConfigError(
                "infra_backoff_seconds must be a finite number >= 0, "
                f"got {backoff!r}")
        limit = self.max_input_tokens
        if limit is not None and (
                isinstance(limit, bool) or not isinstance(limit, int)
                or limit <= 0):
            raise ConfigError(
                "max_input_tokens must be a positive int or None, "
                f"got {limit!r}")


def make_handle(role: str, model_name: str, client: Any, **kw: Any) -> ModelHandle:
    """Convenience constructor mirroring FakeClient.make_fake_handle."""
    return ModelHandle(name=model_name, role=role, client=client, **kw)


# --------------------------------------------------------------------------
# JSON extraction, adapted from SkillExpand runtime/json_output.py
# @ TB-eval 4f436af. Differences from the source:
# - the balanced scanner also accepts top-level arrays (SEGMENT returns one);
# - a successfully parsed candidate may be a dict OR a list;
# - failure raises the B6 ModelOutputError instead of the old
#   JsonExtractionError, so call_model's retry loop catches one taxonomy.
# The quoted-string awareness, trailing-comma repair, "json"-prefix stripping,
# and literal_eval last resort are kept as-is.
#
# 2026-10-13 incident (run tb21_split_v2_20261013_2125, 5/315 stage-3 units):
# the source's unconditional fenced-block preference backfired on an UNFENCED
# JSON object whose string values embed ``` code blocks (heredoc-shaped steps
# again). Fence-first scanning dove into the embedded block, its balanced
# scanner picked up a stray `[]` / `[0]` fragment, and the fragment parsed as
# a top-level array before the enclosing object was ever tried -- the reply
# arrived at the block as a list and died as "expected a JSON object, got
# list". The fence preference is now span-relative (_json_candidates): a
# fence that is not inside any top-level span keeps its priority; a fence
# strictly inside one (a code block quoted in a string value) is demoted
# behind the spans.
# --------------------------------------------------------------------------


def _balanced_json_span_ranges(text: str) -> List[Tuple[int, int]]:
    """(start, end) offsets of complete JSON-like spans, respecting quoted braces.

    The position version of the scanner: _json_candidates must know whether a
    ``` fence sits INSIDE one of these spans (a code block quoted in a string
    value) or wraps / reasons around it. ``find('{')``/``rfind('}')`` joins
    two objects together and makes an otherwise recoverable response
    unparsable. The scanner deliberately does not repair an unterminated
    span: accepting a truncated response would turn a format error into
    data.
    """
    spans: List[Tuple[int, int]] = []
    start = None
    depth = 0
    quoted = False
    escaped = False
    for index, char in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in "{[":
            if depth == 0:
                start = index
            depth += 1
        elif char in "}]" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                spans.append((start, index + 1))
                start = None
    return spans


def _balanced_json_spans(text: str) -> List[str]:
    """Return complete JSON-like object or array spans, respecting quoted braces.

    The substring view of _balanced_json_span_ranges; see its docstring for
    the scanner's contract (no repair of unterminated spans).
    """
    return [text[start:end] for start, end in _balanced_json_span_ranges(text)]


def _json_candidates(text: str) -> List[str]:
    text = str(text or "").replace("\ufeff", "").strip()
    # (1) The whole response, first: an unfenced complete JSON value is the
    # common good case, and when its string values embed ``` code blocks
    # (the 2026-10-13 incident), letting fences jump the queue parses a
    # stray fragment ahead of the object.
    candidates: List[str] = [text]
    spans = _balanced_json_span_ranges(text)
    matches = list(_FENCED_RE.finditer(text))

    def fence_candidates(match: Any) -> None:
        candidates.extend(_balanced_json_spans(match.group(1)))
        candidates.append(match.group(1).strip())

    # (2) A fence NOT inside any top-level span keeps the old priority: it is
    # the classic "reasoning, then the fenced answer" wrapper shape.
    # (4) A fence STRICTLY inside a top-level span is a code block quoted in
    # one of its string values: its candidates are demoted to the last
    # resort, not dropped (the whole text may still fail to parse -- trailing
    # prose, truncation -- and the embedded block can then be all there is).
    for match in matches:
        if not any(start <= match.start() and match.end() <= end
                   for start, end in spans):
            fence_candidates(match)
    # (3) Full-text top-level spans in order of appearance (first wins).
    candidates.extend(text[start:end] for start, end in spans)
    for match in matches:
        if any(start <= match.start() and match.end() <= end
               for start, end in spans):
            fence_candidates(match)
    # Preserve order but avoid repeatedly parsing the same large response.
    return list(dict.fromkeys(item for item in candidates if item))


def _load_json_candidate(candidate: str) -> Optional[Union[dict, list]]:
    candidate = candidate.strip()
    attempts = [candidate]
    # Trailing commas are a common harmless generation error. Do this only
    # outside strings so a reason containing `,}` is left untouched.
    repaired = []
    quoted = False
    escaped = False
    for index, char in enumerate(candidate):
        if quoted:
            repaired.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        if char == "," and candidate[index + 1 :].lstrip().startswith(("}", "]")):
            continue
        repaired.append(char)
    attempts.append("".join(repaired))
    # Some gateways prepend ``json`` to an otherwise valid object.
    if candidate.lower().startswith("json"):
        attempts.append(candidate[4:].lstrip(": \n"))
    for item in attempts:
        try:
            value = json.loads(item)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, (dict, list)):
            return value
    # A few local models use Python quotes/booleans despite being asked for
    # JSON. literal_eval is intentionally the last resort; arbitrary code is
    # never evaluated.
    try:
        value = ast.literal_eval(attempts[1])
    except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
        return None
    return value if isinstance(value, (dict, list)) else None


def extract_json(text: str) -> Union[dict, list]:
    """Extract one complete JSON object or array from free-form model output.

    Incomplete/truncated JSON is intentionally rejected so call_model retries
    instead of silently using partial data.
    """
    for candidate in _json_candidates(text):
        value = _load_json_candidate(candidate)
        if value is not None:
            return value
    raise ModelOutputError("LLM response did not contain a complete JSON value")


# --------------------------------------------------------------------------
# call_model
# --------------------------------------------------------------------------


def _unpack(result: Any) -> Tuple[str, Optional[int], Optional[int],
                                   Optional[str], Optional[int]]:
    """Accept a CompletionResult, any object with .text, or a plain string.

    Returns ``(text, in_tokens, out_tokens, finish_reason, cached_tokens)``;
    client doubles without the new fields report None for them.
    """
    if isinstance(result, str):
        return result, None, None, None, None
    text = getattr(result, "text", None)
    if text is None:
        raise ModelOutputError(
            "client returned neither a string nor a result with .text: "
            f"{type(result).__name__}"
        )
    return (
        text,
        getattr(result, "in_tokens", None),
        getattr(result, "out_tokens", None),
        getattr(result, "finish_reason", None),
        getattr(result, "cached_tokens", None),
    )


def _exceeds(value: Optional[int], limit: Optional[int]) -> bool:
    """True when both are known and ``value`` > ``limit``.

    Either side None (unknown tokens / uncapped) means nothing to compare,
    so no violation -- a flag that fires only on evidence, never on
    absence of data.
    """
    return value is not None and limit is not None and value > limit


def _result_meta(finish_reason: Optional[str],
                 cached_tokens: Optional[int],
                 *,
                 requested_max_tokens: Optional[int],
                 estimated_in: int,
                 cap_violation: bool = False,
                 input_violation: bool = False,
                 **entries: Any) -> Dict[str, Any]:
    """Usage-row meta for one request: caller entries plus the
    provider-reported finish_reason (always; unknown as None), cached_tokens
    (only when the provider reported it), and the budget measurement this
    module adds to every row: the output cap actually sent
    (``requested_max_tokens``, None = uncapped), the pre-request estimate of
    the prompt's own size (``estimated_in``), and the two violation flags
    (present only when observed).
    """
    meta: Dict[str, Any] = dict(entries)
    meta["finish_reason"] = finish_reason
    if cached_tokens is not None:
        meta["cached_tokens"] = cached_tokens
    meta["requested_max_tokens"] = requested_max_tokens
    meta["estimated_in"] = estimated_in
    if cap_violation:
        meta["cap_violation"] = True
    if input_violation:
        meta["input_violation"] = True
    return meta


def _reask_prompt(user_text: str, outcome: str,
                  instruction: str = "") -> str:
    """The shared re-ask shape: the ORIGINAL prompt, a separator, what went
    wrong with the previous reply, and (for JSON calls) the repair
    instruction. Rebuilding from the original prompt keeps re-asks from
    nesting: every attempt quotes the first reply's problem, not the
    previous re-ask."""
    suffix = f"\n\n---\n{outcome}"
    if instruction:
        suffix += f"\n\n{instruction}"
    return user_text + suffix


def _record(usage: Any, handle: ModelHandle, prompt_name: str, request_id: str,
            in_tokens: Optional[int], out_tokens: Optional[int], status: str,
            meta: Optional[Dict[str, Any]]) -> None:
    if usage is None:
        return
    # Tokens pass through as None when unknown: record unknown as unknown, never 0.
    usage.record(
        role=handle.role,
        prompt=prompt_name,
        model=handle.name,
        in_tokens=in_tokens,
        out_tokens=out_tokens,
        request_id=request_id,
        status=status,
        meta=meta,
    )


# One lock for the whole process: K1's B5 threads share run directories, and
# the B7 run lock only keeps other processes out.
_SEQ_LOCK = threading.Lock()
# Next free sequence per dump directory, so a dump costs one reservation
# instead of a directory scan (a run holds tens of thousands of dumps).
_NEXT_SEQ: Dict[str, int] = {}


def _scan_next_seq(directory: Path) -> int:
    seq = 0
    for path in directory.glob("*.json"):
        head = path.name.split("_", 1)[0]
        if head.isdigit():
            seq = max(seq, int(head))
    return seq + 1


def _log_default(value: Any) -> str:
    """Render a non-JSON value for the diagnostic dump.

    A model reply that only parses through ``ast.literal_eval`` can carry
    ``...`` (Python's Ellipsis), tuples or other non-JSON objects. The
    business validator downstream rejects them, but the request dump is
    written first and must never take the run down; ``repr`` keeps the
    value visible without pretending it is valid JSON.
    """
    return repr(value)


class _RequestLog:
    """Per-call dump helper: one JSON file per request/response, never the api key."""

    def __init__(self, run_dir: Optional[Path]):
        self.run_dir = run_dir

    @staticmethod
    def _reserve(directory: Path, key: str, at_least: int = 0) -> int:
        with _SEQ_LOCK:
            seq = _NEXT_SEQ.get(key)
            if seq is None:
                seq = _scan_next_seq(directory)
            seq = max(seq, at_least)
            _NEXT_SEQ[key] = seq + 1
            return seq

    def write(self, handle: ModelHandle, prompt_name: str, **record: Any) -> None:
        if self.run_dir is None:
            return
        directory = self.run_dir / "llm_requests"
        directory.mkdir(parents=True, exist_ok=True)
        key = str(directory.resolve())
        seq = self._reserve(directory, key)
        # A private tmp name per write; "*.json" globs never see it.
        tmp = directory / f".{uuid.uuid4().hex}.json.tmp"
        try:
            while True:
                record.update(seq=seq, prompt_name=prompt_name, role=handle.role,
                              model=handle.name)
                tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2,
                                          default=_log_default),
                               encoding="utf-8")
                path = directory / f"{seq:04d}_{prompt_name}.json"
                # Atomic publish (AGENTS.md section 6): a half-written dump is
                # never visible under its final name. link() unlike rename()
                # refuses to replace an existing file, so a dump some other
                # writer published under this seq is never overwritten.
                try:
                    os.link(tmp, path)
                except FileExistsError:
                    seq = self._reserve(directory, key, at_least=seq + 1)
                    continue
                return
        finally:
            tmp.unlink(missing_ok=True)


def call_model(
    handle: ModelHandle,
    prompt_name: str,
    variables: Dict[str, str],
    *,
    output_json: bool = False,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
    max_format_retries: int = 3,
    system_override: Optional[str] = None,
) -> Union[str, dict, list]:
    """Call one model with one named prompt; return text or parsed JSON.

    ``variables`` are str.format fields of the prompt template; a KeyError is
    a caller bug and propagates. With ``output_json=True`` the response is
    parsed with ``extract_json`` and, on failure, re-asked up to
    ``max_format_retries`` times with ``REPAIR_INSTRUCTION`` appended to the
    conversation; exhaustion raises ModelOutputError. Failure means either
    malformed JSON or a client-raised ModelOutputError (empty content, e.g.
    reasoning burned the whole output budget) -- the same failure class,
    "no parseable response" -- and both kinds share the ONE format budget:
    the ``format_retries`` meta on the success row counts re-asks of both
    kinds. Every request and response is recorded via ``usage`` and dumped
    under ``run_dir/llm_requests/`` (never the api key).

    Plain-text calls (``output_json=False``) treat a response with
    finish_reason="length" like empty content: a reply cut off at the output
    limit is never silently accepted (the truncated-merge-in-bank incident
    put a half merge into the bank); it consumes the same
    ``max_format_retries`` budget and is re-asked from the original prompt
    with a suffix saying the previous reply was cut off and asking for a
    complete, more concise reply. Exhaustion raises ModelOutputError naming
    the truncation, so per-unit dispositions downstream handle it honestly.
    JSON calls reject truncation via ``extract_json`` (a truncated JSON
    value fails to parse and takes the existing format retry); the length
    signal still lands in every row's meta either way.

    Every request is measured before it is sent. ``meta.requested_max_tokens``
    is the output cap actually sent (the handle's ``max_tokens``).
    ``meta.estimated_in`` is ``estimate_tokens(system + user)`` from
    expedistill.inject -- ``len(text) // 4``, a budgeting heuristic, not a
    tokenizer. Known bias: it underestimates terminal-dump-heavy texts by
    roughly 1.4-1.5x and overestimates prose slightly; the per-row ratio of
    ``estimated_in`` to the provider's real ``in_tokens`` is the calibration
    instrument. When the provider reports ``out_tokens`` over the requested
    cap (the endpoint ignored it), the row carries ``cap_violation: true``.
    When the handle sets ``max_input_tokens`` and the estimate exceeds it,
    the row carries ``input_violation: true`` -- measured, never enforced
    (v1: the call always proceeds).

    Transient endpoint failures (``InfrastructureError`` from the client)
    resend the same request up to ``handle.infra_retries`` times with
    exponential backoff; exhaustion re-raises the last error. With
    ``output_json=False`` every other client exception (ProviderRefusalError,
    ConfigError, a client-side ModelOutputError such as empty content, code
    bugs) propagates at once: plain-text callers (method-level since
    protocol v2 that is MERGE alone -- SKILL_DISTILL and REWRITE call with
    ``output_json=True``) turn that into their per-unit dispositions. The
    two budgets are independent:
    infra retries never consume format retries, and each request -- the
    first ask and every repair re-ask -- gets the full infra budget, so one
    call makes at most ``(1 + max_format_retries) * (1 + infra_retries)``
    client calls. Failed attempts are recorded (status "error") and dumped
    like any other.
    """
    template = prompts.get(prompt_name)
    user_text = template.format(**variables)
    system = DEFAULT_SYSTEM if system_override is None else system_override
    if usage is None:
        usage = handle.usage
    if run_dir is None:
        run_dir = handle.run_dir
    log = _RequestLog(Path(run_dir) if run_dir is not None else None)

    current_user = user_text
    retries_used = 0
    infra_total = 0
    # The output cap actually sent with every request (None = uncapped).
    requested = handle.max_tokens
    # Sent only when non-empty: a client double without an ``extra``
    # parameter (an old adapter) must keep working for option-less handles.
    extra_kwargs: Dict[str, Any] = (
        {"extra": handle.extra_body} if handle.extra_body else {})
    while True:
        # Measured BEFORE the request: the estimate covers this exact prompt.
        # The repair/empty/truncation re-asks below build longer prompts, so
        # each attempt gets its own row and its own estimate.
        estimated_in = estimate_tokens(system + current_user)
        input_violation = _exceeds(estimated_in, handle.max_input_tokens)
        # Infra retries resend the same request; a response arriving proves the
        # endpoint recovered, so each request (the first ask and every repair
        # re-ask) gets the handle's full infra budget.
        infra_attempt = 0
        # Set when the request below failed with empty content and the format
        # budget allows a re-ask: the outer loop must resend a NEW prompt, not
        # fall through to _unpack with no result.
        empty_reask = False
        while True:
            request_id = uuid.uuid4().hex
            started = time.monotonic()
            try:
                result = handle.client.complete(
                    handle.name,
                    system,
                    current_user,
                    temperature=handle.temperature,
                    max_tokens=handle.max_tokens,
                    **extra_kwargs,
                )
                break
            except Exception as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                retry = (isinstance(exc, InfrastructureError)
                         and infra_attempt < handle.infra_retries)
                # A client-raised ModelOutputError for empty content on an
                # output_json call is the same failure class as malformed
                # JSON ("no parseable response"): it consumes the SAME format
                # budget. The empty-content raise is the one carrying the
                # rejected response's diagnostics (reliability.py: usage /
                # finish_reason are attached by the client when it raises on
                # a response that actually consumed tokens); bare
                # ModelOutputErrors from other raise sites, and every error
                # on plain-text calls, keep propagating at once -- their
                # per-unit dispositions downstream depend on it.
                format_failure = (
                    output_json and isinstance(exc, ModelOutputError)
                    and (exc.finish_reason is not None
                         or exc.usage is not None))
                format_retry = format_failure and retries_used < max_format_retries
                # A client-raised ModelOutputError (empty content) can carry
                # the provider's finish_reason as an attribute; record it in
                # the usage row and the dump instead of None so "budget
                # burned on reasoning" stays diagnosable from the ledgers.
                failed_finish_reason = getattr(exc, "finish_reason", None)
                # A ModelOutputError from the client may carry the usage of
                # the response it rejected (empty content after the budget
                # burned on reasoning): the spend was real, so the ledger
                # row and dump record it instead of null. Exceptions without
                # the attribute (every other raise site) stay null, never 0.
                exc_usage = getattr(exc, "usage", None)
                exc_in = exc_usage.get("prompt_tokens") \
                    if isinstance(exc_usage, dict) else None
                exc_out = exc_usage.get("completion_tokens") \
                    if isinstance(exc_usage, dict) else None
                meta = _result_meta(
                    failed_finish_reason, None,
                    requested_max_tokens=requested,
                    estimated_in=estimated_in,
                    cap_violation=_exceeds(exc_out, requested),
                    input_violation=input_violation,
                    error=type(exc).__name__)
                if isinstance(exc, InfrastructureError):
                    meta.update(infra_attempt=infra_attempt, retried=retry)
                if format_failure:
                    # Distinguishes a re-asked empty reply from a terminal
                    # one in the ledger; the success row's format_retries
                    # counter covers both empty and malformed re-asks.
                    meta["format_retried"] = format_retry
                _record(usage, handle, prompt_name, request_id, exc_in,
                        exc_out, status="error", meta=meta)
                log.write(handle, prompt_name,
                          request_id=request_id, attempt=retries_used,
                          infra_attempt=infra_attempt, system=system,
                          request_text=current_user, response_text=None,
                          parsed=None, in_tokens=exc_in, out_tokens=exc_out,
                          finish_reason=failed_finish_reason,
                          duration_ms=duration_ms, status="error",
                          error=type(exc).__name__)
                if retry:
                    delay = min(
                        handle.infra_backoff_seconds * 2 ** infra_attempt,
                        MAX_INFRA_BACKOFF_SECONDS)
                    infra_attempt += 1
                    infra_total += 1
                    LOGGER.warning(
                        "call_model %s (role %s, model %s): infra retry "
                        "%d/%d in %.1fs after %s: %s", prompt_name,
                        handle.role, handle.name, infra_attempt,
                        handle.infra_retries, delay, type(exc).__name__, exc)
                    if delay > 0:
                        _sleep(delay)
                    continue
                if format_retry:
                    retries_used += 1
                    # Same repair-suffix pattern as the malformed-JSON path:
                    # rebuild from the original prompt, quoting the previous
                    # (empty) outcome so the model knows what to fix.
                    current_user = _reask_prompt(
                        user_text,
                        "Your previous reply was empty and could not be "
                        "parsed:\n" + str(exc),
                        REPAIR_INSTRUCTION)
                    LOGGER.warning(
                        "call_model %s (role %s, model %s): empty response "
                        "(finish_reason=%r), format retry %d/%d: %s",
                        prompt_name, handle.role, handle.name,
                        failed_finish_reason, retries_used, max_format_retries,
                        exc)
                    empty_reask = True
                    break
                if format_failure:
                    raise ModelOutputError(
                        f"prompt {prompt_name}: model returned empty content "
                        f"after {retries_used + 1} attempt(s)",
                        usage=exc.usage, finish_reason=exc.finish_reason,
                    ) from exc
                raise
        if empty_reask:
            continue
        text, in_tokens, out_tokens, finish_reason, cached_tokens = \
            _unpack(result)
        duration_ms = int((time.monotonic() - started) * 1000)
        cap_violation = _exceeds(out_tokens, requested)

        if not output_json:
            if finish_reason == "length":
                # The truncated-merge-in-bank incident: a plain-text reply
                # cut off at the output limit used to be silently accepted.
                # It is now treated like empty content -- the SAME format
                # budget, a re-ask from the original prompt with a suffix
                # naming the truncation -- so a truncated skill/merge is
                # never quietly kept, and exhaustion raises for the
                # per-unit dispositions downstream.
                format_retry = retries_used < max_format_retries
                _record(usage, handle, prompt_name, request_id, in_tokens,
                        out_tokens, status="format_error",
                        meta=_result_meta(
                            finish_reason, cached_tokens,
                            requested_max_tokens=requested,
                            estimated_in=estimated_in,
                            cap_violation=cap_violation,
                            input_violation=input_violation,
                            error="response cut off at the output limit "
                                  "(finish_reason='length')",
                            format_retried=format_retry))
                log.write(handle, prompt_name,
                          request_id=request_id, attempt=retries_used,
                          infra_attempt=infra_attempt, system=system,
                          request_text=current_user, response_text=text,
                          parsed=None, in_tokens=in_tokens,
                          out_tokens=out_tokens,
                          finish_reason=finish_reason,
                          duration_ms=duration_ms, status="format_error")
                if not format_retry:
                    raise ModelOutputError(
                        f"prompt {prompt_name}: response was cut off at the "
                        f"output limit (finish_reason='length') after "
                        f"{retries_used + 1} attempt(s)",
                        finish_reason="length")
                retries_used += 1
                current_user = _reask_prompt(user_text, _TRUNCATION_NOTE)
                LOGGER.warning(
                    "call_model %s (role %s, model %s): response cut off "
                    "at the output limit, format retry %d/%d",
                    prompt_name, handle.role, handle.name, retries_used,
                    max_format_retries)
                continue
            # finish_reason is recorded even when None: unknown as unknown
            # (AGENTS.md section 6), never silently absent.
            meta = _result_meta(finish_reason, cached_tokens,
                                requested_max_tokens=requested,
                                estimated_in=estimated_in,
                                cap_violation=cap_violation,
                                input_violation=input_violation)
            if infra_total:
                meta["infra_retries"] = infra_total
            if retries_used:
                # Only plain-text calls that survived a truncation re-ask
                # carry the counter; a clean plain reply keeps today's meta.
                meta["format_retries"] = retries_used
            _record(usage, handle, prompt_name, request_id, in_tokens,
                    out_tokens, status="ok", meta=meta)
            log.write(handle, prompt_name,
                      request_id=request_id, attempt=retries_used,
                      infra_attempt=infra_attempt, system=system,
                      request_text=current_user, response_text=text, parsed=None,
                      in_tokens=in_tokens, out_tokens=out_tokens,
                      finish_reason=finish_reason,
                      duration_ms=duration_ms, status="ok")
            return text

        try:
            parsed = extract_json(text)
        except ModelOutputError as exc:
            _record(usage, handle, prompt_name, request_id, in_tokens,
                    out_tokens, status="format_error",
                    meta=_result_meta(finish_reason, cached_tokens,
                                      requested_max_tokens=requested,
                                      estimated_in=estimated_in,
                                      cap_violation=cap_violation,
                                      input_violation=input_violation,
                                      error=str(exc)))
            log.write(handle, prompt_name,
                      request_id=request_id, attempt=retries_used,
                      infra_attempt=infra_attempt, system=system,
                      request_text=current_user, response_text=text, parsed=None,
                      in_tokens=in_tokens, out_tokens=out_tokens,
                      finish_reason=finish_reason,
                      duration_ms=duration_ms, status="format_error")
            if retries_used >= max_format_retries:
                raise ModelOutputError(
                    f"prompt {prompt_name}: response was not valid JSON after "
                    f"{retries_used + 1} attempt(s)"
                ) from exc
            retries_used += 1
            current_user = _reask_prompt(
                user_text,
                "Your previous reply was not valid JSON:\n" + text,
                REPAIR_INSTRUCTION)
            continue

        meta = _result_meta(finish_reason, cached_tokens,
                            requested_max_tokens=requested,
                            estimated_in=estimated_in,
                            cap_violation=cap_violation,
                            input_violation=input_violation,
                            format_retries=retries_used)
        if infra_total:
            meta["infra_retries"] = infra_total
        _record(usage, handle, prompt_name, request_id, in_tokens, out_tokens,
                status="ok", meta=meta)
        log.write(handle, prompt_name,
                  request_id=request_id, attempt=retries_used,
                  infra_attempt=infra_attempt, system=system,
                  request_text=current_user, response_text=text, parsed=parsed,
                  in_tokens=in_tokens, out_tokens=out_tokens,
                  finish_reason=finish_reason,
                  duration_ms=duration_ms, status="ok")
        return parsed
