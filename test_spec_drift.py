"""
Contract drift check.

Compares the committed contract (spec/openapi.json) against what the application
currently generates. If they disagree, the service and its published promise have
diverged.

This is deliberately a TEST rather than a separate CI step: it runs on every
`pytest` invocation, locally and in CI, so drift is caught the moment it is
introduced rather than at review time.

Note what this test protects against. It cannot tell you a contract change is
*wrong* -- only that one happened and was not deliberately re-pinned. That is the
right level of strictness: judging whether a change is acceptable is a human's
job, and this test's role is to guarantee a human is asked.
"""

from scripts.export_spec import SPEC_PATH, render_spec


def test_pinned_contract_exists():
    """
    The contract file must be committed.

    Without it, the harness (Day 8 onward) has no oracle, and every other check
    in this file would silently pass by comparing nothing.
    """
    assert SPEC_PATH.exists(), (
        f"{SPEC_PATH} is missing. Generate it with:\n"
        "    python -m scripts.export_spec"
    )


def test_pinned_contract_matches_the_application():
    """
    The committed contract must describe the service as it currently behaves.

    A failure here is not necessarily a bug -- it usually means the API changed
    and the contract has not been re-pinned yet. The fix is to look at WHAT
    changed before re-pinning, because that diff is a change to the public
    interface.
    """
    pinned = SPEC_PATH.read_text(encoding="utf-8")
    current = render_spec()

    assert pinned == current, (
        "\n"
        "The pinned contract in spec/openapi.json no longer matches the running\n"
        "application. The service and its published promise have diverged.\n"
        "\n"
        "If the change was intentional, re-pin it deliberately:\n"
        "\n"
        "    python -m scripts.export_spec\n"
        "\n"
        "then review `git diff spec/openapi.json` before committing. That diff\n"
        "IS the change to your public contract -- read it as one.\n"
    )