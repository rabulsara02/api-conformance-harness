# api-conformance-harness

A contract-conformance and flaky-test-detection harness for REST APIs.

Checks a live service against its pinned OpenAPI specification, injects network
faults through a proxy, detects statistically flaky tests across repeated runs,
and classifies every failure as a service bug, a test bug, a flake, or an
environment problem.

Software-domain companion to
[modem-conformance-harness](https://github.com/rabulsara02/modem-conformance-harness).

**Status:** in development — see `docs/PROJECT_PLAN.md`.