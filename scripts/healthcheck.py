"""Answer "is this thing actually configured?" from a terminal, before a demo.

:mod:`services.health_service` already runs every probe — the database, the
model providers, the optional CV packages, the downloaded weights, the secrets
that are missing. It is reachable from the admin page inside the app, which is
the wrong place when the question is *why the app will not start*, or when the
answer needs to come from CI where nothing renders.

    python scripts/healthcheck.py            # everything, including model calls
    python scripts/healthcheck.py --offline  # skip anything that hits a network
    python scripts/healthcheck.py --json     # for CI, or for pasting into a bug

Exit status is the point:

* **0** — every check passed, or the only complaints are warnings.
* **1** — at least one check failed. Something the app depends on is missing.
* **2** — the health system itself could not run, which usually means the
  environment is broken badly enough that ``import`` failed.

Warnings do not fail the run. Most of them are optional features declining to
be present — no CUDA, no object-detection weights, no TTS voice — and a portal
with those switched off is a supported configuration, not a broken one.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MARKS = {"ok": "PASS", "warn": "WARN", "fail": "FAIL"}


def _quiet_streamlit() -> None:
    """Stop Streamlit burying the report under warnings about not being a browser.

    The probes import service modules, which import Streamlit, which logs
    "missing ScriptRunContext" and "No runtime found, using
    MemoryCacheStorageManager" the moment it notices nobody is attached. Both are
    correct and neither is a health problem, but they print above the report they
    are meant to accompany.

    Three steps, and all three are needed. Streamlit gives each of its loggers an
    explicit level and its own handler, so nothing propagates and setting the
    parent alone does nothing — ``set_log_level`` is the way in, because it walks
    every logger Streamlit has registered. But reading a config option for the
    first time parses the config file, and *that* re-applies ``logger.level`` to
    all of them, undoing an earlier call. So the config is parsed deliberately
    first, and the level set after it. The environment variable covers the same
    option for any Streamlit that re-reads it later; ``setdefault`` leaves an
    operator's own override alone.
    """
    os.environ.setdefault("STREAMLIT_LOGGER_LEVEL", "error")
    try:
        import streamlit.logger as streamlit_logger
        from streamlit import config

        config.get_option("logger.level")  # parse now, so it cannot undo us later
        streamlit_logger.set_log_level("error")
    except Exception:  # a future Streamlit may not expose either; noise is survivable
        logging.getLogger("streamlit").setLevel(logging.ERROR)


def _utf8_stdout() -> None:
    """A cp1252 Windows console must not turn a health report into a traceback."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass


def _render(report: list[dict], *, verbose: bool) -> None:
    group = ""
    for entry in report:
        if entry.get("group") != group:
            group = entry.get("group", "")
            print(f"\n{group.upper()}")
        status = entry.get("status", "fail")
        mark = MARKS.get(status, status.upper())
        name = entry.get("name", "?")
        detail = entry.get("detail", "")
        print(f"  [{mark}] {name:<22} {detail}")
        if verbose and entry.get("extra"):
            for key, value in entry["extra"].items():
                print(f"           {key}: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="healthcheck",
        description="Check that the portal's dependencies and configuration are in place.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip the LLM, STT and TTS probes, which may call a provider",
    )
    parser.add_argument("--json", action="store_true", help="emit the raw report")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show each check's extra fields"
    )
    args = parser.parse_args(argv)
    _utf8_stdout()
    _quiet_streamlit()

    try:
        from services import health_service
    except Exception as exc:
        # Nothing below can run, and the traceback is the useful part here.
        print(f"Could not load the health system: {exc}", file=sys.stderr)
        return 2


    try:
        report = health_service.collect(include_ai=not args.offline)
    except Exception as exc:
        print(f"Health checks could not run: {exc}", file=sys.stderr)
        return 2

    counts = health_service.summarize(report)

    if args.json:
        print(json.dumps({"summary": counts, "checks": report}, indent=2))
    else:
        _render(report, verbose=args.verbose)
        print(
            f"\n{counts['ok']} passed, {counts['warn']} warning(s), "
            f"{counts['fail']} failure(s)."
        )
        if args.offline:
            print("AI provider checks were skipped (--offline).")
        if counts["fail"]:
            print("\nFix the FAIL lines above; WARN lines are optional features.")

    return 1 if counts["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
