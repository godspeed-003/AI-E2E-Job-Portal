---
name: adversarial-console
description: Build or extend a terminal admin console that drives this portal as a real signed-in user and attacks its own invariants. Use when asked for CLI admin controls, an edge-case harness, a vulnerability hunt, a probe suite, or "check the system works by going at its edge cases" — including when the target is a non-deterministic LLM response that a unit test cannot pin down.
---

# Adversarial console

A terminal that logs in as a real account, drives the real service layer, and then
tries to break the promises the PRD makes. Not a test suite — the test suite
already exercises the happy paths offline. This is the thing you reach for when
the question is *"what happens if someone actively tries?"*

## Why this codebase can have one at all

`services/` is UI-agnostic by design (`prd.md` §4, invariant 1: *pages render,
services decide*). Every page calls a service function and renders the result.
A console that calls the same functions is therefore the same application, not a
parallel reimplementation — which is the only property that makes its findings
evidence about the product rather than evidence about the harness.

**The rule that follows from that:** a probe may never reimplement logic it is
checking. If you find yourself writing `if score < 40: ...` inside a probe, stop —
call `apps.screen()` and assert on what it did. A probe that recomputes the rule
tests your copy of the rule.

## Hard constraints, learned the hard way

**Redirect the environment before the first project import.** `core/config.py`
snapshots `os.environ` at import time behind `@lru_cache(maxsize=1)`, with
`settings = get_settings()` at the module bottom. Any `DATABASE_PATH` /
`MEDIA_DIR` override set *after* an import that transitively reaches `core.config`
is silently ignored, and the run writes to `data/app.db`. Parse the subcommand out
of raw `sys.argv` yourself, set the env, *then* import. `tests/conftest.py` does
the same dance for the same reason.

**Scratch state lives under `data/tmp/`.** Already git-ignored, alongside
`data/media/`, `data/uploads/` and `models/`. Never write a probe database, a
session token file or a captured media artefact anywhere else.

**Never bypass the door you are testing.** Resolve the operator through
`auth.login()` → token → `auth.resolve_session(token)`, which is the same call
`ui/session.py` makes on every rerun. Expiry and revocation then apply to the
console exactly as they apply to a browser. Constructing a `User` dataclass
directly would make every access-control probe vacuous.

**Offline by default, live behind a flag.** `set_provider(FakeProvider())` unless
the operator explicitly asks for the real one. `llm/fake.py` takes scripted
replies via `.push(...)` consumed in order, and records every prompt in `.calls` —
which is how you assert on *what was sent to the model*, not just what came back.

**A hostile persona is a real row.** Registering `injector@probe.local` writes to
`users`. That is fine in a throwaway database and unacceptable in `data/app.db`.

## Structure

Two files, because they have different jobs:

- **A console** — subcommands a human drives interactively: sign in, list roles,
  apply, screen, sit an interview, read the pipeline, run health. Its value is
  that a person can *walk* the product without a browser and see the real error
  strings.
- **A probe suite** — non-interactive, one function per invariant, each handed a
  freshly reset world, returning `None` on pass or raising on failure. Exit 1 if
  anything failed, so CI can run it.

Keep them separate: the console must stay usable when the probe suite is broken.

A draft console already exists at `scripts/console.py` (untracked, **never
executed** — treat every line as unverified). It expects the probe module to
expose:

- `REGISTRY` — entries with `.suite`, `.name`, `.kind`, `.claim`
- `run(selectors, live, verbose)` → results that survive `vars()` (so a plain
  dataclass, never `__slots__`), each with `.status` in `pass` / `fail` / `skip`

Either satisfy that contract or rewrite both ends together. Run the console
end to end before trusting any of it.

## What to attack

Each of these is a sentence the PRD or README asserts. A probe is the attempt to
make that sentence false.

**Identity and session**
- Lockout fires at `LOGIN_MAX_ATTEMPTS`, and is checked *before* the user lookup —
  so a nonexistent email locks out identically to a real one (no enumeration via
  timing or message).
- A revoked token and an expired token both resolve to `None`.
- `logout_all()` kills sessions minted before it, not after.
- Recruiter/admin registration is refused without the exact invite code.
- A recruiter with `company_id = NULL` reaches **nothing** (`access.reach`), not
  everything. This is the failure mode that silently inverts.

**The company boundary** (`services/access.py`)
- A missing record and a forbidden record return the *identical* string. Compare
  the bytes. A different message anywhere turns the id space into a directory.
- `access.role` / `.application` / `.interview` all deny cross-company reads.
- `interviews.require(id, user_id)` refuses another candidate's interview id.
- `apps.withdraw(id, user_id=...)` refuses a non-owner.

**Screening**
- The 40-word `MIN_RESUME_WORDS` floor, quoted back with the real count.
- The ATS floor rejects **before any model call** — assert `len(fake.calls)` did
  not grow. This is a cost guarantee, not just a status.
- `force=True` looks past the floor and *does* call the model.
- A provider exception leaves the row untouched, so a retry is idempotent.
- The five criteria are clamped and summed; a model returning
  `criteria={4,3,4,4,3}, total_score=25` must still store 18.

**Prompt injection**
- In a resume: stripped, flagged on the row, **never rejected** — an honest
  security engineer describing their day job must not be punished.
- In an answer: refused with **no model call** (hard evidence is deterministic).
- Tier 2 fails *open*: kill the provider and confirm the answer is accepted with
  `("low_overlap", "moderation_unavailable")`. A moderation outage must not
  reject every candidate.

**Interview budget and window**
- `max_turns` binds, not `planned_questions`.
- Two adaptive turns in a row maximum; the tail of the budget is reserved for the
  plan so no requirement goes unasked.
- Third rejected attempt is force-accepted with a `forced_accept` flag — a refusal
  loop that never exits is a way to lose an interview by answering badly.
- Before the window: "This interview opens on …". After: "The interview window
  closed on …". One attempt, already used: refused.
- `deadline_at` can never outlive `closes_at`.
- `shorten()` refused on a non-sandbox interview and on one already started.

**Sandbox isolation**
- Sandbox rows stay out of every recruiter read (`include_sandbox=False` default).
- `reset_sandbox()` sweeps media *before* deleting rows, and leaves real rows.
- `ON DELETE CASCADE` really runs: delete a user, assert applications, interviews,
  turns and proctor events are gone.

**Proctoring**
- The integrity score is a pure function of stored rows — recompute twice, get the
  same number.
- One `critical` event floors the verdict at `review` even when the arithmetic
  says clean.
- Repeats of the same kind cost less than the first, and two distinct kinds cost
  more than one kind twice.

## Probing a non-deterministic model

A model's wording is not assertable; its *effect on the system* is. Assert on the
invariant, never the prose:

- **Bounds** — the stored total is in `[0, 25]`, every criterion in `[0, 5]`,
  regardless of what came back. Feed the fake provider `total_score: 999` and
  confirm it is discarded.
- **Malformed and hostile payloads** — push `"not json"`, `"{}"`, `null`,
  a 50 KB string, and JSON with the right shape and wrong types. Nothing may
  raise past the service boundary; the row must stay consistent.
- **Repetition** — run the same input N times against the *live* provider and
  report the score spread. A stable rank ordering across runs is the property that
  matters, not an identical number. This is a measurement to record, not a
  pass/fail assertion.
- **Ordering** — the model may not end an interview while planned questions
  remain, whatever it returns.
- **Prompt contents** — read `fake.calls[-1]["prompt"]`. Did the injected payload
  actually get stripped before it was sent? That is the only place to check.

## Reporting

State the claim, the attack and the observation, in that order. A failure is only
useful if a reader can tell whether the *system* or the *probe* was wrong. Never
report a probe you did not run — an unexecuted script is a hypothesis, and the
whole point of this harness is to stop treating hypotheses as evidence.
