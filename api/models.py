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