"""
What the validator reports when the contract is broken.

WHY A STRUCTURED TYPE RATHER THAN A BOOL OR A STRING

Four different consumers need different things out of a failure:

  * the classifier (Day 14) branches on the KIND of violation;
  * the HTML report (Day 15) shows `expected` beside `actual`;
  * a human needs a sentence;
  * the flake detector (Day 13) needs a stable identity, to tell "the same
    failure again" from "a different one".

A bare bool throws all of that away and forces every later stage to re-derive
what the validator already knew. This is a cheap decision now that pays off
three times in the next week.
"""

from dataclasses import dataclass
from enum import Enum


class ViolationKind(str, Enum):
    """
    The categories of contract breach the validator can report.

    Kept coarse on purpose. These are the distinctions the CLASSIFIER needs to
    make a decision; finer detail belongs in the message. Too many kinds and the
    Day 14 logic becomes a lookup table nobody can reason about.

    Day 9 adds the SCHEMA_* kinds once body validation exists.
    """

    # The contract does not describe this endpoint at all.
    UNKNOWN_OPERATION = "unknown_operation"
    # The endpoint exists, but never promised this status code.
    UNDECLARED_STATUS = "undeclared_status"
    # Right status, wrong media type.
    WRONG_CONTENT_TYPE = "wrong_content_type"
    # The contract declared no body for this status, and one arrived anyway.
    UNEXPECTED_BODY = "unexpected_body"
    # A $ref in the contract points at something that isn't there.
    UNRESOLVABLE_REF = "unresolvable_ref"


@dataclass(frozen=True)
class Violation:
    """
    One specific way a response failed to honour the contract.

    Frozen so a violation cannot be edited after the fact -- a report should
    describe what happened, and a mutable record invites "fixing" it in place.

    location:
        Where the problem is, in dotted notation: "response.status",
        "response.headers.content-type", "response.body.items.0.status". Used by
        the HTML report to point at the exact spot, and it is what makes a
        failure message actionable rather than merely accurate.
    """

    kind: ViolationKind
    message: str
    expected: str = ""
    actual: str = ""
    location: str = ""

    def __str__(self) -> str:
        base = f"[{self.kind.value}] {self.message}"
        if self.expected or self.actual:
            base += f" (expected {self.expected!r}, got {self.actual!r})"
        if self.location:
            base += f" at {self.location}"
        return base