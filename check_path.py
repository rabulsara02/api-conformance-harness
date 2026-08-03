"""
Day 2 connectivity check — throwaway, replaced by the real harness on Day 7.

Proves the three-hop path works inside Docker:

    harness (this script)  ->  proxy  ->  api

It talks to the hostname `proxy`, never to an IP address and never to
`localhost`: inside a container, `localhost` means *this container*. Compose runs
a DNS server that resolves each service name to that service's container.
"""

import sys
import time
import urllib.error
import urllib.request

TARGET = "http://proxy:8080/"
DEADLINE_SECONDS = 20


def wait_for(url: str, deadline_seconds: int) -> str:
    """
    Poll `url` until it answers, or give up after `deadline_seconds`.

    Why a retry loop instead of just one request: docker-compose's `depends_on`
    waits for a container to START, not for the server inside it to be READY to
    accept connections. A single immediate request would sometimes succeed and
    sometimes get "connection refused" — the same code producing different
    results depending on timing. That is precisely a flaky test, and polling
    until ready (or until a deadline) is the correct fix rather than sleeping a
    fixed number of seconds and hoping.
    """
    started = time.monotonic()
    attempt = 0

    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                body = response.read().decode("utf-8", errors="replace")
                print(f"Connected on attempt {attempt}: HTTP {response.status}")
                return body
        except (urllib.error.URLError, OSError) as exc:
            elapsed = time.monotonic() - started
            if elapsed > deadline_seconds:
                # Give up loudly, with the reason and how long we waited.
                print(
                    f"FAILED after {attempt} attempts / {elapsed:.1f}s: {exc}",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            print(f"  attempt {attempt} not ready yet ({exc}) - retrying")
            time.sleep(0.5)


if __name__ == "__main__":
    print(f"Harness container starting. Fetching {TARGET} ...")
    body = wait_for(TARGET, DEADLINE_SECONDS)

    # http.server returns an HTML directory listing. We don't care about the
    # content -- only that bytes travelled harness -> proxy -> api and back.
    print(f"Received {len(body)} bytes through the proxy.")
    print("SUCCESS: harness -> proxy -> api path is working.")