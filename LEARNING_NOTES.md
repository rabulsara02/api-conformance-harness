# Learning Notes — API Contract + Flake Harness (Project 2)

Running log of concepts, interview flashcards, and design decisions to defend.
Appended every work session. Same format as the modem project's
`LEARNING_NOTES.md`.

**How to use this file:** read the "Background primer" section before you write
the code for that day. The flashcards at the end of each day are meant to be
answered *out loud*, from memory, with the answer covered.

---

## Day 0 — The vocabulary, in plain English

You're about to build a tool that tests a web API. Before any code, here is
every term you'll hear, explained from scratch. Nothing here assumes you've done
web development.

### 1. What is a REST API?

Your modem project spoke **AT commands over a TCP socket**. You typed `AT+CSQ`
and the modem typed back `+CSQ: 20,99`. A **request**, a **response**, over a
wire.

A **REST API** is the same idea wearing different clothes. Instead of AT commands
over a raw socket, it's **HTTP requests over TCP**. HTTP is just a text protocol
layered on top of the same sockets you already understand.

```
Modem project:   AT+CSQ<CR><LF>            →  +CSQ: 20,99  OK
API project:     GET /devices/42 HTTP/1.1  →  200 OK  {"id": 42, "name": "..."}
```

The pieces of an HTTP request:

| Piece | Example | What it is |
|---|---|---|
| **Method** | `GET`, `POST`, `PUT`, `DELETE` | The verb — what you want done |
| **Path** | `/devices/42` | The noun — which thing you want |
| **Headers** | `Content-Type: application/json` | Metadata about the message |
| **Body** | `{"name": "modem-01"}` | The payload (usually only on POST/PUT) |

And of the response:

| Piece | Example | What it is |
|---|---|---|
| **Status code** | `200`, `404`, `500` | A 3-digit result code |
| **Headers** | `Content-Type: application/json` | Metadata |
| **Body** | `{"id": 42, ...}` | The data you asked for |

**Status codes** are the part you must know cold. They're grouped by first digit:

- **2xx = it worked.** `200 OK`, `201 Created` (after a POST), `204 No Content`
  (worked, nothing to return).
- **4xx = *you* messed up.** `400 Bad Request` (malformed input),
  `401 Unauthorized` (no credentials), `403 Forbidden` (credentials, wrong
  permissions), `404 Not Found`, `422 Unprocessable Entity` (well-formed but
  semantically invalid — FastAPI's default for validation errors).
- **5xx = *the server* messed up.** `500 Internal Server Error`,
  `503 Service Unavailable`.

That 4xx-vs-5xx split is going to matter enormously for your **classifier**: a
4xx usually means the *test* sent something wrong; a 5xx usually means the
*service* is broken. That's the first classification signal, and it falls out of
the protocol for free.

An **endpoint** is one method + path combination, e.g. `GET /devices/{id}`. An
API is a set of endpoints.

### 2. What is an OpenAPI spec?

Here's the key idea, and it maps directly onto your last project.

In the modem project, **3GPP TS 27.007** was the specification. It said: when you
send `AT+CREG?`, you must get back `+CREG: <n>,<stat>` where `stat` is an integer
0–5. You wrote conformance tests that checked a real modem against that written
spec. If the modem answered `+CREG: banana`, the *modem* was wrong, because the
spec is the authority.

**OpenAPI is TS 27.007 for web APIs.** It's a machine-readable document — a big
YAML or JSON file — that describes every endpoint of an API: the paths, the
methods, what parameters they take, what status codes they can return, and
exactly what shape the response body has.

A tiny slice looks like this:

```yaml
paths:
  /devices/{id}:
    get:
      parameters:
        - name: id
          in: path
          required: true
          schema: { type: integer }
      responses:
        '200':
          description: The device
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Device'
        '404':
          description: No such device

components:
  schemas:
    Device:
      type: object
      required: [id, name, status]
      properties:
        id:     { type: integer }
        name:   { type: string }
        status: { type: string, enum: [online, offline, degraded] }
```

Read that in English: *"`GET /devices/{id}` takes an integer `id` in the path. It
returns either a 200 whose JSON body is a `Device`, or a 404. A `Device` is an
object that **must** have `id`, `name`, and `status`; `id` is an integer, `name`
is a string, and `status` must be exactly one of three words."*

That document is a **contract**. It is a promise the API makes to everyone who
calls it. The critical thing: because it's machine-readable, a program can read
it and check whether the API is keeping its promise. That program is what you're
building.

Two more things to know:

- **`$ref`** is a pointer. `$ref: '#/components/schemas/Device'` means "go look
  up `Device` under `components/schemas` in this same file." Specs use refs so a
  shared shape is defined once and referenced everywhere. Your validator will
  have to **resolve** these refs — follow the pointer — which is a real chunk of
  Day 8's work.
- **FastAPI generates the spec for you.** You write Python classes describing
  your data; FastAPI emits the OpenAPI document automatically at
  `/openapi.json`. That's a big reason we picked it. (It also introduces a
  subtle trap we'll deal with on Day 5 — see below.)

### 3. What is contract testing?

**Contract testing = checking that a real, running API actually behaves the way
its spec says it does.**

That is *exactly* what you did at SGS, just in software. Conformance testing a
modem = "does this device match TS 27.007?" Contract testing an API = "does this
service match its OpenAPI spec?" Same job, same mindset, different protocol. This
is the single sentence that connects your two projects in an interview.

It is deliberately **not** the same as normal functional testing:

| Normal API test | Contract test |
|---|---|
| "Creating a device then fetching it returns the same name" | "Every response from every endpoint matches the declared schema" |
| Tests *business logic* | Tests *the promise* |
| You write one per behavior | Generated/driven from the spec |
| Breaks when behavior changes | Breaks when the **interface** changes |

Why anyone cares: a service can be perfectly correct in its logic and still break
every client that calls it, by quietly renaming a field or changing a `200` to a
`204`. Contract tests catch exactly that class of breakage — the kind that
integration tests miss because each team's tests pass in isolation.

### 4. "Hand-rolled" vs "schemathesis" — what you actually chose

**"Hand-rolled"** is just informal engineering slang for **"written by hand,
yourself, instead of using an off-the-shelf library."** There's nothing technical
about the term. A hand-rolled contract validator = you write the Python function
that takes an HTTP response and the spec, and returns "conforms" or "here's
exactly how it violated the contract."

Concretely, over Days 8–9 you'll write something whose core is roughly:

```python
def check_response(response, spec, path, method):
    """Compare one real HTTP response against what the spec promised."""
    promised = spec["paths"][path][method]["responses"]

    # 1. Was this status code even declared?
    if str(response.status_code) not in promised:
        return Violation("undeclared_status", ...)

    # 2. Is the Content-Type what was declared?
    ...

    # 3. Does the JSON body match the declared schema?
    schema = resolve_refs(promised[str(response.status_code)]...)
    return validate_against_json_schema(response.json(), schema)
```

**JSON Schema**, referenced above, is the small standard used *inside* OpenAPI to
describe the shape of a blob of JSON — the `type: object`, `required: [...]`,
`enum: [...]` vocabulary in the example. Validating a body against a schema is a
solved problem; you'll use the `jsonschema` library for that innermost step and
write everything around it yourself. That's the right line to draw: don't
reimplement a standard, do own the logic that makes it a *test harness*.

**schemathesis** is a third-party Python library that does something different
and complementary. You point it at your OpenAPI spec and it **reads the spec and
invents test cases automatically.** For an endpoint that takes an integer `id`,
it will try `0`, `-1`, `999999999999`, and so on — hunting for inputs that make
your API fall over or answer with something not in the spec.

That technique is called **property-based testing** (sometimes "fuzzing," though
fuzzing is broader). The contrast:

| Example-based testing (what you know) | Property-based testing |
|---|---|
| "`GET /devices/1` returns 200 with these fields" | "For **any** valid input, the response conforms to the spec" |
| You pick the inputs | The tool generates hundreds |
| Finds bugs you thought of | Finds bugs you didn't |
| Deterministic | Random (but seedable, so it's reproducible) |

When a property-based tool finds a failure, good ones **shrink** it: they
automatically simplify the failing input to the smallest version that still
fails, so you get "`id=-1` breaks it" instead of "`id=-849372910` breaks it."

**Why we're doing both, in this order.** Hand-rolled first, on Days 8–9, so you
understand response validation at the level of individual lines and can defend
every decision in it. Then schemathesis on Day 10, layered on top, so the project
also demonstrates you know and use the industry-standard tool. The interview
answer this buys you is strong: *"I wrote the contract validator myself first so
I'd understand response validation end to end — ref resolution, status
declaration, schema checking. Then I added schemathesis for property-based
fuzzing, because generating adversarial inputs is a different problem and a
solved one. My validator found the bugs I seeded; schemathesis found two I
hadn't thought of."* That last clause is the one that lands — and it's a real
outcome we'll go looking for on Day 10.

### 5. What is a proxy?

A **proxy** is a program that sits in the middle of a network conversation. The
client thinks it's talking to the server; really it's talking to the proxy, and
the proxy forwards everything to the real server and passes responses back.

```
Without a proxy:   harness ──────────────────▶ API
With a proxy:      harness ──▶ fault proxy ──▶ API
                                    │
                          (can delay, corrupt,
                           replace, or drop
                           anything passing through)
```

Because everything flows through it, a proxy can *misbehave on purpose*. This is
the direct descendant of your modem simulator's fault-injection mode — but now
the faults live in the **network path** instead of inside the device, which is
actually more realistic: in the real world, most flakiness comes from the network
between services, not from the services themselves.

Your proxy (Day 11) will support four fault modes, deliberately mirroring the
modem project's four:

| Modem project fault | API project equivalent | What it simulates |
|---|---|---|
| delay | **latency injection** | slow network / overloaded service |
| malformed response | **corrupt body** (truncated or invalid JSON) | partial write, bad gateway |
| dropout | **dropped connection** (close mid-response) | crash, network partition |
| wrong-state response | **error-code injection** (500/503/429) | service failure, rate limiting |

### 6. What is a flaky test?

A **flaky test** is a test that passes sometimes and fails other times **without
anything changing** — same code, same commit, same environment. Run it ten times,
get seven passes and three failures.

This is the most expensive problem in real test infrastructure, and it's worth
understanding *why*, because that's the interview answer:

- A red CI build is only useful if it means "something is broken." Once builds go
  red at random, engineers stop believing them.
- Once they stop believing them, they re-run until green — which is how a *real*
  bug ships. The flaky test has trained everyone to ignore the alarm.
- Teams then delete or skip the flaky test, losing the coverage entirely.

Common causes (memorize these — you will be asked):

1. **Timing / race conditions** — test asserts before an async operation finishes.
2. **Test-order dependence** — test B only passes if test A ran first and left
   state behind. (Shows up as "passes alone, fails in the suite.")
3. **Shared mutable state** — two tests fighting over the same database row.
4. **Network / external dependency** — the thing you called was briefly slow.
5. **Time and randomness** — tests that break at midnight, on leap days, or on
   unseeded random values.
6. **Resource exhaustion** — a port, file handle, or connection pool ran out
   under parallel execution.

**The detector you're building.** A single test run cannot tell you a test is
flaky — one failure looks identical whether it's a real bug or a coin flip. The
only way to find flakes is **repetition plus statistics**: run the suite N times
against unchanged code, and look at each test's pass/fail *history*.

- A test that's 10/10 pass → stable green.
- A test that's 0/10 pass → a real, deterministic failure. Go fix the bug.
- A test that's 6/10 pass → **flaky**. This is the signal.

From that history you compute a **flakiness score**. The naive version is just
the failure rate, but you'll do better: a **flip rate** (how often the result
*changes* between consecutive runs, which catches intermittency the raw rate
misses) plus a **Wilson score confidence interval** to express how sure you are
given only N samples. The Wilson interval matters because 1 failure in 3 runs and
33 failures in 100 runs are the same raw rate but wildly different levels of
evidence — and a detector that can't tell those apart will cry wolf. We'll derive
it on Day 13; your math background makes this the easiest part of the project for
you and the most differentiating part for an interviewer.

**Why few candidates build this:** it requires accepting that a test result is a
*random variable*, not a boolean. That framing is the whole insight.

### 7. How the four failure categories differ

Your modem harness classified failures as device / timeout / harness. This one
has four categories, and the whole project exists to tell them apart:

| Category | Means | Typical signal |
|---|---|---|
| **Service bug** | The API violated its own contract | 5xx, or a 2xx whose body fails schema validation, **reproducible across runs** |
| **Test bug** | Our test asked for the wrong thing | 4xx (esp. 400/422), or the expectation contradicts the spec, **reproducible** |
| **Flake** | Non-deterministic | Same test, **same code, inconsistent result across N runs** |
| **Environment** | Infrastructure, not code | Connection refused, DNS failure, timeout at the transport layer, proxy unreachable |

Notice that **flake can only be determined with history** — it is the one
category that a single run structurally cannot produce. That's why the repeat-run
engine (Day 12) has to exist before the classifier (Day 14) can be complete, and
it's the sequencing decision to defend in an interview.

---

### Day 0 flashcards

Cover the answers. Say them out loud.

1. **What's the difference between a 4xx and a 5xx, and why does your classifier
   care?** — 4xx: the client's request was wrong. 5xx: the server failed handling
   a valid request. It's the first-pass signal separating "test bug" from
   "service bug."
2. **What is an OpenAPI spec, in one sentence?** — A machine-readable contract
   describing every endpoint of an API and the exact shape of its responses; the
   web equivalent of 3GPP TS 27.007 for a modem.
3. **What is contract testing and how is it different from integration
   testing?** — Contract testing verifies a service matches its published
   interface; integration testing verifies two components work together. Contract
   tests catch interface drift that passes both sides' own tests.
4. **Why write your own validator when schemathesis exists?** — To own and
   understand the core logic, and because the two do different jobs: mine checks
   conformance of real responses precisely; schemathesis generates adversarial
   inputs. I use both.
5. **What is property-based testing?** — Instead of asserting on hand-picked
   examples, you state a property that must hold for all valid inputs and let the
   tool generate many inputs to try to falsify it.
6. **Why can't a single test run identify a flaky test?** — One failure is
   indistinguishable from a deterministic failure. Flakiness is a property of the
   *distribution* of outcomes, so it requires repeated runs.
7. **Why is a flaky test worse than a failing test?** — A failing test tells you
   something is broken. A flaky test destroys trust in the whole suite, which
   causes real failures to be ignored and coverage to be deleted.
8. **Name four causes of flakiness.** — Timing/races, test-order dependence,
   shared mutable state, external/network dependencies (also: time/randomness,
   resource exhaustion).
9. **What does a proxy let you do that you couldn't do otherwise?** — Inject
   faults into the network path itself — latency, corrupt bodies, dropped
   connections, injected error codes — without modifying either the client or the
   server.
10. **Why a Wilson interval instead of a raw failure rate?** — The raw rate
    ignores sample size; 1-in-3 and 33-in-100 look identical. Wilson gives a
    confidence interval that's honest about small samples, so the detector
    doesn't flag a test on one unlucky run.

### Day 0 design decisions to defend

- **Chose to write the contract validator by hand before adopting schemathesis.**
  Cost: ~2 extra days. Benefit: I can explain response validation at the line
  level, and the two layers catch different bug classes.
- **Chose a service I control as the system under test**, rather than a public
  API. Without seeded, *labeled* bugs there is no ground truth, and without
  ground truth "classification accuracy" is an unmeasurable claim. Same reason
  the modem project needed a simulator.
- **Chose a real network proxy over client-side fault hooks.** A dropped TCP
  connection can't be honestly simulated from inside the client; it has to happen
  on the wire.

---

---

## Day 1 — Version control, environments, tests, and CI

Full primer lives in `docs/DAY_01_CHECKLIST.md`. This section is the distilled
version — what to retain and what you'll be asked.

### Concepts introduced

**git vs GitHub.** git is a version-control program on your machine that records
snapshots (commits) of a folder. GitHub is a website that hosts copies of git
repositories and adds collaboration and automation on top. They're separate
things; git works fine with no GitHub.

**The commit as a unit of work.** A commit records what changed, when, by whom,
and why. Commit history is readable by anyone browsing your repo, which makes it
part of the portfolio — many small, well-messaged commits read very differently
from one giant "did project" commit.

**Virtual environments and hermeticity.** A `.venv` is a per-project, isolated
set of Python packages. `requirements.txt` pins exact versions so the environment
can be recreated anywhere. Together they move you toward a **hermetic** run — one
that depends only on things declared in the project, not on the machine it lands
on. Non-hermetic setups are a first-class cause of flaky tests, so this is not
housekeeping; it's the project's central theme showing up on day one.

**What a test is.** A function that exercises code and asserts what must be true.
`assert` raises on falsehood; no assertion failure = pass. pytest finds
`test_*.py` files and `test_*` functions by convention and rewrites assertions so
failures print actual-vs-expected.

**The oracle.** The thing that decides pass vs fail. Today's oracle is a
hard-coded `== 2`. Worth flagging early because the central idea of this whole
project is replacing hard-coded oracles with **the OpenAPI spec as the oracle** —
the test doesn't say "expect this exact body," it says "expect a body conforming
to what the service promised." That's the leap from example-based assertions to
contract testing, and it's the thing that makes the suite generalize.

**Continuous Integration.** A cloud machine that runs the suite automatically on
every push, from a clean slate. Solves two problems: tests that stop being run,
and "works on my machine." GitHub Actions vocabulary: workflow (a file in
`.github/workflows/`), trigger (`on:`), job, runner (`ubuntu-latest`), step
(`run:` a command or `uses:` a prepackaged action), artifact (a downloadable file
produced by a run).

**Exit codes as the CI contract.** Every command returns 0 for success, non-zero
for failure. `pytest` exits 1 if any test fails; GitHub reads that number and
turns the checkmark red. That's the whole mechanism — which is why any
command-line tool can act as a CI gate.

**YAML.** Indentation-based config format, 2 spaces per level, tabs forbidden,
`- ` for list items. Appears three times in this project: the CI workflow,
`docker-compose.yml` (Day 2), and the declarative test plans (Day 9).

**Validating the oracle (the day's real lesson).** A green checkmark you have
never seen turn red proves nothing — you cannot distinguish "tests ran and
passed" from "tests never ran." So we deliberately broke a test, pushed, confirmed
red, then fixed it. The general principle: *an unvalidated detector is worthless;
if you don't know it can fire, its silence carries no information.* This idea
recurs directly on Day 13, when the flake detector has to be scored against
deliberately seeded flaky tests for exactly the same reason.

### Day 1 flashcards

1. **Difference between git and GitHub?** — git is the local version-control
   program; GitHub is a hosting website for git repos plus collaboration and CI.
2. **Why use a virtual environment?** — Per-project package isolation; prevents
   version conflicts and "works on my machine," and makes the environment
   reproducible via `requirements.txt`.
3. **What does `pip freeze > requirements.txt` do and why does CI need it?** —
   Writes exact installed package versions to a file. The CI runner starts empty
   and installs only from that file, so anything missing there fails in CI while
   working locally.
4. **What makes a test pass in pytest?** — It runs to completion without an
   assertion failing. pytest discovers `test_*.py` files and `test_*` functions
   by naming convention.
5. **What is CI and what problem does it solve?** — Automated test execution on
   every push from a clean machine. Solves tests not being run, and
   environment-specific bugs.
6. **How does GitHub Actions know whether a step passed?** — The command's exit
   code: 0 is success, non-zero is failure.
7. **Why did you deliberately break a test on day one?** — To validate the
   oracle. An untested detector proves nothing when silent; I needed evidence CI
   can actually fail before trusting that it passes.
8. **What is a test oracle, and what's the oracle in this project?** — Whatever
   decides pass vs fail. Here it becomes the pinned OpenAPI spec rather than
   hard-coded expected values.
9. **What does "hermetic" mean and why does it matter for flakiness?** — A run
   depending only on declared, in-project inputs. Undeclared dependence on
   machine state produces results that vary run to run — i.e. flakes.

### Day 1 design decisions to defend

- **Created the GitHub repo empty and ran `git init` locally**, rather than
  creating it initialized and cloning. The folder already had planning docs;
  initializing both sides would have produced two unrelated histories to
  reconcile on day one.
- **Pinned CI's Python version to match local exactly.** A version gap between
  laptop and CI produces bugs that reproduce in only one place — expensive, and
  entirely avoidable.
- **Deliberately failed the build and kept those commits in history.** The
  green→red→green sequence is the evidence that the pipeline detects failure. It
  also demonstrates the testing instinct the rest of the project is built on, so
  hiding it would be throwing away signal.
- **Built infrastructure before product code (Days 1–2).** Same reason you verify
  a test setup against a known reference before testing an unknown device: when
  something fails later, the plumbing is already ruled out.

---

*Next: Day 2 primer — containers, images, why "works on my machine" is a
technical problem with a technical fix, and getting three services to talk over a
private network.*
