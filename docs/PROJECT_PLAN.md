# API Contract + Flake Harness — Project Plan

**Owner:** Rahul
**Repo name:** `api-conformance-harness` *(locked 2026-07-31)*
**Window:** 17 days (~3–4 hrs/day)
**Created:** 2026-07-30
**Predecessor:** `modem-conformance-harness` (complete — 82 tests, 21 conformance
cases, 100% fault-classification accuracy)

---

## 1. Why this project exists

Project 1 proved you can build test infrastructure for **hardware**. It reads as
validation / systems-test work, and it opens hardware-adjacent doors.

This project is the **software-domain twin**. Same spine, same identity — "a
test-tooling engineer who builds harnesses that diagnose *why* things fail" —
executed in pure software so you're credible for **SDET / Test Automation /
Quality Engineering** roles, which is the bullseye.

Two projects with the *same architectural spine* in two different domains reads
as a **specialization**. Two unrelated projects read as a laundry list. That
parallelism is the entire strategic point, so it is a design constraint, not a
nice-to-have: wherever there's a choice, pick the option that rhymes with project
1.

### What this covers that project 1 didn't

| Gap in project 1 | Closed here by |
|---|---|
| No API / HTTP / web-service work (the #1 SDET skill) | Contract testing a REST API against its OpenAPI spec |
| Faults injected *inside* the device | Faults injected in the **network path** (a real proxy) |
| Failure classification without a statistical component | **Flaky-test detection** — the standout differentiator |
| Three failure categories | Four, including one (flake) that is *structurally impossible* to determine from a single run |

### The one feature that makes it not a toy

**The flaky-test detector.** Nearly every SDET candidate can write API tests.
Almost none have built a system that treats a test result as a random variable,
runs the suite N times, and produces a statistically defensible flakiness score
with a confidence interval. Your math background makes this cheap for you and
expensive for everyone else. Protect this block (Days 12–13) the way you
protected the classifier block last time.

---

## 2. Guardrails (read before every work session)

Carried forward from project 1, plus three new ones.

1. **The service under test is scaffolding, not the product.** Cap it hard, freeze
   it on Day 6. Every hour spent making the API "good" is stolen from the harness.
   This is the same trap the modem simulator posed — you know the shape of it now.
2. **Protect Days 11–15 (proxy → repeat-runner → flake detector → classifier →
   reports) at all costs.** If something must be cut, cut service-under-test
   features or YAML case count. Never cut classification or flake detection.
3. **Instrument from commit #1.** Per-case latency, status codes, retry counts,
   violation counts, run duration — captured in a JSON summary from the first
   working version. You are not retrofitting numbers at the end. Again.
4. **De-risk infrastructure first (Days 1–2).** Repo, CI, Docker, Compose before
   any product code. You've done this once; it should be fast — but do it anyway,
   because debugging an async proxy inside a container you've never built is how
   timelines explode.
5. **Document as you write (learning-first rule).** Top-of-file docstring on every
   file saying what it is and *why it exists*. Plain-English comment on every
   non-obvious line, explaining the "why," not restating the "what."
6. **Teach the background BEFORE each new component (standing request).** Before
   you write a file introducing new concepts (ASGI, async/await, JSON Schema ref
   resolution, property-based testing, confidence intervals, HTTP proxying), you
   get a plain-English primer tied to the exact lines you're about to write.
   Captured in `LEARNING_NOTES.md`. You don't have to ask.
7. **You write and push the code.** Every step verified with `pytest` **and** a
   live run before moving on.
   - **Doc convention:** every code block in every checklist starts at the left
     margin — never nested inside a list item — so it copy-pastes cleanly with no
     leading whitespace. Checkbox lines sit *above* the block, not around it.
8. **NEW — Ground truth or it doesn't count.** Every accuracy number in this
   project must be measured against **deliberately labeled** faults. Seeded bugs
   are labeled with their true category; seeded flaky tests are labeled with
   their true flake probability. A classifier you can't score is a claim, not a
   metric.
9. **NEW — Determinism is a feature.** Everything random (flakiness simulation,
   property-based input generation, proxy fault triggering) must be **seedable**.
   A flake detector that produces different numbers every run is unusable and
   embarrassing to demo. Seed goes in the run config and gets printed in the
   report.
10. **NEW — Never test against a third-party live service.** No public APIs in
    the pipeline. It makes CI non-hermetic, breaks when someone else deploys, and
    destroys the ground truth in guardrail 8.

---

## 3. Tech stack (locked — do not shop for alternatives mid-project)

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.13 (match local, as in project 1) | Consistency with project 1's CI |
| Service under test | **FastAPI** + `uvicorn` | Generates the OpenAPI spec automatically; the spec-first workflow is the whole point |
| HTTP client | **httpx** | Modern, sync + async, transport-level hooks; the `requests` successor |
| Contract validation | **Hand-rolled**, with `jsonschema` for the innermost body check | Own the logic; don't reimplement a standard |
| Property-based layer | **schemathesis** | Industry-standard spec-driven fuzzing; added Day 10, on top of, not instead of |
| Fault proxy | **Hand-written `asyncio` HTTP proxy** | Mirrors the modem simulator; teaches HTTP mechanics; enables honest connection-drop faults |
| Test framework | pytest | Same as project 1 |
| Test-plan format | YAML | Same as project 1 — tests-as-data |
| Statistics | `statistics` stdlib + hand-derived Wilson interval | No scipy dependency for one formula; deriving it is the interview asset |
| Reporting | JSON summary → JUnit XML + HTML | Same one-summary-many-reports pattern as project 1 |
| CI | GitHub Actions | Same |
| Containerization | Docker + Compose (3 services: `api`, `proxy`, `harness`) | Same, one service more |

### The system under test (capped scope — freeze Day 6)

A small **device registry** REST API. The domain is deliberately arbitrary — say
so in the README, because it's a good signal: *the domain doesn't matter, the
contract does.* Using devices rather than "todos" keeps a light thread to project
1 without pretending this is hardware work.

**Endpoints (cap at 8 — more is scope creep, not credibility):**

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | Liveness; always 200 |
| `GET` | `/devices` | List, with pagination (`limit`, `offset`) |
| `POST` | `/devices` | Create → 201, validation error → 422 |
| `GET` | `/devices/{id}` | 200 or 404 |
| `PUT` | `/devices/{id}` | Full update → 200, 404 |
| `PATCH` | `/devices/{id}/status` | Enum-constrained state change → 200, 409 on illegal transition |
| `DELETE` | `/devices/{id}` | 204 or 404 |
| `GET` | `/devices/search` | Query params; the endpoint most likely to break under fuzzing |

**Deliberate design choices baked into the API** (each exists to give the harness
something interesting to catch): an **enum-constrained status field** with
**illegal-transition rules** (a small state machine — the echo of project 1's
registration FSM), **pagination** (an easy place for off-by-one contract
violations), and a **structured error model** (so error responses have a schema
to conform to, not just a status code).

### Seeded bug modes (Day 6) — the ground truth for classification

Toggled by env var `BUG_MODE`, exactly like the simulator's fault modes. Each is
labeled with its true category so the classifier can be scored.

| Mode | Injected defect | True label |
|---|---|---|
| `none` | healthy baseline | — |
| `missing_field` | drops a `required` field from a response | service bug |
| `wrong_type` | returns `"42"` where the schema says integer | service bug |
| `bad_enum` | returns a `status` value outside the declared enum | service bug |
| `wrong_status` | returns 200 where the spec declares 201 | service bug |
| `undeclared_500` | throws on a valid request | service bug |
| `off_by_one_page` | pagination returns `limit+1` items | service bug |

**Note the asymmetry, and defend it:** seeded *service* bugs live in the API;
*test* bugs are seeded in the test plans (a case that contradicts the spec);
*flakes* are seeded as probabilistic test behavior; *environment* failures are
produced by the proxy (drops, refused connections). Four categories, four
different injection sites. That's not accidental — it's what makes the accuracy
number meaningful.

---

## 4. Architecture (the mental model)

```
   openapi.json  ──────────┐   (the contract — pinned, versioned)
   (exported from FastAPI)  │
                            ▼
  YAML test plans ──▶  pytest harness  ──▶ HTTP ──▶ fault proxy ──▶ FastAPI service
   (declarative)        │ - HttpTransport (interface)   (latency,      (device registry
                        │ - contract validator           500/503/429,   + seeded bug
                        │   · status declared?           corrupt body,   modes)
                        │   · content-type?              drop conn)
                        │   · body vs JSON Schema
                        │ - schemathesis fuzz layer
                        │ - repeat-runner (N passes)
                        │ - flake detector (statistics)
                        │ - classifier (4 categories)
                        ▼
              JSON run summary  ──▶  JUnit XML  +  HTML report
              (per-case latency,      (CI-consumable)  (flake scores,
               violations, history)                     fault categories)
                        │
                        ▼
              GitHub Actions: per-push suite + nightly flake sweep
```

**Read it against project 1** — the correspondence is one-to-one, and this table
is the backbone of your "how do these two projects relate" interview answer:

| Project 1 | Project 2 |
|---|---|
| 3GPP TS 27.007 (written spec) | OpenAPI document (machine-readable spec) |
| Modem simulator | FastAPI device registry |
| Registration state machine | Device status state machine |
| `Transport` interface (TCP / Serial) | `Transport` interface (direct / via-proxy) |
| Simulator fault-injection mode | Fault proxy |
| YAML test plans | YAML test plans |
| Classifier: device / timeout / harness | Classifier: service / test / flake / environment |
| JSON → JUnit + HTML | JSON → JUnit + HTML |
| Docker Compose (2 services) | Docker Compose (3 services) |

The `Transport` interface earns its keep again, and for the same reason: the
harness must not know whether it's talking to the API directly or through the
proxy. Swapping is a one-line config change. That's dependency inversion, and
it's the single most important design decision in the harness — same as Day 8
last time.

---

## 5. Day-by-day timeline

> ~3–4 focused hrs/day. Each day has a **goal**, **tasks**, and a **done-when**
> gate. Don't start a day until the previous done-when is true. Each day gets its
> own `docs/DAY_NN_CHECKLIST.md` written the morning of, with a background primer
> first.

### Phase 0 — De-risk infrastructure (Days 1–2)
**Do NOT touch product code yet.** You've done this before; it should go fast.

**Day 1 — Repo + CI skeleton**
- Create the GitHub repo (`api-contract-flake-harness`), public, MIT, Python
  `.gitignore`. Clone over SSH (already configured).
- `.venv`, `requirements.txt`, trivial module + trivial test.
- GitHub Actions workflow: install, `pytest`, green on push. Pin Python 3.13 to
  match local, as in project 1.
- **Done when:** green checkmark in the Actions tab.

**Day 2 — Docker + Compose skeleton**
- `Dockerfile` for the app; `docker-compose.yml` with three placeholder services:
  `api`, `proxy`, `harness`, on one network.
- Prove `harness` can reach `api` **through** `proxy` with a trivial echo — the
  three-hop path is the thing that's new versus project 1, so prove it while it's
  trivial.
- **Done when:** `docker compose up` starts all three and the harness container
  gets a response that provably traversed the proxy.

### Phase 1 — The service under test (Days 3–6)
Scaffolding. Cap it. Freeze it.

**Day 3 — FastAPI fundamentals + first endpoints**
- *Primer:* what ASGI is, how FastAPI turns Python type hints into validation and
  documentation, what Pydantic models are.
- `GET /health`, `GET /devices`, `GET /devices/{id}` with a Pydantic `Device`
  model and in-memory storage (a dict — no database, deliberately).
- View the auto-generated spec at `/openapi.json` and the docs at `/docs`.
- **Done when:** `uvicorn` serves the app, `curl /devices/1` returns JSON, and
  `/openapi.json` shows your `Device` schema.

**Day 4 — Full CRUD + status codes + error model**
- `POST` (201), `PUT` (200), `DELETE` (204), plus proper 404s.
- A structured error response model (`{"error": {"code": ..., "message": ...}}`)
  declared in the spec, so error responses are contract-checkable too.
- `GET /devices/search` with query parameters.
- **Done when:** all 8 endpoints respond correctly by hand (`curl` or `/docs`),
  and every declared status code appears in `/openapi.json`.

**Day 5 — Status state machine, pagination, and pinning the contract**
- *Primer:* why a hand-written spec and a generated spec are different artifacts,
  and why we pin one.
- `PATCH /devices/{id}/status` enforcing legal transitions
  (`offline → online → degraded → offline`; illegal → 409). Direct echo of the
  modem's registration FSM.
- Pagination on `GET /devices` (`limit`/`offset`, with a `total` in the response).
- **Pin the contract:** export `/openapi.json` to a committed `spec/openapi.json`
  via a script, and add a CI check that the committed spec matches what the app
  generates. **This is the subtle trap worth understanding:** if the harness
  reads the spec live from the running app, the app can never violate its own
  contract — it just regenerates a spec that matches whatever it does, and your
  entire test suite becomes a tautology. Pinning the spec as a separate,
  committed artifact is what makes contract testing meaningful. Be ready to
  explain this; it's the sharpest design question in the project.
- **Done when:** illegal transitions return 409, pagination works, `spec/openapi.json`
  is committed, and a drift check fails loudly if the app and the spec disagree.

**Day 6 — Seeded bug modes + FREEZE**
- Implement the 7 `BUG_MODE` values from Section 3 as a thin injection layer
  (one middleware / response hook, not defects scattered through handlers — keep
  the honest code honest and the bugs in one file).
- Document each mode and its true label in the README.
- **FREEZE the service under test.** From here it only changes for bug modes.
- **Done when:** starting with each `BUG_MODE` produces the intended violation,
  observable by hand with `curl`, and `BUG_MODE=none` is clean.

### Phase 2 — Contract harness (Days 7–10)
This is where the product starts.

**Day 7 — Transport interface + first driven request**
- *Primer:* dependency inversion, restated for HTTP; pytest fixtures with
  session scope; why the harness must not import the app.
- Define `Transport` (`request(method, path, **kw) -> Response`, `open()`,
  `close()`), with `DirectTransport` (straight to the API) and a stub
  `ProxyTransport` to be filled in on Day 11.
- pytest fixture that provides a transport from config (base URL, seed, timeout).
- Load and parse `spec/openapi.json` into a spec object.
- **Done when:** `pytest` drives a live request through `Transport` and asserts a
  200 from `/health`, with the transport selected by config.

**Day 8 — Hand-rolled contract validator, part 1**
- *Primer:* JSON Schema vocabulary; what `$ref` is and why resolution is
  recursive.
- `validator.py`: given (response, spec, path template, method), check —
  1. is this status code **declared** in the spec at all?
  2. does `Content-Type` match what's declared?
  3. resolve `$ref`s to get the response body schema.
- Return a structured `Violation` (kind, path, expected, actual) rather than a
  bare bool — the classifier will need the detail, and reports need it too.
- Unit tests with hand-built fake responses (no network) — fast and deterministic.
- **Done when:** a fabricated bad response produces the correct `Violation`, and
  a good one produces none, all in unit tests.

**Day 9 — Validator part 2 + YAML test plans + metrics**
- Body validation against the resolved schema via `jsonschema`; translate its
  errors into your `Violation` type (required-field missing, wrong type, enum
  violation, unexpected additional property).
- YAML test-plan schema: `name`, `method`, `path`, `body`, `params`,
  `expect_status`, `timeout_ms`, `retries`, optional `precondition`. Write ~20
  cases across all 8 endpoints, positive and negative.
- Parametrize: one pytest case per YAML entry (same pattern as project 1).
- JSON run summary: per-case status, latency, violations, retries; run totals.
- **Done when:** `pytest` runs ~20 YAML cases against the live API, all green on
  `BUG_MODE=none`, and each seeded `BUG_MODE` produces at least one correctly
  described violation. A JSON summary is written every run.

**Day 10 — schemathesis property-based layer**
- *Primer:* property-based testing, generation, shrinking, seeding.
- Point schemathesis at `spec/openapi.json`, run it against the live API as a
  separate pytest module, with a fixed seed.
- Triage what it finds. **Expect it to find something you didn't seed** — that's
  the point, and it's the best line in your write-up. Record findings in
  `docs/FUZZ_FINDINGS.md`; decide per finding whether to fix the API or accept
  and document.
- **Done when:** the fuzz suite runs green (or with knowingly-accepted, documented
  exceptions) with a recorded seed, and findings are written up.

### Phase 3 — Proxy, flake detection, classification, reporting (Days 11–15)
**Highest-value block. Protect it. Do not let Phase 1 or 2 bleed into it.**

**Day 11 — The async fault proxy**
- *Primer:* async/await and the event loop; how an HTTP forward proxy works;
  why a connection drop must happen on the wire.
- ~150-line `asyncio` proxy: accept a connection, forward to the API, stream the
  response back. Fault modes by config/env, each with a **probability and a
  seed**: `latency` (add N ms), `error_code` (replace with 500/503/429),
  `corrupt_body` (truncate or mangle JSON), `drop` (close mid-response).
- Wire up `ProxyTransport` from Day 7.
- Integration tests that observe each fault over a real socket.
- **Done when:** with the proxy in the path, each fault mode is observable and
  reproducible from a fixed seed, and `BUG_MODE=none` + no faults is still green.

**Day 12 — Repeat-run engine + result history**
- *Primer:* why flakiness needs repetition; test independence and why the suite
  must be safely re-runnable (state reset between passes).
- `harness/repeat.py`: run the whole suite N times (default 20), record per-test
  outcome history to a JSON store: `{test_id: [pass, pass, fail, pass, ...]}`
  plus latency per attempt.
- Seed a few **deliberately flaky test cases** with known probabilities (e.g. via
  proxy fault probability 0.3 on one endpoint) — this is your labeled ground
  truth for scoring the detector.
- **Done when:** one command runs the suite 20× and writes a history file; the
  seeded flaky cases show mixed results and the stable ones don't.

**Day 13 — The flake detector (the differentiator)**
- *Primer:* failure rate vs flip rate; sampling error; deriving the Wilson score
  interval and why it beats the normal approximation at small N and extreme
  rates.
- `harness/flake.py` computes per test: pass count / N, **flip rate**
  (consecutive-result changes ÷ N−1), and a **Wilson 95% interval** on the
  failure probability. Classify as `stable`, `suspect`, or `flaky` using
  thresholds that are **documented and defended**, not magic numbers.
- Produce a **quarantine list** (tests that should be flagged, not trusted) and a
  ranked flakiness leaderboard.
- **Score the detector** against the seeded ground truth: precision and recall on
  "is this test actually flaky." This is your headline metric, the twin of
  project 1's 100% classification accuracy.
- **Done when:** the detector correctly identifies the seeded flaky tests, misses
  none, flags no stable ones, and reports precision/recall as numbers.

**Day 14 — The four-category failure classifier**
- *Primer:* how each category's signals differ, and why history is a required
  input.
- `harness/classifier.py` takes (violation detail, HTTP status, transport
  outcome, **run history**) and returns one of: `service_bug`, `test_bug`,
  `flake`, `environment` — with a confidence and a human-readable reason string.
  A reason string is not decoration; "classified X because Y" is what makes the
  tool diagnostic rather than a label printer.
- Decision logic sketch (refine as you build): transport-level failure
  (refused/DNS/timeout-before-bytes) → `environment`; inconsistent history on
  unchanged code → `flake`; 4xx or expectation contradicting the spec →
  `test_bug`; 5xx or schema violation, reproducible → `service_bug`.
- `harness/selfcheck.py`: run every labeled scenario (7 bug modes + seeded test
  bugs + seeded flakes + proxy drops), compare predicted to true label, print a
  **confusion matrix** and overall accuracy.
- **Done when:** selfcheck runs end-to-end and prints an accuracy figure and
  confusion matrix over all labeled scenarios.

**Day 15 — Reports: JSON → JUnit XML + HTML**
- JUnit XML for CI consumption (failures annotated with category).
- HTML report: run summary, per-case results with latency, **fault category per
  failure**, **flakiness leaderboard with confidence intervals**, seed and config
  used, confusion matrix from selfcheck.
- Single command produces all three artifacts.
- **Done when:** one command emits JSON + JUnit + HTML, and the HTML shows both
  fault categories and flake scores.

### Phase 4 — Integration, CI, polish (Days 16–17)

**Day 16 — Full Compose + CI wiring**
- Compose runs all three services; harness runs the suite against the API through
  the proxy and writes reports to a mounted volume.
- GitHub Actions: (a) **per-push job** — full contract suite, no faults, publish
  JUnit + upload HTML artifact; (b) **nightly scheduled job** — the 20× repeat run
  with proxy faults enabled, publishing the flake report. Splitting these is a
  deliberate call: the flake sweep is slow and would make per-push CI painful.
  Say that out loud in an interview; it's an operational judgment, and those are
  rare in junior candidates.
- **Done when:** a push produces a green run with downloadable reports, and the
  nightly workflow produces a flake report.

**Day 17 — README as a test plan, demo, metrics freeze, review doc**
- README structured as a **test plan**: purpose, architecture diagram, how to run,
  endpoint table, bug-mode matrix, fault-mode matrix, the four categories, and
  the metrics table.
- Demo (asciinema/GIF): healthy run green → enable a bug mode → classified
  `service_bug` → enable proxy faults → flake detector flags the test.
- Freeze `docs/METRICS.md` with reproducible commands, same format as project 1.
- Finalize `docs/REVIEW_01.md` for interview prep.
- **Done when:** a stranger can clone, `docker compose up`, and understand what
  the tool proves in under 5 minutes; metrics table is frozen.

---

## 6. Metrics to instrument (from Day 1, not retrofitted)

Each must be produced by a specific day and reproducible with one command.

| Metric | Instrumented on |
|---|---|
| Endpoints covered / total endpoints | Day 9 |
| Contract test cases and pass rate | Day 9 |
| Contract violations detected per bug mode | Day 9 |
| Per-case latency + total run duration | Day 9 |
| Retry counts | Day 9 |
| Property-based cases generated + findings triaged | Day 10 |
| Fault-injection modes supported | Day 11 |
| Repeat-run count (N) and total test executions | Day 12 |
| **Flake-detection precision / recall vs seeded ground truth** | Day 13 |
| **Four-category classification accuracy + confusion matrix** | Day 14 |
| CI: per-push green + nightly flake sweep | Day 16 |

The two bolded numbers are the headline. If you can't state them at the end, the
instrumentation failed — which is the exact retrofitting trap you escaped last
time. Don't walk back into it.

---

## 7. Definition of done (the whole project)

- [ ] Public GitHub repo, green CI on `main` (Day 1, 16)
- [ ] FastAPI service: 8 endpoints, status state machine, pagination, error model (Days 3–5)
- [ ] Pinned `spec/openapi.json` + drift check in CI (Day 5)
- [ ] 7 seeded bug modes, each labeled with its true category (Day 6)
- [ ] `Transport` interface with direct + proxy implementations (Days 7, 11)
- [ ] Hand-rolled contract validator: status, content-type, `$ref` resolution, JSON Schema body check (Days 8–9)
- [ ] ~20 YAML contract cases, positive and negative (Day 9)
- [ ] schemathesis property-based layer with recorded seed + findings write-up (Day 10)
- [ ] Async fault proxy: latency, error codes, corrupt body, dropped connection (Day 11)
- [ ] Repeat-run engine with per-test history (Day 12)
- [ ] Flake detector: flip rate + Wilson interval + quarantine list, scored for precision/recall (Day 13)
- [ ] Four-category classifier with reason strings + confusion matrix (Day 14)
- [ ] JSON + JUnit XML + HTML reports (Day 15)
- [ ] `docker compose up` runs the whole thing; nightly flake job in CI (Day 16)
- [ ] README reads like a test plan; `METRICS.md` frozen; demo recorded (Day 17)
- [ ] `LEARNING_NOTES.md` and `REVIEW_01.md` current throughout

---

## 8. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Service-under-test gold-plating (the simulator trap, again) | **High** | Hard freeze Day 6; 8-endpoint cap written into the plan |
| Async proxy debugging overruns Day 11 | Med–High | Compose three-hop path proven Day 2; fall back to a synchronous threaded proxy — same interface, drops still honest |
| Flake detector squeezed by earlier slippage | Med | Cut YAML case count and schemathesis triage first; Days 12–13 are untouchable |
| Wilson interval / thresholds turn into a rabbit hole | Med | Timebox the math to Day 13; thresholds are documented judgment calls, not optimized |
| Spec-drift tautology (harness reads live spec) | Med | Addressed head-on Day 5 by pinning the spec + CI drift check |
| schemathesis floods you with low-value findings | Med | Timebox triage to Day 10; document accepted exceptions rather than fixing all |
| Repeat-runs are slow, CI becomes painful | Med | Split per-push vs nightly workflows (Day 16); N configurable |
| Flaky *harness* (your own code non-deterministic) | Med | Guardrail 9: everything seeded; the detector run against `BUG_MODE=none` + no faults must be 100% stable, and that's a Day 12 gate |
| Two projects look like one project twice | Low | Section 4's correspondence table is the answer — the *reuse* is the story |

---

## 9. Stretch goals (only if genuinely ahead)

Ranked by interview value per hour:

1. **Auto-quarantine in CI** — nightly flake report opens a GitHub issue or writes
   a `quarantine.txt` the per-push job reads to mark tests non-blocking. Turns the
   detector from a report into a *system*, which is a meaningfully bigger claim.
2. **Consumer-driven contract testing (Pact-style)** — a second consumer with its
   own expectations, verified against the provider. Names a real methodology
   hiring managers know.
3. **Spec-diff / breaking-change detector** — compare two `openapi.json` versions
   and classify each change as breaking or additive. Small, self-contained,
   genuinely useful.
4. **Latency budget assertions** — per-endpoint p95 thresholds enforced as test
   failures, with the proxy's latency injection as the validation mechanism.
5. **Parallel execution + test-order shuffling** — surfaces order-dependence
   flakiness, the category the current design won't catch.

Anything not on this list is scope creep.

---

## 10. Decisions locked (resolved 2026-07-31)

1. **Repo name — RESOLVED: `api-conformance-harness`.** Chosen to rhyme with
   `modem-conformance-harness`. Side by side on a resume the two read as a single
   specialization rather than two hobbies, and "conformance" is the word Rahul
   already owns from SGS.
2. **Domain of the service under test — RESOLVED: device registry.** Endpoints
   under `/devices` with an `online / offline / degraded` status field. Keeps a
   light thread to project 1 and supplies a natural state machine to test,
   without pretending to be hardware work. The README should say explicitly that
   the domain is arbitrary — *the domain doesn't matter, the contract does.*
3. **Local folder name.** The working folder stays `software-testing`; only the
   GitHub repo is named `api-conformance-harness`. A local folder name does not
   have to match its remote, and renaming it would break the Cowork folder mount
   mid-project. Not worth it.
4. **Nothing else is open.** Stack, timeline, contract-engine approach, and proxy
   approach are locked as of 2026-07-30. Do not reopen them mid-project.

---

*Follow the done-when gates in order. When a day's gate isn't met, roll the
remainder forward and cut from Phase 1 or the YAML case count — never from the
flake detector or the classifier.*
