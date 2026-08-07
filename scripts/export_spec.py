"""
Export the generated OpenAPI document to a committed file.

WHY THIS EXISTS -- the most important idea in the project:

FastAPI generates /openapi.json from the code, so the live spec always agrees
with the code by construction. A harness that fetched that live spec and checked
responses against it could never fail: the service would be graded against a
description of itself. That is a tautology, not a test.

Pinning the document to a file in version control breaks the circle. The pinned
copy is the CONTRACT -- a fixed artifact with a history -- and from Day 8 the
harness reads it instead of the live endpoint.

Pinning does not make the contract unchangeable. It makes changing it VISIBLE.
Re-run this script whenever a change is intentional; the change then shows up as
a diff in a committed file, in a pull request, where a human is asked about it.
The goal is not to prevent change, it is to prevent *accidental* change.

Usage, from the repository root:

    python -m scripts.export_spec
"""

import json
import pathlib
import sys

from api.main import app

# Repository root is this file's parent's parent.
SPEC_PATH = pathlib.Path(__file__).resolve().parent.parent / "spec" / "openapi.json"

def _tighten_optional_parameters(spec: dict) -> dict:
    """
    Correct FastAPI's over-declaration of optional query parameters.

    FastAPI renders `status: DeviceStatus | None = None` as
    `anyOf: [DeviceStatus, {"type": "null"}]`, which declares JSON null as a
    VALID VALUE. In a query string there is no JSON null -- `?status=null` is the
    four-character string "null" -- so the contract promises something that
    cannot be represented, and a client reading the spec would send it and get a
    422.

    OPTIONAL is not the same as NULLABLE. "May be omitted" is expressed by
    `required: false`, which FastAPI already sets correctly. The null branch is
    simply wrong, and no FastAPI-level annotation removes it (verified against
    the plain, Annotated, and Query forms), so the correction happens here.

    Found by property-based testing on Day 10.

    DELIBERATELY NARROW: touches only `parameters`, never request bodies or
    response schemas, and only collapses a two-branch anyOf where exactly one
    branch is null. A broader transformation could silently reshape the contract
    the harness validates against -- which would be a far worse defect than the
    one it fixes. A tool that edits the oracle needs a very short reach.
    """
    for path_item in spec.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            for parameter in operation.get("parameters", []):
                schema = parameter.get("schema")
                if not isinstance(schema, dict):
                    continue

                branches = schema.get("anyOf")
                if not isinstance(branches, list) or len(branches) != 2:
                    continue

                non_null = [b for b in branches if b.get("type") != "null"]
                if len(non_null) != 1:
                    continue

                siblings = {k: v for k, v in schema.items() if k != "anyOf"}
                parameter["schema"] = {**non_null[0], **siblings}

    return spec


def render_spec() -> str:
    """
    Serialize the application's OpenAPI document deterministically.

    `sort_keys=True` and a fixed indent matter more than they look. Without
    them, dictionary ordering could differ between runs and the drift check
    would fail for no reason -- a FLAKY TEST inside the tool being built to
    detect flaky tests. Deterministic serialization is what keeps the check
    meaningful.

    The trailing newline keeps the file POSIX-clean and stops diffs from
    reporting "no newline at end of file" noise.
    """
    return json.dumps(_tighten_optional_parameters(app.openapi()), indent=2, sort_keys=True) + "\n"


def main() -> int:
    SPEC_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPEC_PATH.write_text(render_spec(), encoding="utf-8")
    print(f"Wrote {SPEC_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())