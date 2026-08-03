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
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    SPEC_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPEC_PATH.write_text(render_spec(), encoding="utf-8")
    print(f"Wrote {SPEC_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())