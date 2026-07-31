"""
Pipeline smoke test.

Verifies the test runner and CI are wired correctly. Not a test of anything that
matters yet — that's the point.
"""

from hello import add


def test_add_positive_numbers():
    """Two positives sum correctly."""
    assert add(1, 1) == 2


def test_add_negative_numbers():
    """Negatives sum correctly (a second case, so a partial failure is visible)."""
    assert add(-1, -1) == -2