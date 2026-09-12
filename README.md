# AI E2E Job Portal

An end-to-end hiring portal: candidates apply, an AI screens the resume, an AI
**interviews them over video**, proctoring watches the room, and a recruiter reads
the whole file — scores, transcript and integrity evidence — in one place.

Everything runs on free and open-source software. The only optional paid piece is
the text model, and it sits behind a provider interface: `LLM_PROVIDER=ollama`
moves the whole portal onto a local model without touching a line of code.

```bash
streamlit run app.py
```

---

## What it does

| Stage | What happens | Who sees it |
| --- | --- | --- |
| **Register / sign in** | scrypt-hashed passwords, cookie-backed sessions that survive a refresh, login rate limiting | everyone |
| **Apply** | PDF/DOCX/paste → text → keyword (ATS) score → model evaluation on five criteria → `rejected` / `under_review` / `shortlisted` | candidate |
| **Plan** | on shortlist, questions are generated from *this* resume against *this* job description, each tagged with its source | nobody — it runs in the background |
| **Interview** | a WebRTC room: the AI asks aloud and in text, the candidate answers by voice, Whisper transcribes, the agent decides whether to probe, steer or move on | candidate |
| **Proctor** | face presence, head pose, identity drift, phone/second-person detection, tab switches, background voices | recruiter, afterwards |
| **Score** | transcript → five criteria out of 5, strengths, gaps, summary; integrity score 0–100 with a verdict | recruiter |
| **Review** | ranked pipeline, per-candidate tabs for screening / interview / integrity, audio of each answer, manual status override | recruiter |

Five roles ship in the catalogue across two companies — backend, data, HR,
marketing and operations — so there is something to apply to on the first run,
and the interview plan has a non-engineering role to be tested against.

---

## Quickstart

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

On Linux/macOS use `.venv/bin/python` and `cp`.

Then edit `.env`. Three settings actually matter to get started:

```ini
GEMINI_API_KEY=your-key-here      # or LLM_PROVIDER=ollama for no key at all
ADMIN_PASSWORD=something-you-pick # the bootstrap admin account
RECRUITER_INVITE_CODE=something   # anyone with this can register as a recruiter
```

Check the wiring before you trust it:

```bash
python scripts/healthcheck.py
```

Give yourself something to look at, then start:

```bash
python scripts/seed.py --candidates 6
```

```bash
streamlit run app.py
```

The seed script prints every account it made and its password. `--offline` seeds
with canned scores and makes no model calls at all; `--reset` deletes everything
it created and nothing else.

### No API key?

```bash
ollama pull llama3.1
ollama serve
```

…and set `LLM_PROVIDER=ollama`. Speech-to-text (faster-whisper) and
text-to-speech (pyttsx3) are already local, so the portal then runs with no
network access whatsoever.

---

## The pages

Navigation is role-aware: `app.py` hands `st.navigation` only the pages the
signed-in account may use, and every page re-checks the role on entry anyway.

**Candidate** — Home (roles, applications, interview invites) · Apply · Interview
room · Account

**Recruiter** — Pipeline (ranked candidates, three tabs each, status override) ·
Roles (create/edit roles, thresholds, interview windows)

**Admin** — Users (search, enable/disable, force sign-out, audit tail) · Health
(per-backend probes, model downloads) · Sandbox (throwaway personas, skip-ahead,
one-click reset)

---

## Architecture

```text
app.py                  role-aware router (st.navigation)
ui/
  theme.py              injected CSS design system: pills, score rings, tiles
  session.py            auth token ⇄ cookie, sandbox view flag
  pages/                one module per screen, render() only
services/               all business logic, UI-agnostic
  auth_service          register / login / sessions / roles
  catalog_service       companies and roles
  application_service   apply → ATS → evaluate → status → prepare interview
  interview_service     plan, turn decisions, budget, scoring
  proctor_service       events, snapshots, integrity score
  recording_service     answer audio on disk
  guardrail_service     prompt-injection defence, answer checks
  access.py             the company boundary
  health_service        every probe, for the admin page and the CLI
core/
  config.py             one frozen settings object, read from env at import
  db.py                 SQLite schema, in-place migration, WAL, connections
  resume.py             PDF/DOCX/text → clean text → keyword score
  security.py           scrypt hashing, session tokens
  model_assets.py       cached download of the CV weights
llm/                    base · gemini · ollama · openai_compat · fake
speech/                 stt (faster-whisper) · tts (pyttsx3 / piper / browser)
proctoring/             analyzer (vision) · audio · rules · session (threading)
prompts/                every prompt as a file, not a string literal
scripts/                seed.py · healthcheck.py
tests/                  272 tests, fully offline
```

Three rules hold the shape together:

1. **Pages render; services decide.** No page computes a score or writes a row it
   owns. A React front end later would be additive, not a rewrite.
2. **State lives in SQLite, never in session state.** An interview survives F5
   because there was nothing in memory to lose.
3. **Every AI call goes through a provider.** Switching backends is an env var;
   the test suite swaps in `llm/fake.py` and runs with no network.

### Data model

Eleven tables in one SQLite file (`data/app.db`, WAL mode). Foreign keys are on
with `ON DELETE CASCADE` all the way down: `users` → `applications` →
`interviews` → `interview_turns` → `proctor_events`. Deleting an account really
does delete everything it produced, which is what makes sandbox reset honest.

Schema changes land as `CREATE TABLE IF NOT EXISTS` → `ALTER TABLE` migration →
indexes, in that order, so an existing database upgrades in place rather than
needing a wipe. `tests/test_db_migration.py` builds a legacy table and asserts it.

---

## Switching the AI backends

| What | Env var | Options |
| --- | --- | --- |
| Text model | `LLM_PROVIDER` | `gemini` · `ollama` · `openai_compat` (llama.cpp, vLLM, LM Studio) · `fake` |
| Speech → text | `STT_PROVIDER` | `faster_whisper` (local, default) · `gemini` · `disabled` |
| Text → speech | `TTS_PROVIDER` | `pyttsx3` (local, default) · `piper` · `browser` · `disabled` |

There is no cloud SDK in `requirements.txt` on purpose. The Gemini backend is
plain REST over `requests`, the same shape as the other two, so moving to a local
model is a config change and not an install.

Gemini keys rotate: give several comma-separated or as `GEMINI_API_KEY2`, and the
provider moves to the next one on HTTP 429.

---

## Proctoring, honestly

Proctoring produces **advisory evidence for a human reviewer**. It is not proof
of cheating and the UI never claims it is.

| Signal | How | Backend |
| --- | --- | --- |
| No face / prolonged absence | face detection over sampled frames | YuNet (Apache-2.0, ships in OpenCV) |
| Multiple faces | same | YuNet |
| Looking away | head pose from landmarks, yaw/pitch limits | MediaPipe FaceLandmarker (Apache-2.0) |
| Candidate substitution | embedding distance against the enrolment frame | SFace (Apache-2.0) |
| Phone, second person, book | object detection, **off by default** | YOLO11n (AGPL-3.0) |
| Tab switch, window blur, paste | browser watcher → query param → event rows | no model |
| Another voice in the room | near-field vs far-field loudness in the answer audio | numpy only |

What it cannot do, stated plainly:

- **No speaker identification.** The audio check is loudness arithmetic — the
  candidate is ~40 cm from the mic and anyone else is metres away and 15–25 dB
  down. A television reads the same as a person, and a *lone* distant voice reads
  as the candidate. Both audio signals are `medium` severity with confidence
  capped at 0.9 for exactly this reason.
- **No fullscreen-exit detection.** Streamlit owns the page chrome; there is no
  fullscreen to leave.
- **A missing model or failed import silences one signal**, never the interview.

The integrity score (0–100) is a pure function of the stored event rows, so it
can be re-derived later without the video. A `critical` event floors the verdict
at `review` rather than letting the arithmetic call a session clean.

Answer audio is kept at `data/media/interviews/<id>/answer_NN.wav`
(`PROCTOR_RECORD_SESSION=false` to stop), because every score in the portal is
computed from a transcript a speech model *guessed at*, and the recording is the
only way to check the guess. Video is never written to disk.

---

## Testing

```bash
python -m pytest -q
```

272 tests, no network, no API key, no model weights, no frame decoded. The suite
points itself at a throwaway database in `conftest.py` before any project module
is imported, so it can never touch `data/app.db`.

```text
test_screening.py         57   ATS scoring, ingestion of real PDF/DOCX bytes, guardrails
test_proctoring.py        54   debounce, severity weighting, integrity scoring
test_interview.py         36   plan, steering, budget, scoring, window rules
test_auth.py              28   hashing, lockout, expiry, role gating, cookies
test_recruiter.py         23   the company boundary, cross-company reads, ranking
test_apply_page.py        18   the apply screen driven through AppTest
test_audio_proctoring.py  16   near/far-field attribution, and what it cannot do
test_app_routing.py       12   role-aware navigation
test_sandbox.py           14   the sandbox boundary and the skip-ahead shortcut
test_recording.py         11   clips reachable from their turn; failures degrade
test_db_migration.py       3   a legacy database upgrades in place
```

A full interview is sat in the suite **with every provider broken**, which is the
test that matters most: a throttled model must never cost a candidate their
interview.

---

## Testing it by hand

The admin **Sandbox** page exists so nobody has to invent a fake person in the
real database. Everything created there carries `is_sandbox = 1`, which keeps it
out of recruiter reporting and makes cleanup one click.

1. Create a disposable persona (`candidate@sandbox.local`, fixed password).
2. **Skip ahead**: shortlist it for a role with a **2-question** interview.
3. Sign in as the persona in a private window and sit the interview.
4. Read it back on the recruiter pipeline.
5. **Reset sandbox** deletes every sandbox row, cascade and media included.

---

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `python scripts/healthcheck.py` says FAIL on the LLM | no key, or `LLM_PROVIDER` points at a server that is not running |
| Face detection never fires | `cv2` resolved to the non-contrib build; `pip install --force-reinstall opencv-contrib-python==5.0.0.93` |
| Camera preview is black | another tab holds the device; Streamlit cannot take it from them |
| "Paste a resume of at least 40 words" | a scanned PDF with no text layer — PyMuPDF found nothing to extract |
| Login succeeds then F5 signs you out | cookies blocked for `localhost` |
| `WinError 32` during tests | a stale `.venv` process holding `test.db`; the suite closes every connection, but a crashed run can leak one |

---

## Legacy analytics

`generate_metrics.py` and `generate_dummies.py` predate the portal and read the
old JSON pipeline in `data/results/`. The charts in `results/metrics/` were
produced by that pipeline, not by the app described above. They are kept as a
record of the original MVP's evaluation rather than as current output, and
neither script is needed to run anything here. They additionally want
`matplotlib` and `seaborn`, which is why those are not pinned in
`requirements.txt`.

---

## Project status

`tasks.md` is the build log, phase by phase, including the decisions taken and
why. Known gaps as of the last commit:

- **LiveKit transport** (Phase 4b) is deliberately not built. It is the better
  realtime stack — server-side VAD, barge-in, streaming speech — but it costs two
  extra processes to run, which fights "easy to set up". The interview engine
  takes text answers in and questions out, so a LiveKit worker can call the same
  functions later without touching the logic.
- **Live candidate coaching** during the interview ("centre yourself in frame")
  is not wired.
- The browser-signal watcher is verified in a browser but **not** end to end with
  a live camera.

---

## Licence

MIT — see [LICENSE](LICENSE).

Bundled models carry their own licences: YuNet, SFace and MediaPipe
FaceLandmarker are Apache-2.0; YOLO11n is **AGPL-3.0**, which is why object
detection ships switched off behind `PROCTOR_OBJECT_DETECTION`.
