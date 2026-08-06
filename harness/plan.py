"""
Declarative test plans -- tests as data.

WHY YAML RATHER THAN PYTHON FUNCTIONS

A case described as data can be added in six lines, reviewed as a list, written
by someone who does not program, generated mechanically, and -- the part that
matters most later -- run by a DIFFERENT runner without being touched. On Day 12
these same files are executed twenty times over by the repeat-runner, and on Day
15 they feed the reports. None of that requires editing them, because they are
inert.

WHY A RESTRICTED ASSERTION VOCABULARY RATHER THAN EMBEDDED EXPRESSIONS

It would be more expressive to allow `len(body['items']) <= 2` as a string and
eval it. It would also destroy the property that makes plans valuable: the moment
a plan can execute code it stops being data, and can no longer be safely
reviewed, generated, or run by anyone who has not read every line. Same instinct
as yaml.safe_load over yaml.load. If the five predicates are ever genuinely
insufficient, add a sixth deliberately rather than opening the door to arbitrary
code.
"""

import pathlib
from dataclasses import dataclass, field
from typing import Any

import yaml

# Sentinel: `equals: null` is a legitimate expectation, so "absent" and "None"
# must be distinguishable. A plain None default would conflate them.
_UNSET = object()

# From Day 4's safety/idempotency table. PATCH is here only because of the Day 5
# decision to allow no-op status transitions -- had illegal no-ops returned 409,
# a retried PATCH would fail BECAUSE the first attempt succeeded, and retrying it
# would be unsafe. Two days of design converging on one frozenset.
IDEMPOTENT_METHODS = frozenset({"GET", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"})

_CASE_KEYS = {
    "name", "method", "path", "params", "body",
    "expect_status", "expect", "timeout_ms", "retries", "description",
}
_EXPECT_KEYS = {"path", "equals", "max_length", "min_length", "one_of", "type"}


class PlanError(ValueError):
    """A test plan is malformed. Raised loudly rather than skipped quietly."""


@dataclass(frozen=True)
class Expectation:
    """
    One declarative assertion about a response body.

    `path` is a dotted accessor: "error.code", "items", "items.0.status", or ""
    for the whole body. Deliberately simple -- a full JSONPath implementation
    would be more powerful and much harder to review at a glance, and
    reviewability is the point of putting cases in data.
    """

    path: str = ""
    equals: Any = _UNSET
    max_length: int | None = None
    min_length: int | None = None
    one_of: list[Any] | None = None
    type: str | None = None


@dataclass(frozen=True)
class TestCase:
    """One case from a plan file."""

    name: str
    method: str
    path: str
    expect_status: int
    params: dict[str, Any] = field(default_factory=dict)
    body: Any = None
    expect: tuple[Expectation, ...] = ()
    timeout_ms: int | None = None
    retries: int = 0
    description: str = ""

    @property
    def is_idempotent(self) -> bool:
        """Whether this case's method may safely be retried (Day 4)."""
        return self.method in IDEMPOTENT_METHODS


def _parse_expectation(raw: Any, case_name: str) -> Expectation:
    if not isinstance(raw, dict):
        raise PlanError(
            f"{case_name}: each expect entry must be a mapping, got {type(raw).__name__}"
        )

    unknown = set(raw) - _EXPECT_KEYS
    if unknown:
        raise PlanError(
            f"{case_name}: unknown expect keys {sorted(unknown)}; "
            f"valid keys are {sorted(_EXPECT_KEYS)}"
        )

    return Expectation(
        path=raw.get("path", ""),
        equals=raw["equals"] if "equals" in raw else _UNSET,
        max_length=raw.get("max_length"),
        min_length=raw.get("min_length"),
        one_of=raw.get("one_of"),
        type=raw.get("type"),
    )


def parse_case(raw: Any) -> TestCase:
    """
    Turn one YAML mapping into a TestCase, rejecting anything unexpected.

    Unknown keys are an ERROR, not a warning. A typo'd `expect_stauts` that was
    silently ignored would leave a case that passes while checking nothing --
    the same "wrong in the dangerous direction" failure as a silently-ignored
    BUG_MODE on Day 6. A test that cannot fail is worse than no test, because it
    reports confidence it has not earned.
    """
    if not isinstance(raw, dict):
        raise PlanError(f"each case must be a mapping, got {type(raw).__name__}")

    for required in ("name", "method", "path", "expect_status"):
        if required not in raw:
            raise PlanError(f"case is missing required key {required!r}: {raw}")

    unknown = set(raw) - _CASE_KEYS
    if unknown:
        raise PlanError(
            f"{raw['name']}: unknown keys {sorted(unknown)}; "
            f"valid keys are {sorted(_CASE_KEYS)}"
        )

    return TestCase(
        name=raw["name"],
        method=str(raw["method"]).upper(),
        path=raw["path"],
        expect_status=int(raw["expect_status"]),
        params=raw.get("params") or {},
        body=raw.get("body"),
        expect=tuple(
            _parse_expectation(entry, raw["name"]) for entry in (raw.get("expect") or [])
        ),
        timeout_ms=raw.get("timeout_ms"),
        retries=int(raw.get("retries", 0)),
        description=raw.get("description", ""),
    )


def load_plan(path: pathlib.Path) -> list[TestCase]:
    """
    Load one plan file.

    yaml.safe_load, never yaml.load: the unsafe loader can construct arbitrary
    Python objects from a document, which would make a plan file a way to run
    code. Same reasoning as refusing embedded expressions -- data must stay data.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    if not isinstance(raw, list):
        raise PlanError(
            f"{path}: a plan must be a list of cases, got {type(raw).__name__}"
        )

    cases = [parse_case(entry) for entry in raw]
    _reject_duplicate_names(cases, str(path))
    return cases


def load_all_plans(directory: pathlib.Path) -> list[TestCase]:
    """
    Load every *.yaml in a directory, sorted for a stable order.

    Sorting matters: pytest identifies parametrized tests by index as well as
    name, and an order that varies between runs would make failures appear to
    move around -- which looks exactly like flakiness.
    """
    cases: list[TestCase] = []
    for plan_file in sorted(directory.glob("*.yaml")):
        cases.extend(load_plan(plan_file))
    _reject_duplicate_names(cases, str(directory))
    return cases


def _reject_duplicate_names(cases: list[TestCase], where: str) -> None:
    """
    Case names must be unique.

    They are the identity used by pytest, the run summary, and -- from Day 12 --
    the flake detector's per-test history. Two cases sharing a name would have
    their histories merged, and the flakiness score for both would be nonsense.
    """
    names = [case.name for case in cases]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise PlanError(f"{where}: duplicate case names {duplicates}")