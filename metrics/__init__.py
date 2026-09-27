"""A metrics harness whose every number comes from executing this project's code.

Run it::

    .venv/Scripts/python -m metrics.run_all          # Windows
    .venv/bin/python     -m metrics.run_all          # Linux / macOS

It writes machine-readable results to ``research paper/results/`` and figures to
``research paper/figures/``, and prints a provenance table naming the function
that produced each number.

Why this package replaces ``generate_metrics.py``
-------------------------------------------------

The repo shipped a metrics script that manufactured its results::

    item['processing_time_sec'] = base_parsing_time + llm_processing_time + random.uniform(0.1, 0.5)
    item['human_score']        = item['ai_score_normalized'] + random.uniform(-1.0, 1.0)
    item['parsing_failed']     = True if random.random() < 0.02 else False

and then reported ``human_ai_correlation: 0.9897`` from the second line — a
correlation between a random number and itself, in a project where no human
rater has ever scored a candidate. Publishing that figure would be fabrication,
not optimism. ``generate_metrics.py`` is kept in the tree only so the record of
what it did survives; nothing here imports it, and
``research paper/METRICS-PROVENANCE.md`` lists every figure it produced that
must not be cited.

The rules this package holds itself to
--------------------------------------

1. **Every reported number is the return value of project code**, executed
   during the run, from inputs that are either checked into the repo or
   generated from a fixed seed.
2. **No random number is ever reported as a result.** Randomness appears only
   in generated *inputs* (:mod:`metrics.corpora`), never in an output.
3. **Anything that needs a model, a GPU or a human is reported as
   unavailable**, with the exact run that would produce it, rather than
   simulated. See :data:`UNAVAILABLE` in :mod:`metrics.run_all`.
4. **Simulation is labelled as simulation.** The fusion audit measures what the
   fusion function does with interview evidence of a stated quality; it does not
   measure whether the LLM interviewer supplies evidence of that quality, and it
   says so in its own output.
5. **Every measurement names its source.** Each module returns a
   :class:`Measurement` carrying ``source`` — the ``module:function`` under test
   — so the paper can cite a line of code for each table cell.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PAPER_DIR = PROJECT_ROOT / "research paper"
RESULTS_DIR = PAPER_DIR / "results"
FIGURES_DIR = PAPER_DIR / "figures"


@dataclass
class Measurement:
    """One measured quantity, with the provenance the paper needs to cite it.

    ``kind`` is the honesty label and is not decoration:

    ``measured``
        Produced by executing project code on repo-checked-in or
        deterministically generated input. Citable as a result.
    ``derived``
        Arithmetic over ``measured`` values (a rate, a ratio, a correlation).
    ``specification``
        A constant read out of the source or config — a fact about the system,
        not an experimental finding.
    ``simulated``
        Produced by running project code on a generated cohort. Citable, but
        only for the claim about the *function*, never about the model.
    ``unavailable``
        Cannot be produced on this machine. Carries ``needs`` describing the run
        that would produce it. Never given a number.
    """

    name: str
    value: Any
    unit: str = ""
    kind: str = "measured"
    source: str = ""
    note: str = ""
    needs: str = ""

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        return {k: v for k, v in out.items() if v not in ("", None)}


@dataclass
class Section:
    """A group of measurements plus whatever tables the figures need."""

    key: str
    title: str
    measurements: list[Measurement] = field(default_factory=list)
    tables: dict[str, Any] = field(default_factory=dict)
    commentary: str = ""

    def add(self, name: str, value: Any, **kwargs: Any) -> Measurement:
        measurement = Measurement(name=name, value=value, **kwargs)
        self.measurements.append(measurement)
        return measurement

    def get(self, name: str) -> Any:
        for measurement in self.measurements:
            if measurement.name == name:
                return measurement.value
        raise KeyError(name)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "commentary": self.commentary,
            "measurements": [m.as_dict() for m in self.measurements],
            "tables": self.tables,
        }


def environment() -> dict[str, Any]:
    """What the numbers were measured on. Latency means nothing without it."""
    try:
        commit = (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=PROJECT_ROOT,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            or "unknown"
        )
    except Exception:
        commit = "unknown"

    cpu = platform.processor() or platform.machine()
    return {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": commit,
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "cpu": cpu,
        "note": (
            "All latency figures are single-machine, CPU-only, and measured with "
            "the fake LLM provider so that no network time is included. They "
            "bound the portal's own overhead; they are not model latencies."
        ),
    }


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> Path:
    """Minimal CSV writer — no pandas dependency for the data path."""
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return path
    columns = columns or list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path
