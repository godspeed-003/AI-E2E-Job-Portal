"""Run every measurement module, write the results, draw the figures.

    .venv/Scripts/python -m metrics.run_all          # Windows
    .venv/bin/python     -m metrics.run_all          # Linux / macOS

Outputs, all under ``research paper/``:

``results/results.json``
    Everything: environment, each section's measurements with their provenance
    labels, and the row data the figures were drawn from. This is the file the
    paper's tables should be written from.
``results/provenance.csv``
    One row per reported number: name, value, unit, honesty ``kind``, and the
    ``module:function`` that produced it. Sort by ``kind`` to see at a glance
    which claims are measured and which are specification.
``results/unavailable.md``
    The claims this machine cannot support, each with the exact run that would
    produce it. Written as a file rather than a console warning because it is
    the paper's limitations section in draft form.
``results/tables/*.csv``
    Each section's row data, for pasting into LaTeX or a spreadsheet.
``figures/*.pdf`` and ``figures/*.png``
    See :mod:`metrics.figures`.

The run is ordered cheapest-first so a failure in the slow end-to-end timing
still leaves the ranking and guardrail numbers on disk. A module that raises is
recorded as a failure in ``results.json`` and the run continues: a broken
measurement must not be able to delete the seven that worked.
"""

from __future__ import annotations

# Must precede every project import: core.config snapshots os.environ behind an
# lru_cache at import time, so the throwaway database and the fake provider have
# to be in the environment before anything reads it.
from metrics import bootstrap  # noqa: F401  (imported for effect)

import argparse
import sys
import traceback
from typing import Any, Callable

from metrics import (
    FIGURES_DIR,
    RESULTS_DIR,
    Section,
    environment,
    write_csv,
    write_json,
)

MODULES: tuple[tuple[str, str], ...] = (
    ("m1_integrity", "Integrity scoring"),
    ("m2_guardrails", "Prompt-injection guardrails"),
    ("m3_ats", "Keyword pre-filter and the rejection threshold"),
    ("m4_baselines", "Resume-only ranking baselines"),
    ("m5_fusion_audit", "Fusion correction audit"),
    ("m10_stability", "Stability under cohort resampling"),
    ("m7_codebase", "Implementation size, tests and schema"),
    ("m8_egress", "What leaves the host, per provider"),
    ("m9_provider_contract", "Provider contract"),
    ("m11_live_screening", "Real-model screening (opt-in; replays from cache)"),
    ("m6_latency", "Per-stage latency (slowest; runs last)"),
)

# Claims the paper may want to make that this machine cannot support. Listed
# here as well as in each section so the limitations section can be written from
# one place. The rule the harness follows: a claim that needs a model, a GPU or
# a human rater is reported as unavailable with the run that would produce it,
# never estimated.
UNAVAILABLE: dict[str, str] = {
    "interview_score_validity": (
        "Whether the model's 0–25 interview score tracks human judgement. Needs "
        "at least three independent human raters scoring the same recorded "
        "interviews against the shipped rubric, reported as Fleiss' kappa for "
        "inter-rater agreement and Spearman's rho against the model. Until that "
        "exists the paper must not claim the interviewer is accurate — only that "
        "the fusion function uses whatever the interviewer supplies in the way "
        "described."
    ),
    "end_to_end_error_recovery": (
        "The headline claim 'the pipeline recovers X% of planted resume "
        "misrepresentations'. The fusion audit supports the conditional form — "
        "given interview evidence with residual error sigma, the fusion recovers "
        "X% — and sigma has to be stated. The unconditional form needs real "
        "interviews conducted by the real model against a cohort whose true "
        "competence is known, which means human-labelled ground truth."
    ),
    "model_inference_latency": (
        "Screening and interview-turn latency with a real model. Needs an Ollama "
        "run reporting p50/p95 per call alongside model tag, quantisation, "
        "context length, CPU/GPU and peak RAM or VRAM. Five facts, or it is not "
        "a result."
    ),
    "tokens_per_second": (
        "Generation throughput. Ollama already returns the token counts and "
        "llm/ollama.py already sums them; the durations it returns "
        "(prompt_eval_duration, eval_duration) are dropped and are the "
        "denominators. Keeping them makes this measurable with no new dependency."
    ),
    "peak_ram_and_vram": (
        "Memory residency during local inference — the number the deployability "
        "argument rests on, since the case for local models is that a hiring team "
        "can run this on hardware they already own. Needs `ollama ps` plus psutil "
        "RSS sampling, and nvidia-smi if a GPU is involved."
    ),
    "whisper_transcription_latency": (
        "Speech-to-text cost, as a real-time factor (audio seconds per wall "
        "second) rather than milliseconds. Needs faster-whisper with a downloaded "
        "model over recorded audio of known duration."
    ),
    "ablation_ladder_A0_to_A5": (
        "The A0 resume-only through A5 full-system ladder. Each rung needs the "
        "real model, because the rungs differ in what the model is asked to do "
        "(static questions vs adaptive follow-ups vs structured pros and cons). "
        "With the fake provider every rung returns the same canned JSON, so an "
        "ablation run now would measure nothing."
    ),
    "encryption_at_rest": (
        "Not implemented. SQLite stores rows in plaintext and MEDIA_DIR holds "
        "plain video, audio and image files; filesystem permissions are the only "
        "control. This is a real gap and belongs in the limitations section, not "
        "in the design section as a choice. SQLCipher or an encrypted volume "
        "would close it."
    ),
    "automatic_retention_policy": (
        "Three purge functions exist and are correct, but nothing calls them on a "
        "schedule, so deletion is an operator action rather than a property of "
        "the system. The supportable sentence is 'deletion primitives exist and "
        "cascade correctly; retention is the operator's responsibility'."
    ),
    "human_ai_correlation": (
        "DO NOT CITE the 0.9897 figure in results/metrics/. "
        "generate_metrics.py produced it from "
        "`item['human_score'] = item['ai_score_normalized'] + "
        "random.uniform(-1.0, 1.0)` — a correlation between a random number and "
        "itself. No human rater has scored a candidate in this project. See "
        "research paper/METRICS-PROVENANCE.md."
    ),
}


def _load(name: str) -> Callable[[], Section]:
    module = __import__(f"metrics.{name}", fromlist=["run"])
    return module.run


def _flatten_table(value: Any) -> list[dict[str, Any]] | None:
    """Coerce a section table into CSV rows, or ``None`` if it will not fit."""
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return [
            {
                key: (
                    "; ".join(str(v) for v in cell)
                    if isinstance(cell, (list, tuple))
                    else cell
                )
                for key, cell in row.items()
            }
            for row in value
        ]
    if isinstance(value, dict) and value:
        first = next(iter(value.values()))
        if isinstance(first, dict):
            return [{"key": k, **v} for k, v in value.items()]
        if not isinstance(first, (list, tuple, dict)):
            return [{"key": k, "value": v} for k, v in value.items()]
    return None


def _provenance_rows(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for section in sections:
        for measurement in section.get("measurements", []):
            value = measurement.get("value")
            if isinstance(value, (list, tuple)):
                rendered = "; ".join(str(v) for v in value)
            elif isinstance(value, dict):
                rendered = "; ".join(f"{k}={v}" for k, v in value.items())
            else:
                rendered = "" if value is None else str(value)
            rows.append(
                {
                    "section": section["key"],
                    "name": measurement["name"],
                    "value": rendered,
                    "unit": measurement.get("unit", ""),
                    "kind": measurement.get("kind", ""),
                    "source": measurement.get("source", ""),
                    "note": measurement.get("note", ""),
                    "needs": measurement.get("needs", ""),
                }
            )
    return rows


def _write_unavailable(sections: list[dict[str, Any]]) -> None:
    lines = [
        "# Claims this machine cannot support",
        "",
        "Written by `metrics.run_all`. Each entry names the run that would",
        "produce the number. Nothing here has been estimated, and nothing here",
        "should appear in the paper as a result — this is the limitations",
        "section in draft form.",
        "",
        "## From the measurement modules",
        "",
    ]
    seen = set()
    for section in sections:
        entries = [
            m for m in section.get("measurements", []) if m.get("kind") == "unavailable"
        ]
        if not entries:
            continue
        lines.append(f"### {section['key']} — {section['title']}")
        lines.append("")
        for measurement in entries:
            seen.add(measurement["name"])
            lines.append(f"**`{measurement['name']}`**")
            lines.append("")
            if measurement.get("source"):
                lines.append(f"- Relevant code: `{measurement['source']}`")
            if measurement.get("needs"):
                lines.append(f"- Needs: {measurement['needs']}")
            if measurement.get("note"):
                lines.append(f"- Note: {measurement['note']}")
            lines.append("")

    extra = {k: v for k, v in UNAVAILABLE.items() if k not in seen}
    if extra:
        lines += ["## Additional, declared in `metrics.run_all.UNAVAILABLE`", ""]
        for name, text in extra.items():
            lines += [f"**`{name}`**", "", text, ""]

    path = RESULTS_DIR / "unavailable.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ascii(text: str) -> str:
    """Console-safe text.

    The notes and units carry real typography — λ, τ, ×, ≈, em dashes — and a
    Windows console defaults to cp1252, which cannot encode them. The provenance
    table is a convenience view; the JSON and CSV keep the real characters, so
    transliterating here loses nothing that matters.
    """
    replacements = {
        "λ": "lambda", "τ": "tau", "ρ": "rho", "κ": "kappa", "σ": "sigma",
        "Δ": "d", "≈": "~", "×": "x", "≥": ">=", "≤": "<=", "—": "-",
        "–": "-", "·": ".", "“": '"', "”": '"', "’": "'", "‘": "'", "…": "...",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text.encode("ascii", "replace").decode("ascii")


def _print_provenance(rows: list[dict[str, Any]]) -> None:
    order = {"measured": 0, "simulated": 1, "derived": 2, "specification": 3, "unavailable": 4}
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1

    print()
    print("=" * 96)
    print("PROVENANCE - every reported number and the code that produced it")
    print("=" * 96)
    for kind in sorted(counts, key=lambda k: order.get(k, 9)):
        bucket = [r for r in rows if r["kind"] == kind]
        print()
        print(f"--- {kind.upper()}  ({len(bucket)}) ---")
        for row in bucket:
            value = _ascii(row["value"])
            if len(value) > 34:
                value = value[:31] + "..."
            unit = f" {_ascii(row['unit'])}" if row["unit"] else ""
            name = _ascii(row["name"])[:44]
            source = _ascii(row["source"])[:60]
            print(f"  {name:<44} {value + unit:<38} {source}")
    print()
    print("-" * 96)
    summary = "  ".join(
        f"{kind}={counts[kind]}" for kind in sorted(counts, key=lambda k: order.get(k, 9))
    )
    print(f"  {len(rows)} reported quantities:  {summary}")
    print("-" * 96)


def main(argv: list[str] | None = None) -> int:
    # The notes carry real typography and a Windows console is cp1252 by
    # default. Never let an encoding error lose a completed run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        nargs="*",
        metavar="MODULE",
        help="run only these modules, e.g. --only m5_fusion_audit m2_guardrails",
    )
    parser.add_argument(
        "--no-figures", action="store_true", help="skip figure generation"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="suppress the provenance table"
    )
    args = parser.parse_args(argv)

    selected = [
        (name, title)
        for name, title in MODULES
        if not args.only or name in args.only
    ]
    if not selected:
        print(f"no such module. available: {', '.join(n for n, _ in MODULES)}")
        return 2

    env = environment()
    env["environment_overrides"] = dict(bootstrap.ENVIRONMENT_OVERRIDES)
    env["throwaway_root"] = str(bootstrap.TMP_ROOT)

    print(f"metrics harness — commit {env['git_commit']}, python {env['python']}")
    print(f"throwaway database: {env['environment_overrides']['DATABASE_PATH']}")
    print()

    sections: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for name, title in selected:
        print(f"  running {name:<18} {title} ... ", end="", flush=True)
        try:
            section = _load(name)()
        except Exception as exc:
            print(f"FAILED ({type(exc).__name__}: {exc})")
            failures.append(
                {
                    "module": name,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(limit=8),
                }
            )
            continue
        payload = section.as_dict()
        sections.append(payload)
        measured = sum(
            1
            for m in payload["measurements"]
            if m.get("kind") in ("measured", "simulated", "derived")
        )
        gaps = sum(1 for m in payload["measurements"] if m.get("kind") == "unavailable")
        print(f"ok ({measured} results, {gaps} unavailable)")

    results: dict[str, Any] = {
        "environment": env,
        "sections": sections,
        "failures": failures,
        "unavailable_declared": UNAVAILABLE,
    }

    figures: list[dict[str, Any]] = []
    if not args.no_figures and sections:
        print()
        print("  drawing figures ... ", end="", flush=True)
        try:
            from metrics import figures as figure_module

            figures = figure_module.draw_all(results)
            drawn = [f for f in figures if not f.get("skipped")]
            skipped = [f for f in figures if f.get("skipped")]
            print(f"{len(drawn)} written to {FIGURES_DIR.name}/", end="")
            print(f", {len(skipped)} skipped" if skipped else "")
            for entry in skipped:
                print(f"      skipped {entry['name']}: {entry['reason']}")
        except Exception as exc:
            print(f"FAILED ({type(exc).__name__}: {exc})")
            failures.append(
                {
                    "module": "figures",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(limit=8),
                }
            )
    results["figures"] = figures

    write_json(RESULTS_DIR / "results.json", results)

    provenance = _provenance_rows(sections)
    write_csv(
        RESULTS_DIR / "provenance.csv",
        provenance,
        columns=["section", "name", "value", "unit", "kind", "source", "note", "needs"],
    )
    _write_unavailable(sections)

    table_count = 0
    for section in sections:
        for table_name, value in section["tables"].items():
            rows = _flatten_table(value)
            if rows is None:
                continue
            write_csv(RESULTS_DIR / "tables" / f"{section['key']}__{table_name}.csv", rows)
            table_count += 1

    if not args.quiet:
        _print_provenance(provenance)

    print()
    print(f"  results.json     {RESULTS_DIR / 'results.json'}")
    print(f"  provenance.csv   {len(provenance)} rows")
    print(f"  tables/          {table_count} CSVs")
    print(f"  unavailable.md   {sum(1 for r in provenance if r['kind'] == 'unavailable')} "
          f"section gaps + {len(UNAVAILABLE)} declared")
    print(f"  figures/         {len([f for f in figures if not f.get('skipped')])} PDF+PNG pairs")
    if failures:
        print()
        print("  FAILURES:")
        for failure in failures:
            print(f"    {failure['module']}: {failure['error']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
