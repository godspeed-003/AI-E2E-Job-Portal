"""M8 — data egress: how much of a candidate leaves the machine, per provider.

This is the measurement behind the paper's deployment argument, and it is the
one number a hiring team is entitled to before they install anything.

Method
------

``requests.Session.post`` is replaced with a recorder that captures the exact
request body each provider would send and returns a schema-valid stub, then the
**real** pipeline is run: screen an application, generate a question plan,
conduct every turn, grade the transcript. Nothing is estimated — the bytes
counted are the bytes ``json.dumps`` would put on the wire, for the same code
path that runs in production, with only the socket removed.

Three configurations are compared:

``gemini``   ``POST https://generativelanguage.googleapis.com/...`` — the current
             default. Every byte crosses the public internet to a third party.
``ollama``   ``POST http://localhost:11434/api/generate`` — the same bytes, to a
             loopback socket. They do not leave the host.
``fake``     no HTTP at all.

What the comparison does and does not show
------------------------------------------

The byte counts are nearly identical across providers by design: the same prompt
is built either way, which is the point of the provider abstraction. **The
finding is not a smaller number — it is a different destination.** So the figure
worth putting in the paper is not "Ollama sends fewer bytes" but "the same
resume text goes to a loopback socket instead of to a third-party processor",
and the unit that matters is *who receives it*, measured here as the destination
host and whether it is loopback.

One consequence that is easy to miss and is measured here: the **guardrail's
tier 2** is a second, independent egress path. An off-topic answer is sent to
the model for moderation, so a candidate's words can leave the process even when
no interview turn is being graded.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse

import requests

import llm
from core import db, resume as resume_core
from core.config import settings
from llm.fake import FakeProvider
from llm.gemini import GeminiProvider
from llm.ollama import OllamaProvider
from metrics import Section
from metrics.corpora import make_cohort, make_resume_text
from services import (
    application_service as applications,
    auth_service as auth,
    catalog_service as catalog,
    guardrail_service as guardrails,
    interview_service as interviews,
)

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

_ANSWERS = (
    "We had p99 latency spikes in checkout during the evening peak. I captured "
    "a flame graph under load, found a synchronous call to the pricing service "
    "inside a per-item loop, and batched it into one request. p99 fell from "
    "2.1 s to 240 ms and the on-call pages stopped.",
    "I owned the migration of a nightly batch job to an incremental pipeline. "
    "The hardest constraint was that the downstream warehouse could not be "
    "locked for more than ninety seconds, so I wrote it as an append-only load "
    "with a view swap at the end, and kept the old job running in parallel for "
    "two weeks to diff the outputs.",
    "When a teammate disagreed with my schema design I asked them to write the "
    "three queries they cared about most. Two of them were awkward under my "
    "version, so we took theirs for those tables and mine for the event log, "
    "and documented why in the pull request.",
)


class _Response:
    """The minimum surface the provider code touches on a response."""

    def __init__(self, payload: dict[str, Any]):
        self._payload = payload
        self.status_code = 200
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class _Recorder:
    """Captures every request body a provider would send."""

    def __init__(self, provider_name: str):
        self.provider_name = provider_name
        self.requests: list[dict[str, Any]] = []
        self._stub = FakeProvider()

    def _reply_text(self, body: dict[str, Any]) -> str:
        """A schema-valid answer, chosen by the same heuristics the fake uses."""
        if self.provider_name == "gemini":
            prompt = body["contents"][0]["parts"][0]["text"]
        else:
            prompt = body.get("prompt", "")
        return self._stub._canned(prompt)

    def post(self, url: str, **kwargs: Any) -> _Response:
        body = kwargs.get("json") or {}
        serialised = json.dumps(body, separators=(",", ":"))
        parsed = urlparse(url)
        self.requests.append(
            {
                "url": url,
                "host": parsed.hostname or "",
                "scheme": parsed.scheme,
                "is_loopback": (parsed.hostname or "") in LOOPBACK_HOSTS,
                "body_bytes": len(serialised.encode("utf-8")),
                "header_bytes": sum(
                    len(str(k)) + len(str(v))
                    for k, v in (kwargs.get("headers") or {}).items()
                ),
                "body": body,
            }
        )
        text = self._reply_text(body)
        if self.provider_name == "gemini":
            payload = {
                "candidates": [{"content": {"parts": [{"text": text}]}}],
                "usageMetadata": {"totalTokenCount": len(text) // 4},
            }
        else:
            payload = {
                "response": text,
                "prompt_eval_count": len(serialised) // 4,
                "eval_count": len(text) // 4,
            }
        return _Response(payload)


@contextmanager
def _recording(provider_name: str):
    recorder = _Recorder(provider_name)
    original = requests.Session.post

    def patched(self, url, **kwargs):  # noqa: ANN001
        return recorder.post(url, **kwargs)

    requests.Session.post = patched  # type: ignore[method-assign]
    try:
        yield recorder
    finally:
        requests.Session.post = original  # type: ignore[method-assign]


def _build_provider(name: str):
    if name == "gemini":
        return GeminiProvider(
            api_keys=("not-a-real-key-no-request-is-sent",),
            model=settings.llm.gemini_model,
            api_base=settings.llm.gemini_api_base,
            temperature=settings.llm.temperature,
            timeout=settings.llm.timeout_seconds,
            retries=0,
        )
    if name == "ollama":
        return OllamaProvider(
            base_url=settings.llm.ollama_base_url,
            model=settings.llm.ollama_model,
            temperature=settings.llm.temperature,
            timeout=settings.llm.timeout_seconds,
            retries=0,
        )
    return FakeProvider()


def _one_candidate(counter: dict[str, int], role, resume) -> None:
    """Screen, interview and grade one candidate through the real services."""
    counter["n"] += 1
    user = auth.register(
        f"egress-{counter['n']}@example.test",
        "metrics-harness-1234",
        full_name="Egress Probe",
        is_sandbox=True,
    )
    application = applications.apply(user, role.id, resume, is_sandbox=True)
    application = applications.screen(application.id, force=True)

    interview = interviews.ensure_for_application(application, plan=True)
    try:
        interviews.start(interview.id, user_id=user.id, consent=True)
    except interviews.InterviewError:
        return
    for index in range(12):
        turn = interviews.ask_next(interview.id)
        if turn is None:
            break
        try:
            interviews.submit_answer(
                interview.id,
                _ANSWERS[index % len(_ANSWERS)],
                user_id=user.id,
                transcript_source="typed",
            )
        except interviews.InterviewError:
            break
    try:
        interviews.finish(interview.id, reason="completed", score_now=True)
    except interviews.InterviewError:
        pass


def run() -> Section:
    section = Section(
        key="m8_egress",
        title="Data egress per candidate, by provider (bytes and destination)",
    )

    db.init_db()
    catalog.seed_from_json()
    roles = catalog.list_roles()
    if not roles:
        section.add(
            "role_catalogue",
            None,
            kind="unavailable",
            source="data/roles.json",
            needs="a seeded role catalogue",
        )
        return section
    role = max(roles, key=lambda r: len(r.requirements))

    cohort = make_cohort(n_per_case=4)
    text, _ = make_resume_text(cohort.candidates[0], list(role.requirements))
    resume = resume_core.from_text(text)
    resume_bytes = len(text.encode("utf-8"))

    section.add(
        "resume_size",
        resume_bytes,
        unit="bytes",
        kind="specification",
        source="metrics.corpora.make_resume_text",
        note="one generated resume, used identically across all three providers",
    )
    section.add(
        "max_resume_chars_sent",
        applications.MAX_RESUME_CHARS,
        unit="characters",
        kind="specification",
        source="services.application_service.MAX_RESUME_CHARS",
        note=(
            "the prompt truncates the resume here, so this is the ceiling on how "
            "much of one document can be sent in a single screening call"
        ),
    )

    counter = {"n": 0}
    per_provider: dict[str, Any] = {}
    # Every body intercepted anywhere in this run, so the "media never leaves"
    # claim below can be *computed* over all of them rather than asserted.
    all_intercepted: list[dict[str, Any]] = []
    original = llm.get_llm()

    for name in ("fake", "gemini", "ollama"):
        provider = _build_provider(name)
        llm.set_provider(provider)
        with _recording(name) as recorder:
            _one_candidate(counter, role, resume)
        calls = recorder.requests
        all_intercepted.extend(calls)
        body_bytes = sum(c["body_bytes"] for c in calls)
        hosts = sorted({c["host"] for c in calls if c["host"]})
        resume_text_copies = sum(
            1
            for call in calls
            if text[:400] in json.dumps(call["body"])
        )
        per_provider[name] = {
            "http_requests": len(calls),
            "body_bytes": body_bytes,
            "header_bytes": sum(c["header_bytes"] for c in calls),
            "destination_hosts": hosts,
            "all_loopback": all(c["is_loopback"] for c in calls) if calls else True,
            "leaves_the_host": any(not c["is_loopback"] for c in calls),
            "mean_bytes_per_call": (body_bytes / len(calls)) if calls else 0,
            "calls_containing_resume_text": resume_text_copies,
            "bytes_per_resume_byte": (
                round(body_bytes / resume_bytes, 2) if resume_bytes else 0
            ),
        }
    llm.set_provider(original)

    section.tables["by_provider"] = per_provider

    for name, stats in per_provider.items():
        section.add(
            f"{name}_http_requests_per_candidate",
            stats["http_requests"],
            unit="requests",
            kind="measured",
            source="metrics.m8_egress (requests.Session.post intercepted)",
        )
        section.add(
            f"{name}_bytes_per_candidate",
            stats["body_bytes"],
            unit="bytes",
            kind="measured",
            source="metrics.m8_egress",
            note=(
                "destination: "
                + (", ".join(stats["destination_hosts"]) or "none — no HTTP at all")
            ),
        )
        section.add(
            f"{name}_leaves_the_host",
            stats["leaves_the_host"],
            kind="measured",
            source="metrics.m8_egress",
        )

    gemini = per_provider["gemini"]
    ollama = per_provider["ollama"]
    section.add(
        "bytes_crossing_the_network_gemini",
        gemini["body_bytes"],
        unit="bytes per candidate",
        kind="measured",
        source="metrics.m8_egress",
        note=(
            f"{gemini['http_requests']} requests to "
            f"{', '.join(gemini['destination_hosts'])}, covering one screening, "
            "the question plan, every interview turn, each turn's guardrail "
            "check and the final grading. Most of the volume is prompt template "
            "and accumulated transcript rather than the resume itself — see the "
            "size decomposition below."
        ),
    )
    section.add(
        "bytes_crossing_the_network_ollama",
        0,
        unit="bytes per candidate",
        kind="measured",
        source="metrics.m8_egress",
        note=(
            f"{ollama['http_requests']} requests carrying {ollama['body_bytes']} "
            "bytes, all to a loopback socket. The payload is the same size; the "
            "difference the paper should claim is the recipient, not the volume."
        ),
    )
    section.add(
        "payload_size_ratio_ollama_to_gemini",
        round(ollama["body_bytes"] / gemini["body_bytes"], 4)
        if gemini["body_bytes"]
        else None,
        kind="derived",
        source="metrics.m8_egress",
        note=(
            "≈1 by construction: the provider abstraction builds the same prompt "
            "for both. This is the evidence that switching to local inference is "
            "a configuration change and not a capability reduction."
        ),
    )
    section.add(
        "third_party_processors_gemini",
        1,
        unit="processors",
        kind="derived",
        source="llm.gemini.GeminiProvider",
        note=(
            "Google, receiving resume text, generated questions and the "
            "candidate's verbatim answers. Under GDPR Art. 28 that is a "
            "processor relationship requiring a contract; under the EU AI Act's "
            "Annex III it is a high-risk employment use. The paper must say this "
            "plainly rather than describing the cloud provider as an "
            "implementation detail."
        ),
    )
    section.add(
        "third_party_processors_ollama",
        0,
        unit="processors",
        kind="derived",
        source="llm.ollama.OllamaProvider",
        note=(
            "the candidate's data reaches no party other than the operator. This "
            "is the substantive privacy claim of the architecture."
        ),
    )

    # ------------------------------------------------------------------ #
    # How egress scales with resume size
    # ------------------------------------------------------------------ #
    # A single byte count is not much use to a reader with a different corpus.
    # Running the same pipeline with a resume an order of magnitude larger gives
    # the slope, so the paper can state egress as a formula rather than as one
    # number measured on one synthetic document.
    long_text = "\n".join(text for _ in range(10))
    long_resume = resume_core.from_text(long_text)
    long_bytes = len(long_text.encode("utf-8"))
    provider = _build_provider("ollama")
    llm.set_provider(provider)
    with _recording("ollama") as recorder:
        _one_candidate(counter, role, long_resume)
    llm.set_provider(original)
    all_intercepted.extend(recorder.requests)
    long_total = sum(c["body_bytes"] for c in recorder.requests)

    delta_resume = long_bytes - resume_bytes
    slope = (
        round((long_total - ollama["body_bytes"]) / delta_resume, 2)
        if delta_resume
        else None
    )
    fixed = (
        int(round(ollama["body_bytes"] - (slope or 0) * resume_bytes))
        if slope is not None
        else None
    )
    section.tables["egress_scaling"] = [
        {
            "resume_bytes": resume_bytes,
            "total_request_bytes": ollama["body_bytes"],
            "http_requests": ollama["http_requests"],
        },
        {
            "resume_bytes": long_bytes,
            "total_request_bytes": long_total,
            "http_requests": len(recorder.requests),
        },
    ]
    section.add(
        "egress_bytes_per_resume_byte",
        slope,
        unit="request bytes per resume byte",
        kind="derived",
        source="metrics.m8_egress (two resume sizes, same pipeline)",
        note=(
            f"measured between a {resume_bytes} B and a {long_bytes} B resume. "
            "Only the screening call and the question-plan call carry the resume, "
            "so the slope is close to the number of prompts that include it — the "
            "rest of the payload does not grow with the document."
        ),
    )
    section.add(
        "egress_fixed_overhead",
        fixed,
        unit="bytes per candidate",
        kind="derived",
        source="metrics.m8_egress",
        note=(
            "the resume-independent part: prompt templates, the role "
            "description, the rubric and the accumulating transcript. For a "
            "realistic 2–3 page resume of 5–15 KB the total is roughly "
            "`fixed + slope x resume_bytes`, which is the form the paper should "
            "quote rather than a single figure measured on one synthetic document."
        ),
    )

    # ------------------------------------------------------------------ #
    # Per-call breakdown: which prompts carry the sensitive material
    # ------------------------------------------------------------------ #
    provider = _build_provider("ollama")
    llm.set_provider(provider)
    with _recording("ollama") as recorder:
        _one_candidate(counter, role, resume)
    llm.set_provider(original)
    all_intercepted.extend(recorder.requests)
    call_rows = []
    for index, call in enumerate(recorder.requests, start=1):
        prompt = call["body"].get("prompt", "")
        call_rows.append(
            {
                "call": index,
                "body_bytes": call["body_bytes"],
                "prompt_chars": len(prompt),
                "contains_resume_text": text[:400] in prompt,
                "contains_candidate_answer": any(
                    answer[:80] in prompt for answer in _ANSWERS
                ),
                "schema_constrained": isinstance(call["body"].get("format"), dict),
                "stage": _classify(prompt),
            }
        )
    section.tables["per_call"] = call_rows

    resume_carrying = [r for r in call_rows if r["contains_resume_text"]]
    answer_carrying = [r for r in call_rows if r["contains_candidate_answer"]]
    section.add(
        "calls_carrying_resume_text",
        len(resume_carrying),
        unit=f"of {len(call_rows)}",
        kind="measured",
        source="metrics.m8_egress",
        note=(
            "the resume is not sent once — it is sent again with every prompt "
            "that needs context, so a per-candidate egress figure is several "
            "multiples of the document's size"
        ),
    )
    section.add(
        "calls_carrying_candidate_answers",
        len(answer_carrying),
        unit=f"of {len(call_rows)}",
        kind="measured",
        source="metrics.m8_egress",
    )
    section.add(
        "schema_constrained_calls",
        sum(1 for r in call_rows if r["schema_constrained"]),
        unit=f"of {len(call_rows)}",
        kind="measured",
        source="llm.ollama.OllamaProvider._generate_once",
        note=(
            "a JSON Schema is passed in `format`, which constrains Ollama's "
            "decoding so the reply is structurally incapable of carrying an "
            "instruction the parser would act on. The remaining calls pass "
            "`format=\"json\"`, which guarantees valid JSON but not a fixed "
            "shape — a weaker guarantee, and the difference is worth stating: "
            "registering schemas for the interview prompts too would extend the "
            "strong form to the whole pipeline."
        ),
    )

    tier2_in_pipeline = [r for r in call_rows if r["stage"] == "guardrail_tier2"]
    section.add(
        "tier2_calls_within_one_interview",
        len(tier2_in_pipeline),
        unit=f"of {len(call_rows)} calls",
        kind="measured",
        source="services.guardrail_service.check_answer",
        note=(
            "escalations observed in this run. The *rate* is an artefact of the "
            "stub, not a finding: the fake provider's canned question bears no "
            "lexical relation to the canned answer, so word overlap falls below "
            "the 0.06 threshold and tier 2 fires almost every turn. With a real "
            "model the question is derived from the resume and the answer "
            "addresses it, so overlap is higher and escalation rarer. What is "
            "measured here is that the path exists and carries the candidate's "
            "verbatim answer; how often it fires needs a real run."
        ),
    )

    # ------------------------------------------------------------------ #
    # The second egress path: tier-2 moderation
    # ------------------------------------------------------------------ #
    provider = _build_provider("ollama")
    llm.set_provider(provider)
    with _recording("ollama") as recorder:
        guardrails.check_answer(
            "I have been cycling for eleven years and completed three long "
            "distance tours across the Alps, which taught me a great deal about "
            "pacing and about planning routes carefully well in advance.",
            question="Tell me about a production incident you debugged.",
            use_model=True,
        )
    llm.set_provider(original)
    tier2_calls = list(recorder.requests)
    all_intercepted.extend(tier2_calls)
    section.add(
        "tier2_moderation_egress_calls",
        len(tier2_calls),
        unit="requests",
        kind="measured",
        source="services.guardrail_service._model_verdict",
        note=(
            "one off-topic answer triggers a model call purely to decide whether "
            "it is on topic. This is a second egress path, independent of "
            "grading: a candidate's words can leave the process even when no "
            "turn is being scored. Under Ollama it stays on the host; under "
            "Gemini it does not. The paper's threat model has to list it."
        ),
    )
    section.tables["tier2_moderation_request"] = [
        {
            "body_bytes": call["body_bytes"],
            "host": call["host"],
            "is_loopback": call["is_loopback"],
        }
        for call in tier2_calls
    ]

    # ------------------------------------------------------------------ #
    # What stays local regardless of provider
    # ------------------------------------------------------------------ #
    section.tables["never_transmitted"] = {
        "video_recording": "written to MEDIA_DIR, never attached to any request",
        "enrollment_snapshot": "local file; face matching runs in-process (MediaPipe/OpenCV)",
        "proctor_snapshots": "local files, paths only in the database",
        "answer_audio": "local files; transcription is faster-whisper in-process",
        "password_hash": "never read by any provider code path",
        "session_tokens": "sha256-hashed at rest, never sent anywhere",
    }
    # The claim is computed, not asserted: scan every body intercepted anywhere
    # in this run — all three providers, the per-call pass and the tier-2 call.
    media_terms = (
        "recording_path",
        "snapshot_path",
        "enrollment_snapshot",
        "answer_audio",
        ".mp4",
        ".webm",
        ".wav",
        ".jpg",
        ".png",
        "data:video",
        "data:audio",
        "data:image",
        "base64",
    )
    leaked = []
    for row in all_intercepted:
        serialised = json.dumps(row["body"])
        hits = [term for term in media_terms if term in serialised]
        if hits:
            leaked.append({"host": row["host"], "terms": hits})

    section.add(
        "media_paths_found_in_request_bodies",
        len(leaked),
        unit=f"of {len(all_intercepted)} intercepted requests",
        kind="measured",
        source="metrics.m8_egress",
        note=(
            "scanned for file paths, media extensions, data: URIs and base64 "
            "blobs. A positive number here would contradict the claim below."
            if not leaked
            else "CONTRADICTS the claim below: " + json.dumps(leaked[:3])
        ),
    )
    section.add(
        "media_never_leaves_the_host",
        not leaked,
        kind="measured",
        source="metrics.m8_egress (every intercepted body scanned)",
        note=(
            f"computed over all {len(all_intercepted)} intercepted request bodies "
            "across all three providers: video, audio and snapshot data appear in "
            "none of them. All computer vision and speech-to-text run in-process, "
            "so the biometric material — the most sensitive category in the "
            "inventory — is local even in the cloud configuration."
        ),
    )

    section.add(
        "encryption_at_rest",
        None,
        kind="unavailable",
        source="not implemented",
        needs=(
            "SQLite is unencrypted and MEDIA_DIR holds plain video files, so the "
            "database-file protections rest entirely on filesystem permissions. "
            "Options worth naming in the paper: SQLCipher for the database, an "
            "OS-level encrypted volume for media, or a retention job that deletes "
            "recordings after the hiring decision. State this as a limitation — "
            "it is a real gap, not a configuration choice."
        ),
    )
    section.add(
        "automatic_retention_policy",
        None,
        kind="unavailable",
        source="services.proctor_service.purge_snapshots, "
        "services.recording_service, services.auth_service.purge_expired_sessions",
        needs=(
            "Three purge functions exist and are correct, but nothing calls them "
            "on a schedule: deletion is manual. A defensible retention claim "
            "needs a documented period and a job that enforces it. Until then "
            "the honest sentence is 'deletion primitives exist; retention is the "
            "operator's responsibility'."
        ),
    )

    section.commentary = (
        "Byte volume is almost identical across providers, because the same "
        "prompt is built either way — so the result is about destination, not "
        "size. Under the shipped default, a candidate's resume text and every "
        "verbatim answer reach a third-party processor; under Ollama the same "
        "payload reaches a loopback socket. Biometric media never leaves the "
        "host in either configuration, because vision and speech run in-process. "
        "The two gaps are named rather than glossed: nothing is encrypted at "
        "rest, and retention is manual."
    )
    return section


def _classify(prompt: str) -> str:
    lowered = prompt.lower()
    if "question plan" in lowered or "interview_plan" in lowered:
        return "interview_plan"
    if "decide the next question" in lowered or "next_turn" in lowered:
        return "next_turn"
    if "interview transcript" in lowered or "interview_scoring" in lowered:
        return "interview_score"
    if "guardrail" in lowered or "prompt injection" in lowered:
        return "guardrail_tier2"
    return "resume_evaluation"
