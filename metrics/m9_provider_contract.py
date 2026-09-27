"""M9 — does the provider still accept what we send it?

Every other module in this harness measures code we wrote. This one measures
something we do not control: the dialect of JSON Schema that a hosted model API
happens to accept this week. That distinction is the reason the module exists.

The bug it was written for
--------------------------

``llm/gemini.py`` filters caller schemas through an allow-list, because Gemini
rejects unknown JSON-Schema keywords. The filter was applied uniformly — and
inside ``properties`` the dictionary keys are not keywords, they are the
caller's own field names. So ``{"safe": ..., "reason": ...}`` was filtered to
``{}``, leaving an empty ``properties`` beside a ``required`` list naming fields
that no longer existed, and every schema-constrained call in the project came
back as::

    HTTP 400 … response_schema.required[0]: property is not defined

``tests/test_llm_gemini.py`` now pins the shape of the sanitised output. But a
unit test can only prove we build what we meant to build. It cannot prove the
vendor still accepts it, and the failure mode here is specifically a vendor-side
change. So this module does two different jobs:

**Offline, always.** Walk the schemas the project actually ships — not toy ones —
and check that sanitisation preserves every field named in a ``required`` list,
and that no keyword the shipped schemas rely on is being silently dropped by the
allow-list. This runs in the default harness pass and needs no network.

**Live, opt-in.** Send those same schemas to the real API and record what comes
back, together with two controls: the pre-fix broken shape, and an unsanitised
schema. If the controls are *accepted* then sanitisation is no longer load-bearing
and the paper should stop describing it as a necessary adaptation; if the shipped
schemas are *rejected* then the vendor's dialect has moved. Either way the answer
arrives as a measurement rather than as a support ticket.

Enable the live half with::

    METRICS_LIVE_PROVIDER=gemini .venv/Scripts/python -m metrics.run_all --only m9_provider_contract

It costs four API calls, uses the key already in ``.env``, and sends only the
fixed probe strings below — no cohort data, no resume text, nothing from the
database. When the variable is unset every live quantity is reported
``unavailable`` with the command that would produce it, and the offline
structural checks still run.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests

from metrics import PROJECT_ROOT, RESULTS_DIR, Section, write_json

# The switch that lets a network call out of the harness. Off by default: a
# metrics run must stay reproducible on a machine with no key and no internet,
# and a module that silently spends someone's quota is a bad neighbour.
LIVE_ENV_VAR = "METRICS_LIVE_PROVIDER"

# Where a live run leaves its evidence so an offline run can still report it.
# Committed with the rest of the results: it is the record of a measurement that
# happened, which is exactly what a paper cites when the measurement needs a
# credential the reader does not have.
LIVE_CACHE = RESULTS_DIR / "live_provider_contract.json"

# Probe prompts. Deliberately trivial and deliberately fixed: this module is
# measuring whether the request shape is accepted, not what the model knows, so
# the cheapest prompt that can legitimately satisfy each schema is the right one.
# Nothing candidate-derived is ever sent from here.
#
# ``max_output_tokens`` mirrors the production call site exactly — 1200 for
# screening, unset for the guardrail — because a probe that invents its own
# budget measures the probe. That is not hypothetical: an earlier draft of this
# module used a flat 800 and the screening schema came back HTTP 200 with
# truncated JSON, which looks like a schema failure and is not one.
_EVAL_PROBE = (
    "Score this one-line resume against a backend role. "
    "Resume: 'Five years of Python and PostgreSQL, led two API migrations.'"
)
_ANSWER_PROBE = (
    "Question: 'Describe a system you designed.' "
    "Answer: 'I built a job queue on Postgres that handled retries idempotently.' "
    "Is this a genuine attempt at the question?"
)

# name -> (prompt, max_output_tokens as the shipping call site passes it)
_PROBES: dict[str, tuple[str, int | None]] = {
    "screening_evaluation": (_EVAL_PROBE, 1200),
    "guardrail_answer_verdict": (_ANSWER_PROBE, None),
}


def _shipped_schemas() -> dict[str, dict[str, Any]]:
    """The schema constants the project really sends, imported from their modules."""
    from services.application_service import _EVAL_SCHEMA
    from services.guardrail_service import _ANSWER_SCHEMA

    return {
        "screening_evaluation": _EVAL_SCHEMA,
        "guardrail_answer_verdict": _ANSWER_SCHEMA,
    }


def _keywords_used(schema: Any) -> set[str]:
    """Schema *keywords* in a schema, with caller field names excluded.

    Mirrors ``sanitize_schema``'s own structure: descend into ``properties``
    values but never treat a ``properties`` key as a keyword, because that is
    exactly the conflation the original defect was made of.
    """
    found: set[str] = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            found.add(key)
            if key == "properties" and isinstance(value, dict):
                for sub in value.values():
                    found |= _keywords_used(sub)
            else:
                found |= _keywords_used(value)
    elif isinstance(schema, list):
        for item in schema:
            found |= _keywords_used(item)
    return found


def _orphaned_required_fields(schema: Any, cleaned: Any) -> list[str]:
    """Names a ``required`` list demands that sanitisation left no property for.

    This is the defect stated as a measurable quantity. An empty list is the
    only acceptable result; anything in it is a schema the API will reject with
    "property is not defined".
    """
    orphans: list[str] = []
    if not isinstance(schema, dict) or not isinstance(cleaned, dict):
        return orphans

    clean_properties = cleaned.get("properties") or {}
    for name in schema.get("required") or []:
        if name not in clean_properties:
            orphans.append(name)

    for name, sub in (schema.get("properties") or {}).items():
        orphans += _orphaned_required_fields(sub, clean_properties.get(name))
    if "items" in schema:
        orphans += _orphaned_required_fields(schema["items"], cleaned.get("items"))
    return orphans


def _broken_sanitiser_output(schema: dict[str, Any]) -> dict[str, Any]:
    """Reproduce the pre-fix output: keywords kept, field names deleted.

    Used as a live control. If the API accepts this, the bug it caused was never
    the API's doing and the fix is explained wrongly in the paper.
    """
    return {
        "type": schema.get("type", "object"),
        "properties": {},
        "required": list(schema.get("required") or []),
    }


def _call_site_census() -> list[dict[str, Any]]:
    """Which model call sites constrain the response with a schema, and which do not.

    A fact about the system worth stating plainly: only two of the five call
    sites pass a schema. The other three ask for JSON in the prompt and lean on
    ``llm.base.extract_json`` to repair whatever comes back. That is a
    deliberate trade — the interview prompts are long and their shapes are
    nested — but it means the JSON-repair path is load-bearing in production,
    not a fallback, and the paper should not imply every call is schema-pinned.
    """
    rows: list[dict[str, Any]] = []
    pattern = re.compile(r"generate_json\(", re.MULTILINE)
    for relative in ("services/application_service.py",
                     "services/guardrail_service.py",
                     "services/interview_service.py"):
        path = PROJECT_ROOT / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            # Look at the argument list only, bounded by the next blank line or
            # 400 characters — enough to see whether `schema=` was passed.
            window = text[match.end(): match.end() + 400]
            line_number = text.count("\n", 0, match.start()) + 1
            rows.append(
                {
                    "module": relative,
                    "line": line_number,
                    "schema_constrained": "schema=" in window.split("\n\n")[0],
                }
            )
    return rows


# --------------------------------------------------------------------------- #
# Live probing
# --------------------------------------------------------------------------- #


def _post_raw(
    provider: Any,
    schema: dict[str, Any] | None,
    prompt: str,
    max_output_tokens: int | None = None,
) -> dict[str, Any]:
    """One generateContent call with a caller-supplied ``responseSchema``.

    Deliberately bypasses ``GeminiProvider.generate`` rather than reusing it.
    Two of the four probes below are *supposed* to be rejected, and the provider
    would sanitise them into validity before they ever left the process — which
    would measure the sanitiser twice and the API not at all. Going round it is
    the only way to ask the vendor the question directly.
    """
    generation_config: dict[str, Any] = {
        "temperature": 0.0,
        "responseMimeType": "application/json",
    }
    if max_output_tokens:
        generation_config["maxOutputTokens"] = max_output_tokens
    if schema is not None:
        generation_config["responseSchema"] = schema

    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }
    url = f"{provider.api_base}/models/{provider.model}:generateContent"
    started = time.time()
    try:
        response = requests.post(
            url,
            headers={
                "x-goog-api-key": provider.api_keys[0],
                "Content-Type": "application/json",
            },
            json=body,
            timeout=provider.timeout,
        )
    except requests.RequestException as exc:
        return {"ok": False, "status": None, "error": str(exc), "seconds": None}

    elapsed = time.time() - started
    payload: dict[str, Any] = {}
    try:
        payload = response.json()
    except ValueError:
        pass
    usage = payload.get("usageMetadata") or {}
    candidates = payload.get("candidates") or []
    return {
        "ok": response.status_code == 200,
        "status": response.status_code,
        "seconds": round(elapsed, 3),
        "error": ("" if response.status_code == 200 else response.text[:400]),
        "finish_reason": (candidates[0].get("finishReason") if candidates else None),
        "prompt_tokens": usage.get("promptTokenCount"),
        "output_tokens": usage.get("candidatesTokenCount"),
        # gemini-2.5-flash is a reasoning model: its thinking tokens are billed
        # and are charged against maxOutputTokens, but they are reported
        # separately and never appear in the text. A budget set from the size of
        # the expected JSON alone is therefore too small by an amount nothing in
        # the response body reveals.
        "thought_tokens": usage.get("thoughtsTokenCount"),
        "total_tokens": usage.get("totalTokenCount"),
        "payload": payload,
    }


def _run_live(section: Section) -> None:
    from llm import build_provider
    from llm.base import LLMError, extract_json
    from llm.gemini import sanitize_schema

    try:
        provider = build_provider("gemini")
    except LLMError as exc:
        section.add(
            "live_provider_contract",
            None,
            kind="unavailable",
            source="llm.build_provider('gemini')",
            needs=str(exc),
        )
        return

    schemas = _shipped_schemas()
    rows: list[dict[str, Any]] = []
    for name, schema in schemas.items():
        prompt, budget = _PROBES[name]
        result = _post_raw(provider, sanitize_schema(schema), prompt, budget)
        parsed_ok = False
        missing: list[str] = []
        if result["ok"]:
            try:
                candidates = result["payload"].get("candidates") or []
                parts = candidates[0].get("content", {}).get("parts") or []
                parsed = extract_json("".join(p.get("text", "") for p in parts))
                parsed_ok = isinstance(parsed, dict)
                missing = [
                    key for key in (schema.get("required") or []) if key not in parsed
                ]
            except Exception as exc:  # noqa: BLE001 — recorded, not raised
                result["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(
            {
                "probe": name,
                "schema": "shipped, sanitised",
                "expected": "accepted",
                "max_output_tokens": budget,
                "http_status": result["status"],
                "accepted": result["ok"],
                "finish_reason": result.get("finish_reason"),
                "parsed_as_object": parsed_ok,
                "required_fields_missing": "; ".join(missing) or "none",
                "seconds": result["seconds"],
                "prompt_tokens": result.get("prompt_tokens"),
                "output_tokens": result.get("output_tokens"),
                "thought_tokens": result.get("thought_tokens"),
                "error": result["error"][:160],
            }
        )

    # Control 1 — the pre-fix sanitiser output. Expected to be rejected.
    broken = _post_raw(
        provider,
        _broken_sanitiser_output(schemas["guardrail_answer_verdict"]),
        _ANSWER_PROBE,
    )
    rows.append(
        {
            "probe": "guardrail_answer_verdict",
            "schema": "pre-fix: properties emptied, required kept",
            "expected": "rejected",
            "max_output_tokens": None,
            "http_status": broken["status"],
            "accepted": broken["ok"],
            "finish_reason": broken.get("finish_reason"),
            "parsed_as_object": None,
            "required_fields_missing": "all",
            "seconds": broken["seconds"],
            "prompt_tokens": broken.get("prompt_tokens"),
            "output_tokens": broken.get("output_tokens"),
            "thought_tokens": broken.get("thought_tokens"),
            "error": broken["error"][:160],
        }
    )

    # Control 2 — an unsanitised schema carrying a keyword the allow-list drops.
    unsanitised = dict(schemas["guardrail_answer_verdict"])
    unsanitised["additionalProperties"] = False
    raw = _post_raw(provider, unsanitised, _ANSWER_PROBE)
    rows.append(
        {
            "probe": "guardrail_answer_verdict",
            "schema": "unsanitised: + additionalProperties",
            "expected": "rejected",
            "max_output_tokens": None,
            "http_status": raw["status"],
            "accepted": raw["ok"],
            "finish_reason": raw.get("finish_reason"),
            "parsed_as_object": None,
            "required_fields_missing": "none",
            "seconds": raw["seconds"],
            "prompt_tokens": raw.get("prompt_tokens"),
            "output_tokens": raw.get("output_tokens"),
            "thought_tokens": raw.get("thought_tokens"),
            "error": raw["error"][:160],
        }
    )

    _write_cache(rows, model=provider.model)
    _report_live(section, rows, model=provider.model, measured_at=None)


def _write_cache(rows: list[dict[str, Any]], *, model: str) -> None:
    """Persist the live result so an offline run can still report it.

    A live probe needs a key, so it cannot run in the default pass — and a
    measurement that vanishes the moment the key is absent is a measurement the
    paper cannot cite. The cache is the run's own record: the probe rows, the
    model tag and the timestamp, and nothing else. No key, no candidate data,
    no response body beyond the pass/fail fields already in the rows.
    """
    write_json(
        LIVE_CACHE,
        {
            "measured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": model,
            "probes": rows,
        },
    )


def _read_cache() -> dict[str, Any] | None:
    if not LIVE_CACHE.exists():
        return None
    try:
        return json.loads(LIVE_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _report_live(
    section: Section,
    rows: list[dict[str, Any]],
    *,
    model: str,
    measured_at: str | None,
) -> None:
    """Turn probe rows into measurements. Same path for a fresh run and a cache.

    ``measured_at`` is ``None`` for a run that just happened and a timestamp for
    one replayed from the cache, and it is appended to every note either way.
    A provider-contract result is a claim about a moment — the vendor can change
    its dialect tomorrow — so an undated one would be worse than none.
    """
    section.tables["live_probes"] = rows
    when = (
        " Measured live during this run."
        if measured_at is None
        else f" Measured live on {measured_at}; replayed from "
        f"{LIVE_CACHE.name} because this run had no live provider enabled."
    )

    shipped = [r for r in rows if r["schema"] == "shipped, sanitised"]
    broken = next(
        (r for r in rows if r["schema"].startswith("pre-fix")), None
    )
    unsanitised = next(
        (r for r in rows if r["schema"].startswith("unsanitised")), None
    )

    section.add(
        "shipped_schemas_accepted_live",
        bool(shipped) and all(r["accepted"] and r["parsed_as_object"] for r in shipped),
        kind="measured",
        source="metrics.m9_provider_contract._run_live",
        note=(
            f"{sum(1 for r in shipped if r['accepted'])} of {len(shipped)} accepted "
            f"against {model}; every required field present in the reply." + when
        ),
    )
    if broken is not None:
        section.add(
            "pre_fix_schema_rejected_live",
            not broken["accepted"],
            kind="measured",
            source="metrics.m9_provider_contract._run_live",
            note=(
                (
                    f"HTTP {broken['http_status']}. The API returns exactly the "
                    "error llm/gemini.py's docstring quotes — "
                    "'response_schema.required[0]: property is not defined' — so "
                    "the properties branch of sanitize_schema is load-bearing, "
                    "measured rather than recalled."
                    if not broken["accepted"]
                    else "ACCEPTED — the API no longer rejects the pre-fix shape, "
                    "so the defect narrative in §III must be rewritten: the 400 "
                    "came from somewhere else"
                )
                + when
            ),
        )
    if unsanitised is not None:
        section.add(
            "unsanitised_schema_rejected_live",
            not unsanitised["accepted"],
            kind="measured",
            source="metrics.m9_provider_contract._run_live",
            note=(
                (
                    f"HTTP {unsanitised['http_status']} for a schema carrying "
                    "additionalProperties: 'Unknown name'. The keyword allow-list "
                    "is a real adaptation to the provider's dialect, not caution."
                    if not unsanitised["accepted"]
                    else "ACCEPTED — Gemini now tolerates this keyword; the "
                    "allow-list is more conservative than the API requires, which "
                    "is safe but should not be described as necessary"
                )
                + when
            ),
        )

    latencies = [r["seconds"] for r in shipped if r["seconds"] is not None]
    if latencies:
        section.add(
            "hosted_api_round_trip_seconds",
            round(sum(latencies) / len(latencies), 3),
            unit="s",
            kind="measured",
            source="metrics.m9_provider_contract._run_live",
            note=(
                f"mean over {len(latencies)} schema-constrained calls to {model} "
                f"({', '.join(f'{r}s' for r in latencies)}). This is a hosted-API "
                "round trip — network, queueing and generation together — and it "
                "is NOT the local inference latency the deployability argument "
                "needs. It bounds the cloud path only." + when
            ),
        )

    thoughts = [r.get("thought_tokens") or 0 for r in shipped]
    visible = [r.get("output_tokens") or 0 for r in shipped]
    if any(thoughts):
        section.add(
            "reasoning_tokens_per_call",
            sum(thoughts),
            unit="tokens",
            kind="measured",
            source="usageMetadata.thoughtsTokenCount",
            note=(
                f"{sum(thoughts)} reasoning tokens against {sum(visible)} tokens "
                f"of visible output across {len(shipped)} shipped-schema probes — "
                f"{sum(thoughts) / max(1, sum(visible)):.1f}x the output the "
                "system actually uses. They are billed, they are charged against "
                "maxOutputTokens, and they never appear in the response body, so "
                "a token budget sized from the expected JSON is short by an "
                "amount the response does not reveal. A local model run without "
                "a reasoning phase does not carry this cost, which cuts the "
                "opposite way to the usual cloud-versus-local comparison." + when
            ),
        )

    truncated = [r for r in shipped if r.get("finish_reason") == "MAX_TOKENS"]
    section.add(
        "shipped_budgets_complete_the_response",
        not truncated,
        kind="measured",
        source="candidates[0].finishReason over the shipped-schema probes",
        note=(
            (
                "every probe finished with STOP at the max_output_tokens its "
                "production call site passes, so neither shipped budget "
                "truncates on a minimal input. Note the failure mode this rules "
                "out: a truncated reply still returns HTTP 200 with partial "
                "text, so it surfaces as a JSON parse error and a silent re-ask, "
                "not as a recognisable budget error."
                if not truncated
                else "TRUNCATED at the production budget: "
                + ", ".join(r["probe"] for r in truncated)
            )
            + when
        ),
    )

    section.add(
        "gemini_reports_generation_time",
        False,
        kind="measured",
        source="usageMetadata in the live response",
        note=(
            "the response carries promptTokenCount / candidatesTokenCount / "
            "thoughtsTokenCount but no server-side generation duration, so "
            "tokens-per-second cannot be computed for this provider without "
            "folding the network round trip into the denominator. "
            "LLMResult.eval_seconds is left None rather than filled from "
            "wall-clock." + when
        ),
    )


# --------------------------------------------------------------------------- #


def run() -> Section:
    section = Section(key="m9_provider_contract", title="Provider contract")

    from llm.gemini import _ALLOWED_SCHEMA_KEYS, sanitize_schema

    # ------------------------------------------------------------------ #
    # Offline: does sanitisation preserve the schemas we actually ship?
    # ------------------------------------------------------------------ #
    schemas = _shipped_schemas()
    schema_rows = []
    all_orphans: list[str] = []
    all_dropped: set[str] = set()
    for name, schema in schemas.items():
        cleaned = sanitize_schema(schema)
        orphans = _orphaned_required_fields(schema, cleaned)
        used = _keywords_used(schema)
        dropped = sorted(used - _ALLOWED_SCHEMA_KEYS)
        all_orphans += orphans
        all_dropped |= set(dropped)
        schema_rows.append(
            {
                "schema": name,
                "top_level_properties": len(schema.get("properties") or {}),
                "required_fields": len(schema.get("required") or []),
                "keywords_used": "; ".join(sorted(used - set(
                    (schema.get("properties") or {}).keys()
                ))),
                "keywords_dropped_by_allow_list": "; ".join(dropped) or "none",
                "properties_surviving": len(cleaned.get("properties") or {}),
                "orphaned_required_fields": "; ".join(orphans) or "none",
            }
        )
    section.tables["shipped_schemas"] = schema_rows

    section.add(
        "schemas_shipped_to_the_provider",
        len(schemas),
        unit="schemas",
        kind="measured",
        source="services.application_service._EVAL_SCHEMA, "
        "services.guardrail_service._ANSWER_SCHEMA",
        note=", ".join(schemas),
    )
    section.add(
        "sanitisation_preserves_required_fields",
        not all_orphans,
        kind="measured",
        source="llm.gemini.sanitize_schema over the shipped schemas",
        note=(
            "every field named in a `required` list still has a property after "
            "sanitisation. This is the exact invariant whose violation produced "
            "HTTP 400 'property is not defined' on every schema-constrained "
            "call in the project."
            if not all_orphans
            else "BROKEN — orphaned: " + ", ".join(sorted(set(all_orphans)))
        ),
    )
    section.add(
        "shipped_keywords_dropped_by_the_allow_list",
        sorted(all_dropped),
        kind="measured",
        source="llm.gemini._ALLOWED_SCHEMA_KEYS",
        note=(
            "nothing the shipped schemas rely on is filtered out, so the "
            "constraint the provider enforces is the constraint the caller wrote"
            if not all_dropped
            else "these constraints are silently NOT enforced by the provider"
        ),
    )
    section.add(
        "allowed_schema_keywords",
        len(_ALLOWED_SCHEMA_KEYS),
        unit="keywords",
        kind="specification",
        source="llm.gemini._ALLOWED_SCHEMA_KEYS",
        note=", ".join(sorted(_ALLOWED_SCHEMA_KEYS)),
    )

    # ------------------------------------------------------------------ #
    # Offline: how much of the project is schema-constrained at all?
    # ------------------------------------------------------------------ #
    census = _call_site_census()
    section.tables["model_call_sites"] = census
    constrained = sum(1 for row in census if row["schema_constrained"])
    section.add(
        "model_call_sites",
        len(census),
        unit="call sites",
        kind="measured",
        source="generate_json( occurrences across services/",
    )
    section.add(
        "schema_constrained_call_sites",
        constrained,
        unit="call sites",
        kind="measured",
        source="metrics.m9_provider_contract._call_site_census",
        note=(
            f"{constrained} of {len(census)} pin the response with a schema; the "
            f"remaining {len(census) - constrained} ask for JSON in the prompt "
            "and rely on llm.base.extract_json to repair the reply. The repair "
            "path is therefore production behaviour, not a fallback, and the "
            "paper should not imply otherwise."
        ),
    )

    # ------------------------------------------------------------------ #
    # Offline: which providers can support a tokens/sec figure at all
    # ------------------------------------------------------------------ #
    throughput_rows = [
        {
            "provider": "ollama",
            "token_counts": "prompt_eval_count, eval_count",
            "generation_time": "prompt_eval_duration, eval_duration (ns)",
            "tokens_per_second_derivable": True,
        },
        {
            "provider": "gemini",
            "token_counts": "promptTokenCount, candidatesTokenCount",
            "generation_time": "not reported",
            "tokens_per_second_derivable": False,
        },
        {
            "provider": "openai_compat",
            "token_counts": "prompt_tokens, completion_tokens",
            "generation_time": "not in the chat-completions schema",
            "tokens_per_second_derivable": False,
        },
    ]
    section.tables["throughput_reporting"] = throughput_rows
    section.add(
        "providers_reporting_generation_time",
        [r["provider"] for r in throughput_rows if r["tokens_per_second_derivable"]],
        kind="specification",
        source="llm/ollama.py, llm/gemini.py, llm/openai_compat.py",
        note=(
            "only Ollama returns a server-side generation duration, so the "
            "tokens-per-second figure the deployability section wants is "
            "measurable on the local backend and on no other. LLMResult carries "
            "the fields; the hosted providers leave them None rather than "
            "substituting wall-clock."
        ),
    )

    # ------------------------------------------------------------------ #
    # Live, if the operator asked for it — or replayed from the last run
    # ------------------------------------------------------------------ #
    requested = (os.environ.get(LIVE_ENV_VAR) or "").strip().lower()
    cached = None if requested == "gemini" else _read_cache()
    if requested == "gemini":
        _run_live(section)
    elif cached and cached.get("probes"):
        # The default pass has no key, but a live pass already happened and left
        # its evidence. Reporting the cached rows with their own timestamp keeps
        # the numbers citable; dropping them would mean the paper could only
        # quote a provider result on a machine holding the credential.
        _report_live(
            section,
            cached["probes"],
            model=cached.get("model", "unknown"),
            measured_at=cached.get("measured_at_utc", "an unrecorded date"),
        )
    else:
        for name, what in (
            (
                "shipped_schemas_accepted_live",
                "whether the vendor still accepts the schemas this project sends",
            ),
            (
                "pre_fix_schema_rejected_live",
                "whether the pre-fix sanitiser output is still rejected, which is "
                "what makes the fix necessary rather than cosmetic",
            ),
            (
                "unsanitised_schema_rejected_live",
                "whether the keyword allow-list is load-bearing or merely cautious",
            ),
            (
                "hosted_api_round_trip_seconds",
                "the cloud round trip for a schema-constrained call",
            ),
        ):
            section.add(
                name,
                None,
                kind="unavailable",
                source="metrics.m9_provider_contract._run_live",
                needs=(
                    f"{what}. Four API calls against the key already in .env: "
                    f"`{LIVE_ENV_VAR}=gemini .venv/Scripts/python -m metrics.run_all "
                    "--only m9_provider_contract`. Off by default so a metrics run "
                    "stays reproducible without a key and spends nobody's quota."
                ),
            )

    section.commentary = (
        "This is the only module that measures something outside the project's "
        "control. The offline half proves the sanitiser preserves the schemas we "
        "ship — the invariant whose violation broke every schema-constrained "
        "call — and the live half asks the vendor directly, with the pre-fix "
        "shape sent alongside as a control so an acceptance would be visible "
        "rather than assumed. The call-site census is the honest qualifier on "
        "all of it: three of five model calls are not schema-constrained at all, "
        "so JSON repair is production behaviour."
    )
    return section
