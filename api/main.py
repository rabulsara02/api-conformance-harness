"""
FastAPI application -- the service under test.

This is what the harness will interrogate for the rest of the project. It is
deliberately small: the goal is not an impressive API, it is one whose contract
can be checked precisely and whose bugs can be seeded on demand (Day 6).

Run locally with:

    uvicorn api.main:app --reload

`api.main:app` is a coordinate: module path, colon, variable name.

ROUTE ORDER MATTERS in this file -- see the note above /devices/search.
"""

from fastapi import FastAPI, HTTPException, Response, status

from api import store
from api.errors import register_error_handlers
from api.models import (
    Device,
    DeviceCreate,
    DeviceStatus,
    DeviceUpdate,
    ErrorResponse,
)

app = FastAPI(
    title="Device Registry",
    version="0.2.0",
    description=(
        "A small REST API used as the system under test for a contract-"
        "conformance and flaky-test-detection harness. The domain is "
        "arbitrary -- the domain does not matter, the contract does."
    ),
)

# Normalise every error response to the declared ErrorResponse shape.
register_error_handlers(app)


# Reused response declarations. Every status an endpoint can return must appear
# in the spec, because the harness's first check (Day 8) is "was this status code
# declared at all?" -- an undeclared 404 would be reported as a violation even
# though it is correct behaviour.
_NOT_FOUND = {
    404: {"model": ErrorResponse, "description": "No device with that id."}
}
_CONFLICT = {
    409: {"model": ErrorResponse, "description": "A device with that name exists."}
}
# FastAPI auto-declares 422 using its OWN HTTPValidationError schema. Since
# errors.py replaces the body with ErrorResponse, that automatic declaration
# would describe a shape this service no longer produces -- the spec would lie.
# Declaring 422 explicitly overrides it and keeps the document honest.
_VALIDATION = {
    422: {"model": ErrorResponse, "description": "Request failed validation."}
}


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """
    Liveness probe: is the process up and serving?

    Kept trivial on purpose. Compose and CI use it to answer "is this thing
    ready yet" (the started-vs-ready distinction from Day 2), so it must not
    depend on anything that could itself be broken.
    """
    return {"status": "ok"}


@app.get("/devices", response_model=list[Device], tags=["devices"])
def list_devices() -> list[Device]:
    """
    Return every device in the registry.

    `response_model=list[Device]` tells FastAPI two things: serialize the return
    value as a list of Devices, and declare that shape in the OpenAPI spec. The
    declaration is the half that matters here -- it is what the harness checks
    responses against.
    """
    return store.list_devices()


@app.post(
    "/devices",
    response_model=Device,
    status_code=status.HTTP_201_CREATED,
    responses={**_CONFLICT, **_VALIDATION},
    tags=["devices"],
)
def create_device(payload: DeviceCreate, response: Response) -> Device:
    """
    Create a device. The server assigns the id.

    Returns 201 (not 200) because a new resource came into existence, plus a
    `Location` header pointing at it -- that is how the client learns the
    assigned id without guessing.

    POST is neither safe nor idempotent: calling it twice creates two devices.
    That is why the harness must never blindly retry it (Day 9).
    """
    if store.find_by_name(payload.name) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A device named '{payload.name}' already exists",
        )

    device = store.create_device(payload.name, payload.status)

    # Declaring `response: Response` as a parameter lets us set headers on the
    # response FastAPI is building, while still returning a model normally.
    response.headers["Location"] = f"/devices/{device.id}"
    return device


# ---------------------------------------------------------------------------
# ROUTE ORDER: /devices/search MUST be registered before /devices/{device_id}.
#
# FastAPI matches routes in registration order. If the parametrized route came
# first, a request for /devices/search would match it, try to parse "search" as
# an int, and fail with 422 -- the search endpoint would be unreachable.
# Specific paths before parametrized ones, always.
# ---------------------------------------------------------------------------
@app.get(
    "/devices/search",
    response_model=list[Device],
    responses={**_VALIDATION},
    tags=["devices"],
)
def search_devices(
    name_contains: str | None = None,
    status: DeviceStatus | None = None,
) -> list[Device]:
    """
    Search devices by name substring and/or status.

    Both parameters are QUERY parameters, not path parameters: FastAPI infers
    that because their names do not appear in the route path and their types are
    simple. Both default to None, making them optional; passing neither returns
    every device.

    Example: /devices/search?name_contains=router&status=online
    """
    return store.search_devices(name_contains=name_contains, status=status)


@app.get(
    "/devices/{device_id}",
    response_model=Device,
    responses={**_NOT_FOUND, **_VALIDATION},
    tags=["devices"],
)
def get_device(device_id: int) -> Device:
    """
    Return one device by id.

    `device_id: int` is doing real work, not documentation: FastAPI converts the
    URL text "1" into the integer 1, and rejects /devices/banana with a 422
    before this function is ever called. Free validation, straight from the type
    hint.

    Raising HTTPException rather than returning an error value means the error
    path cannot be silently ignored, and FastAPI turns it into a proper HTTP
    response.
    """
    device = store.get_device(device_id)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No device with id {device_id}",
        )
    return device


@app.put(
    "/devices/{device_id}",
    response_model=Device,
    responses={**_NOT_FOUND, **_VALIDATION},
    tags=["devices"],
)
def replace_device(device_id: int, payload: DeviceUpdate) -> Device:
    """
    Replace a device wholesale.

    PUT means "make this resource look exactly like the body I sent" -- every
    field is required, and omitting one is a request to blank it, not to leave
    it alone. Partial modification is PATCH's job (Day 5).

    PUT is idempotent: sending the same body twice leaves the same final state,
    which is what makes it safe for the harness to retry after a timeout.
    """
    device = store.replace_device(device_id, payload.name, payload.status)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No device with id {device_id}",
        )
    return device


@app.delete(
    "/devices/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**_NOT_FOUND, **_VALIDATION},
    tags=["devices"],
)
def delete_device(device_id: int) -> Response:
    """
    Delete a device.

    Returns 204 No Content on success. A 204 response MUST NOT carry a body --
    that is why this returns a bare Response object rather than a value FastAPI
    would serialize into JSON. Returning None here would risk emitting the four
    bytes "null", which is a protocol violation and precisely the sort of thing
    a contract test should catch.

    DELETE is idempotent in effect: once the device is gone, deleting again
    changes nothing. We still answer 404 the second time, because the client
    asked us to delete something that does not exist and deserves to know.
    """
    if not store.delete_device(device_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No device with id {device_id}",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)