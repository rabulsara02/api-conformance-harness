"""
Run the conformance suite against every seeded bug mode.

Answers one question: does the harness detect each deliberate defect, and by
which mechanism? The mechanism matters as much as the detection -- off_by_one_page
must be caught by a declarative expectation, because no schema check can see it.

This is a throwaway diagnostic, superseded by the Day 14 selfcheck which scores
the classifier against the same ground truth.

    python -m scripts.bug_sweep
"""

import importlib
import os
import pathlib
import socket
import sys
import threading
import time

import uvicorn

from harness.config import HarnessConfig
from harness.plan import load_all_plans
from harness.runner import run_plan
from harness.spec import load_contract
from harness.transport import build_transport

MODES = [
    "none", "missing_field", "wrong_type", "bad_enum",
    "wrong_status", "undeclared_500", "off_by_one_page",
]


def _serve(mode: str):
    """Start the app with one bug mode active, on an OS-chosen port."""
    os.environ["BUG_MODE"] = mode

    import api.bugs
    import api.main
    import api.store

    # Reload so the new BUG_MODE is picked up: the mode is read at import time
    # (Day 6), which is what makes an invalid value fail at startup.
    importlib.reload(api.bugs)
    importlib.reload(api.main)
    api.store.reset()

    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    server = uvicorn.Server(uvicorn.Config(api.main.app, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)

    return server, thread, port


def main() -> int:
    contract = load_contract()
    cases = load_all_plans(pathlib.Path("testplans"))
    clean = True

    print(f"{'BUG_MODE':18} {'pass':>5} {'fail':>5}  detected by")
    print("-" * 78)

    for mode in MODES:
        server, thread, port = _serve(mode)
        config = HarnessConfig(base_url=f"http://127.0.0.1:{port}")

        with build_transport(config) as transport:
            summary = run_plan(transport, contract, cases, config)

        kinds = sorted({v.kind.value for r in summary.results for v in r.violations})
        print(f"{mode:18} {summary.passed:>5} {summary.failed:>5}  {', '.join(kinds) or '-'}")

        if mode == "none" and summary.failed:
            clean = False
        if mode != "none" and not summary.failed:
            clean = False

        server.should_exit = True
        thread.join(timeout=5)

    print("-" * 78)
    print("OK: healthy is clean and every bug is detected" if clean else "PROBLEM: see above")
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())