#!/usr/bin/env python3
"""One OpenAI-compatible endpoint in front of several vLLM replicas.

Each request goes to the backend with the fewest requests in flight (ties
rotate), and the response is streamed back as it arrives, so server-sent
events keep their timing. A backend that refuses the connection is skipped
for that request; the next backend gets it. Once a backend has started
answering, the request is never retried: a half-sent completion cannot be
replayed safely, so the client sees the failure and retries itself.

Usage:
    vllm_lb.py --listen 127.0.0.1:8010 --backend http://127.0.0.1:8110 \
        --backend http://127.0.0.1:8111 --backend http://127.0.0.1:8112

``GET /lb_health`` answers 200 with the in-flight count of each backend when
at least one backend answers ``/health``, and 503 otherwise.
"""

import argparse
import asyncio
import itertools
import json
import sys
import time
from typing import Dict, List

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

    def order(self) -> List[str]:
        """Every backend, the least busy first; equal loads rotate. A backend
        that recently refused a connection comes last."""
        start = next(self._turn) % len(self.backends)
        rotated = self.backends[start:] + self.backends[:start]
        now = time.monotonic()
        return sorted(rotated, key=lambda url: (
            self._down_until.get(url, 0.0) > now, self.inflight[url]))


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
    last_error = "no backend"
    for url in pool.order():
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


def make_app(backends: List[str]) -> web.Application:
    app = web.Application(client_max_size=1024 ** 3)
    app["pool"] = Pool(backends)
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
    args = parser.parse_args(argv)
    host, _, port = args.listen.rpartition(":")
    try:
        app = make_app([url.rstrip("/") for url in args.backend])
    except ValueError as exc:
        parser.error(str(exc))
    web.run_app(app, host=host or "127.0.0.1", port=int(port),
                access_log=None, print=lambda *_: None)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
