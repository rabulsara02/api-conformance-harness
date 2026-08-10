"""
What the proxy can do wrong, and when.

Four modes, mirroring the modem simulator's four in project 1 -- but moved from
inside the device to the network path between client and service, which is more
realistic: in real systems most flakiness comes from the network, not from the
service.

Every decision is SEEDED. A flake detector built on a fault generator you cannot
reproduce would produce numbers nobody can check (guardrail 9).
"""

import os
import random
from dataclasses import dataclass
from enum import Enum

ENV_PREFIX = "PROXY_"


class FaultMode(str, Enum):
    """
    The faults this proxy can inject.

    NONE          -- transparent; the proxy forwards untouched.
    LATENCY       -- sleep before forwarding (slow network, overloaded service).
    ERROR_CODE    -- answer directly with 5xx/429; never reach the service.
    CORRUPT_BODY  -- truncate the response body (partial write, bad gateway).
    DROP          -- close the connection mid-response (crash, partition).
    """

    NONE = "none"
    LATENCY = "latency"
    ERROR_CODE = "error_code"
    CORRUPT_BODY = "corrupt_body"
    DROP = "drop"


# Ground truth for Day 14. Every proxy-injected fault is an ENVIRONMENT failure:
# the service was never asked, or its correct answer was damaged in transit.
# Neither the service nor the test did anything wrong.
TRUE_LABEL: dict[FaultMode, str] = {
    FaultMode.LATENCY: "environment",
    FaultMode.ERROR_CODE: "environment",
    FaultMode.CORRUPT_BODY: "environment",
    FaultMode.DROP: "environment",
}


@dataclass(frozen=True)
class FaultConfig:
    """
    How the proxy should misbehave.

    probability:
        Fraction of requests that get the fault. 1.0 makes every request fail
        deterministically -- useful for a demo, useless as a flake source.
        Values around 0.2-0.4 produce intermittent behaviour (Day 12).
    seed:
        Makes the sequence of decisions reproducible.
    latency_ms:
        Only a failure if it exceeds the harness timeout -- see the primer. The
        interesting values are near that boundary.
    """

    mode: FaultMode = FaultMode.NONE
    probability: float = 1.0
    seed: int = 1234
    latency_ms: int = 800
    error_status: int = 503

    @classmethod
    def from_env(cls) -> "FaultConfig":
        """
        Build from environment variables.

        Configuration, not a control endpoint -- same decision as Day 6's
        BUG_MODE. A `POST /proxy/fault` route would be a control plane the
        harness could accidentally depend on, and would turn "what was injected"
        into a runtime conversation rather than a recorded fact.

        An unknown mode raises rather than defaulting to healthy: a typo that
        silently ran a transparent proxy would make later accuracy figures wrong
        in the dangerous direction.
        """
        raw = os.environ.get(ENV_PREFIX + "FAULT", FaultMode.NONE.value).strip().lower()
        try:
            mode = FaultMode(raw)
        except ValueError as exc:
            valid = ", ".join(m.value for m in FaultMode)
            raise ValueError(
                f"Unknown {ENV_PREFIX}FAULT={raw!r}. Valid modes: {valid}"
            ) from exc

        return cls(
            mode=mode,
            probability=float(os.environ.get(ENV_PREFIX + "PROBABILITY", "1.0")),
            seed=int(os.environ.get(ENV_PREFIX + "SEED", "1234")),
            latency_ms=int(os.environ.get(ENV_PREFIX + "LATENCY_MS", "800")),
            error_status=int(os.environ.get(ENV_PREFIX + "ERROR_STATUS", "503")),
        )


class FaultDecider:
    """
    Decides, reproducibly, whether a given request gets a fault.

    IMPORTANT PROPERTY, and the one Day 12 depends on: the generator advances
    with every request and is reset only when the proxy process restarts.

      * Restart the proxy between runs -> identical fault pattern each run ->
        the same tests fail every time -> a deterministic failure, not a flake.
      * Leave the proxy running across runs -> the pattern keeps advancing ->
        roughly the same NUMBER of failures but DIFFERENT tests each run -> a
        flaky test, manufactured deliberately.

    Measured on this project: three runs with the proxy restarted produced
    identical failure sets; three runs against one long-lived proxy flipped 15
    of 22 cases. Day 12 starts the proxy once and runs the suite N times.
    """

    def __init__(self, config: FaultConfig) -> None:
        self._config = config
        self._random = random.Random(config.seed)
        self.decisions: list[bool] = []

    @property
    def config(self) -> FaultConfig:
        return self._config

    def should_inject(self) -> bool:
        """True if this request should be faulted. Records every decision."""
        if self._config.mode is FaultMode.NONE:
            self.decisions.append(False)
            return False

        hit = self._random.random() < self._config.probability
        self.decisions.append(hit)
        return hit