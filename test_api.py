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
    """All three seeded devices come back, now inside a page envelope."""
    response = client.get("/devices")
    assert response.status_code == 200

    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


def test_list_devices_is_ordered_by_id():
    """
    Ordering is part of the behaviour, so it gets its own test.

    Not pedantry: an unstable order is exactly the kind of thing that makes a
    test pass a hundred times and fail on the hundred-and-first.
    """
    body = client.get("/devices").json()
    assert [device["id"] for device in body["items"]] == [1, 2, 3]


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


# ---------------------------------------------------------------------------
# Day 4 -- writes, search, and the error contract
# ---------------------------------------------------------------------------


def test_create_device_returns_201_and_location_header():
    """A successful create reports 201 and where the new resource lives."""
    response = client.post(
        "/devices", json={"name": "edge-router-03", "status": "online"}
    )
    assert response.status_code == 201
    assert response.headers["location"] == "/devices/4"

    body = response.json()
    assert body["id"] == 4
    assert body["name"] == "edge-router-03"
    assert body["status"] == "online"


def test_create_device_defaults_status_to_offline():
    """`status` is optional on create; the declared default applies."""
    response = client.post("/devices", json={"name": "sensor-gateway-02"})
    assert response.status_code == 201
    assert response.json()["status"] == "offline"


def test_create_device_with_duplicate_name_returns_409():
    """Well-formed but impossible: a conflict, not a validation error."""
    response = client.post("/devices", json={"name": "edge-router-01"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_create_device_rejects_client_supplied_id():
    """
    `id` is not part of DeviceCreate, so sending one is not silently honoured.

    The value is ignored rather than rejected (Pydantic's default), but the
    important part is that the server still assigns the id -- a client cannot
    overwrite device 1 by claiming to be it.
    """
    response = client.post("/devices", json={"id": 1, "name": "impostor"})
    assert response.status_code == 201
    assert response.json()["id"] != 1


def test_create_device_with_empty_name_returns_422():
    """`min_length=1` is enforced, and the failure uses our error shape."""
    response = client.post("/devices", json={"name": ""})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_replace_device_updates_every_field():
    """PUT overwrites the whole resource."""
    response = client.put(
        "/devices/1", json={"name": "renamed", "status": "degraded"}
    )
    assert response.status_code == 200
    assert response.json() == {"id": 1, "name": "renamed", "status": "degraded"}


def test_replace_device_is_idempotent():
    """
    Sending the same PUT twice lands in the same state.

    This is the property that makes it safe for the harness to retry a PUT after
    a timeout (Day 9). Asserting it here rather than assuming it is the point.
    """
    body = {"name": "renamed", "status": "degraded"}
    first = client.put("/devices/1", json=body)
    second = client.put("/devices/1", json=body)
    assert first.json() == second.json()


def test_replace_missing_device_returns_404():
    response = client.put(
        "/devices/999", json={"name": "ghost", "status": "offline"}
    )
    assert response.status_code == 404


def test_replace_device_requires_every_field():
    """PUT means "exactly this", so a partial body is invalid."""
    response = client.put("/devices/1", json={"name": "only-a-name"})
    assert response.status_code == 422


def test_delete_device_returns_204_with_no_body():
    """
    204 must carry no body.

    `response.content` is the raw bytes -- asserting it is empty catches the
    common bug of serializing None into the four bytes "null", which would be a
    protocol violation.
    """
    response = client.delete("/devices/1")
    assert response.status_code == 204
    assert response.content == b""


def test_deleted_device_is_really_gone():
    client.delete("/devices/1")
    assert client.get("/devices/1").status_code == 404


def test_delete_missing_device_returns_404():
    assert client.delete("/devices/999").status_code == 404


def test_created_ids_are_not_reused_after_delete():
    """
    Ids increase monotonically; a deleted id is never handed out again.

    Reuse would let a client holding a stale id silently address a different
    device. Monotonic ids turn that into an honest 404 instead.
    """
    created = client.post("/devices", json={"name": "temp"}).json()
    client.delete(f"/devices/{created['id']}")
    recreated = client.post("/devices", json={"name": "temp-again"}).json()
    assert recreated["id"] > created["id"]


def test_search_by_name_substring():
    body = client.get("/devices/search?name_contains=router").json()
    assert {d["name"] for d in body} == {"edge-router-01", "edge-router-02"}


def test_search_by_status():
    body = client.get("/devices/search?status=degraded").json()
    assert [d["id"] for d in body] == [3]


def test_search_filters_combine_with_and():
    body = client.get("/devices/search?name_contains=edge&status=online").json()
    assert [d["id"] for d in body] == [1]


def test_search_with_no_filters_returns_everything():
    assert len(client.get("/devices/search").json()) == 3


def test_search_route_is_not_shadowed_by_the_id_route():
    """
    Regression guard for route ordering.

    If /devices/{device_id} were ever registered before /devices/search, this
    request would match it, fail to parse "search" as an int, and return 422.
    The bug is invisible in code review and obvious here.
    """
    assert client.get("/devices/search").status_code == 200


def test_search_with_invalid_status_returns_422():
    """The enum constraint applies to query parameters too."""
    response = client.get("/devices/search?status=banana")
    assert response.status_code == 422


# --- The error contract itself -------------------------------------------


@pytest.mark.parametrize(
    "method,path,expected_status",
    [
        ("get", "/devices/999", 404),
        ("get", "/devices/banana", 422),
        ("get", "/no-such-path", 404),
        ("delete", "/devices/999", 404),
    ],
)
def test_every_error_uses_the_declared_shape(method, path, expected_status):
    """
    One shape for every error, including ones the framework raises.

    /no-such-path is the interesting case: our code never raises it. It is
    caught because the handler is registered against Starlette's HTTPException,
    the parent of FastAPI's. Without that, this single response would disagree
    with every other error in the API -- exactly the inconsistency the contract
    validator is built to find.
    """
    response = getattr(client, method)(path)
    assert response.status_code == expected_status

    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert isinstance(body["error"]["code"], str)
    assert isinstance(body["error"]["message"], str)


def test_spec_declares_the_error_model():
    spec = client.get("/openapi.json").json()
    assert "ErrorResponse" in spec["components"]["schemas"]
    assert spec["components"]["schemas"]["ErrorDetail"]["required"] == [
        "code",
        "message",
    ]


def test_spec_declares_every_status_each_endpoint_can_return():
    """
    The spec must list every status code, or the Day 8 validator's first check
    ("was this status declared?") produces false positives on correct behaviour.
    """
    spec = client.get("/openapi.json").json()

    expected = {
        ("/devices", "get"): {"200", "422"},
        ("/devices", "post"): {"201", "400", "409", "422"},
        ("/devices/search", "get"): {"200", "422"},
        ("/devices/{device_id}", "get"): {"200", "404", "422"},
        ("/devices/{device_id}", "put"): {"200", "400", "404", "422"},
        ("/devices/{device_id}", "delete"): {"204", "404", "422"},
        ("/devices/{device_id}/status", "patch"): {"200", "400", "404", "409", "422"},
        ("/health", "get"): {"200"},
    }

    for (path, method), codes in expected.items():
        declared = set(spec["paths"][path][method]["responses"])
        assert declared == codes, f"{method.upper()} {path} declares {declared}"


def test_spec_422_uses_our_error_model_not_fastapis_default():
    """
    Guard against the spec-drift trap from the Day 4 primer.

    FastAPI auto-declares 422 with its own HTTPValidationError schema. Because
    errors.py replaces the body with ErrorResponse, that automatic declaration
    would describe a shape the service no longer produces -- the document would
    lie about the service while every test still passed.

    This is the class of bug the whole project exists to detect, so it gets an
    explicit test.
    """
    spec = client.get("/openapi.json").json()
    schema = spec["paths"]["/devices/{device_id}"]["get"]["responses"]["422"][
        "content"
    ]["application/json"]["schema"]
    assert schema["$ref"].endswith("/ErrorResponse")


# ---------------------------------------------------------------------------
# Day 5 -- status state machine and pagination
# ---------------------------------------------------------------------------


def test_legal_transition_offline_to_online():
    """Device 2 starts offline; coming up is permitted."""
    response = client.patch("/devices/2/status", json={"status": "online"})
    assert response.status_code == 200
    assert response.json()["status"] == "online"


def test_illegal_transition_offline_to_degraded_returns_409():
    """
    A device that was never up cannot be 'degraded'.

    409, not 422: the request is perfectly well-formed, it is just impossible
    given the current state. Keeping those two codes distinct is what lets a
    client tell a bad request from a bad moment.
    """
    response = client.patch("/devices/2/status", json={"status": "degraded"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_transition_becomes_legal_via_an_intermediate_state():
    """offline -> degraded is illegal, but offline -> online -> degraded is fine."""
    assert client.patch("/devices/2/status", json={"status": "online"}).status_code == 200
    assert client.patch("/devices/2/status", json={"status": "degraded"}).status_code == 200


def test_status_patch_is_idempotent():
    """
    Setting a device to the state it is already in is a legal no-op.

    This is a deliberate design decision, not an accident. If no-ops were
    rejected, the same PATCH sent twice would return 200 then 409, and the
    harness could not safely retry it after a timeout (Day 9). Asserting it here
    keeps the property from being lost in a later refactor.
    """
    first = client.patch("/devices/1/status", json={"status": "online"})
    second = client.patch("/devices/1/status", json={"status": "online"})
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()


def test_status_patch_on_missing_device_returns_404():
    response = client.patch("/devices/999/status", json={"status": "online"})
    assert response.status_code == 404


def test_status_patch_rejects_unknown_status():
    """The enum constraint applies to request bodies too."""
    response = client.patch("/devices/1/status", json={"status": "banana"})
    assert response.status_code == 422


def test_status_patch_does_not_change_other_fields():
    """PATCH modifies one field; everything else survives untouched."""
    before = client.get("/devices/1").json()
    after = client.patch("/devices/1/status", json={"status": "degraded"}).json()
    assert after["id"] == before["id"]
    assert after["name"] == before["name"]
    assert after["status"] == "degraded"


def test_pagination_returns_a_page_and_a_total():
    body = client.get("/devices?limit=2&offset=0").json()
    assert [d["id"] for d in body["items"]] == [1, 2]
    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["offset"] == 0


def test_pagination_second_page():
    body = client.get("/devices?limit=2&offset=2").json()
    assert [d["id"] for d in body["items"]] == [3]
    assert body["total"] == 3


def test_pagination_beyond_the_end_returns_an_empty_page():
    """
    An out-of-range offset is an empty page, not an error.

    `total` still reports the real count, so a client that overshot can work out
    where it should have been. Least-surprising behaviour, and one fewer error
    path to declare in the contract.
    """
    body = client.get("/devices?limit=2&offset=99").json()
    assert body["items"] == []
    assert body["total"] == 3


def test_pagination_defaults_apply_when_omitted():
    body = client.get("/devices").json()
    assert body["limit"] == 50
    assert body["offset"] == 0


def test_pagination_rejects_out_of_range_limits():
    """
    `ge=1, le=100` are declared constraints, not just guards.

    They appear in the spec, which means the contract can be checked against
    them and the Day 10 fuzzer has real boundaries to probe.
    """
    assert client.get("/devices?limit=0").status_code == 422
    assert client.get("/devices?limit=101").status_code == 422
    assert client.get("/devices?offset=-1").status_code == 422


def test_spec_declares_pagination_constraints():
    """The bounds must survive into the contract, or they are untestable."""
    spec = client.get("/openapi.json").json()
    params = {
        p["name"]: p["schema"]
        for p in spec["paths"]["/devices"]["get"]["parameters"]
    }
    assert params["limit"]["minimum"] == 1
    assert params["limit"]["maximum"] == 100
    assert params["offset"]["minimum"] == 0