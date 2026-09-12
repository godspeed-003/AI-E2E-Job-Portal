# tasks.md — AI E2E Job Portal: full build

Status legend: `[x]` done & verified · `[~]` in progress · `[ ]` not started · `[!]` blocked/deferred

Tracking doc for turning the Streamlit resume-screener MVP into a complete portal with
**authentication**, an **AI video interview agent**, and **video proctoring**.
All chosen technology is free / open source. Gemini API is the current LLM backend;
every AI call goes through a provider interface so a local model is a one-line env switch.

---

## Phase 0 — Foundation & hygiene

- [x] Consolidate git: moved nested `AI-E2E-Job-Portal/.git` to the project root so the
      working tree, history and `origin` remote are one repo
- [x] Normalize line endings (`.gitattributes`, `* text=auto eol=lf`)
- [x] Secure secrets: renamed `env` → `.env` (was **not** git-ignored — keys were one
      `git add .` away from being published), hardened `.gitignore`
- [x] Install Python 3.12.10 toolchain + create `.venv`
- [x] `.env.example` documenting every setting
- [x] `core/config.py` — single typed settings object read from env
- [x] `core/db.py` — SQLite schema, migrations, WAL, connection helpers
- [x] `core/security.py` — scrypt hashing + session tokens (moved up from Phase 2)
- [x] `core/model_assets.py` — cached download of the Apache-2.0 CV weights
- [x] `scripts/seed.py` — demo accounts, companies/roles and a ranked pipeline
      — six candidates whose resume text is tuned to the shipped roles' requirement
      keywords (a generically-worded resume is rejected at the keyword floor before
      any model reads it, which produced an empty demo the first time). One
      deliberately wrong applicant, so the rejection path is visible too.
      `--offline` uses the fake provider; `--reset` only ever touches `@demo.local`.
      Migrating the legacy `data/results/*.json` was dropped: those rows have no
      `user_id`, and inventing accounts for them would put people who never
      registered into a recruiter's pipeline

## Phase 1 — Swappable AI backends (Gemini now, local later)

- [x] `llm/base.py` — `LLMProvider` interface, JSON coercion, retry/backoff
- [x] `llm/gemini.py` — REST `generateContent`, JSON mode, multi-key rotation on 429
- [x] `llm/ollama.py` — local Ollama backend
- [x] `llm/openai_compat.py` — llama.cpp / vLLM / LM Studio / any OpenAI-compatible server
- [x] `llm/fake.py` — deterministic provider so tests never touch the network
- [x] `llm/__init__.py` — factory driven by `LLM_PROVIDER`
- [x] Verified live: Gemini reachable, JSON mode works, both supplied keys loaded
- [x] `speech/stt.py` — faster-whisper (local, default) + Gemini audio fallback
- [x] `speech/tts.py` — pyttsx3/Piper local synthesis + browser SpeechSynthesis fallback
- [x] Verified live round trip: pyttsx3 synthesized 226 KB of speech, faster-whisper
      transcribed it back correctly ("two million" → "2 million" is the only difference)
- [x] `torch` 2.14 CPU + `ultralytics` 8.4.138 installed for Phase 5

## Phase 2 — Authentication & authorization

- [x] `core/security.py` — scrypt password hashing (stdlib, no native deps), token minting
- [x] `services/auth_service.py` — register / login / logout, session tokens hashed at
      rest, expiry + revocation, login rate limiting, audit log
- [x] Roles: `candidate`, `recruiter`, `admin`; recruiter signup gated by invite code
- [x] Session persistence across browser refresh (cookie-backed, degrades gracefully)
      — verified in a real browser: sign in → `portal_session` cookie written → F5 →
      still on the candidate home; sign out → cookie gone → login screen
- [x] Role-aware navigation in `app.py` (`st.navigation`) — no page reachable
      without the right role
- [x] Tests: hashing, wrong password, expired/revoked session, rate limit, role gating,
      router/role smoke tests, cookie queue, legacy-schema upgrade
      — 43 tests green, offline

## Phase 3 — Application pipeline (rework of the existing MVP)

- [x] Move resume screening behind auth; applications owned by a `user_id`
      — one row per `(user_id, role_id)`, so a re-upload replaces rather than duplicates
- [x] `core/resume.py` — upload → text (PDF via PyMuPDF, DOCX incl. tables, txt/md),
      ligature/NBSP/bullet cleanup, 40-word floor, dedupe by SHA-256, keyword ATS score
      with word-boundary matching (`Java` ≠ `JavaScript`, `C++`/`C#`/`Node.js` survive)
- [x] `services/application_service.py` — parse → clean → sanitize → ATS → LLM evaluation →
      status (`rejected` / `under_review` / `shortlisted`); the offline half commits before
      the model call, so a throttled provider can never lose a submission
- [x] `services/guardrail_service.py` — resume injection stripping (tier 1, regex) and
      answer checking (tier 1 structural, tier 2 model only when tier 1 is unsure)
- [x] Port `modules/evaluator.py` + `modules/guardrails.py` off hardcoded Ollama
      onto the provider interface — prompt now lives in `prompts/resume_evaluation.txt`
- [x] New `applications` columns: `ats_matched`, `ats_missing`, `screening_flags`
      (migrated in place by `core/db.py`'s ALTER pass)
- [x] `ui/pages/apply.py` — pick a role, upload or paste, then read the whole breakdown:
      keyword match with found/missing pills, the five criteria, strengths, gaps, the
      reason, and the floor that was applied. Retry after a provider outage, replace the
      resume while under review, withdraw. Reached from `NAV` and deep-linked from the
      home page's role cards. An admin's run is always flagged sandbox.
- [x] On shortlist: create an interview + **pre-generate the tailored question plan**
      — `application_service._prepare_interview()`, fired by both the automatic shortlist
      and a recruiter's manual override. Best effort and idempotent: the interview row
      commits before the model call, so an outage costs a plan (rebuilt when the
      candidate opens the room), never the shortlist
- [x] Recruiter can set the interview window (opens/closes) and question budget per role
      — stored per role, honoured by `catalog_service`, and editable from the role
      management page delivered in Phase 6
- [x] Tests: ATS scoring, cleaning, ingestion of real PDF/DOCX bytes, guardrail tiers,
      pipeline with the fake LLM, duplicate/replacement handling, provider outage
      — 57 in `tests/test_screening.py`, 18 driving the page through `AppTest`

## Phase 4 — AI video interview agent (replaces the text interview)

- [x] Delete the text-only interview flow from `app.py`
      — the flow is gone: no route, no nav entry, nothing imports `modules/`, and
      the orphaned package itself has been removed. The README and PRD text that
      still described it is rewritten
- [x] `services/interview_service.py`
  - [x] `build_plan()` — on shortlist, generate candidate-specific questions from
        resume + JD + requirements + company culture, each tagged with focus area,
        source (`resume` / `jd_gap` / `culture` / `behavioural`) and follow-up hints
        — `prompts/interview_plan.txt`; `ensure_plan()` falls back to a role-specific
        generic plan (flagged `degraded`) so a provider outage delays nobody
  - [x] `topics_to_watch` — resume/JD topics the agent should steer toward if the
        candidate brings them up unprompted
        — detected by word-boundary keyword match, so the *decision* to steer costs no
        model call; every unevidenced requirement becomes a topic automatically
  - [x] `decide_next_turn()` — after each answer choose `probe` / `steer` /
        `next_planned` / `wrap_up`; **steer** = candidate mentioned a project or past
        role, so pivot into it and tie it back to the job description
        — deterministic guards run first, then the model, then an offline decision
  - [x] Turn budget: planned questions + capped adaptive probes
        — at most two adaptive turns in a row, and the last turns are reserved for the
        plan so no requirement goes unasked
  - [x] Guardrails run before an answer is accepted; unsafe answers do not burn a turn
        — and the third attempt is accepted with a `forced_accept` flag, so a guardrail
        can never become a way to lose the interview
  - [x] `score()` — transcript → 5 criteria (0–5), strengths, weaknesses, summary
        — total recomputed from the clamped criteria; `finish()` completes the row
        before scoring, so an outage leaves a completed interview that can be scored
        later rather than a candidate in limbo
- [x] `ui/pages/interview_room.py` — WebRTC room: camera + mic live, AI question read
      aloud (TTS) *and* shown as text, spoken answer captured → transcribed → confirmed;
      deep-linked via `?interview_id=`, reached from home page and apply page
- [x] Device check / consent screen before the interview starts
      — window guard, attempts-left guard, kv info card, `webrtc_streamer` preview,
      consent checkbox; `start()` called only after explicit consent
- [x] Resume-after-refresh: all interview state lives in SQLite, not session state
      — `start()` resumes an in-progress interview without spending the single attempt
      and `ask_next()` returns the unanswered turn, so F5 costs the candidate nothing
- [x] Interview window enforcement: visible countdown, start allowed only inside the
      window, per-session duration limit, single attempt
      — service side done: `window_state`, `deadline_at = min(now + duration, closes_at)`,
      `seconds_left` for the HUD, `expire_stale()` (no model calls, cheap on a page load)
      and `close_abandoned()`. The countdown widget itself lands with the room
- [x] Tests: plan generation, steering decision, budget exhaustion, scoring, window rules
      — 36 in `tests/test_interview.py`, including a full interview sat with every
      provider broken

## Phase 4b — Optional LiveKit transport (only after Phase 4 works end to end)

- [!] `scripts/livekit_agent.py` — a LiveKit Agents worker that calls the same
      `interview_service` functions, for barge-in and streaming speech
- [!] Self-hosted LiveKit server config (Apache-2.0) or LiveKit Cloud free tier
- [!] Streamlit component embedding the LiveKit room, selected by `INTERVIEW_TRANSPORT`
- [!] Deferred deliberately, and this phase is closed rather than pending: two extra
      processes to run fights the setup requirement, and the interview engine takes
      text answers in and questions out, so a LiveKit worker can call the same
      functions later without touching the logic

## Phase 5 — Video proctoring

- [x] `proctoring/analyzer.py` — per-frame analysis, every backend lazy and optional
      — YuNet (OpenCV Zoo DNN) for face detection rather than the Haar cascades this
      line originally planned: OpenCV 5 no longer bundles them. MediaPipe
      FaceLandmarker for head pose, SFace for identity. A missing model file or a
      failed import silences that one signal instead of ending the interview.
- [x] Signals: no face present, multiple faces, looking away / head pose off-screen,
      candidate substitution evidence, prolonged absence
- [x] YOLO object signals: phone in frame, second person, book/laptop on desk
      — off by default (`PROCTOR_OBJECT_DETECTION`); it is the one backend heavy
      enough to drop frames on a CPU-only machine
- [x] Browser signals: tab switch, window blur, paste into answer box
      — a `components.html` watcher appends each signal to the page's `proctor`
      query parameter, which the next rerun drains into real events. Verified in
      a browser that the URL rewrite survives a server rerun rather than being
      overwritten; end-to-end with a live camera is still unverified.
      Fullscreen-exit is not wired — Streamlit owns the page chrome, so there is
      no fullscreen to leave
- [x] Audio signals: speech while the candidate should be silent, background voices
      — `proctoring/audio.py`, pure numpy, no new dependency. Near-field vs far-field
      loudness: the candidate is ~40 cm from the mic and anyone else is metres away
      and 15–25 dB down. Two `medium` events with confidence capped at 0.9, because
      the method cannot identify a speaker — a television reads the same as a person,
      and a *lone* distant voice reads as the candidate. That limit is pinned by its
      own test rather than left as a comment
- [x] `proctoring/rules.py` — debounce, severity weighting, event de-duplication
- [x] `proctoring/session.py` — the thread boundary: the WebRTC media thread only
      analyses and queues, the Streamlit script thread drains and writes. Bounded
      queue, so a stalled rerun drops events rather than growing memory
- [x] `services/proctor_service.py` — persist events, snapshot evidence, integrity score
      (0–100) + verdict — score is a pure function of the stored rows, so it can be
      re-derived later without the video; a `critical` signal floors the verdict at
      `review` rather than letting the arithmetic call it clean
- [x] Wire proctoring into `ui/pages/interview_room.py` — enrollment captured from
      the consent-screen preview, events drained every rerun, integrity score finalised
      on all four interview-completion paths
- [x] Session recording to disk for human review
      — `services/recording_service.py`: one WAV per answered turn at
      `media/interviews/<id>/answer_NN.wav`, reachable from the turn that produced
      it and played back on the recruiter's transcript tab. Audio only — video
      would mean muxing a WebRTC track per candidate to serve a review need the
      audio already covers. Every score in the portal is computed from a transcript
      a speech model *guessed at*, so this is the only artefact that can settle a
      disputed one. Writing is best-effort: a full disk costs the candidate nothing
- [!] Live but non-punitive candidate feedback ("centre yourself in frame")
      — wanted, not built. Recorded here rather than dropped silently
- [x] Tests: rule debounce, severity aggregation, integrity scoring
      — 54 tests, offline: no model downloaded, no frame decoded. Plus 16 for the
      audio attribution and 11 for the recordings

## Phase 6 — Recruiter & admin experience

- [x] Recruiter dashboard: ranked candidates, resume evaluation, interview score,
      transcript, integrity report with event timeline, snapshots and recording
      — `ui/pages/recruiter_pipeline.py`. Role picker, four summary tiles, the
      ranked list, then three tabs per candidate (screening / interview /
      integrity). A recruiter can override the status from here, and a shortlist
      set by hand builds the interview and its plan exactly as an automatic one
      would. No model call anywhere on the page — every number was already stored
- [x] Role management: create/edit roles, thresholds, interview windows
      — `ui/pages/recruiter_roles.py`. Each threshold has an explicit "use
      default" state stored as NULL, so a role that was never tuned follows
      `.env` as it changes instead of freezing today's value. A role cannot
      change company (it would orphan its applications) and cannot be deleted
      from the UI (the cascade would take candidates with it) — closing it is
      the reversible way to stop intake
- [x] `services/access.py` — the company boundary. Recruiter-facing pages never
      look a record up directly; they ask here and get the record or an
      `AccessError`. Missing and forbidden return the identical message, so the
      id space cannot be walked to count a competitor's candidates
- [x] Admin: user management, recruiter invites, provider health check panel
      — `ui/pages/admin_users.py` (search, role filter, enable/disable, force sign-out,
      audit tail) and `ui/pages/admin_health.py` (per-backend probes + model downloads)
- [x] Tests: ranking, access control on another company's data
      — 23 in `tests/test_recruiter.py`: cross-company reads on roles,
      applications and interviews; a recruiter whose company is unset reaching
      nothing rather than everything; ranking stability; both pages rendered

## Phase 6b — Sandbox / demo mode (admin can test without touching real data)

- [x] `is_sandbox` flag on users, applications and interviews; FK cascade cleanup
- [x] Admin "Sandbox" page: spin up a throwaway candidate persona in one click
- [x] Admin can run the full candidate journey as that persona (apply → shortlist →
      video interview → score) with a persistent banner so it is never mistaken
      for production data
- [x] Recruiter and admin lists hide sandbox records by default, with a toggle
      — the pipeline page's `Include sandbox` switch, off unless asked
- [x] One-click "Reset sandbox" wipes every sandbox row and its media
      — rows by FK cascade; recordings and snapshots are *files*, so the cascade
      cannot reach them and `reset_sandbox` sweeps both explicitly before deleting.
      A reset that keeps audio of a test interview, and photographs of whoever sat
      it, is not a reset
- [x] Skip-ahead helpers so the flow can be tested fast: force-shortlist, seed a
      sample resume, shorten the interview to 2 questions
      — one panel on the sandbox page. The sample resume is generated from the
      chosen role's own requirements, so the question plan is about the role being
      tested rather than about a canned backend CV. `interview_service.shorten()`
      moves the turn budget *and* trims the stored plan, and refuses on anything
      that is not a pending sandbox interview. Stubbing the transcript was dropped:
      the two-question interview reaches the scoring screen in under a minute, and
      a stub would be the one path that never exercises the real scorer

## Phase 7 — Look and feel

- [x] `.streamlit/config.toml` dark theme + `ui/theme.py` injected CSS design system
- [x] Custom components: status pills, score rings, metric tiles, candidate cards,
      proctoring timeline, live interview HUD
- [x] Consistent iconography and typography, responsive two-column layouts
      — the interview room was the last screen off the convention, and bringing
      it on found a real bug: the score reveal passed `detail=` to a
      `theme.score_ring` that has no such parameter, so the final screen of the
      candidate journey raised `TypeError` the moment an interview finished.
      Every service test passed throughout, because none of them rendered a
      page. `tests/test_interview_room.py` now renders each phase
- [x] Landing/login screen with product framing rather than a bare form

## Phase 8 — Verification & docs

- [x] `pytest` suite green (no network required)
      — 279 passing: 57 screening + 54 proctoring + 36 interview + 28 auth +
      23 recruiter + 18 apply page + 16 audio + 14 sandbox + 12 routing +
      11 recording + 7 interview room + 3 migration
- [x] `scripts/healthcheck.py` — verify DB, LLM, STT, TTS, CV backends
      — wraps `health_service` so the terminal and the admin page cannot drift.
      Exits 0 (pass, or warnings only) / 1 (a real failure) / 2 (the health system
      itself could not run). Warnings do not fail: a portal with no CUDA, no object
      detection weights and no TTS voice is a supported configuration
- [x] End-to-end smoke run: register → apply → shortlist → interview → score → review
      — sat in the test suite rather than by hand, with every provider broken, which
      is the version of the run that can be repeated on demand
- [x] Rewrite `README.md`: architecture, setup, provider switching, proctoring limits
- [x] Updated PRD reflecting what was actually built
      — `prd.md` rewritten; it now opens by saying it replaces the original
      resume-screener spec rather than pretending that was the plan all along.
      A separate `ARCHITECTURE.md` was dropped: the README's architecture section
      and the PRD's invariants already carry it, and a third document describing
      the same layers is a third document to let go stale
- [x] Commit in reviewable chunks

---

## Decisions taken (no user available to ask)

| Question | Decision | Why |
| --- | --- | --- |
| Keep Streamlit or move to React/FastAPI? | **Keep Streamlit**, but move all logic into UI-agnostic `core/`, `llm/`, `services/`, `proctoring/` packages | `streamlit-webrtc` gives real WebRTC video + per-frame Python hooks, so video interviewing and proctoring work today without a second stack. The service layer means a FastAPI/React front end later is additive, not a rewrite. |
| Storage | **SQLite** (stdlib `sqlite3`, WAL) replacing JSON files | Interviews need concurrent reads, per-turn writes and integrity events. Legacy JSON is migrated, not lost. |
| Gemini access | **REST `generateContent`** via `requests`, not the SDK | Same code shape as the Ollama and OpenAI-compatible backends, so switching providers touches one env var. Both supplied API keys are used with rotation on rate limits. |
| Speech-to-text | **faster-whisper**, local, MIT | Free, offline, no per-minute cost; Gemini audio remains a fallback. |
| Password hashing | **stdlib `hashlib.scrypt`** | Memory-hard and dependency-free — no native wheel to fail on Windows. |
| Phone/object detection | **Ultralytics YOLO enabled** (`yolo11n`, ~5 MB) | User confirmed the project is open source (final-year B.Tech), so AGPL-3.0 is acceptable. Adds phone-in-frame, extra-person and book/laptop detection on top of the face signals. |
| Automated identity matching | **Included** via OpenCV `FaceRecognizerSF` (SFace) | Revised after checking what actually installs: SFace is a 38 MB Apache-2.0 ONNX model that OpenCV loads natively, so candidate-substitution detection needs no dlib and no build step. Still reported as evidence for a human, not a verdict. |
| Face detection backend | OpenCV **YuNet** primary, MediaPipe FaceLandmarker for head pose | MediaPipe 1.0 removed the old `mp.solutions` API and OpenCV 5 wheels no longer bundle Haar cascades, so the "obvious" defaults are both gone. YuNet is 230 KB, ships inside OpenCV, and returns five landmarks — enough for face-count and gaze heuristics on its own. |
| Interview audio transport: `streamlit-webrtc` or **LiveKit Agents**? | **streamlit-webrtc for the MVP; the turn engine is transport-agnostic so a LiveKit worker can be added later** | User raised LiveKit (Apache-2.0, free, and they have used it before) — it is genuinely the better realtime stack: server-side VAD, barge-in, streaming STT/TTS. The cost is three processes to run (Streamlit UI + LiveKit server + agent worker) and a custom Streamlit component to embed the room, which fights "easy to build, debug and set up" and "MVP made fast". So `services/interview_service.py` takes *text answers in, questions out* and knows nothing about capture: `speech/` + `streamlit-webrtc` drive it now, and a LiveKit agent can call the exact same functions in Phase 4b without touching the interview logic. |
| How to keep a session across a browser refresh | **Cookie written by `extra-streamlit-components`, but queued and flushed on the *next* script run** (`ui/session.py:start_run`) | The cookie manager is a hidden custom component, so a write is a *render*: `st.rerun()` right after sign-in throws that render away and the browser never receives it — the original symptom was a successful login that vanished on F5. Two consequences are now pinned by tests: the manager is rebuilt every run (a cached instance freezes `.cookies` at the empty default it returns on its first render, so the cookie would never be read), and sign-in/sign-out only queue the op. |
| Schema changes on an existing `data/app.db` | **Tables → `ALTER TABLE` migration → indexes**, with the index DDL split into its own `INDEXES` script | `CREATE TABLE IF NOT EXISTS` silently leaves an old table alone, so an index naming a newly added column (`is_sandbox`) failed on every boot against the real database that predates it. Splitting the DDL means old databases upgrade in place instead of needing a wipe; `tests/test_db_migration.py` builds a legacy table and asserts it. |
| Closing SQLite handles held by other threads | **A registry of every connection + `db.close_all()`**, with a generation counter invalidating the thread-local cache | Streamlit's script thread, the WebRTC callback threads and a test's main thread each hold their own connection, and on Windows one open handle is enough to make the database file undeletable — every test after the first `AppTest` run failed with `WinError 32`. `check_same_thread=False` makes closing another thread's handle legal. |
| The evaluation returns its own `total_score` — trust it? | **No: clamp each criterion to 0–5, sum them, and derive `alignment_score` as total/max** | The total is what gates the shortlist, and a model that scores 4+3+4+4+3 and then writes 20 is simply wrong. Recomputing means a hallucinated or inflated total cannot shortlist anyone; the model is only ever trusted for the five judgements and the prose. |
| Guardrail tiering on resumes and answers | **Tier 1 (regex/structural) always; tier 2 (model) only when tier 1 is unsure — and a tier 2 outage fails open with a flag** | Nearly every injection in practice matches a pattern, so paying for a model call on all of them is latency for nothing; the ambiguous middle is where a model earns it. Failing open matters more: a moderation call that 429s must not swallow a candidate's submission, so the answer is accepted and flagged for a human instead. |
| A resume containing prompt injection | **Strip the offending lines, score the rest, and name what was cut to the candidate** | Template resumes off the internet carry "ignore all previous instructions" with the candidate none the wiser, so rejecting punishes the wrong person. The flags are stored on the row (`screening_flags`), so the recruiter sees the same notice and can judge intent. |
| Can the keyword floor be bypassed? | **Yes, per role, via `ignore_ats`** | The floor is a cost guard — it stops the model reading resumes that share no vocabulary with the role — not a judgement about a person. For a role whose requirement list is noisy or unusually worded, a recruiter can send every resume to the reviewer instead. |
| An admin submitting a resume on the apply page | **The row is always written as sandbox, with no toggle** | The page is reachable by admins so the module can be exercised end to end, and an admin is never a real candidate for the role. A switch would eventually be left off once and taint the shortlist a recruiter reads; making it unconditional means there is nothing to forget. |
| Text too short to be a resume | **A named floor — `MIN_RESUME_WORDS = 40` — quoted back with the actual count** | A scanned PDF yields a handful of stray words, and the old wording ("paste a few sentences") hid the rule it was enforcing. Naming the number turns a dead end into an instruction, and the same floor is quoted by both the upload and the paste path. |
| Getting from the home page to the right role | **Home writes `apply_role_id` into session state, then `st.switch_page`** | Streamlit seeds a keyed widget from session state, so writing the apply page's `st.selectbox` key *is* the deep link — no query params, no extra routing. The key is shared across two files, so a rename would break the link silently: `tests/test_apply_page.py` pins each half separately and they only pass together. |
| The plan vs. the model, mid-interview | **The tailored plan is authoritative. The model cannot end early while planned questions remain, and `next_planned` asks the plan's exact wording.** | A `wrap_up` that arrives two questions early, or a paraphrase that smooths off the "you lean on Kubernetes which your resume does not show" edge, quietly discards the one thing the plan was built to do. Each is blocked with its own comment and test; the model's value is *choosing between* the plan's moves, not rewriting them. |
| Steering cost | **The decision to steer is keyword work (watch topics matched against the answer); only the pivot's wording needs a model, and there is a template for it.** | The candidate raising a watch topic is deterministic to detect, so a throttled provider cannot stop the agent following the conversation — it can only make the follow-up read generic. `tests/test_interview.py` proves the whole interview, steering included, runs with every provider broken. |
| A guardrail that keeps refusing | **Accept the third attempt with a `forced_accept` flag, then ask the next planned question.** | A rejection loop that never exits is a way to lose an interview by answering badly three times. Once the retries are spent the answer is stored, sanitised, and flagged for the recruiter; the agent moves on rather than probing an answer it could not trust. |
| The interview timer at the window edge | **`deadline_at = min(now + duration, closes_at)`; a refresh resumes without spending the attempt.** | The deadline must never outlive the window, and a dropped connection is not a retry. Reporting `closes_at` as "time left" while the interview is still pending is also avoided — the countdown starts at `start()`, not at shortlist. |
| Detecting a second voice in the room | **Near-field vs far-field loudness in numpy, not a speaker-ID model** | Video proctoring has a structural blind spot: someone off camera reading answers aloud. Closing it with a diarisation model means another dependency, another download and another thing to be wrong about a person. The inverse-square law does most of the work — the candidate is ~40 cm from the mic and anyone else is metres away — and the *limits* of that (a television reads as a person; a lone distant voice reads as the candidate) are honest enough to write in the UI and pin in a test. Both signals stay `medium` with confidence ≤ 0.9 because loudness cannot identify anyone. |
| Recording the interview | **Answer audio only, one WAV per turn; no video** | Every score is computed from a transcript that a speech model *guessed at*, so a disputed score is unanswerable without the audio — and the background-voice signal is advisory precisely because a human is expected to listen. Video would mean muxing a WebRTC track per candidate: large dependency, large files, much heavier consent conversation, for a review need the audio already covers. |
| Demo data: sandbox rows or real ones? | **Real accounts on a `@demo.local` domain** | Sandbox rows are hidden from the recruiter pipeline by default, which is right for a persona an admin minted to test with and exactly wrong for a demo whose whole point is that the pipeline has people in it. The cost is that `--reset` deletes real rows, so it only ever touches that one domain and prints what it is about to remove. |
| What a sandbox reset owes the disk | **Sweep recordings and snapshots explicitly before the cascade** | The FK cascade reaches rows, and media is not a row. A "reset" that leaves audio of a test interview — and photographs of whoever sat it — on disk indefinitely is the kind of promise that is worse than not making it. |

## Notes / risks

- `st.navigation` is **sticky on the frontend**: a script run that does not call it keeps
  the previous run's page list on screen, so after a sign-out the departing account's nav
  lingered next to the login form. The signed-out screens now call `theme.hide_sidebar()`.
  Nothing was reachable through it — every page re-checks `session.require` — but it read
  as a bug.
- `AI-E2E-Job-Portal/` — the empty leftover from the git consolidation in Phase 0 —
  is gone. Windows held a handle on it for most of the build; it released.
- Proctoring is **advisory evidence for a human reviewer**, not proof of cheating. The
  integrity report is worded that way deliberately.
- `generate_dummies.py`, `generate_metrics.py` and `data/results/*.json` are the
  pre-portal pipeline. Nothing in the app reads them and neither script is needed to
  run anything; they are kept as a record of the original MVP's evaluation. The
  README says so rather than leaving a reader to guess which set of numbers is live.

