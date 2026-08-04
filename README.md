# api-conformance-harness

A contract-conformance and flaky-test-detection harness for REST APIs.

Checks a live service against its pinned OpenAPI specification, injects network
faults through a proxy, detects statistically flaky tests across repeated runs,
and classifies every failure as a service bug, a test bug, a flake, or an
environment problem.

Software-domain companion to
[modem-conformance-harness](https://github.com/rabulsara02/modem-conformance-harness).

**Status:** in development — see `docs/PROJECT_PLAN.md`.


## Seeded bug modes

The service can be asked to misbehave in six specific, labelled ways. This is
what makes the harness's fault-classification accuracy a *measurement* rather
than a claim — every injected defect has a known true cause.

Set with the `BUG_MODE` environment variable:

```bash
BUG_MODE=missing_field uvicorn api.main:app
```

| Mode | Endpoint | What breaks | Detected by | True label |
|---|---|---|---|---|
| `none` | — | nothing (healthy baseline) | — | — |
| `missing_field` | `GET /devices/{id}` | required field `name` absent | schema validation | service bug |
| `wrong_type` | `GET /devices/{id}` | `id` returned as a string | schema validation | service bug |
| `bad_enum` | `GET /devices/{id}` | `status` outside the declared enum | schema validation | service bug |
| `wrong_status` | `POST /devices` | 200 returned where 201 is declared | status-code check | service bug |
| `undeclared_500` | `GET /devices/{id}` | 500 on a valid request | status-code check | service bug |
| `off_by_one_page` | `GET /devices` | `limit + 1` items returned | **declarative assertion** | service bug |

`off_by_one_page` is deliberate: it satisfies the schema completely — right
fields, right types, legal enum values — while being plainly wrong. Not every
contract violation is a schema violation, which is why the harness pairs
automatic schema checking with hand-written assertions about meaning.

All defects live in a single middleware (`api/bugs.py`); the route handlers and
store remain honest. Enabling a bug changes the service's **behaviour** and never
its **contract** — `spec/openapi.json` is byte-identical under every mode, which
is what makes the violations detectable.