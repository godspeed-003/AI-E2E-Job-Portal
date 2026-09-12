# Product Requirements — AI E2E Job Portal

**Status: this document describes what was built.** It replaces the original
resume-screener PRD, which specified a single-page Streamlit app with no accounts,
JSON file storage and a text-only interview. All three were superseded during the
build; `tasks.md` carries the phase-by-phase log and the reasoning behind each
change of direction.

---

## 1. Product

A hiring portal that takes a candidate from *"I saw the job posting"* to
*"a recruiter has my interview score in front of them"* without a human in the
loop until the decision itself.

Three things make it more than a resume screener:

1. **The interview is conducted by an AI, over video**, with questions written
   against the candidate's own resume rather than picked from a list.
2. **Proctoring runs alongside it**, producing evidence a reviewer can read —
   never a verdict it claims on its own.
3. **Nothing is hidden from the candidate.** They see the keyword match, the
   five criteria, which requirement they failed to evidence and the floor that
   was applied to them.

### Principles that shaped the build

| Principle | What it ruled out |
| --- | --- |
| Free and open source throughout | every cloud SDK; paid STT/TTS; any managed database |
| A local model must be one env var away | SDK-shaped provider code; prompts embedded in Python |
| Easy to build, debug and set up | LiveKit on the critical path (two extra processes to run) |
| The model is never trusted with a decision | accepting a model's own `total_score`; letting it end an interview early |
| Proctoring is evidence, not proof | any UI that says a candidate cheated |

---

## 2. Scope

### Built

- Email/password accounts with three roles: `candidate`, `recruiter`, `admin`
- Recruiter signup gated behind an invite code; one bootstrap admin from `.env`
- Resume ingestion: PDF, DOCX (tables included), txt/md, or pasted text
- Keyword (ATS) scoring with word-boundary matching, then model evaluation on
  five criteria out of 5
- Automatic status: `rejected` (keyword floor) / `under_review` / `shortlisted`
- Question plan generated per candidate at shortlist, tagged by source
- WebRTC interview room: spoken question, spoken answer, local transcription
- Adaptive turns: probe, steer, next planned question, wrap up
- Transcript scoring; integrity score 0–100 with a verdict
- Recruiter pipeline with ranking, per-candidate tabs and manual override
- Role management: thresholds, question budget, interview window, open/closed
- Admin: user management, health probes, sandbox with skip-ahead and reset
- Answer audio kept on disk for review
- 272 offline tests

### Deliberately not built

| Excluded | Why |
| --- | --- |
| LiveKit transport (barge-in, streaming speech) | genuinely better realtime, but three processes to run fights the setup requirement. The interview engine is transport-agnostic so it can be added later without touching the logic. |
| Email delivery | no account the demo can rely on; the portal shows invites in-app instead |
| Semantic/embedding ATS matching | the keyword floor is a *cost guard* before a model call, not a judgement; making it cleverer adds a model call to the cheap half of the pipeline |
| Fullscreen-exit proctoring | Streamlit owns the page chrome — there is no fullscreen to leave |
| Video recording to disk | muxing a WebRTC video track per candidate: large dependency, large files, much heavier consent conversation, to serve a review need the audio already covers |
| Live candidate coaching in the room | wanted, not built |
| Bias/fairness auditing | research-scale, and doing it badly is worse than not claiming it |

---

## 3. Personas

**Candidate.** Wants to know where they stand and why. Gets the full breakdown of
their own screening, one interview attempt inside a window they can see, and a
transcript they can read back.

**Recruiter (company HR).** Belongs to exactly one company and can see nothing
outside it. Reads a ranked pipeline, overrides statuses, tunes role thresholds.
A recruiter account whose company is unset reaches *nothing*, not everything.

**Admin.** Runs the installation: accounts, provider health, sandbox. Explicitly
not a super-recruiter — an admin applying for a role is always written as sandbox
data, with no toggle to forget.

---

## 4. Architecture

```text
         ┌──────────────────────────────────────────┐
         │  app.py — role-aware st.navigation       │
         └───────────────────┬──────────────────────┘
                             │
         ┌───────────────────▼──────────────────────┐
         │  ui/pages/*  ·  render() only            │
         │  ui/theme.py ·  ui/session.py            │
         └───────────────────┬──────────────────────┘
                             │  every decision delegated
         ┌───────────────────▼──────────────────────┐
         │  services/  ·  all business logic        │
         │  access.py gates every recruiter read    │
         └──────┬──────────────┬──────────────┬─────┘
                │              │              │
         ┌──────▼─────┐ ┌──────▼─────┐ ┌──────▼──────┐
         │ core/      │ │ llm/       │ │ proctoring/ │
         │ db, config │ │ speech/    │ │ analyzer    │
         │ resume     │ │ (providers)│ │ audio/rules │
         └──────┬─────┘ └────────────┘ └─────────────┘
                │
         ┌──────▼──────────────────────────────────┐
         │  SQLite (WAL) — 11 tables, FK cascade   │
         └─────────────────────────────────────────┘
```

### Invariants

1. **Pages render; services decide.** No page computes a score or owns a write.
   A different front end later is additive, not a rewrite.
2. **All state is a row.** Interview progress, proctoring events, session tokens.
   A refresh mid-interview costs the candidate nothing because there was nothing
   in memory to lose.
3. **Providers, not integrations.** `llm/`, `speech/` and every CV backend are
   swappable and individually optional. A missing backend degrades one signal.
4. **The cheap half commits first.** `apply()` writes the application before
   `screen()` calls a model; the interview row commits before its plan is
   generated. A throttled provider can never swallow a candidate's submission or
   un-shortlist them.

---

## 5. Data model

Eleven tables in one SQLite file, WAL mode, foreign keys enforced:

```text
users ──┬── sessions                     (token hashes, expiry, revocation)
        ├── login_attempts               (rate limiting)
        ├── audit_log                    (who did what)
        └── applications ── interviews ── interview_turns ── proctor_events
companies ── roles ──┘
```

`ON DELETE CASCADE` runs the whole chain, which is what makes "delete this
account" and "reset the sandbox" honest rather than aspirational. Media is *not*
a row, so `recording_service.purge()` exists to say so out loud.

Migration order is `CREATE TABLE IF NOT EXISTS` → `ALTER TABLE` → indexes.
`CREATE TABLE IF NOT EXISTS` silently leaves an old table alone, so an index
naming a newly added column fails on every boot against a database that predates
it — the reason the index DDL is a separate script.

### Key fields

| Table | Field | Note |
| --- | --- | --- |
| `users` | `company_id` | mandatory for recruiters — this is what makes the role a company HR login |
| `users` | `is_sandbox` | propagates to applications and interviews |
| `applications` | `ats_matched` / `ats_missing` | so the candidate can read the rule applied to them |
| `applications` | `screening_flags` | prompt injection found and stripped |
| `interviews` | `planned_questions` / `max_turns` | the budget; `max_turns` is the binding one |
| `interviews` | `plan` | the full generated plan as JSON, with `degraded` when it is the fallback |
| `interviews` | `deadline_at` | `min(now + duration, closes_at)` — the deadline can never outlive the window |
| `interview_turns` | `answer_audio_path` | the clip behind the transcript |
| `proctor_events` | `severity`, `confidence` | weighted into the integrity score |

---

## 6. Screening pipeline

```text
upload ─→ extract ─→ clean ─→ sanitise ─→ ATS score ─→ [commit] ─→ evaluate ─→ status
         PyMuPDF    ligatures  injection   keywords                  model
         python-docx NBSP      stripping   word-boundary             5 criteria
```

**Cleaning** fixes ligatures, non-breaking spaces and bullet debris, then enforces
a 40-word floor quoted back with the actual count — a scanned PDF yields a handful
of stray words, and "paste a few sentences" hid the rule being enforced.

**Word-boundary matching** is load-bearing: `Java` must not match `JavaScript`,
while `C++`, `C#` and `Node.js` must still match themselves.

**Injection handling** strips the offending lines, scores the rest, and names what
was cut. Template resumes off the internet carry *"ignore all previous
instructions"* with the candidate none the wiser; rejecting punishes the wrong
person. The flags are stored on the row so the recruiter sees the same notice.

**Status**: below the keyword floor → `rejected`, on a rule the candidate can read
for themselves. Nothing is auto-rejected on the model's judgement — under the
shortlist floor an application waits for a human. A recruiter can bypass the
keyword floor per role (`ignore_ats`) when a requirement list is worded oddly.

**The total is recomputed.** Each criterion is clamped to 0–5 and summed; the
model's own `total_score` is discarded. A model that scores 4+3+4+4+3 and then
writes 20 is simply wrong, and the total is what gates the shortlist.

---

## 7. The interview agent

### Plan (at shortlist, before the candidate arrives)

Generated from resume + job description + requirements + company culture. Each
question is tagged with a focus area, a source (`resume` / `jd_gap` / `culture` /
`behavioural`) and follow-up hints, so a recruiter can see *why* it was asked.
Every requirement the resume failed to evidence becomes a question or a watch
topic automatically.

If the provider is down, `ensure_plan()` falls back to a role-specific generic
plan flagged `degraded`. A shortlist is a promise; an outage delays nobody.

### Turns

After each answer the agent picks one of four moves:

| Move | When |
| --- | --- |
| `probe` | the answer was thin, or invited a specific follow-up |
| `steer` | the candidate named a watch topic unprompted — pivot into it and tie it back to the JD |
| `next_planned` | default |
| `wrap_up` | the plan is exhausted, the budget is spent, or time is up |

**The plan is authoritative.** The model may not end the interview while planned
questions remain, and `next_planned` asks the plan's *exact* wording — a
paraphrase would smooth off the "you lean on Kubernetes, which your resume does
not show" edge that the plan exists to create.

**Budget**: at most two adaptive turns in a row, and the final turns are reserved
for the plan so no requirement goes unasked.

**Detecting a steer costs no model call.** Watch topics are matched by
word-boundary keyword, so a throttled provider can make the follow-up read
generic but cannot stop the agent following the conversation.

**Guardrails run before an answer is accepted**, and a rejected answer does not
burn a turn. But the third attempt is accepted with a `forced_accept` flag: a
refusal loop that never exits would be a way to lose an interview by answering
badly three times.

### Window and attempts

One attempt, inside a window the candidate can see, with a visible countdown.
A refresh resumes an in-progress interview without spending the attempt — a
dropped connection is not a retry. Time remaining is only reported once the
interview has started; quoting `closes_at` while it is still pending would be a
different number dressed as the same one.

---

## 8. Proctoring

Per-frame analysis on the WebRTC media thread, which **only analyses and queues**.
The Streamlit script thread drains the queue and writes the rows. The queue is
bounded, so a stalled rerun drops events rather than growing memory.

| Signal | Backend | Licence |
| --- | --- | --- |
| No face, multiple faces, prolonged absence | YuNet (in OpenCV) | Apache-2.0 |
| Looking away (yaw/pitch) | MediaPipe FaceLandmarker | Apache-2.0 |
| Candidate substitution | SFace embeddings | Apache-2.0 |
| Phone, second person, book — **off by default** | YOLO11n | AGPL-3.0 |
| Tab switch, window blur, paste | browser watcher → query param | — |
| Another voice in the room, transcript with no speech | near/far-field loudness | numpy |

### Integrity score

A pure function of the stored rows — re-derivable later without the video.
Severity weights: critical 12, high 6, medium 3, low 1. A single `critical` event
floors the verdict at `review` rather than letting the arithmetic call the session
clean. Verdicts are `clean` / `review` / `flag`, and the report is worded as
evidence for a reviewer throughout.

### Stated limits

- **No speaker identification.** The audio signal is loudness arithmetic: the
  candidate is ~40 cm from the mic, anyone else is metres away and 15–25 dB down.
  A television reads the same as a person; a *lone* distant voice reads as the
  candidate. Capped at `medium` severity, confidence ≤ 0.9.
- Audio is analysed once per **accepted** answer. A failed transcription is
  usually a broken microphone and must not accumulate integrity events.
- A missing model file or failed import silences that one signal, never the
  interview.
- Verified in a browser for the tab/blur/paste path; **not** verified end to end
  with a live camera.

---

## 9. Security and access control

| Concern | Mechanism |
| --- | --- |
| Passwords | stdlib `hashlib.scrypt` — memory-hard, no native wheel to fail on Windows |
| Sessions | random tokens, **hashed at rest**, TTL + revocation, cookie-backed |
| Brute force | per-email attempt counting with a lockout window |
| Recruiter signup | invite code from `.env` |
| Company boundary | `services/access.py` — pages ask for a record and get it or an `AccessError` |
| Enumeration | missing and forbidden return the *identical* message, so the id space cannot be walked to count a competitor's candidates |
| Prompt injection | tier 1 regex always; tier 2 model only when tier 1 is unsure, and a tier 2 outage fails **open** with a flag |
| Audit | `audit_log` row for every state change, with the previous value |
| Secrets | `.env` git-ignored; `data/app.db`, model weights and media never committed |

---

## 10. Verification

272 tests, fully offline: no network, no API key, no model weights, no frame
decoded. `tests/conftest.py` points at a throwaway database *before* any project
module is imported, because `core/config.py` snapshots the environment at import
time.

The test that matters most sits a **complete interview with every provider
broken**. The second is `tests/test_db_migration.py`, which builds a legacy table
and asserts it upgrades in place — a schema change that requires a wipe is a
schema change that loses a real database.

Operationally, `scripts/healthcheck.py` runs every probe from a terminal and exits
0 / 1 / 2 (pass-or-warnings / at least one failure / the health system itself
could not run), because *"why will the app not start"* cannot be answered from a
page inside the app.

---

## 11. Open items

- LiveKit transport (Phase 4b) — deferred, not abandoned
- Live non-punitive candidate feedback in the room
- End-to-end camera verification of the browser-signal watcher
- Interview room is the one screen not yet brought fully onto the theme's
  two-column layout conventions
