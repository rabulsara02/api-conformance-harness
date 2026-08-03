"""
Pydantic models — the shapes of data this API accepts and returns.

These classes are the single source of truth for the API's contract. FastAPI
reads them to validate incoming requests, to serialize outgoing responses, and
to generate the OpenAPI specification that the harness will later test against.

Every constraint declared here (a required field, a type, an enum, a length
limit) becomes a checkable clause in that contract. Constraints we *don't*
declare are constraints the harness can never verify -- so the modelling here is
driven by testability, not just by correctness.
"""

from enum import Enum

from pydantic import BaseModel, Field


class DeviceStatus(str, Enum):
    """
    The only values a device's `status` field may take.

    Inheriting from `str` as well as `Enum` makes members behave like plain
    strings: they compare equal to "online" and serialize to JSON as "online"
    rather than as something enum-shaped.

    Why an enum instead of a plain string field: FastAPI turns this into an
    `enum` constraint in the OpenAPI spec, so the Day 8 contract validator can
    assert that a returned status is one of exactly these three. A free-form
    string would be unfalsifiable -- no response could ever violate it.
    """

    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"


class Device(BaseModel):
    """
    One device in the registry.

    `Field(...)` -- the literal Ellipsis as the first argument -- marks a field
    as REQUIRED with no default. That requirement shows up in the spec's
    `required` list, which is exactly the kind of promise the harness checks
    (and which the `missing_field` bug mode will deliberately break on Day 6).
    """

    id: int = Field(
        ...,
        description="Unique identifier for the device, assigned by the server.",
        examples=[1],
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Human-readable label for the device.",
        examples=["edge-router-01"],
    )
    status: DeviceStatus = Field(
        ...,
        description="Current operational state of the device.",
    )


class DeviceCreate(BaseModel):
    """
    The body a client sends to CREATE a device.

    Deliberately has no `id`: identifiers are assigned by the server. Modelling
    this as a separate class rather than reusing `Device` and ignoring the id is
    a contract decision, not a style one. "We ignore id if you send it" is a rule
    that exists only in the implementation; a separate request schema puts the
    rule *in the specification*, where a client can read it and a contract test
    can enforce it.

    `status` has a default, so it is optional in the request. That default also
    appears in the spec, so clients know what they get if they omit it.
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Human-readable label for the device. Must be unique.",
        examples=["edge-router-03"],
    )
    status: DeviceStatus = Field(
        default=DeviceStatus.OFFLINE,
        description="Initial state. Defaults to 'offline' if omitted.",
    )


class DeviceUpdate(BaseModel):
    """
    The body a client sends to REPLACE a device (PUT).

    Every field is required, because PUT means "make the resource look exactly
    like this" -- omitting a field is a request to blank it, not to leave it
    alone. Partial updates are what PATCH is for (Day 5).
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Human-readable label for the device.",
    )
    status: DeviceStatus = Field(
        ...,
        description="Operational state to set.",
    )


class ErrorDetail(BaseModel):
    """The inner object of an error response."""

    code: str = Field(
        ...,
        description="Stable, machine-readable error identifier.",
        examples=["not_found"],
    )
    message: str = Field(
        ...,
        description="Human-readable explanation. May change; do not parse it.",
        examples=["No device with id 999"],
    )


class ErrorResponse(BaseModel):
    """
    The ONE shape every error response from this API takes.

    FastAPI's default error body puts a `detail` key at the top level whose value
    is sometimes a string and sometimes a list of objects. No single useful
    schema describes that, which means error responses cannot be contract-tested
    -- and for most APIs, error behaviour is a large share of the behaviour that
    matters.

    Wrapping the payload in a named `error` object (rather than putting `code`
    and `message` at the top level) leaves room to add fields later -- a
    `request_id`, a list of field-level problems -- without colliding with any
    successful response shape.
    """

    error: ErrorDetail


class StatusUpdate(BaseModel):
    """
    The body for PATCH /devices/{id}/status.

    A single field, because PATCH modifies part of a resource rather than
    replacing it. A dedicated model rather than reusing DeviceUpdate makes the
    spec say precisely what this endpoint accepts -- sending a `name` here is a
    declared violation, not an undocumented no-op.
    """

    status: DeviceStatus = Field(
        ...,
        description="The state to transition the device into.",
    )


class DevicePage(BaseModel):
    """
    One page of devices.

    A bare JSON array cannot carry `total`, so a client has no way to know
    whether more pages exist. Wrapping the list in an envelope solves that.

    `limit` and `offset` are echoed back deliberately: the server may clamp what
    was requested, and the response should state what actually happened rather
    than leaving the client to assume its request was honoured verbatim.
    """

    items: list[Device] = Field(..., description="The devices on this page.")
    total: int = Field(
        ...,
        description="Total devices matching the query, ignoring pagination.",
        examples=[3],
    )
    limit: int = Field(..., description="Maximum items per page, as applied.")
    offset: int = Field(..., description="Number of items skipped, as applied.")