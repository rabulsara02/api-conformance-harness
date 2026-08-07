# Property-based testing findings — Day 10

Tool: schemathesis, driven by the pinned contract (`spec/openapi.json`).
Reproduce: `st run spec/openapi.json --url <base> --seed 1234 --max-examples 20`

**9 unique failures against a healthy service** (`BUG_MODE=none`, full test suite
green, all 22 declarative conformance cases passing). None were seeded.

## Fixed

### 1. Undocumented `400` on a malformed request body

**Endpoints:** `POST /devices`, `PUT /devices/{id}`, `PATCH /devices/{id}/status`

A body that is not valid JSON never reaches validation — Starlette fails to parse
it and answers `400`. The contract declared `422` as the only failure mode for a
bad body.

**Verdict: fix the contract.** The service is correct — 400 for syntactically
broken, 422 for well-formed-but-invalid. The specification was incomplete.

**Why no hand-written case caught it:** every declarative case sends well-formed
JSON. Sending garbage bytes is something you have to think of.

### 2. Optional query parameter declared as nullable

**Endpoint:** `GET /devices/search`, parameter `status`

FastAPI rendered `status: DeviceStatus | None = None` as
`anyOf: [DeviceStatus, {"type": "null"}]`, declaring JSON null a valid *value*.
In a query string there is no JSON null — `?status=null` is the string `"null"` —
so a client following the spec would send it and receive a 422.

**Verdict: fix the contract.** *Optional* means "may be omitted" (`required:
false`, already correct). *Nullable* means "null is a legal value", which was
never true here.

No FastAPI annotation avoids this (verified against plain, `Annotated`, and
`Query()` forms), so the correction is applied in `scripts/export_spec.py`,
scoped narrowly to parameter schemas only.

## Accepted — not defects

### 3. Unknown query parameters are ignored

Deliberate. Ignoring unrecognised query parameters is near-universal and is what
makes additive API changes non-breaking. Rejecting them would make every future
parameter addition a breaking change.

### 4. `405` responses omit the `Allow` header (5 instances)

Real per RFC 9110, and Starlette does not emit it. But this is **protocol**
conformance, not **contract** conformance: the OpenAPI document never mentions
`Allow`, and this harness is scoped to the contract.

Recorded rather than silently dropped. An honest limitation you can name is worth
more than one you never noticed.

## After the fixes

`No issues found` — 578 generated cases, 0 failures, seed 1234.

## Cross-validation

Run against the seeded bug modes, schemathesis independently reports the same
defects the hand-written validator reports:

| `BUG_MODE` | schemathesis | our validator |
|---|---|---|
| `missing_field` | Response violates schema | `schema_missing_field` |
| `bad_enum` | Response violates schema | `schema_enum` |
| `undeclared_500` | Undocumented status + server error | `undeclared_status` |

Two independent implementations reading the same contract and agreeing is
stronger evidence of correctness than either tool's own test suite.