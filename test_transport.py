"""
Tests for the Transport abstraction and the contract loader.

These are the first tests in the project that use REAL HTTP over a REAL socket
to a SEPARATELY RUNNING process. Everything in test_api.py runs the app
in-process via TestClient and answers "is the application logic right?"; these
answer "can the harness talk to a deployed service?" -- and the second question
is the one the rest of the project is built on.
"""

import pathlib

import pytest

from harness.config import HarnessConfig
from harness.spec import ContractError, declared_operations, load_contract
from harness.transport import (
    DirectTransport,
    HttpResponse,
    NotJsonError,
    ProxyTransport,
    Transport,
    build_transport,
)

# --- the transport actually works -----------------------------------------


def test_transport_reaches_the_live_service(transport: Transport):
    """The first real HTTP request the harness has ever made."""
    response = transport.request("GET", "/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_transport_records_elapsed_time(transport: Transport):
    """
    Timing comes back with every response.

    Recording it at the transport means every caller gets it for free. The flake
    detector and the classifier both need latency, and a number measured in one
    consistent place is worth more than one each caller measures its own way.
    """
    response = transport.request("GET", "/health")
    assert response.elapsed_ms > 0
    assert response.elapsed_ms < 5_000


def test_transport_returns_error_statuses_instead_of_raising(transport: Transport):
    """
    A 404 is data, not an exception.

    Pinned deliberately. Half this API's contract is about error responses (Day
    4 existed to make them checkable), so a transport that raised on 4xx would
    make them unreachable. This test exists to stop a future tidy-up adding a
    raise_for_status().
    """
    response = transport.request("GET", "/devices/999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_transport_sends_query_parameters(transport: Transport):
    response = transport.request("GET", "/devices", params={"limit": 2, "offset": 0})
    body = response.json()
    assert len(body["items"]) == 2
    assert body["limit"] == 2


def test_transport_sends_a_json_body(transport: Transport):
    """A write, proving the transport handles more than GETs."""
    response = transport.request(
        "POST", "/devices", json_body={"name": "harness-created", "status": "offline"}
    )
    assert response.status_code == 201
    assert response.json()["name"] == "harness-created"


def test_transport_exposes_headers_lowercased(transport: Transport):
    """
    Header names are case-insensitive in HTTP, so we normalise once here.

    Doing it at the boundary means no caller ever has to remember. Leaving it to
    callers is how you get a lookup that works on one client library and
    silently returns None on another.
    """
    response = transport.request("GET", "/health")
    assert response.content_type == "application/json"
    assert "content-type" in response.headers


def test_response_json_raises_a_specific_error_on_non_json():
    """
    A specific exception type, not a bare ValueError.

    On Day 11 the proxy truncates bodies on purpose, and "the JSON did not
    parse" becomes an expected, classifiable outcome. A distinct type lets the
    classifier recognise it rather than guessing from a message string.
    """
    response = HttpResponse(
        status_code=200, headers={}, text="<html>not json</html>", elapsed_ms=1.0
    )
    with pytest.raises(NotJsonError):
        response.json()


def test_transport_rejects_use_before_open():
    """Failing clearly beats failing with an AttributeError three frames down."""
    closed = DirectTransport(base_url="http://127.0.0.1:1")
    with pytest.raises(RuntimeError, match="not open"):
        closed.request("GET", "/health")


def test_transport_close_is_idempotent(harness_config: HarnessConfig):
    """Teardown paths run twice more often than anyone expects."""
    t = build_transport(harness_config)
    t.open()
    t.close()
    t.close()


# --- configuration ---------------------------------------------------------


def test_config_selects_the_transport_implementation():
    """
    The switch that Day 11 turns on.

    build_transport is the only place in the harness that names a concrete
    implementation. If a second place ever appears, the abstraction has started
    leaking.
    """
    direct = build_transport(HarnessConfig(base_url="http://example", use_proxy=False))
    proxied = build_transport(HarnessConfig(base_url="http://example", use_proxy=True))

    assert isinstance(direct, DirectTransport)
    assert isinstance(proxied, ProxyTransport)


def test_config_reads_the_environment(monkeypatch):
    """
    HARNESS_BASE_URL overriding the default is what lets the same suite run
    against a container or a deployment with no code change.
    """
    monkeypatch.setenv("HARNESS_BASE_URL", "http://elsewhere:9000/")
    monkeypatch.setenv("HARNESS_TIMEOUT_MS", "250")
    monkeypatch.setenv("HARNESS_SEED", "99")

    config = HarnessConfig.from_env(default_base_url="http://ignored")

    assert config.base_url == "http://elsewhere:9000"  # trailing slash stripped
    assert config.timeout_ms == 250
    assert config.seed == 99


def test_config_requires_a_base_url(monkeypatch):
    monkeypatch.delenv("HARNESS_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="No base URL"):
        HarnessConfig.from_env()


# --- the pinned contract ---------------------------------------------------


def test_contract_loads_from_disk(contract: dict):
    assert contract["openapi"].startswith("3.")
    assert contract["info"]["title"] == "Device Registry"


def test_contract_declares_every_endpoint(contract: dict):
    """
    Guards the harness's view of the API surface.

    If an endpoint were added or removed without re-pinning, this fails --
    a second line of defence behind the drift check, from the harness's side.
    """
    operations = set(declared_operations(contract))
    assert operations == {
        ("/devices", "get"),
        ("/devices", "post"),
        ("/devices/search", "get"),
        ("/devices/{device_id}", "delete"),
        ("/devices/{device_id}", "get"),
        ("/devices/{device_id}", "put"),
        ("/devices/{device_id}/status", "patch"),
        ("/health", "get"),
    }


def test_contract_loader_reports_a_missing_file_clearly(tmp_path: pathlib.Path):
    """An operator error deserves a sentence, not a stack trace."""
    with pytest.raises(ContractError, match="python -m scripts.export_spec"):
        load_contract(tmp_path / "nope.json")


def test_contract_loader_rejects_non_openapi_json(tmp_path: pathlib.Path):
    bogus = tmp_path / "bogus.json"
    bogus.write_text('{"hello": "world"}', encoding="utf-8")
    with pytest.raises(ContractError, match="openapi"):
        load_contract(bogus)


# --- the architectural boundary --------------------------------------------


def test_harness_never_imports_the_application():
    """
    THE BOUNDARY TEST.

    The harness must work against any deployment, including one it did not
    start. Importing `api` would make it silently dependent on the service
    living in the same process -- and the import would be an easy, invisible
    convenience to reach for while debugging.

    A rule nobody checks is a rule that erodes, so this checks it. Reading the
    source rather than inspecting sys.modules catches the violation even when
    the import is inside a function and never executed during a test run.
    """
    harness_dir = pathlib.Path(__file__).resolve().parent / "harness"
    offenders = []

    for source_file in sorted(harness_dir.glob("*.py")):
        for number, line in enumerate(
            source_file.read_text(encoding="utf-8").splitlines(), 1
        ):
            stripped = line.strip()
            if stripped.startswith(("import api", "from api")):
                offenders.append(f"{source_file.name}:{number}: {stripped}")

    assert not offenders, (
        "The harness must never import the application under test.\n"
        "It has to work against a service running in another process, "
        "container, or machine.\n"
        "Offending lines:\n  " + "\n  ".join(offenders)
    )