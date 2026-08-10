"""
Tests for the fault-injection proxy.

Two jobs:
  1. The proxy is TRANSPARENT when told to be. An instrument that distorts when
     idle is worse than no instrument.
  2. Each fault produces the failure it claims to, over a real socket -- because
     a dropped connection cannot be honestly simulated in-process.
"""

import asyncio
import socket
import threading
import time

import httpx
import pytest

from proxy.faults import FaultConfig, FaultDecider, FaultMode
from proxy.server import FaultProxy


@pytest.fixture
def proxy_factory(live_server):
    """
    Start a fault proxy in a background thread, pointed at the live service.

    Returns a factory so each test can choose its own fault configuration. Each
    proxy gets a fresh FaultDecider, which means a fresh random sequence -- see
    the Day 11 primer §8 for why that matters.
    """
    started = []

    def start(config: FaultConfig) -> str:
        ready: list[int] = []

        def run() -> None:
            async def main() -> None:
                proxy = FaultProxy("127.0.0.1", 0, live_server, config)
                server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
                ready.append(server.sockets[0].getsockname()[1])
                async with server:
                    await server.serve_forever()

            asyncio.run(main())

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        deadline = time.monotonic() + 5
        while not ready:
            if time.monotonic() > deadline:
                raise RuntimeError("proxy did not start")
            time.sleep(0.01)

        started.append(thread)
        return f"http://127.0.0.1:{ready[0]}"

    return start


@pytest.fixture
def client():
    # trust_env=False for the same reason as the harness (Day 7): this must not
    # route through whatever proxy the developer's shell happens to name.
    with httpx.Client(trust_env=False, timeout=10) as c:
        yield c


def test_proxy_is_transparent_when_healthy(proxy_factory, client):
    """
    The most important test in this file.

    An instrument that distorts the signal when it is supposed to be idle makes
    every measurement taken through it worthless.
    """
    base = proxy_factory(FaultConfig(mode=FaultMode.NONE))
    response = client.get(f"{base}/devices/1")

    assert response.status_code == 200
    assert response.json() == {"id": 1, "name": "edge-router-01", "status": "online"}


def test_proxy_forwards_query_parameters_and_bodies(proxy_factory, client):
    base = proxy_factory(FaultConfig(mode=FaultMode.NONE))

    listed = client.get(f"{base}/devices", params={"limit": 2})
    assert listed.status_code == 200
    assert listed.json()["limit"] == 2

    created = client.post(f"{base}/devices", json={"name": "via-proxy"})
    assert created.status_code == 201
    assert created.json()["name"] == "via-proxy"


def test_latency_delays_the_response(proxy_factory, client):
    """Correct response, just late. Latency alone is not a failure."""
    base = proxy_factory(
        FaultConfig(mode=FaultMode.LATENCY, probability=1.0, latency_ms=400)
    )

    started = time.monotonic()
    response = client.get(f"{base}/devices/1")
    elapsed_ms = (time.monotonic() - started) * 1000

    assert response.status_code == 200
    assert elapsed_ms >= 400


def test_error_code_replaces_the_response(proxy_factory, client):
    """
    The service is never contacted.

    That is what makes this an ENVIRONMENT failure rather than a service one --
    the service had no opportunity to be wrong.
    """
    base = proxy_factory(
        FaultConfig(mode=FaultMode.ERROR_CODE, probability=1.0, error_status=503)
    )
    response = client.get(f"{base}/devices/1")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "proxy_injected"


def test_corrupt_body_produces_valid_http_and_invalid_json(proxy_factory, client):
    """
    Valid HTTP framing, unparseable payload.

    Content-Length is corrected to match the truncated body -- otherwise the
    client would hang waiting for bytes that never arrive, which is a different
    (and unlabelled) fault from the one we asked for.
    """
    base = proxy_factory(FaultConfig(mode=FaultMode.CORRUPT_BODY, probability=1.0))
    response = client.get(f"{base}/devices/1")

    assert response.status_code == 200
    with pytest.raises(Exception):
        response.json()


def test_drop_produces_no_response_at_all(proxy_factory, client):
    """
    THE fault that justifies building a real network proxy.

    You cannot honestly simulate a connection dying mid-response from inside the
    client. There is no HTTP response to inspect -- which is categorically
    different from an error response, and is why the harness distinguishes
    "answered badly" from "did not answer".
    """
    base = proxy_factory(FaultConfig(mode=FaultMode.DROP, probability=1.0))

    with pytest.raises(httpx.HTTPError):
        client.get(f"{base}/devices/1")


def test_probability_zero_injects_nothing(proxy_factory, client):
    base = proxy_factory(FaultConfig(mode=FaultMode.DROP, probability=0.0))
    assert client.get(f"{base}/devices/1").status_code == 200


# --- the decider ------------------------------------------------------------


def test_same_seed_produces_the_same_decisions():
    """Reproducibility (guardrail 9). Without it, no measurement is checkable."""
    config = FaultConfig(mode=FaultMode.DROP, probability=0.3, seed=1234)

    a = FaultDecider(config)
    b = FaultDecider(config)

    assert [a.should_inject() for _ in range(20)] == [
        b.should_inject() for _ in range(20)
    ]


def test_different_seeds_produce_different_decisions():
    a = FaultDecider(FaultConfig(mode=FaultMode.DROP, probability=0.3, seed=1))
    b = FaultDecider(FaultConfig(mode=FaultMode.DROP, probability=0.3, seed=2))

    assert [a.should_inject() for _ in range(30)] != [
        b.should_inject() for _ in range(30)
    ]


def test_decider_advances_and_does_not_repeat_itself():
    """
    THE PROPERTY DAY 12 DEPENDS ON.

    The generator advances with every request and resets only when the process
    restarts. Restart the proxy between runs and every run replays the first --
    the same tests fail every time, which is a deterministic failure, not a
    flake. Leave it running and the pattern keeps moving, which is what
    manufactures flakiness.
    """
    decider = FaultDecider(FaultConfig(mode=FaultMode.DROP, probability=0.5, seed=1234))

    first_twenty = [decider.should_inject() for _ in range(20)]
    second_twenty = [decider.should_inject() for _ in range(20)]

    assert first_twenty != second_twenty


def test_none_mode_never_injects():
    decider = FaultDecider(FaultConfig(mode=FaultMode.NONE, probability=1.0))
    assert not any(decider.should_inject() for _ in range(50))


def test_unknown_fault_mode_is_rejected(monkeypatch):
    """A typo must fail loudly, not silently run a transparent proxy."""
    monkeypatch.setenv("PROXY_FAULT", "definitely-not-a-mode")
    with pytest.raises(ValueError, match="Unknown PROXY_FAULT"):
        FaultConfig.from_env()