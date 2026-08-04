"""
The contract-conformance and flaky-test-detection harness.

This package NEVER imports `api`. It drives the service over real HTTP against a
base URL it is given, so it works identically against a local process, a
container, or a remote deployment. An architecture test enforces the rule, since
a boundary nobody checks is a boundary that erodes.
"""