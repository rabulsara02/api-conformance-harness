"""
Seeded bug modes -- deliberate, labelled defects in the service under test.

WHY THIS FILE EXISTS

The Day 14 classifier reports an ACCURACY figure. An accuracy figure requires
knowing the right answer in advance for every case -- ground truth. A service
that is always correct provides none, so this module makes the service
misbehave in six specific ways that we chose and labelled ahead of time.

This is the direct analogue of simulator/faults.py in the modem project, which
is what made "100% fault-classification accuracy" a measurement rather than a
claim.

WHY THE BUGS LIVE HERE AND NOT IN THE HANDLERS

Scattering `if BUG_MODE == ...` through main.py would tangle the honest
behaviour with the fake behaviour, so nobody could read the service and tell
what it is supposed to do. Keeping every defect in one middleware means:

  * main.py and store.py stay correct and readable;
  * the full list of defects is enumerable in one place -- and that list IS the
    ground truth the Day 14 selfcheck iterates over;
  * enabling a bug is configuration, not a code change;
  * it mirrors reality, where response corruption really does happen in
    middleware and serialization layers rather than in business logic.

WHAT MUST NOT HAPPEN

Turning a bug on changes the service's BEHAVIOUR. It must never change the
service's CONTRACT. That is what makes a violation detectable: the promise stays
fixed while the behaviour deviates from it. Hence no HTTP control endpoint --
that would appear in the generated spec, force a re-pin, and put a control panel
for the bugs into the published interface.
"""

import json
import os
import re
from enum import Enum

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

# Matches /devices/1 but deliberately NOT /devices/search -- \d+ only.
_DEVICE_DETAIL = re.compile(r"^/devices/\d+$")

ENV_VAR = "BUG_MODE"


class BugMode(str, Enum):
    """
    Every defect this service can be asked to exhibit.

    Inheriting from `str` means the values compare equal to plain strings, so an
    environment variable can be turned into a member directly.

    Each member's true category is `service_bug` -- the service violated its own
    published contract. The other three categories the Day 14 classifier must
    distinguish are seeded elsewhere on purpose: TEST bugs in the test plans
    (Day 9), FLAKES as probabilistic behaviour (Day 12), and ENVIRONMENT
    failures by the proxy (Day 11). Four categories, four different injection
    sites -- that separation is what makes the accuracy number mean something.
    """

    NONE = "none"
    MISSING_FIELD = "missing_field"
    WRONG_TYPE = "wrong_type"
    BAD_ENUM = "bad_enum"
    WRONG_STATUS = "wrong_status"
    UNDECLARED_500 = "undeclared_500"
    OFF_BY_ONE_PAGE = "off_by_one_page"


# The ground-truth table. The Day 14 selfcheck iterates over this, so a new bug
# mode becomes part of the measured set simply by being added here.
TRUE_LABEL: dict[BugMode, str] = {
    BugMode.MISSING_FIELD: "service_bug",
    BugMode.WRONG_TYPE: "service_bug",
    BugMode.BAD_ENUM: "service_bug",
    BugMode.WRONG_STATUS: "service_bug",
    BugMode.UNDECLARED_500: "service_bug",
    BugMode.OFF_BY_ONE_PAGE: "service_bug",
}


# Module-level state, initialised from the environment at import time.
_mode: BugMode = BugMode.NONE


def load_mode_from_env() -> BugMode:
    """
    Read BUG_MODE from the environment, defaulting to a healthy service.

    An unrecognised value fails loudly rather than silently running healthy. A
    typo'd mode name that quietly produced a clean run would make the Day 14
    accuracy figure wrong in the most dangerous direction: too good.
    """
    raw = os.environ.get(ENV_VAR, BugMode.NONE.value).strip().lower()
    try:
        return BugMode(raw)
    except ValueError as exc:
        valid = ", ".join(m.value for m in BugMode)
        raise ValueError(
            f"Unknown {ENV_VAR}={raw!r}. Valid modes: {valid}"
        ) from exc


def current_mode() -> BugMode:
    """The mode currently in force."""
    return _mode


def set_mode(mode: BugMode) -> None:
    """
    Change the active mode.

    Exists for tests, and deliberately NOT exposed over HTTP -- see the module
    docstring. Tests must reset this between cases or one test's bug leaks into
    the next, which is textbook test-order dependence.
    """
    global _mode
    _mode = mode


def _is_device_detail(path: str) -> bool:
    return bool(_DEVICE_DETAIL.match(path))


def _corrupt(
    mode: BugMode, method: str, path: str, payload: object, status_code: int
) -> tuple[object, int]:
    """
    Apply one seeded defect to an otherwise-correct response.

    Each branch targets ONE endpoint and produces ONE kind of violation, so that
    when the classifier sees a failure there is exactly one right answer for
    what went wrong. Overlapping defects would make the accuracy figure
    ambiguous.
    """
    if mode is BugMode.MISSING_FIELD:
        # Schema violation: `name` is in the spec's `required` list.
        if method == "GET" and _is_device_detail(path) and status_code == 200:
            payload.pop("name", None)

    elif mode is BugMode.WRONG_TYPE:
        # Schema violation: the spec declares `id` as an integer.
        if method == "GET" and _is_device_detail(path) and status_code == 200:
            payload["id"] = str(payload["id"])

    elif mode is BugMode.BAD_ENUM:
        # Schema violation: not one of online / offline / degraded.
        if method == "GET" and _is_device_detail(path) and status_code == 200:
            payload["status"] = "exploded"

    elif mode is BugMode.WRONG_STATUS:
        # Undeclared status: POST /devices declares 201, 409, 422 -- not 200.
        if method == "POST" and path == "/devices" and status_code == 201:
            status_code = 200

    elif mode is BugMode.OFF_BY_ONE_PAGE:
        # SEMANTIC violation, and the interesting one. The schema says `items`
        # is a list of Device; it says nothing about how many. Returning one
        # item too many is valid JSON, schema-valid, and wrong. No schema
        # validator can catch this -- it needs a hand-written assertion that
        # len(items) <= limit (Day 9). Not every contract violation is a schema
        # violation, and this mode exists to force that lesson.
        if method == "GET" and path == "/devices" and status_code == 200:
            items = payload.get("items", [])
            if items:
                payload["items"] = items + [items[-1]]

    return payload, status_code


class BugInjectionMiddleware(BaseHTTPMiddleware):
    """
    Rewrites outgoing responses according to the active bug mode.

    A middleware wraps the whole application: every request passes through on
    the way in and every response on the way out. That is why one file can
    corrupt any endpoint without any endpoint knowing about it.
    """

    async def dispatch(self, request, call_next) -> Response:
        mode = current_mode()

        # Fast path: a healthy service does no extra work at all.
        if mode is BugMode.NONE:
            return await call_next(request)

        # undeclared_500 short-circuits: the handler never runs, because we are
        # simulating the service falling over rather than answering wrongly.
        if (
            mode is BugMode.UNDECLARED_500
            and request.method == "GET"
            and _is_device_detail(request.url.path)
        ):
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "internal_error",
                        "message": "seeded fault: undeclared_500",
                    }
                },
            )

        response = await call_next(request)

        # The response body arrives as a stream of chunks, so it has to be
        # collected before it can be inspected or changed.
        raw = b""
        async for chunk in response.body_iterator:
            raw += chunk

        content_type = response.headers.get("content-type", "")
        headers = dict(response.headers)

        # Leave anything that is not JSON strictly alone -- notably the 204 from
        # DELETE, which must stay empty.
        if "application/json" not in content_type or not raw:
            headers.pop("content-length", None)
            return Response(
                content=raw,
                status_code=response.status_code,
                headers=headers,
                media_type=response.media_type,
            )

        payload = json.loads(raw)
        payload, status_code = _corrupt(
            mode, request.method, request.url.path, payload, response.status_code
        )
        body = json.dumps(payload).encode("utf-8")

        # Content-Length described the ORIGINAL body and is now wrong. A stale
        # value is a real protocol bug: too large and the client hangs waiting
        # for bytes that never arrive, too small and the body is truncated.
        # Dropping it lets the framework recompute it.
        headers.pop("content-length", None)

        return Response(
            content=body,
            status_code=status_code,
            headers=headers,
            media_type="application/json",
        )