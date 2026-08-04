"""
Shared pytest fixtures, auto-discovered by pytest for every test file.

NOTE ON THE BOUNDARY: this file imports `api` in order to start a server for
local convenience. The HARNESS never does, and a test enforces that. The
distinction is deliberate:

    harness/     -- the product. Works against any base URL, including a service
                    it did not start. Must never import the app.
    conftest.py  -- test scaffolding. Starts a server so `pytest` works with one
                    command and no manual setup.

The proof the boundary is real: set HARNESS_BASE_URL and the same suite runs
against a container or a remote deployment with no code change. The fixture is a
convenience, not a dependency.
"""

import socket
import threading
import time

import pytest
import uvicorn

from harness.config import HarnessConfig
from harness.spec import load_contract
from harness.transport import build_transport


def _reserve_free_port() -> socket.socket:
    """
    Bind a socket to an OS-chosen free port and return it, still open.

    Handing the LIVE socket to uvicorn (rather than looking up a free port,
    closing it, and hoping) removes a race: between closing and rebinding,
    another process could take the port and the suite would fail intermittently.
    That intermittent failure would be a flaky test -- in the project built to
    detect flaky tests. Same idea as project 1's conftest binding to port 0.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return sock


@pytest.fixture(scope="session")
def live_server() -> str:
    """
    Run the service in a background thread for the whole test session, and
    yield its base URL.

    Session-scoped because starting a server costs ~100ms and doing it per test
    would add seconds for no benefit. The usual danger of session scope is
    shared state causing test-order dependence; it is acceptable here because
    today's harness tests only READ. When Day 9 adds cases that create and
    delete devices, they must reset state themselves -- a deliberate decision
    rather than an accident.
    """
    # Imported inside the fixture, not at module level, to keep the import of
    # the application confined to the one place that genuinely needs it.
    from api.main import app

    sock = _reserve_free_port()
    port = sock.getsockname()[1]

    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()

    # STARTED IS NOT READY -- the same lesson as Day 2's docker-compose
    # depends_on. Poll for readiness with a deadline instead of sleeping a
    # guessed number of seconds: too short is flaky, too long is wasted on every
    # run, and neither reports anything useful when it goes wrong.
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("Server did not become ready within 10s")
        time.sleep(0.02)

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def harness_config(live_server: str) -> HarnessConfig:
    """
    Configuration for the harness under test.

    Built with from_env() so HARNESS_BASE_URL can override the local server --
    which is exactly how the same suite gets pointed at a container or a
    deployment without touching any code.
    """
    return HarnessConfig.from_env(default_base_url=live_server)


@pytest.fixture
def transport(harness_config: HarnessConfig):
    """
    An open Transport, closed afterwards even if the test fails.

    Function-scoped although the server is session-scoped: a connection pool is
    cheap, and a fresh one per test means a connection left in a strange state
    by one test cannot affect the next.
    """
    with build_transport(harness_config) as open_transport:
        yield open_transport


@pytest.fixture(scope="session")
def contract() -> dict:
    """
    The pinned contract, loaded once from disk.

    Note it takes no server fixture: the contract is a file, so this works with
    nothing running at all.
    """
    return load_contract()