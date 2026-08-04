"""
The Transport abstraction -- how the harness reaches the service.

WHY AN INTERFACE INSTEAD OF JUST CALLING httpx

This is the single most important design decision in the harness, and it is the
same one that carried project 1: build against an abstraction, not a concrete
detail. There, a `Transport` interface meant a real modem over /dev/ttyUSB0 was
a one-class addition rather than a rewrite. Here it means routing every request
through a fault-injecting proxy (Day 11) is a configuration change, and the
validator, runner and test plans above it do not change by a single line.

That is dependency inversion: high-level policy depends on an abstraction; the
low-level detail depends on it too. Nothing that matters depends on anything
replaceable.

WHY OUR OWN RESPONSE TYPE

If `request()` returned httpx's Response, every layer above would depend on
httpx, and the abstraction would be decorative. A small type of our own keeps the
boundary real -- and lets us carry `elapsed_ms`, which the classifier and flake
detector need and which callers would otherwise have to measure themselves.
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx


class NotJsonError(ValueError):
    """Raised when a response body was expected to be JSON and was not."""


@dataclass(frozen=True)
class HttpResponse:
    """
    One response, in harness terms.

    Deliberately dumb: it records what happened and makes no judgement about
    whether it was correct. Judging is the validator's job (Day 8), and keeping
    the two apart is what will let the same response be checked against the
    contract, timed, retried and classified without any of those steps knowing
    about each other.
    """

    status_code: int
    headers: Mapping[str, str]
    text: str
    elapsed_ms: float
    request_method: str = ""
    request_path: str = ""

    @property
    def content_type(self) -> str:
        """The media type, without any `; charset=...` suffix."""
        return self.headers.get("content-type", "").split(";")[0].strip()

    def json(self) -> Any:
        """
        Parse the body as JSON.

        Raises NotJsonError -- not a bare ValueError -- so a caller can tell an
        unparseable BODY apart from any other ValueError in its own logic. That
        distinction matters on Day 11, when the proxy starts truncating bodies
        on purpose and "the JSON did not parse" becomes a specific, expected,
        classifiable outcome rather than a crash.
        """
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            preview = self.text[:120]
            raise NotJsonError(
                f"Body is not valid JSON ({exc}). First 120 chars: {preview!r}"
            ) from exc


class Transport(ABC):
    """
    How the harness sends a request and gets a response.

    Three methods only. Everything above this interface is written against these
    and nothing else.
    """

    @abstractmethod
    def open(self) -> None:
        """Acquire whatever resources are needed (a connection pool, a port)."""

    @abstractmethod
    def close(self) -> None:
        """Release them. Safe to call more than once."""

    @abstractmethod
    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        timeout_ms: int | None = None,
    ) -> HttpResponse:
        """
        Send one request and return the response.

        MUST NOT raise on 4xx or 5xx. An error status is DATA the harness needs
        to examine -- half the contract is about error responses. Raising would
        make them unreachable.

        MAY raise on transport-level failure (connection refused, DNS failure,
        timeout). Those are genuinely different: no HTTP response exists at all,
        and on Day 14 they classify as `environment` rather than as a service or
        test bug. The distinction between "answered badly" and "did not answer"
        is drawn here, at the lowest level, because higher layers cannot recover
        it later.
        """

    def __enter__(self) -> "Transport":
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class DirectTransport(Transport):
    """
    Straight at the service, no proxy in the path.

    The baseline. Any failure seen through this transport is the service's or the
    test's -- there is nothing else in the way to blame, which is what makes it
    the right thing to run the healthy suite against.
    """

    def __init__(self, base_url: str, timeout_ms: int = 5_000) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_ms = timeout_ms
        self._client: httpx.Client | None = None

    def open(self) -> None:
        if self._client is not None:
            return

        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout_ms / 1000,  # httpx works in seconds
            # trust_env=False is NOT a detail. By default httpx reads HTTP_PROXY,
            # HTTPS_PROXY and ALL_PROXY from the environment and silently routes
            # through whatever it finds. That would make results depend on a
            # developer's shell -- the same non-hermetic failure as an inaccurate
            # requirements.txt -- and would be maddening on Day 11, when we run
            # our OWN proxy and need to know traffic goes where we sent it.
            trust_env=False,
            # Do not follow redirects. A 301/302 is a contract-relevant fact the
            # validator should see, not something to silently resolve.
            follow_redirects=False,
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        timeout_ms: int | None = None,
    ) -> HttpResponse:
        if self._client is None:
            raise RuntimeError("Transport is not open. Call open() first.")

        timeout = (timeout_ms or self._timeout_ms) / 1000

        response = self._client.request(
            method.upper(),
            path,
            params=params,
            json=json_body,
            timeout=timeout,
        )

        return HttpResponse(
            status_code=response.status_code,
            # Copy into a plain dict: the harness should not hold a live
            # reference into httpx's object graph once the call is over.
            headers={k.lower(): v for k, v in response.headers.items()},
            text=response.text,
            # httpx measures this for us; recording it here means every caller
            # gets timing for free and none of them has to remember to time.
            elapsed_ms=response.elapsed.total_seconds() * 1000,
            request_method=method.upper(),
            request_path=path,
        )


class ProxyTransport(DirectTransport):
    """
    The same requests, routed through the fault-injection proxy.

    Today this is DirectTransport pointed at a different address -- and that is
    the abstraction working, not a shortcut. A forward proxy is transparent to
    the client, so "go through the proxy" genuinely is a change of base URL.

    It exists as its own class because on Day 11 it stops being only that: the
    proxy will need to be told which fault to inject and with what probability,
    and that instruction belongs here rather than leaking into every caller.
    Naming it now means Day 11 changes one class instead of every call site.
    """


def build_transport(config: "object") -> Transport:
    """
    Choose a transport from configuration.

    The only place in the harness that knows which implementations exist.
    Everything else takes a `Transport` and does not care -- which is the whole
    point, and the thing to check if the abstraction ever starts feeling
    decorative.
    """
    transport_cls = ProxyTransport if config.use_proxy else DirectTransport
    return transport_cls(base_url=config.base_url, timeout_ms=config.timeout_ms)