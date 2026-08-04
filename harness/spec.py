"""
Loading the pinned contract.

Reads spec/openapi.json FROM DISK. Never from GET /openapi.json on the running
service -- that would restore the tautology Day 5 removed, grading the service
against a description of itself.

Practical bonus: the contract loads with the service down, so a test asserting
"the contract declares X" needs no server at all.
"""

import json
import pathlib
from typing import Any


# harness/ -> repository root -> spec/openapi.json
DEFAULT_SPEC_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "spec" / "openapi.json"
)


class ContractError(RuntimeError):
    """The pinned contract is missing or unusable."""


def load_contract(path: pathlib.Path | None = None) -> dict[str, Any]:
    """
    Load and lightly sanity-check the pinned OpenAPI document.

    The checks below are deliberately shallow -- enough to fail with a useful
    message rather than with a KeyError three layers deep on Day 8. A contract
    that is missing or malformed is an operator error, and operator errors
    deserve a sentence, not a stack trace.
    """
    spec_path = path or DEFAULT_SPEC_PATH

    if not spec_path.exists():
        raise ContractError(
            f"No pinned contract at {spec_path}. Generate it with:\n"
            "    python -m scripts.export_spec"
        )

    try:
        contract = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"{spec_path} is not valid JSON: {exc}") from exc

    for required_key in ("openapi", "paths", "components"):
        if required_key not in contract:
            raise ContractError(
                f"{spec_path} has no {required_key!r} key -- not an OpenAPI document?"
            )

    return contract


def declared_paths(contract: dict[str, Any]) -> list[str]:
    """Every path template the contract declares, sorted for stable output."""
    return sorted(contract["paths"])


def declared_operations(contract: dict[str, Any]) -> list[tuple[str, str]]:
    """
    Every (path, method) pair the contract declares.

    Sorted so reports and any generated tests come out in a stable order --
    unstable ordering is the kind of thing that makes a diff unreadable and a
    test intermittently fail.
    """
    return sorted(
        (path, method.lower())
        for path, operations in contract["paths"].items()
        for method in operations
    )