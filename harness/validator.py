"""
The contract validator -- the oracle.

Given a response and the pinned contract, decide whether the service kept its
promise, and if not, say exactly how.

This is the file that makes the project a CONFORMANCE harness rather than a
collection of assertions. In test_api.py the oracle is a hard-coded expected
value, written by hand, one per behaviour. Here the oracle is a document, and one
validator checks every endpoint -- which is why it keeps working as the API grows
and why it catches breakage nobody thought to write a test for.

Everything here is hand-written on purpose. Understanding response validation at
this level of detail is the point; schemathesis arrives on Day 10 to do a
different job (generating adversarial inputs), not to replace this.

Today: status declaration, content type, path matching, $ref resolution.
Day 9: validating the body against the resolved schema.
"""

import re
from typing import Any

from harness.transport import HttpResponse
from harness.violations import Violation, ViolationKind

# A path parameter stands for exactly one segment, so it must not match a "/".
_PARAM = re.compile(r"\{[^/{}]+\}")


class ContractLookupError(LookupError):
    """A $ref could not be resolved within the contract."""


# ---------------------------------------------------------------------------
# $ref resolution
# ---------------------------------------------------------------------------


def resolve_ref(contract: dict[str, Any], ref: str) -> dict[str, Any]:
    """
    Follow one local JSON pointer, e.g. "#/components/schemas/Device".

    Only local refs (starting with "#/") are supported. Remote refs -- pointers
    into other files or URLs -- are deliberately unsupported: a contract that
    reaches out to the network to be understood cannot be pinned, and pinning is
    the whole basis of this project.
    """
    if not ref.startswith("#/"):
        raise ContractLookupError(
            f"Only local refs are supported, got {ref!r}. A contract that must "
            "fetch part of itself from elsewhere cannot be pinned."
        )

    node: Any = contract
    for part in ref[2:].split("/"):
        # JSON Pointer escapes: "~1" means "/" and "~0" means "~".
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise ContractLookupError(f"{ref!r} does not resolve: no {part!r}")
        node = node[part]

    if not isinstance(node, dict):
        raise ContractLookupError(f"{ref!r} resolves to {type(node).__name__}, not an object")

    return node


def resolve_schema(
    contract: dict[str, Any], schema: Any, _stack: tuple[str, ...] = ()
) -> Any:
    """
    Recursively replace every $ref in a schema with what it points at.

    WHY RECURSIVE: refs nest. DevicePage -> items (array) -> items (element) ->
    $ref -> Device -> properties.status -> $ref -> DeviceStatus. Following one
    pointer is not enough; the whole tree has to be walked.

    TWO DETAILS THAT SEPARATE THIS FROM A NAIVE RESOLVER:

    1. Siblings alongside $ref are preserved. OpenAPI 3.1 allows keys next to a
       $ref -- our own spec has a `description` beside the status ref. Replacing
       the object wholesale would silently delete them. We resolve the target
       first, then lay the local keys on top so local values win.

    2. Cycles terminate. A schema may reference itself (a Node containing child
       Nodes is the classic case). Naive recursion follows that forever and the
       harness hangs with no message -- the worst failure mode for a test tool,
       because a hang looks like a slow test rather than a bug. `_stack` records
       the refs on the current path and stops when one repeats.
    """
    if isinstance(schema, list):
        return [resolve_schema(contract, item, _stack) for item in schema]

    if not isinstance(schema, dict):
        return schema

    if "$ref" in schema:
        ref = schema["$ref"]

        if ref in _stack:
            # Cycle. Return the pointer unresolved and flag it, so downstream
            # code can see what happened instead of receiving silence.
            return {"$ref": ref, "x-circular": True}

        target = resolve_schema(contract, resolve_ref(contract, ref), _stack + (ref,))
        siblings = {key: value for key, value in schema.items() if key != "$ref"}
        # Local keys win: they are the more specific statement.
        return {**target, **siblings}

    return {key: resolve_schema(contract, value, _stack) for key, value in schema.items()}


# ---------------------------------------------------------------------------
# Path matching
# ---------------------------------------------------------------------------


def _template_to_regex(template: str) -> re.Pattern[str]:
    """
    Turn "/devices/{device_id}" into a regex matching "/devices/1".

    `[^/]+` -- one or more non-slash characters -- because a path parameter
    stands for exactly ONE segment. Using `.+` instead would let
    "/devices/{device_id}" match "/devices/1/status", which would validate the
    PATCH endpoint's responses against the GET endpoint's schema and report
    violations that are pure fiction.
    """
    escaped = re.escape(template)
    # re.escape() escapes the braces, so match the escaped form.
    pattern = re.sub(r"\\\{[^/]+?\\\}", r"[^/]+", escaped)
    return re.compile(f"^{pattern}$")


def _specificity(template: str) -> int:
    """
    How many literal (non-parameter) segments a template has.

    Used to break ties -- see match_path_template.
    """
    return sum(1 for segment in template.strip("/").split("/") if not _PARAM.fullmatch(segment))


def match_path_template(contract: dict[str, Any], concrete_path: str) -> str | None:
    """
    Find which declared template a real request path belongs to.

    THE AMBIGUITY, and it is one you have already met from the other side:

        /devices/search  matches  /devices/search        (literal)
        /devices/search  matches  /devices/{device_id}   (parametrized)

    Both are legitimate matches. Choosing wrong means validating a search
    response against the single-device schema and reporting confident nonsense.

    The rule is the same one as Day 4's route ordering: SPECIFIC BEATS
    PARAMETRIZED. There it was registration order on the server; here it is
    template selection on the client. Same ambiguity, opposite side of the wire
    -- which is a good sign it is a real property of URL design rather than a
    framework quirk.

    Implemented by scoring literal segments: /devices/search scores 2,
    /devices/{device_id} scores 1.
    """
    candidates = [
        template
        for template in contract.get("paths", {})
        if _template_to_regex(template).match(concrete_path)
    ]

    if not candidates:
        return None

    return max(candidates, key=_specificity)


# ---------------------------------------------------------------------------
# Reading the contract
# ---------------------------------------------------------------------------


def find_operation(
    contract: dict[str, Any], method: str, path_template: str
) -> dict[str, Any] | None:
    """The operation object for one (path, method), or None if undeclared."""
    return contract.get("paths", {}).get(path_template, {}).get(method.lower())


def declared_statuses(
    contract: dict[str, Any], method: str, path_template: str
) -> set[str]:
    """Every status code this operation promises. Strings, as OpenAPI stores them."""
    operation = find_operation(contract, method, path_template)
    if operation is None:
        return set()
    return set(operation.get("responses", {}))


def response_schema(
    contract: dict[str, Any],
    method: str,
    path_template: str,
    status_code: int,
    media_type: str = "application/json",
) -> dict[str, Any] | None:
    """
    The fully-resolved body schema for one (path, method, status), or None.

    None means the contract declares no body for this response -- a 204, for
    instance. That is a promise in itself ("there is no body"), not an absence of
    information, and check_response enforces it.

    Day 9 feeds the returned schema to jsonschema.
    """
    operation = find_operation(contract, method, path_template)
    if operation is None:
        return None

    declared = operation.get("responses", {}).get(str(status_code))
    if declared is None:
        return None

    content = declared.get("content")
    if not content or media_type not in content:
        return None

    schema = content[media_type].get("schema")
    if schema is None:
        return None

    return resolve_schema(contract, schema)


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


def check_response(
    contract: dict[str, Any],
    response: HttpResponse,
    path_template: str | None = None,
) -> list[Violation]:
    """
    Check one response against the contract.

    Returns a LIST because a single response can break the contract several ways
    at once, and reporting only the first would hide the rest. An empty list
    means conformant.

    `path_template` can be passed explicitly; otherwise it is derived from the
    response's own request path. Deriving it is possible only because Day 7's
    HttpResponse carries `request_method` and `request_path` -- a small decision
    then that removes an argument from every call site now.

    Today: status declared, content type, unexpected body.
    Day 9 adds: body against the resolved schema.
    """
    violations: list[Violation] = []

    method = response.request_method or "GET"
    template = path_template or match_path_template(contract, response.request_path)

    # --- Is this endpoint in the contract at all? --------------------------
    if template is None or find_operation(contract, method, template) is None:
        return [
            Violation(
                kind=ViolationKind.UNKNOWN_OPERATION,
                message=(
                    f"The contract declares no operation for "
                    f"{method} {response.request_path}"
                ),
                expected="a declared path + method",
                actual=f"{method} {response.request_path}",
                location="request",
            )
        ]

    operation = find_operation(contract, method, template)
    status = str(response.status_code)
    declared = set(operation.get("responses", {}))

    # --- Check 1: was this status code ever promised? ----------------------
    if status not in declared:
        # Return early and deliberately. Once the status is undeclared there is
        # no declared response object to check the body or content type
        # against, so every further check would be measuring against nothing.
        # One precise violation beats a cascade of derived noise -- the
        # classifier and the human both benefit.
        return [
            Violation(
                kind=ViolationKind.UNDECLARED_STATUS,
                message=(
                    f"{method} {template} returned {status}, which the contract "
                    f"never declares"
                ),
                expected=", ".join(sorted(declared)) or "(none declared)",
                actual=status,
                location="response.status",
            )
        ]

    declared_response = operation["responses"][status]
    content = declared_response.get("content")
    body_is_empty = response.text.strip() == ""

    # --- Check 2: content type, or the absence of a body -------------------
    if not content:
        # No `content` key means the contract promises NO BODY -- a 204, for
        # instance. This is the check that catches a 204 carrying a serialized
        # "null", the protocol violation flagged back on Day 4.
        if not body_is_empty:
            violations.append(
                Violation(
                    kind=ViolationKind.UNEXPECTED_BODY,
                    message=(
                        f"{method} {template} returned {status} with a body, but "
                        f"the contract declares no content for that status"
                    ),
                    expected="empty body",
                    actual=f"{len(response.text)} bytes",
                    location="response.body",
                )
            )
    else:
        declared_types = set(content)
        actual_type = response.content_type

        if actual_type not in declared_types:
            violations.append(
                Violation(
                    kind=ViolationKind.WRONG_CONTENT_TYPE,
                    message=(
                        f"{method} {template} returned {status} as "
                        f"{actual_type or '(none)'}, which is not declared"
                    ),
                    expected=", ".join(sorted(declared_types)),
                    actual=actual_type or "(none)",
                    location="response.headers.content-type",
                )
            )

    return violations