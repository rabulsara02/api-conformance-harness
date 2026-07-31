# Review — Interview Prep (living doc, updated as the project grows)

Companion to `LEARNING_NOTES.md`. Notes teach the concepts as we build; this doc
is what you rehearse. Same format as project 1's `REVIEW_01.md`.

**Status:** Day 0 — scaffolding only. Sections fill in as each phase lands.

---

## 1. The one-paragraph pitch (memorize the shape, not the words)

There are two versions here on purpose.

**Version A is where you are now.** It uses no jargon and you should be able to
say it today, before writing a line of code. If you can't explain the project in
these words, you don't understand it yet — and that's the actual failure mode
this project exists to fix.

**Version B is where you'll be on Day 17.** Every technical term in it will have
been earned by building the thing it names. Don't try to memorize B yet. Come
back and re-read it every few days; the goal is that it slowly stops looking like
a foreign language. When B reads as obvious to you, you're interview-ready.

### Version A — plain English (usable today)

> Software services talk to each other over the internet by sending messages back
> and forth. A service publishes a written promise about what its messages will
> look like — what information they'll contain, what type each piece of data is.
> The problem is that services break that promise all the time, usually by
> accident, and it silently breaks everyone who depends on them.
>
> I built a tool that checks whether a running service is actually keeping its
> promise. It reads the promise, sends real requests, and reports exactly how any
> response differs from what was promised.
>
> Two things make it more than a checker. First, I built a piece that sits in the
> middle of the network conversation and deliberately causes trouble — slowing
> messages down, corrupting them, cutting the connection. That lets me test how
> things behave when the network misbehaves, which is where most real-world
> failures come from.
>
> Second, and this is the part I'm proudest of: some tests fail randomly. They
> pass, then fail, then pass again, with nothing changing. Those are called flaky
> tests, and they're poisonous, because once a test suite fails at random people
> stop believing it and start ignoring real failures. My tool runs the whole suite
> many times over and uses statistics to work out which tests are genuinely broken
> versus which are just unreliable. Then it tells you, for every failure, which of
> four things went wrong: the service has a bug, my test has a bug, the test is
> flaky, or the infrastructure had a hiccup.
>
> That last part — saying *why* something failed instead of just *that* it failed
> — is the same thing my previous project did for cellular modems. This one does
> it for web services.

**Why version A is good, not a consolation prize:** most engineers cannot explain
their own work without jargon, and interviewers notice. Being able to drop into
plain English on demand reads as *deeper* understanding, not shallower. Keep this
version even after B becomes natural.

### Version B — technical (the Day 17 target)

*Draft — refine after Day 15 when the real numbers exist. Terms are glossed in
`LEARNING_NOTES.md` Day 0.*

> I built a contract-and-flake test harness for REST APIs. It reads a pinned
> OpenAPI spec and checks that a live service actually honors it — status codes,
> content types, and response bodies validated against the declared JSON Schema.
> A fault proxy sits between the harness and the service and can inject latency,
> error codes, corrupt bodies, or dropped connections. On top of that there's a
> statistical flaky-test detector: it runs the suite N times and scores each test
> by flip rate with a Wilson confidence interval, so a test that fails
> intermittently gets flagged as flaky rather than treated as a real failure.
> Every failure is classified into one of four categories — service bug, test bug,
> flake, or environment — and I measure the classifier's accuracy against
> deliberately seeded, labeled faults. It's the software twin of a cellular-modem
> conformance harness I built previously; same architecture, different protocol.

**Two-project framing, when they ask how these relate:**

> Both are conformance harnesses that diagnose *why* a test failed. The first
> checks a modem against 3GPP TS 27.007 over AT commands; the second checks a
> service against its OpenAPI spec over HTTP. Same spine — tests as data, driver
> behind an interface, fault injection, failure classification, one summary
> rendering to many report formats. I did it twice on purpose, in two domains, to
> show it's an approach rather than a one-off.

---

## 2. Architecture — how the pieces fit

*Fill in after Day 11. See `PROJECT_PLAN.md` §4 for the diagram to internalize.*

---

## 3. Concepts grouped by area (with the questions they invite)

### A. HTTP / web fundamentals
*Day 3+*

### B. API design & specification
*Day 5+*

### C. Software design
*Day 7+*

### D. Testing theory
*Day 9+*

### E. Statistics & flakiness
*Day 13+*

### F. Infra / DevOps
*Day 16+*

---

## 4. Design decisions to defend ("why not the alternative?")

Seeded from Day 0. Grows every day.

| Decision | Alternative rejected | Why |
|---|---|---|
| Hand-rolled validator, then schemathesis | schemathesis alone | Own the core logic; the two catch different bug classes |
| Service I control as the SUT | A public API | No labeled ground truth means no measurable accuracy |
| Real network proxy | Client-side fault hooks | A dropped TCP connection can't be honestly faked in-process |
| Pinned `spec/openapi.json` | Reading the spec live from the app | Reading it live makes the suite a tautology — the app can't violate a spec it regenerates |
| Split per-push vs nightly CI | One workflow | Repeat runs are slow; per-push CI must stay fast or people route around it |

---

## 5. Bugs & gotchas we hit (and what they show)

*Log these as they happen — they're the best interview material you have, and
they're only capturable in the moment.*

### Day 1 — Froze a polluted environment into `requirements.txt`

**What happened.** I created the virtual environment but never activated it, so
`pip install pytest` went to system-wide Python and `pip freeze` captured my
entire global environment — 17 packages, including several the project had never
installed.

**How I caught it.** Two signals. The shell prompt was missing the `(.venv)`
marker, and `requirements.txt` listed packages a fresh venv structurally cannot
contain — a new venv ships with pip and nothing else. The second signal is the
stronger one: it's a *content* check rather than a *cosmetic* one.

**Why it mattered.** CI installs only from `requirements.txt` onto a blank
machine. A polluted file means two distinct failure modes: unrelated pinned
versions get dragged into the build, and — worse — any package that's globally
installed on my laptop but absent from the file works locally and fails in CI.
That's the textbook non-hermetic build, and it's a leading cause of "passes for
me, fails for everyone else."

**The fix, and the generalizable lesson.** Recreated and activated the venv, then
verified with `which python` / `which pip` before installing anything. Switched
all pip invocations to `python -m pip`, which guarantees pip belongs to the same
interpreter as `python` rather than to whatever happens to be first on PATH.

**What this shows in an interview.** It's a small mistake with a good answer,
because the interesting part isn't the mistake — it's that a *cosmetic* check
(is `(.venv)` in my prompt?) is weaker than a *semantic* one (does this file
contain things it couldn't possibly contain?). I then converted the lesson into a
process change: the checklist now treats "created" and "activated" as separate
steps with a mandatory verification gate between them. Same instinct as the
green→red→green exercise from the same day — don't trust a signal you haven't
proven can fire.

**Bonus connection.** Environment pollution is a genuine source of flaky tests: a
suite that depends on undeclared machine state produces results that vary by
machine and over time. So this bug is a miniature of the exact problem the
project's flake detector exists to surface.

---

## 6. Honest limitations (say these before they ask — it builds credibility)

*Fill in as the shape becomes clear. Early candidates: in-memory storage means no
persistence-layer flakiness; single-process service means no distributed-systems
failure modes; the flake detector needs N runs, so it's a nightly tool, not a
per-push one.*

---

## 7. "Walk me through your project" — the narrative arc

*Fill in after Day 15.*

---

## 8. Mock-interview drill

Day 0 questions live in `LEARNING_NOTES.md` §"Day 0 flashcards". Move them here
once you can answer them cold.
