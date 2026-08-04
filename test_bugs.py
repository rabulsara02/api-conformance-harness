"""
Tests for the seeded bug modes.

Two jobs:

  1. Every mode produces the violation it claims to. If a seeded bug did not
     actually break anything, the Day 14 accuracy figure would be measured
     against fiction.
  2. Turning bugs on never changes the CONTRACT -- only the behaviour. That
     invariant is what makes the violations detectable at all.
"""

import pytest
from fastapi.testclient import TestClient

from api import store
from api.bugs import BugMode, current_mode, set_mode
from api.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_slate():
    """
    Reset store AND bug mode around every test.

    The mode reset is the important half: without it, one test's seeded bug
    leaks into every test that runs afterwards -- textbook test-order
    dependence, and a bug in the tool built to detect exactly that.

    Resetting AFTER as well as before (via yield) means a test that fails
    partway through still cannot poison its neighbours.
    """
    store.reset()
    set_mode(BugMode.NONE)
    yield
    store.reset()
    set_mode(BugMode.NONE)


def test_default_mode_is_healthy():
    assert current_mode() is BugMode.NONE


def test_healthy_mode_returns_a_complete_device():
    """The baseline every other test in this file is measured against."""
    body = client.get("/devices/1").json()
    assert body == {"id": 1, "name": "edge-router-01", "status": "online"}


def test_missing_field_drops_a_required_field():
    """`name` is in the spec's `required` list, so its absence is a violation."""
    set_mode(BugMode.MISSING_FIELD)
    body = client.get("/devices/1").json()
    assert "name" not in body
    assert body["id"] == 1


def test_wrong_type_returns_a_string_id():
    """The spec declares `id` as an integer."""
    set_mode(BugMode.WRONG_TYPE)
    body = client.get("/devices/1").json()
    assert body["id"] == "1"
    assert isinstance(body["id"], str)


def test_bad_enum_returns_an_undeclared_status_value():
    """Not one of online / offline / degraded."""
    set_mode(BugMode.BAD_ENUM)
    body = client.get("/devices/1").json()
    assert body["status"] == "exploded"


def test_wrong_status_returns_200_instead_of_201():
    """POST /devices declares 201, 409, 422. A 200 is undeclared."""
    set_mode(BugMode.WRONG_STATUS)
    response = client.post("/devices", json={"name": "new-device"})
    assert response.status_code == 200


def test_undeclared_500_returns_a_status_no_endpoint_declares():
    set_mode(BugMode.UNDECLARED_500)
    assert client.get("/devices/1").status_code == 500


def test_off_by_one_page_returns_one_item_too_many():
    """
    The semantic violation.

    Note what this test has to assert on: a COUNT, compared against the request.
    There is no field to check and no type to compare -- the schema is entirely
    satisfied. This is the concrete reason the harness needs declarative test
    cases (Day 9) in addition to schema validation (Day 8).
    """
    set_mode(BugMode.OFF_BY_ONE_PAGE)
    body = client.get("/devices?limit=2&offset=0").json()
    assert len(body["items"]) == 3
    assert body["limit"] == 2


def test_off_by_one_page_still_satisfies_the_declared_schema():
    """
    Proves the point above rather than merely asserting it.

    Every item still has every required field with the right type, so a pure
    schema check passes while the response is plainly wrong.
    """
    set_mode(BugMode.OFF_BY_ONE_PAGE)
    body = client.get("/devices?limit=2&offset=0").json()

    assert set(body) == {"items", "total", "limit", "offset"}
    for item in body["items"]:
        assert set(item) == {"id", "name", "status"}
        assert isinstance(item["id"], int)
        assert isinstance(item["name"], str)
        assert item["status"] in {"online", "offline", "degraded"}


def test_bugs_target_only_their_own_endpoint():
    """
    A mode aimed at /devices/{id} must leave /health alone.

    Overlapping blast radii would make the Day 14 accuracy figure ambiguous:
    a failure could have more than one correct explanation.
    """
    set_mode(BugMode.MISSING_FIELD)
    assert client.get("/health").json() == {"status": "ok"}
    assert len(client.get("/devices").json()["items"]) == 3


def test_delete_still_returns_an_empty_204_under_a_bug_mode():
    """
    The middleware must not put a body on a 204.

    It rebuilds every response, so a careless implementation would serialize
    something into the empty one and commit a protocol violation we never asked
    for -- an unlabelled defect, which is worse than a labelled one.
    """
    set_mode(BugMode.MISSING_FIELD)
    response = client.delete("/devices/1")
    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.parametrize("mode", list(BugMode))
def test_no_bug_mode_changes_the_published_contract(mode):
    """
    THE INVARIANT: bugs change behaviour, never the contract.

    If enabling a bug also moved the spec, the violation would be undetectable —
    the promise would have moved along with the behaviour. Parametrized over
    every mode so a future one cannot break this silently.
    """
    set_mode(BugMode.NONE)
    clean = client.get("/openapi.json").json()

    set_mode(mode)
    assert client.get("/openapi.json").json() == clean


def test_unknown_mode_name_is_rejected():
    """
    A typo must fail loudly.

    Silently defaulting to healthy would mean running a clean service while
    believing a bug was active -- and the Day 14 accuracy figure would come out
    too good, which is the most dangerous direction for a number to be wrong in.
    """
    import os

    from api.bugs import ENV_VAR, load_mode_from_env

    original = os.environ.get(ENV_VAR)
    os.environ[ENV_VAR] = "definitely-not-a-mode"
    try:
        with pytest.raises(ValueError, match="Unknown BUG_MODE"):
            load_mode_from_env()
    finally:
        if original is None:
            os.environ.pop(ENV_VAR, None)
        else:
            os.environ[ENV_VAR] = original