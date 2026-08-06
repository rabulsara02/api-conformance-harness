"""
Running declarative plans, and recording what happened.

Three jobs: execute a case, collect every violation from every mechanism, and
aggregate the results into a summary that can be reproduced from its own
contents.
"""

import json
import pathlib
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from harness.config import HarnessConfig
from harness.plan import _UNSET, Expectation, TestCase
from harness.transport import HttpResponse, NotJsonError, Transport
from harness.validator import (
    check_body_against_schema,
    check_response,
    match_path_template,
    response_schema,
)
from harness.violations import Violation, ViolationKind

# JSON type names -> Python types, for the `type` predicate. bool is checked
# before int elsewhere in Python's hierarchy, but JSON treats them as distinct,
# so "number" accepts both int and float while "boolean" accepts only bool.
_JSON_TYPES: dict[str, Any] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}


def _dig(body: Any, dotted: str) -> Any:
    """Follow a dotted path into a parsed body. Raises if it does not exist."""
    if not dotted:
        return body

    node = body
    for part in dotted.split("."):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def check_expectations(
    body: Any, expectations: tuple[Expectation, ...]
) -> list[Violation]:
    """
    Apply a case's declarative assertions to a response body.

    This is the mechanism that catches semantic violations schema validation
    structurally cannot -- off_by_one_page being the worked example. Every
    predicate is evaluated (rather than stopping at the first failure) so one
    case reports everything wrong with a response at once.
    """
    violations: list[Violation] = []

    for expectation in expectations:
        where = (
            f"response.body.{expectation.path}" if expectation.path else "response.body"
        )

        try:
            value = _dig(body, expectation.path)
        except (KeyError, IndexError, TypeError, ValueError):
            violations.append(
                Violation(
                    kind=ViolationKind.EXPECTATION_FAILED,
                    message=f"no value at {expectation.path!r}",
                    expected=expectation.path,
                    actual="(absent)",
                    location=where,
                )
            )
            continue

        label = expectation.path or "body"

        if expectation.equals is not _UNSET and value != expectation.equals:
            violations.append(
                Violation(
                    kind=ViolationKind.EXPECTATION_FAILED,
                    message=f"{label} is not the expected value",
                    expected=repr(expectation.equals),
                    actual=repr(value),
                    location=where,
                )
            )

        if expectation.max_length is not None and len(value) > expectation.max_length:
            violations.append(
                Violation(
                    kind=ViolationKind.EXPECTATION_FAILED,
                    message=(
                        f"{label} has {len(value)} items, at most "
                        f"{expectation.max_length} allowed"
                    ),
                    expected=f"<= {expectation.max_length}",
                    actual=str(len(value)),
                    location=where,
                )
            )

        if expectation.min_length is not None and len(value) < expectation.min_length:
            violations.append(
                Violation(
                    kind=ViolationKind.EXPECTATION_FAILED,
                    message=(
                        f"{label} has {len(value)} items, at least "
                        f"{expectation.min_length} required"
                    ),
                    expected=f">= {expectation.min_length}",
                    actual=str(len(value)),
                    location=where,
                )
            )

        if expectation.one_of is not None and value not in expectation.one_of:
            violations.append(
                Violation(
                    kind=ViolationKind.EXPECTATION_FAILED,
                    message=f"{label} is not among the allowed values",
                    expected=", ".join(str(v) for v in expectation.one_of),
                    actual=repr(value),
                    location=where,
                )
            )

        if expectation.type is not None:
            wanted = _JSON_TYPES.get(expectation.type)
            if wanted is not None and not isinstance(value, wanted):
                violations.append(
                    Violation(
                        kind=ViolationKind.EXPECTATION_FAILED,
                        message=f"{label} has the wrong JSON type",
                        expected=expectation.type,
                        actual=type(value).__name__,
                        location=where,
                    )
                )

    return violations


@dataclass
class CaseResult:
    """What happened when one case ran."""

    name: str
    method: str
    path: str
    expect_status: int
    status_code: int | None
    elapsed_ms: float
    attempts: int
    violations: list[Violation] = field(default_factory=list)
    transport_error: str = ""

    @property
    def passed(self) -> bool:
        return not self.violations and not self.transport_error


def run_case(transport: Transport, contract: dict[str, Any], case: TestCase) -> CaseResult:
    """
    Execute one case and collect every violation from every mechanism.

    RETRY POLICY -- two rules, both inherited from earlier days:

    1. Retry only on TRANSPORT failure (connection refused, DNS, timeout). Never
       on a validation failure: a response that violates the contract will
       violate it again, and retrying only hides a real bug behind a green
       result. Only "we never got an answer" deserves another attempt.

    2. Retry only IDEMPOTENT methods (Day 4). When a request times out you do not
       know whether it landed; retrying a POST may create two devices. A test
       tool that corrupts the system under test is worse than no test tool.

    Backoff grows with each attempt, so a service that is briefly overwhelmed is
    not hammered by the thing measuring it.
    """
    max_attempts = 1 + (case.retries if case.is_idempotent else 0)
    started = time.monotonic()
    attempts = 0
    response: HttpResponse | None = None
    transport_error = ""

    while attempts < max_attempts:
        attempts += 1
        try:
            response = transport.request(
                case.method,
                case.path,
                params=case.params or None,
                json_body=case.body,
                timeout_ms=case.timeout_ms,
            )
            transport_error = ""
            break
        except Exception as exc:  # transport-level only; see docstring
            transport_error = f"{type(exc).__name__}: {exc}"
            if attempts < max_attempts:
                time.sleep(0.05 * attempts)

    wall_ms = (time.monotonic() - started) * 1000

    if response is None:
        # No HTTP response at all. Distinct from "answered badly" -- Day 14
        # classifies this as `environment`, not as a service or test bug.
        return CaseResult(
            name=case.name, method=case.method, path=case.path,
            expect_status=case.expect_status, status_code=None,
            elapsed_ms=wall_ms, attempts=attempts,
            violations=[], transport_error=transport_error,
        )

    violations: list[Violation] = []

    # 1. Did we get the status the CASE expected? (a plan-level expectation)
    if response.status_code != case.expect_status:
        violations.append(
            Violation(
                kind=ViolationKind.EXPECTATION_FAILED,
                message=f"expected HTTP {case.expect_status}, got {response.status_code}",
                expected=str(case.expect_status),
                actual=str(response.status_code),
                location="response.status",
            )
        )

    # 2. Contract checks: status declared, content type, unexpected body (Day 8).
    violations.extend(check_response(contract, response))

    # 3. Body checks, if there is a schema or any expectations to apply.
    template = match_path_template(contract, case.path)
    schema = (
        response_schema(contract, case.method, template, response.status_code)
        if template
        else None
    )

    body: Any = None
    if schema is not None or case.expect:
        try:
            body = response.json()
        except NotJsonError as exc:
            violations.append(
                Violation(
                    kind=ViolationKind.BODY_NOT_JSON,
                    message=str(exc),
                    expected="a JSON body",
                    actual=response.text[:60],
                    location="response.body",
                )
            )

    if schema is not None and body is not None:
        violations.extend(check_body_against_schema(body, schema))

    if case.expect and body is not None:
        violations.extend(check_expectations(body, case.expect))

    return CaseResult(
        name=case.name, method=case.method, path=case.path,
        expect_status=case.expect_status, status_code=response.status_code,
        elapsed_ms=response.elapsed_ms, attempts=attempts,
        violations=violations, transport_error="",
    )


@dataclass
class RunSummary:
    """Everything one run produced."""

    config: dict[str, Any]
    results: list[CaseResult]
    duration_ms: float

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def pass_rate(self) -> float:
        return 100.0 * self.passed / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        """
        The machine-readable form.

        The CONFIG is included deliberately. A result you cannot reproduce is an
        anecdote, and a report that does not say what it ran against cannot be
        reproduced. This is why Day 7 put every knob in one HarnessConfig rather
        than reading os.environ at the point of use: configuration scattered
        through the code cannot be reported.

        Day 15 renders this same structure as JUnit XML and HTML. One summary,
        many reports -- the pattern from project 1, and the reason the reporting
        day is cheap.
        """
        return {
            "config": self.config,
            "totals": {
                "cases": self.total,
                "passed": self.passed,
                "failed": self.failed,
                "pass_rate_pct": round(self.pass_rate, 1),
                "duration_ms": round(self.duration_ms, 2),
                "total_attempts": sum(r.attempts for r in self.results),
                "violations": sum(len(r.violations) for r in self.results),
            },
            "cases": [
                {
                    **asdict(result),
                    "passed": result.passed,
                    "violations": [
                        {**asdict(v), "kind": v.kind.value} for v in result.violations
                    ],
                }
                for result in self.results
            ],
        }


def run_plan(
    transport: Transport,
    contract: dict[str, Any],
    cases: list[TestCase],
    config: HarnessConfig,
) -> RunSummary:
    """Run every case and aggregate the results."""
    started = time.monotonic()
    results = [run_case(transport, contract, case) for case in cases]

    return RunSummary(
        config={
            "base_url": config.base_url,
            "timeout_ms": config.timeout_ms,
            "use_proxy": config.use_proxy,
            "seed": config.seed,
        },
        results=results,
        duration_ms=(time.monotonic() - started) * 1000,
    )


def write_summary(summary: RunSummary, path: pathlib.Path) -> pathlib.Path:
    """
    Write the summary as JSON.

    sort_keys and a fixed indent, for the same reason as the spec export on Day
    5: a file that reshuffles between identical runs makes diffs unreadable and
    turns a comparison into a source of noise.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path