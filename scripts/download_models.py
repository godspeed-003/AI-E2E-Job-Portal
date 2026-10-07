"""Download the proctoring model weights.

The CV pipeline needs four small model files. None of them ship inside the
Python wheels and none of them are in git — they are weights, so committing them
would bloat the repository, and one of them is AGPL-3.0, which is not a licence
to vendor into a project by accident. So they are fetched once into ``models/``
and cached there.

``core.model_assets.ensure`` already downloads them lazily on first use, but that
is the wrong moment to find out you are offline: it happens mid-interview, it
fails quietly by design, and the only trace is a line in the log. ``healthcheck``
reports them missing but could not fix it. This script is the missing step —
run it once after cloning, on the machine that will run the demo.

    python scripts/download_models.py             # fetch whatever is missing
    python scripts/download_models.py --check     # report, download nothing
    python scripts/download_models.py --force     # re-fetch even if cached
    python scripts/download_models.py --only yolo # one asset

Exit codes: 0 everything present, 1 something is still missing.

Licence note, printed at the end of every run because it matters: three of the
four models are Apache-2.0 and ``yolo11n`` is AGPL-3.0. The portal ships with
object detection *disabled* (``PROCTORING_OBJECT_DETECTION=false``), so the
default deployment never executes the AGPL component. Turning it on takes on
AGPL network-use obligations for your deployment.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import model_assets  # noqa: E402  (after the path fix)


def _utf8_stdout() -> None:
    """Windows consoles default to cp1252 and would die on the box characters."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


def _mb(value: float) -> str:
    return f"{value / 1_048_576:.1f} MB"


class _Progress:
    """A single rewritten line per asset. No dependency on tqdm."""

    def __init__(self, label: str, expected: int) -> None:
        self.label = label
        self.expected = expected
        self.started = time.monotonic()
        self._last = 0.0

    def __call__(self, done: int, total: int) -> None:
        now = time.monotonic()
        # Redrawing on every 64 KiB chunk makes a slow terminal the bottleneck.
        if now - self._last < 0.1 and done < (total or self.expected):
            return
        self._last = now

        target = total or self.expected
        elapsed = max(now - self.started, 1e-6)
        rate = done / elapsed
        if target:
            share = min(done / target, 1.0)
            filled = int(share * 24)
            bar = "█" * filled + "·" * (24 - filled)
            line = (
                f"  {self.label:<16} [{bar}] {share * 100:5.1f}%  "
                f"{_mb(done):>9} / {_mb(target):<9} {_mb(rate)}/s"
            )
        else:
            line = f"  {self.label:<16} {_mb(done):>9} at {_mb(rate)}/s"
        print(f"\r{line}", end="", flush=True)

    def done(self, ok: bool, detail: str = "") -> None:
        width = shutil.get_terminal_size((100, 20)).columns
        print("\r" + " " * min(width - 1, 110), end="\r")
        mark = "[OK]  " if ok else "[FAIL]"
        print(f"  {mark} {self.label:<16} {detail}")


def _report() -> list[dict[str, object]]:
    rows = model_assets.status()
    print("\nMODELS")
    for row in rows:
        path = model_assets.local_path(str(row["key"]))
        if row["cached"]:
            actual = _mb(path.stat().st_size)
            print(f"  [OK]   {row['key']:<16} {actual:>9}  {row['licence']}")
        else:
            print(
                f"  [MISS] {row['key']:<16} {row['size_mb']:>6} MB  {row['licence']}"
            )
        print(f"         {row['purpose']}")
    return rows


def _licence_note() -> None:
    agpl = [a.key for a in model_assets.ASSETS.values() if "AGPL" in a.licence]
    print(
        "\nLicences: three face models are Apache-2.0; "
        f"{', '.join(agpl)} is AGPL-3.0.\n"
        "Object detection ships disabled (PROCTORING_OBJECT_DETECTION=false), so\n"
        "the default deployment does not execute the AGPL component. Enabling it\n"
        "takes on AGPL network-use obligations for your deployment.\n"
        "No weights are committed to this repository."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="download_models",
        description="Fetch the open-source CV model weights into models/.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report what is present and exit without downloading",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-download even if a cached copy looks complete",
    )
    parser.add_argument(
        "--only",
        metavar="KEY",
        action="append",
        choices=sorted(model_assets.ASSETS),
        help="fetch just this asset (repeatable)",
    )
    args = parser.parse_args(argv)
    _utf8_stdout()

    keys = args.only or sorted(model_assets.ASSETS)

    if args.check:
        rows = _report()
        missing = [r for r in rows if not r["cached"]]
        print(
            f"\n{len(rows) - len(missing)} of {len(rows)} present."
            + (
                "\nRun `python scripts/download_models.py` to fetch the rest."
                if missing
                else ""
            )
        )
        _licence_note()
        return 1 if missing else 0

    print(f"Fetching into {model_assets.MODELS_DIR}")
    total_expected = sum(model_assets.ASSETS[k].approx_bytes for k in keys)
    print(f"{len(keys)} asset(s), about {_mb(total_expected)} in total.\n")

    failures: list[str] = []
    for key in keys:
        asset = model_assets.ASSETS[key]
        path = model_assets.local_path(key)

        if args.force and path.exists():
            # Only unlink once we are actually about to re-fetch, so a --force
            # run that is offline does not leave the machine worse than it was.
            path.unlink()

        if model_assets.is_cached(key):
            print(f"  [SKIP] {key:<16} already present ({_mb(path.stat().st_size)})")
            continue

        bar = _Progress(key, asset.approx_bytes)
        result = model_assets.ensure(key, on_progress=bar)
        if result is None:
            bar.done(False, "download failed — see the warning above")
            failures.append(key)
        else:
            bar.done(True, f"{_mb(result.stat().st_size)}  {asset.licence}")

    rows = model_assets.status()
    missing = [str(r["key"]) for r in rows if not r["cached"]]

    print()
    if missing:
        print(f"Still missing: {', '.join(missing)}")
        print(
            "The portal runs without these — proctoring degrades to whatever\n"
            "backend is available rather than taking the interview down — but the\n"
            "integrity signal is weaker. Check the network and run this again."
        )
    else:
        print("All model weights are present. `python scripts/healthcheck.py` to confirm.")

    _licence_note()
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
