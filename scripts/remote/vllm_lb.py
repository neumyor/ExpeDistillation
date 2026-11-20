#!/usr/bin/env python3
"""One OpenAI-compatible endpoint in front of several vLLM replicas.

Routing keeps the replicas' prefix caches useful. A vLLM replica reuses the
cached prefix of a prompt only when an earlier prompt went to the SAME
replica, so spreading the steps of one episode (or the attempts of one task)
over the replicas throws most of that reuse away. Every request therefore gets
an affinity key, taken from the request body: the text matched by
``--affinity-regex`` when it is given, otherwise the first
``--affinity-chars`` characters of the first user message. Backends are ranked
for a key by rendezvous hashing, and the request goes to the first one whose
load is within a bound (a quarter above the average in flight, at least
four); a key whose preferred backend is busy spills to the next one. Requests
without a usable key (GET, malformed body) go to the least busy backend.

The response is streamed back as it arrives, so server-sent events keep their
timing. A backend that refuses the connection is skipped
for that request; the next backend gets it. Once a backend has started
answering, the request is never retried: a half-sent completion cannot be
replayed safely, so the client sees the failure and retries itself.

Usage:
    vllm_lb.py --listen 127.0.0.1:8010 --backend http://127.0.0.1:8110 \
        --backend http://127.0.0.1:8111 --backend http://127.0.0.1:8112 \
        [--affinity-regex 'Your task is to: [^\n]*'] [--affinity-chars 150]

``GET /lb_health`` answers 200 with the in-flight count of each backend when
at least one backend answers ``/health``, and 503 otherwise.
"""

import argparse
import asyncio
import hashlib
import itertools
import json
import math
import re
import sys
import time
from typing import Dict, List, Optional

from aiohttp import ClientError, ClientSession, ClientTimeout, TCPConnector, web

#: Headers that belong to one hop and must not be copied across the proxy.
_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}

#: A reasoning model can think for a long time before its first token.
_SOCK_READ_SECONDS = 7200

#: Seconds a backend that refused a connection is left out of the rotation.
_DOWN_SECONDS = 5.0

#: How far above the average load a backend may be and still keep the keys it
#: is preferred for, and the smallest absolute load that counts as busy.
_LOAD_SLACK = 1.25
_LOAD_FLOOR = 4

#: Characters of the first user message that form the key when no regex is set.
DEFAULT_AFFINITY_CHARS = 150


class Pool:
    """Least-in-flight choice over the backends, rotating among ties."""

    def __init__(self, backends: List[str]) -> None:
        if not backends:
            raise ValueError("at least one --backend is required")
        self.backends = list(backends)
        self.inflight: Dict[str, int] = {url: 0 for url in self.backends}
        self._down_until: Dict[str, float] = {}
        self._turn = itertools.count()

    def mark_down(self, url: str) -> None:
        self._down_until[url] = time.monotonic() + _DOWN_SECONDS

    def order(self, key: Optional[str] = None) -> List[str]:
        """Every backend, in the order to try them. Without a key the least
        busy first, equal loads rotating. With a key the rendezvous ranking for
        it, except that a backend over the load bound moves behind the ones
        within it. A backend that recently refused a connection comes last."""
        now = time.monotonic()
        down = lambda url: self._down_until.get(url, 0.0) > now  # noqa: E731
        if key is None:
            start = next(self._turn) % len(self.backends)
            rotated = self.backends[start:] + self.backends[:start]
            return sorted(rotated, key=lambda url: (down(url),
                                                    self.inflight[url]))
        ranked = sorted(self.backends, key=lambda url: hashlib.sha1(
            (key + "\0" + url).encode()).digest(), reverse=True)
        bound = max(_LOAD_FLOOR, math.ceil(
            _LOAD_SLACK * (sum(self.inflight.values()) + 1)
            / len(self.backends)))
        return sorted(ranked, key=lambda url: (
            down(url), self.inflight[url] >= bound))


def affinity_key(body: bytes, regex: Optional["re.Pattern"],
                 chars: int) -> Optional[str]:
    """The routing key of one request body, None when there is no usable text.

    The text is the first user message of a chat request (or the prompt of a
    completion request); the key is what ``regex`` finds in it, or its first
    ``chars`` characters."""
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    text = None
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            content = message.get("content") if isinstance(message, dict) \
                and message.get("role") == "user" else None
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content
                                  if isinstance(part, dict))
            if isinstance(content, str):
                text = content
                break
    elif isinstance(payload.get("prompt"), str):
        text = payload["prompt"]
    if not text:
        return None
    if regex is not None:
        found = regex.search(text)
        return found.group(0) if found else text[:chars]
    return text[:chars]


def _forward_headers(headers) -> Dict[str, str]:
    return {key: value for key, value in headers.items()
            if key.lower() not in _HOP_HEADERS}


async def _health(request: web.Request) -> web.Response:
    pool: Pool = request.app["pool"]
    session: ClientSession = request.app["session"]
    alive = 0
    for url in pool.backends:
        try:
            async with session.get(url + "/health",
                                   timeout=ClientTimeout(total=5)) as reply:
                alive += reply.status == 200
        except (ClientError, asyncio.TimeoutError):
            pass
    body = json.dumps({"alive": alive, "backends": len(pool.backends),
                       "inflight": pool.inflight})
    return web.Response(status=200 if alive else 503, text=body,
                        content_type="application/json")


async def _proxy(request: web.Request) -> web.StreamResponse:
    pool: Pool = request.app["pool"]
    session: ClientSession = request.app["session"]
    body = await request.read()
    key = affinity_key(body, request.app["affinity_regex"],
                       request.app["affinity_chars"]) if body else None
    last_error = "no backend"
    for url in pool.order(key):
        pool.inflight[url] += 1
        upstream = None
        try:
            try:
                upstream = await session.request(
                    request.method, url + request.rel_url.raw_path_qs,
                    data=body or None,
                    headers=_forward_headers(request.headers),
                    allow_redirects=False)
            except (ClientError, OSError) as exc:
                pool.mark_down(url)
                last_error = f"{url}: {exc}"
                continue
            response = web.StreamResponse(
                status=upstream.status, reason=upstream.reason,
                headers=_forward_headers(upstream.headers))
            await response.prepare(request)
            async for chunk in upstream.content.iter_any():
                await response.write(chunk)
            await response.write_eof()
            return response
        finally:
            pool.inflight[url] -= 1
            if upstream is not None:
                upstream.release()
    return web.Response(status=502, text=f"all backends failed: {last_error}")


async def _open_session(app: web.Application) -> None:
    app["session"] = ClientSession(
        connector=TCPConnector(limit=0),
        timeout=ClientTimeout(total=None, sock_connect=10,
                              sock_read=_SOCK_READ_SECONDS))


async def _close_session(app: web.Application) -> None:
    await app["session"].close()


def make_app(backends: List[str], affinity_regex: Optional[str] = None,
             affinity_chars: int = DEFAULT_AFFINITY_CHARS) -> web.Application:
    app = web.Application(client_max_size=1024 ** 3)
    app["pool"] = Pool(backends)
    app["affinity_regex"] = re.compile(affinity_regex) \
        if affinity_regex else None
    app["affinity_chars"] = affinity_chars
    app.on_startup.append(_open_session)
    app.on_cleanup.append(_close_session)
    app.router.add_get("/lb_health", _health)
    app.router.add_route("*", "/{tail:.*}", _proxy)
    return app


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--listen", default="127.0.0.1:8010",
                        help="host:port to serve on (default %(default)s)")
    parser.add_argument("--backend", action="append", default=[],
                        help="base URL of one vLLM replica, repeatable")
    parser.add_argument("--affinity-regex", default=None,
                        help="requests whose first user message gives the "
                             "same match go to the same replica (default: "
                             "the first --affinity-chars characters)")
    parser.add_argument("--affinity-chars", type=int,
                        default=DEFAULT_AFFINITY_CHARS,
                        help="length of the default key (default %(default)s)")
    args = parser.parse_args(argv)
    host, _, port = args.listen.rpartition(":")
    try:
        app = make_app([url.rstrip("/") for url in args.backend],
                       args.affinity_regex, args.affinity_chars)
    except (ValueError, re.error) as exc:
        parser.error(str(exc))
    web.run_app(app, host=host or "127.0.0.1", port=int(port),
                access_log=None, print=lambda *_: None)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
