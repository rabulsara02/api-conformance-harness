"""
Harness configuration.

One immutable object carrying everything the harness needs to know about the
run, built either explicitly (tests) or from the environment (containers, CI).

Every knob lives here rather than being read from os.environ at the point of use.
That matters for a specific reason: the run summary (Day 9) prints this object,
so any run can be reproduced exactly from its own report. Configuration scattered
through the code cannot be reported, and a result you cannot reproduce is an
anecdote.
"""

import os
from dataclasses import dataclass

DEFAULT_TIMEOUT_MS = 5_000
DEFAULT_SEED = 1_234


@dataclass(frozen=True)
class HarnessConfig:
    """
    Settings for one harness run.

    Frozen (immutable) on purpose: a run's configuration must not change halfway
    through, or the summary would describe something that never happened.

    base_url:
        Where the service under test lives. The harness has no opinion about
        what is behind it -- a local uvicorn, a container, a remote deployment.
        That indifference IS the Transport abstraction paying off.
    timeout_ms:
        Per-request ceiling. Milliseconds because that is the unit test plans
        will use (Day 9) and mixed units are a reliable source of bugs.
    use_proxy:
        Route through the fault proxy instead of straight at the service. Wired
        up properly on Day 11; the switch exists now so no calling code has to
        change then.
    seed:
        Seeds every random decision in the project -- fault probabilities,
        property-based input generation. Nothing uses it yet, and it is here
        anyway: guardrail 9 says everything random must be reproducible, and
        retrofitting a seed after randomness exists is how you end up with
        results nobody can reproduce.
    """

    base_url: str
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    use_proxy: bool = False
    seed: int = DEFAULT_SEED

    @classmethod
    def from_env(cls, default_base_url: str | None = None) -> "HarnessConfig":
        """
        Build a config from environment variables.

        Used by the container and CI entrypoints. `default_base_url` lets the
        local pytest fixture supply the address of the server it just started,
        while still allowing HARNESS_BASE_URL to override it -- which is exactly
        how the same suite gets pointed at a deployed service with no code
        change.
        """
        base_url = os.environ.get("HARNESS_BASE_URL", default_base_url)
        if not base_url:
            raise ValueError(
                "No base URL. Set HARNESS_BASE_URL or pass default_base_url."
            )

        return cls(
            base_url=base_url.rstrip("/"),
            timeout_ms=int(os.environ.get("HARNESS_TIMEOUT_MS", DEFAULT_TIMEOUT_MS)),
            use_proxy=os.environ.get("HARNESS_USE_PROXY", "").lower()
            in {"1", "true", "yes"},
            seed=int(os.environ.get("HARNESS_SEED", DEFAULT_SEED)),
        )