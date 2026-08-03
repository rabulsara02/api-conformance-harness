"""
Tests for the device-registry API.

Uses FastAPI's TestClient, which drives the application IN-PROCESS: no server
starts, no port is bound, no sockets are opened. That makes these tests fast and
deterministic, and they answer the question "is my application logic correct?"

The harness (Day 7 onward) deliberately does the opposite -- real HTTP, to a
real running service, through a real proxy -- because it answers a different
question: "does the deployed service honor its published contract?" Both are
worth having, and knowing why is more useful than knowing either one.
"""

import pytest
from fastapi.testclient import TestClient

from api import store
from api.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_store():
    """
    Reset the in-memory store before every test in this file.

    `autouse=True` means every test gets this automatically without asking for
    it by name. This guarantees test independence: no test can be affected by
    data another test left behind, which removes test-order dependence -- a
    classic source of flaky tests, and the exact category this project's
    detector will later hunt for.
    """
    store.reset()


def test_health_returns_ok():
    """The liveness probe answers 200 with a known body."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_devices_returns_all_seeded_devices():
    """All three seeded devices come back."""
    response = client.get("/devices")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 3


def test_list_devices_is_ordered_by_id():
    """
    Ordering is part of the behaviour, so it gets its own test.

    Not pedantry: an unstable order is exactly the kind of thing that makes a
    test pass a hundred times and fail on the hundred-and-first.
    """
    body = client.get("/devices").json()
    assert [device["id"] for device in body] == [1, 2, 3]


def test_get_device_returns_the_right_device():
    """Fetching by id returns that device, with every declared field."""
    response = client.get("/devices/1")
    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "name": "edge-router-01",
        "status": "online",
    }


def test_get_unknown_device_returns_404():
    """A device that doesn't exist is a client error, not a server error."""
    response = client.get("/devices/999")
    assert response.status_code == 404


def test_get_device_with_non_integer_id_returns_422():
    """
    FastAPI validates the path parameter's type before our handler runs.

    We wrote no code for this case -- the `device_id: int` hint produced it.
    Worth an explicit test precisely because it's behaviour we get for free and
    could lose without noticing.
    """
    response = client.get("/devices/banana")
    assert response.status_code == 422


def test_openapi_spec_declares_the_device_schema():
    """
    Canary test on the generated contract.

    The spec is derived from the models, so this fails the moment someone
    changes Device's shape. From Day 5 the spec is pinned as a committed file
    and a CI check enforces that the app and the pinned copy agree; this test is
    the lightweight precursor to that.
    """
    spec = client.get("/openapi.json").json()
    device_schema = spec["components"]["schemas"]["Device"]

    assert set(device_schema["required"]) == {"id", "name", "status"}
    assert device_schema["properties"]["id"]["type"] == "integer"
    assert device_schema["properties"]["name"]["type"] == "string"


def test_openapi_spec_constrains_status_to_three_values():
    """
    The enum constraint must survive into the spec.

    This is the constraint that gives the Day 8 validator something real to
    check, and that the `bad_enum` bug mode will violate on Day 6. If it ever
    stopped appearing in the spec, a whole test would quietly become vacuous --
    so it gets asserted here.
    """
    spec = client.get("/openapi.json").json()
    status_schema = spec["components"]["schemas"]["DeviceStatus"]
    assert set(status_schema["enum"]) == {"online", "offline", "degraded"}