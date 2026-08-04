"""
Unit tests for the contract validator.

No network, no server: responses are FABRICATED. That is deliberate.

  * Fast and deterministic -- no sockets, no timing, no fixtures to boot.
  * We can construct the impossible. To test the content-type check we need a
    response claiming text/html; our service will never send one, and building
    it by hand takes a line.
  * It isolates the unit. A failure here means the validator is wrong -- not the
    network, the service, or the fixture.

The integration tests (validator + live service + seeded bugs) arrive on Day 9,
once body validation exists. Both layers, each answering what it is good at --
the same split as TestClient versus the harness.
"""

import pytest

from harness.transport import HttpResponse
from harness.validator import (
    ContractLookupError,
    check_response,
    declared_statuses,
    match_path_template,
    resolve_ref,
    resolve_schema,
    response_schema,
)
from harness.violations import ViolationKind


def make_response(
    status_code: int = 200,
    body: str = "{}",
    content_type: str = "application/json",
    method: str = "GET",
    path: str = "/health",
) -> HttpResponse:
    """Build a response by hand. The point of this file."""
    headers = {"content-type": content_type} if content_type else {}
    return HttpResponse(
        status_code=status_code,
        headers=headers,
        text=body,
        elapsed_ms=1.0,
        request_method=method,
        request_path=path,
    )


# --- $ref resolution -------------------------------------------------------


def test_resolve_ref_follows_a_local_pointer(contract: dict):
    device = resolve_ref(contract, "#/components/schemas/Device")
    assert device["type"] == "object"
    assert set(device["required"]) == {"id", "name", "status"}


def test_resolve_ref_rejects_remote_pointers(contract: dict):
    """
    Remote refs are refused on purpose.

    A contract that must fetch part of itself over the network cannot be pinned,
    and pinning is the basis of the whole project.
    """
    with pytest.raises(ContractLookupError, match="local refs"):
        resolve_ref(contract, "https://example.com/schema.json#/Device")


def test_resolve_ref_reports_a_dangling_pointer(contract: dict):
    with pytest.raises(ContractLookupError, match="does not resolve"):
        resolve_ref(contract, "#/components/schemas/NoSuchThing")


def test_resolve_schema_inlines_a_nested_enum(contract: dict):
    """
    THE REASON THIS DAY EXISTS.

    In the raw contract, Device.properties.status is only a POINTER -- the
    allowed values live in a separate schema object. A validator reading the
    property directly learns nothing about which values are legal. After
    resolution the enum is right there.
    """
    raw = contract["components"]["schemas"]["Device"]["properties"]["status"]
    assert "$ref" in raw
    assert "enum" not in raw

    resolved = resolve_schema(contract, contract["components"]["schemas"]["Device"])
    assert set(resolved["properties"]["status"]["enum"]) == {
        "online",
        "offline",
        "degraded",
    }


def test_resolve_schema_preserves_keys_beside_a_ref(contract: dict):
    """
    OpenAPI 3.1 allows siblings next to $ref, and our own spec has one.

    A resolver that replaced the object wholesale would silently delete the
    description. Local keys must survive and win.
    """
    resolved = resolve_schema(contract, contract["components"]["schemas"]["Device"])
    status = resolved["properties"]["status"]
    assert status["description"] == "Current operational state of the device."
    assert "enum" in status  # and the target's content is still there


def test_resolve_schema_recurses_through_arrays(contract: dict):
    """DevicePage -> items (array) -> element -> Device -> status -> enum."""
    resolved = resolve_schema(contract, contract["components"]["schemas"]["DevicePage"])
    element = resolved["properties"]["items"]["items"]
    assert set(element["required"]) == {"id", "name", "status"}
    assert "enum" in element["properties"]["status"]


def test_resolve_schema_terminates_on_a_cycle():
    """
    A self-referencing schema must not hang the harness.

    Our contract has no cycles. The guard exists anyway: a validator that hangs
    on a legal document is broken, and a hang is the worst failure mode for a
    test tool because it looks like slowness rather than a bug. Better to find
    that here than while pointing the harness at somebody else's API.
    """
    cyclic = {
        "components": {
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "children": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Node"},
                        }
                    },
                }
            }
        }
    }
    # Start from the REF, not the dereferenced object: that puts Node on the
    # resolution stack immediately, so the self-reference one level down is the
    # one that trips the guard. Passing the object itself would resolve one
    # extra level first -- correct, but it makes the assertion harder to read.
    resolved = resolve_schema(cyclic, {"$ref": "#/components/schemas/Node"})
    inner = resolved["properties"]["children"]["items"]
    assert inner["x-circular"] is True
    assert inner["$ref"] == "#/components/schemas/Node"


# --- path matching ---------------------------------------------------------


def test_match_path_template_handles_a_parameter(contract: dict):
    assert match_path_template(contract, "/devices/1") == "/devices/{device_id}"
    assert match_path_template(contract, "/devices/99999") == "/devices/{device_id}"


def test_match_path_template_prefers_the_specific_template(contract: dict):
    """
    THE AMBIGUITY, and the same rule as Day 4's route ordering.

    /devices/search matches both the literal template and /devices/{device_id}.
    Choosing wrong would validate every search response against the
    single-device schema and report violations that are pure fiction.
    """
    assert match_path_template(contract, "/devices/search") == "/devices/search"


def test_match_path_template_does_not_span_segments(contract: dict):
    """
    A path parameter is ONE segment.

    If the regex used `.+` instead of `[^/]+`, /devices/{device_id} would
    swallow /devices/1/status and PATCH responses would be checked against the
    GET schema.
    """
    assert match_path_template(contract, "/devices/1/status") == "/devices/{device_id}/status"


def test_match_path_template_returns_none_for_an_unknown_path(contract: dict):
    assert match_path_template(contract, "/nope") is None


# --- reading the contract --------------------------------------------------


def test_declared_statuses_matches_the_contract(contract: dict):
    assert declared_statuses(contract, "GET", "/devices/{device_id}") == {
        "200",
        "404",
        "422",
    }
    assert declared_statuses(contract, "POST", "/devices") == {"201", "409", "422"}


def test_response_schema_returns_a_resolved_schema(contract: dict):
    schema = response_schema(contract, "GET", "/devices/{device_id}", 200)
    assert set(schema["required"]) == {"id", "name", "status"}
    assert "enum" in schema["properties"]["status"]  # resolved, not a pointer


def test_response_schema_is_none_when_no_body_is_declared(contract: dict):
    """204 declares no `content`. That is a promise, not missing information."""
    assert response_schema(contract, "DELETE", "/devices/{device_id}", 204) is None


# --- check_response --------------------------------------------------------


def test_a_conformant_response_produces_no_violations(contract: dict):
    response = make_response(
        status_code=200,
        body='{"id": 1, "name": "edge-router-01", "status": "online"}',
        method="GET",
        path="/devices/1",
    )
    assert check_response(contract, response) == []


def test_undeclared_status_is_reported(contract: dict):
    """Catches the `undeclared_500` seeded bug."""
    response = make_response(
        status_code=500, body='{"error": {}}', method="GET", path="/devices/1"
    )
    violations = check_response(contract, response)

    assert len(violations) == 1
    assert violations[0].kind is ViolationKind.UNDECLARED_STATUS
    assert violations[0].actual == "500"
    assert "200" in violations[0].expected


def test_wrong_status_on_create_is_reported(contract: dict):
    """Catches the `wrong_status` seeded bug: 200 where 201 was declared."""
    response = make_response(
        status_code=200, body="{}", method="POST", path="/devices"
    )
    violations = check_response(contract, response)

    assert len(violations) == 1
    assert violations[0].kind is ViolationKind.UNDECLARED_STATUS


def test_wrong_content_type_is_reported(contract: dict):
    """
    The impossible response, fabricated.

    Our service will never return HTML. A proxy error page or a gateway timeout
    absolutely will, and it is one of the most common real-world violations --
    which is why content-type checking turns "the JSON didn't parse" from a
    crash into a diagnosis.
    """
    response = make_response(
        status_code=200,
        body="<html>502 Bad Gateway</html>",
        content_type="text/html",
        method="GET",
        path="/devices/1",
    )
    violations = check_response(contract, response)

    assert len(violations) == 1
    assert violations[0].kind is ViolationKind.WRONG_CONTENT_TYPE
    assert violations[0].actual == "text/html"


def test_a_body_on_a_204_is_reported(contract: dict):
    """
    The protocol violation Day 4 warned about, now enforced rather than trusted.

    204 declares no `content`, so the promise is "there is no body". A
    serialized `null` breaks it.
    """
    response = make_response(
        status_code=204, body="null", method="DELETE", path="/devices/1"
    )
    violations = check_response(contract, response)

    assert len(violations) == 1
    assert violations[0].kind is ViolationKind.UNEXPECTED_BODY


def test_an_empty_204_is_conformant(contract: dict):
    response = make_response(
        status_code=204, body="", content_type="", method="DELETE", path="/devices/1"
    )
    assert check_response(contract, response) == []


def test_unknown_operation_is_reported(contract: dict):
    response = make_response(status_code=200, method="GET", path="/not-a-real-path")
    violations = check_response(contract, response)

    assert len(violations) == 1
    assert violations[0].kind is ViolationKind.UNKNOWN_OPERATION


def test_wrong_method_on_a_known_path_is_reported(contract: dict):
    """The path exists; this method on it does not."""
    response = make_response(status_code=200, method="DELETE", path="/health")
    violations = check_response(contract, response)

    assert violations[0].kind is ViolationKind.UNKNOWN_OPERATION


def test_undeclared_status_short_circuits_further_checks(contract: dict):
    """
    Once the status is undeclared, stop.

    There is no declared response object left to check content type against, so
    continuing would measure against nothing and emit derived noise. One precise
    violation is worth more to both the classifier and the human than a cascade.
    """
    response = make_response(
        status_code=500,
        body="<html>oh no</html>",
        content_type="text/html",
        method="GET",
        path="/devices/1",
    )
    violations = check_response(contract, response)

    assert len(violations) == 1
    assert violations[0].kind is ViolationKind.UNDECLARED_STATUS


def test_violation_renders_readably(contract: dict):
    """A violation has to be legible in a terminal, not only machine-readable."""
    response = make_response(
        status_code=500, body="{}", method="GET", path="/devices/1"
    )
    text = str(check_response(contract, response)[0])

    assert "undeclared_status" in text
    assert "response.status" in text