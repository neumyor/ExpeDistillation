#!/usr/bin/env python3
"""A small HTTP CONNECT proxy, for giving an offline host a way out.

It runs on a machine that has outbound access (the Mac) and is reached from the
offline host (g49) through a reverse ssh tunnel (see ``egress_tunnel.sh``).
Only ``CONNECT host:port`` is served, so HTTPS and every protocol carried over
a CONNECT tunnel works; plain ``GET http://...`` requests are refused.
Destinations are limited to an allow-list of host suffixes, so the tunnel is
not an open door to the whole internet.

Usage:
    connect_proxy.py [--listen 127.0.0.1:18118] [--allow SUFFIX ...]

``--allow`` suffixes match the host itself or any subdomain (``a.com`` matches
``a.com`` and ``x.a.com``). Pass ``--allow '*'`` to allow every destination.
"""

import argparse
import asyncio
import sys
from typing import List, Tuple

#: Hosts the experiments need: the model gateway, the sandbox service, and the
#: package mirrors for installing the Python environments.
DEFAULT_ALLOW = ("modelbest.cn", "modelbest.co", "tencentags.com",
                 "tuna.tsinghua.edu.cn", "pypi.org", "pythonhosted.org")

_BUFFER = 1 << 16
_CONNECT_TIMEOUT = 15
_HEADER_LIMIT = 1 << 14


def allowed(host: str, suffixes: Tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    if "*" in suffixes:
        return True
    return any(host == s or host.endswith("." + s) for s in suffixes)


async def _pipe(reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(_BUFFER)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _reply(writer: asyncio.StreamWriter, line: str) -> None:
    writer.write(f"{line}\r\n\r\n".encode())
    await writer.drain()
    writer.close()


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 suffixes: Tuple[str, ...]) -> None:
    try:
        head = await asyncio.wait_for(
            reader.readuntil(b"\r\n\r\n"), timeout=30)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError,
            asyncio.TimeoutError, ConnectionError):
        writer.close()
        return
    if len(head) > _HEADER_LIMIT:
        await _reply(writer, "HTTP/1.1 431 Request Header Fields Too Large")
        return
    parts = head.split(b"\r\n", 1)[0].decode("latin-1").split()
    if len(parts) != 3 or parts[0].upper() != "CONNECT":
        await _reply(writer, "HTTP/1.1 405 Method Not Allowed")
        return
    host, _, port = parts[1].rpartition(":")
    host = host.strip("[]")
    if not host or not port.isdigit():
        await _reply(writer, "HTTP/1.1 400 Bad Request")
        return
    if not allowed(host, suffixes):
        print(f"denied {host}:{port}", file=sys.stderr, flush=True)
        await _reply(writer, "HTTP/1.1 403 Forbidden")
        return
    try:
        up_reader, up_writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port)),
            timeout=_CONNECT_TIMEOUT)
    except (OSError, asyncio.TimeoutError) as exc:
        print(f"unreachable {host}:{port}: {exc}", file=sys.stderr, flush=True)
        await _reply(writer, "HTTP/1.1 502 Bad Gateway")
        return
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()
    await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))


async def serve(listen: str, suffixes: Tuple[str, ...]) -> None:
    host, _, port = listen.rpartition(":")
    server = await asyncio.start_server(
        lambda r, w: handle(r, w, suffixes), host or "127.0.0.1", int(port))
    print(f"connect proxy on {listen}, allow {', '.join(suffixes)}",
          flush=True)
    async with server:
        await server.serve_forever()


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--listen", default="127.0.0.1:18118",
                        help="host:port (default %(default)s); keep it on "
                             "127.0.0.1")
    parser.add_argument("--allow", action="append", metavar="SUFFIX",
                        help="allowed host suffix, repeatable (default: "
                             + ", ".join(DEFAULT_ALLOW) + ")")
    args = parser.parse_args(argv)
    suffixes = tuple(s.lower() for s in (args.allow or DEFAULT_ALLOW))
    try:
        asyncio.run(serve(args.listen, suffixes))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
