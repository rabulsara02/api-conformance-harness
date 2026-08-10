"""
An asyncio HTTP forward proxy that can misbehave on purpose.

For each request it: accepts a TCP connection, reads the HTTP request, opens a
connection upstream, forwards the request, reads the response, and writes it
back. Every fault is a modification of one of those steps.

TWO DELIBERATE SIMPLIFICATIONS, named rather than hidden:

  1. `Connection: close` -- one request per connection. This avoids implementing
     connection reuse and chunked-transfer framing, which would triple the code
     for no benefit at 22 test cases. It is slower than a real proxy.
  2. No streaming -- the full response is buffered before being sent on. A real
     proxy streams; buffering is what makes truncation and mid-response cuts easy
     to implement precisely, and our largest response is a few hundred bytes.

Run it with:

    python -m proxy.server --port 8080 --upstream http://127.0.0.1:8000
"""

import argparse
import asyncio
import os
import sys
from urllib.parse import urlsplit

from proxy.faults import FaultConfig, FaultDecider, FaultMode

CRLF = b"\r\n"
HEADER_END = b"\r\n\r\n"


class HttpMessage:
    """
    One parsed HTTP message -- request or response.

    Deliberately minimal: a start line, a list of header pairs, and body bytes.
    Headers are a LIST rather than a dict because HTTP permits repeats, and
    collapsing them into a dict would silently discard information the proxy is
    supposed to pass through untouched.
    """

    def __init__(
        self, start_line: bytes, headers: list[tuple[bytes, bytes]], body: bytes
    ) -> None:
        self.start_line = start_line
        self.headers = headers
        self.body = body

    def header(self, name: bytes) -> bytes | None:
        """Header names are case-insensitive in HTTP, so compare lowercased."""
        lowered = name.lower()
        for key, value in self.headers:
            if key.lower() == lowered:
                return value
        return None

    def replace_header(self, name: bytes, value: bytes) -> None:
        lowered = name.lower()
        self.headers = [(k, v) for k, v in self.headers if k.lower() != lowered]
        self.headers.append((name, value))

    def drop_header(self, name: bytes) -> None:
        lowered = name.lower()
        self.headers = [(k, v) for k, v in self.headers if k.lower() != lowered]

    def to_bytes(self) -> bytes:
        head = self.start_line + CRLF
        for key, value in self.headers:
            head += key + b": " + value + CRLF
        return head + CRLF + self.body


async def read_message(
    reader: asyncio.StreamReader, *, read_to_eof: bool
) -> HttpMessage | None:
    """
    Read one HTTP message off a socket.

    The framing rules, and there are only three:
      * lines end with CRLF;
      * a blank line (CRLF CRLF) ends the headers -- so `readuntil` gives us the
        entire head in one call;
      * Content-Length says how many body bytes follow.

    `read_to_eof` handles responses: because we force `Connection: close`
    upstream, the service closes the socket when it is finished, so end-of-file
    marks the end of the body even without a Content-Length.

    Returns None on a closed or malformed connection rather than raising --
    clients disconnect for all sorts of ordinary reasons, and a proxy that dies
    when one does is worse than useless.
    """
    try:
        head = await reader.readuntil(HEADER_END)
    except (
        asyncio.IncompleteReadError,
        asyncio.LimitOverrunError,
        ConnectionResetError,
    ):
        return None

    if not head:
        return None

    lines = head[: -len(HEADER_END)].split(CRLF)
    headers: list[tuple[bytes, bytes]] = []
    for line in lines[1:]:
        if b":" in line:
            key, _, value = line.partition(b":")
            headers.append((key.strip(), value.strip()))

    message = HttpMessage(lines[0], headers, b"")

    length = message.header(b"content-length")
    if length is not None and int(length) > 0:
        message.body = await reader.readexactly(int(length))
    elif read_to_eof:
        message.body = await reader.read()

    return message


def _error_response(status: int) -> bytes:
    """
    A complete, well-formed error response the proxy produces itself.

    Deliberately well-formed: the point of `error_code` is an UNEXPECTED STATUS,
    not malformed output. Keeping the fault modes non-overlapping is what lets
    the Day 14 classifier have exactly one right answer per failure.
    """
    body = b'{"error":{"code":"proxy_injected","message":"injected fault: error_code"}}'
    return (
        f"HTTP/1.1 {status} Service Unavailable\r\n".encode()
        + b"content-type: application/json" + CRLF
        + b"content-length: " + str(len(body)).encode() + CRLF
        + b"connection: close" + CRLF + CRLF
        + body
    )


class FaultProxy:
    """The proxy itself. One instance per process."""

    def __init__(
        self, listen_host: str, listen_port: int, upstream: str, config: FaultConfig
    ) -> None:
        self.listen_host = listen_host
        self.listen_port = listen_port

        parts = urlsplit(upstream)
        self.upstream_host = parts.hostname or "127.0.0.1"
        self.upstream_port = parts.port or 80

        self.decider = FaultDecider(config)
        self.requests_seen = 0
        self.faults_injected = 0

    async def handle(self, client_reader, client_writer) -> None:
        """
        Handle one client connection.

        The order of the fault checks matters: LATENCY happens before
        forwarding, ERROR_CODE replaces forwarding entirely, and CORRUPT_BODY
        and DROP act on the way back. That mirrors where each failure really
        occurs in a network path.
        """
        try:
            request = await read_message(client_reader, read_to_eof=False)
            if request is None:
                return

            self.requests_seen += 1
            mode = self.decider.config.mode
            inject = self.decider.should_inject()
            if inject:
                self.faults_injected += 1

            # LATENCY: await, never time.sleep(). A blocking sleep would freeze
            # the whole event loop and stall every other connection -- the
            # classic async mistake, and it would make the injected delay affect
            # requests that were not selected for it.
            if inject and mode is FaultMode.LATENCY:
                await asyncio.sleep(self.decider.config.latency_ms / 1000)

            # ERROR_CODE: answer directly. The service is never contacted, which
            # is what makes this an ENVIRONMENT failure rather than a service
            # one -- the service had no opportunity to be wrong.
            if inject and mode is FaultMode.ERROR_CODE:
                client_writer.write(_error_response(self.decider.config.error_status))
                await client_writer.drain()
                return

            # Force one-request-per-connection, per the module docstring.
            request.replace_header(b"Connection", b"close")
            request.drop_header(b"Keep-Alive")

            upstream_reader, upstream_writer = await asyncio.open_connection(
                self.upstream_host, self.upstream_port
            )
            upstream_writer.write(request.to_bytes())
            await upstream_writer.drain()

            response = await read_message(upstream_reader, read_to_eof=True)
            upstream_writer.close()
            if response is None:
                return

            # CORRUPT_BODY: truncate to half. Content-Length MUST be corrected to
            # match, or the client hangs waiting for bytes that never arrive --
            # the same rule as Day 6's bug middleware, on the other side of the
            # wire. The truncation makes the JSON unparseable, which surfaces in
            # the harness as NotJsonError (Day 7).
            if inject and mode is FaultMode.CORRUPT_BODY and response.body:
                response.body = response.body[: max(1, len(response.body) // 2)]
                response.replace_header(
                    b"content-length", str(len(response.body)).encode()
                )

            raw = response.to_bytes()

            # DROP: write half the bytes, then close. This is why the proxy had
            # to be a real network program: you cannot honestly simulate a
            # connection dying mid-response from inside the client. The harness
            # sees a transport-level failure with no HTTP response at all.
            if inject and mode is FaultMode.DROP:
                client_writer.write(raw[: len(raw) // 2])
                await client_writer.drain()
                client_writer.close()
                return

            client_writer.write(raw)
            await client_writer.drain()

        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            # Clients disconnect for ordinary reasons. A proxy that crashes when
            # one does would inject faults nobody asked for.
            pass
        finally:
            try:
                client_writer.close()
            except Exception:
                pass

    async def serve_forever(self) -> None:
        server = await asyncio.start_server(
            self.handle, self.listen_host, self.listen_port
        )
        async with server:
            await server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fault-injecting HTTP proxy")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--upstream",
        default=os.environ.get("PROXY_UPSTREAM", "http://127.0.0.1:8000"),
    )
    args = parser.parse_args(argv)

    config = FaultConfig.from_env()
    proxy = FaultProxy(args.host, args.port, args.upstream, config)

    # Print the configuration at startup. Same reasoning as putting the config in
    # the run summary: a fault run you cannot reproduce is an anecdote.
    print(
        f"proxy listening on {args.host}:{args.port} -> {args.upstream} | "
        f"fault={config.mode.value} probability={config.probability} "
        f"seed={config.seed} latency_ms={config.latency_ms}",
        flush=True,
    )

    asyncio.run(proxy.serve_forever())
    return 0


if __name__ == "__main__":
    sys.exit(main())