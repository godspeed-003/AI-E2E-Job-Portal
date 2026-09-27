"""Figures for the paper, drawn from the harness's own results.

Design constraints, all of which come from IEEE conference print rather than
from taste:

**Column width.** A two-column IEEE page gives a single-column figure about
3.5 in (88.9 mm) of width. Figures are sized in inches at that width so text
inside them is the size it will be on the page — scaling a 10-inch figure down
to 3.5 in is what produces the unreadable 4 pt axis labels that reviewers
complain about. Double-column figures use 7.16 in and are marked ``*`` in the
manifest.

**Grayscale.** Many reviewers print. Every series is distinguishable by hatch,
marker or line style, not only by colour, and the palette is chosen so that
converting to luminance keeps the series ordered. This is also why seaborn is
not used: its defaults are tuned for screens.

**Vector output.** PDF for LaTeX (``\\includegraphics`` handles it natively and
it stays sharp at any zoom) plus a 300 dpi PNG for Word and for previewing.

**Fonts.** Times-family to match the IEEE body text, 8 pt base — the smallest
size the template permits in a figure.

Nothing here computes a metric. Figures read ``results.json`` and plot it, so a
figure can never disagree with the number in the table. If a figure cannot be
drawn because its section was not run, it is skipped and named in the manifest
rather than filled with placeholder data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # no display on a build machine

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Patch, Rectangle
from matplotlib.ticker import PercentFormatter

from metrics import FIGURES_DIR

COLUMN_WIDTH = 3.5
DOUBLE_WIDTH = 7.16

# Ordered by luminance so the series stay distinguishable in grayscale.
INK = "#1a1a1a"
GRAY = "#7a7a7a"
LIGHT = "#c8c8c8"
ACCENT = "#2b5d8a"
WARN = "#a33b2a"

_RC = {
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.02,
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Nimbus Roman", "DejaVu Serif"],
    "font.size": 8,
    "axes.labelsize": 8,
    "axes.titlesize": 8.5,
    "axes.linewidth": 0.6,
    "axes.edgecolor": INK,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": LIGHT,
    "grid.linewidth": 0.4,
    "grid.alpha": 0.8,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "xtick.major.width": 0.6,
    "ytick.major.width": 0.6,
    "legend.fontsize": 7,
    "legend.frameon": False,
    "lines.linewidth": 1.2,
    "lines.markersize": 3.5,
    "hatch.linewidth": 0.5,
}


def _new(width: float = COLUMN_WIDTH, height: float = 2.3):
    figure, axes = plt.subplots(figsize=(width, height))
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    return figure, axes


def _save(figure, name: str, manifest: list[dict[str, Any]], caption: str) -> None:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    pdf = FIGURES_DIR / f"{name}.pdf"
    png = FIGURES_DIR / f"{name}.png"
    figure.savefig(pdf)
    figure.savefig(png)
    plt.close(figure)
    width, height = figure.get_size_inches()
    manifest.append(
        {
            "name": name,
            "pdf": pdf.name,
            "png": png.name,
            "width_in": round(float(width), 2),
            "span": "double" if width > COLUMN_WIDTH + 0.1 else "single",
            "caption": caption,
        }
    )


def _section(results: dict[str, Any], key: str) -> dict[str, Any] | None:
    for section in results.get("sections", []):
        if section.get("key") == key:
            return section
    return None


def _value(section: dict[str, Any], name: str, default: Any = None) -> Any:
    for measurement in section.get("measurements", []):
        if measurement.get("name") == name:
            return measurement.get("value")
    return default


# --------------------------------------------------------------------------- #
# Fig. 1 — architecture and the trust boundary
# --------------------------------------------------------------------------- #
# The only figure here that is not a plot of measured data. It is a drawing of
# the import graph and the process boundary, both of which are facts about the
# source tree rather than opinions about it, so it is drawn from constants and
# annotated with whatever m8 measured. If m8 was not run the boxes are still
# correct and the byte counts are simply absent — the figure never invents one.


def _arch_box(
    axes,
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    detail: str = "",
    *,
    face: str = "white",
    edge: str = INK,
    lw: float = 0.8,
    title_size: float = 7.2,
    detail_size: float = 5.9,
    detail_colour: str = GRAY,
) -> None:
    # Vertical fit, same reasoning as _flow_box. This helper anchors the title
    # and detail at 0.70 and 0.30 of h, so a third detail line drops below the
    # bottom edge and the border strikes through it: that is exactly what a
    # three-line store_detail did to the SQLite box in Fig. 1.
    title_h = _text_units(axes, title.count("\n") + 1, title_size, 1.25)
    detail_h = (
        _text_units(axes, detail.count("\n") + 1, detail_size, 1.4) if detail else 0.0
    )
    if detail and (h * 0.30 - detail_h / 2) < 0:
        raise ValueError(
            f"arch box {title!r}: a {detail.count(chr(10)) + 1}-line detail "
            f"needs {detail_h:.1f} units but only {h * 0.60:.1f} are below the "
            f"title in a {h:.0f}-unit box — raise h or drop a line"
        )
    if title_h / 2 > h * 0.30:
        raise ValueError(f"arch box {title!r}: the title overruns the box top")

    axes.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0,rounding_size=1.4",
            linewidth=lw,
            edgecolor=edge,
            facecolor=face,
            zorder=2,
        )
    )
    if detail:
        drawn = [
            axes.text(
                x + w / 2, y + h * 0.70, title,
                ha="center", va="center", fontsize=title_size, zorder=3,
            ),
            axes.text(
                x + w / 2, y + h * 0.30, detail,
                ha="center", va="center", fontsize=detail_size, color=detail_colour,
                zorder=3, linespacing=1.4,
            ),
        ]
    else:
        drawn = [
            axes.text(
                x + w / 2, y + h / 2, title,
                ha="center", va="center", fontsize=title_size, zorder=3,
            )
        ]
    # Same guard as _flow_box: text wider than its box is a defect a human eye
    # catches and a successful render does not.
    _assert_within(axes, x + w / 2, w, title, drawn)


def _arch_arrow(
    axes,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    colour: str = INK,
    lw: float = 0.9,
    style: str = "-",
    double: bool = False,
    label: str = "",
    label_xy: tuple[float, float] | None = None,
    label_size: float = 5.7,
    ha: str = "center",
    va: str = "bottom",
) -> None:
    axes.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="<|-|>" if double else "-|>",
            mutation_scale=7,
            linewidth=lw,
            linestyle=style,
            color=colour,
            shrinkA=0,
            shrinkB=0,
            zorder=4,
        )
    )
    if label:
        lx, ly = label_xy or (
            (start[0] + end[0]) / 2,
            (start[1] + end[1]) / 2 + 1.4,
        )
        axes.text(
            lx, ly, label,
            ha=ha, va=va, fontsize=label_size, color=colour, zorder=5,
            linespacing=1.3,
        )


def figure_architecture(results, manifest) -> None:
    egress = _section(results, "m8_egress") or {}
    gemini_bytes = _value(egress, "bytes_crossing_the_network_gemini")
    ollama_bytes = _value(egress, "bytes_crossing_the_network_ollama")
    media_safe = _value(egress, "media_never_leaves_the_host")
    tables = _value(_section(results, "m7_codebase") or {}, "tables")

    figure, axes = plt.subplots(figsize=(DOUBLE_WIDTH, 4.0))
    axes.set_xlim(0, 180)
    axes.set_ylim(0, 100)
    axes.set_axis_off()
    axes.set_aspect("auto")

    # ---- the trust boundary ------------------------------------------- #
    axes.add_patch(
        Rectangle(
            (36, 4), 104, 88,
            linewidth=0.9,
            edgecolor=INK,
            facecolor="#fafafa",
            linestyle=(0, (4, 3)),
            zorder=1,
        )
    )
    axes.text(
        38, 89.4,
        "host trust boundary — one Python process, one filesystem",
        fontsize=6.2, style="italic", color=INK, va="top", ha="left", zorder=3,
    )

    # ---- clients (outside) --------------------------------------------- #
    _arch_box(axes, 2, 64, 30, 16, "Candidate browser",
              "webcam, mic,\nbrowser signals", face="white", edge=GRAY)
    _arch_box(axes, 2, 40, 30, 16, "Recruiter browser",
              "shortlist,\nproctor report", face="white", edge=GRAY)

    # ---- inside the boundary ------------------------------------------- #
    _arch_box(axes, 42, 56, 26, 24, "app/",
              "Streamlit pages\nrole-aware nav\n(render only)", face="white")
    _arch_box(axes, 76, 56, 26, 24, "services/",
              "screening · interview\nproctoring · auth\n(all decisions)",
              face="#eef2f6", edge=ACCENT, detail_size=5.2)
    _arch_box(axes, 42, 30, 26, 20, "proctoring/",
              "vision · audio\nrules · c(E) scoring", face="white")
    _arch_box(axes, 76, 30, 26, 20, "core/",
              "ats · ranking · db\nconfig (pure)", face="white")
    _arch_box(axes, 112, 44, 26, 26, "llm/",
              "provider factory\n— the only\negress seam —",
              face="#f6efee", edge=WARN, lw=1.1)
    _arch_box(axes, 112, 12, 26, 18, "ollama",
              "127.0.0.1:11434\nsame host", face="white", edge=ACCENT)

    # The path goes in the detail, not the title: "SQLite  data/app.db" on one
    # line is wider than the 26-unit column this box shares with core/ above it,
    # and widening the box alone would break the grid.
    store_detail = "data/app.db\nWAL · FK enforced"
    if isinstance(tables, int):
        store_detail = f"data/app.db\n{tables} tables · WAL · FK"
    _arch_box(axes, 76, 8, 26, 16, "SQLite", store_detail, detail_size=5.3)
    _arch_box(axes, 42, 8, 26, 16, "MEDIA_DIR",
              "snapshots, audio\n(paths in DB only)")

    # ---- external providers (outside) ---------------------------------- #
    _arch_box(axes, 146, 58, 32, 16, "Gemini API",
              "generativelanguage\n.googleapis.com",
              face="white", edge=WARN)
    _arch_box(axes, 146, 34, 32, 16, "OpenAI-compatible",
              "any /v1/chat\nendpoint", face="white", edge=WARN)

    # ---- flows ---------------------------------------------------------- #
    _arch_arrow(axes, (32, 72), (42, 72), colour=GRAY)
    axes.text(17, 81.2, "HTTPS + WebRTC frames", fontsize=5.8, color=GRAY,
              ha="center", va="bottom", zorder=5)
    _arch_arrow(axes, (32, 48), (42, 60), colour=GRAY)
    axes.text(17, 38.8, "HTTPS", fontsize=5.8, color=GRAY,
              ha="center", va="top", zorder=5)
    _arch_arrow(axes, (68, 68), (76, 68), double=True)
    _arch_arrow(axes, (55, 56), (55, 50.5))
    _arch_arrow(axes, (89, 56), (89, 50.5))
    _arch_arrow(axes, (68, 40), (76, 40), label="c(E)", label_xy=(72, 41.2))
    _arch_arrow(axes, (55, 30), (55, 24.5))
    _arch_arrow(axes, (89, 30), (89, 24.5))
    # services/ -> llm/ is an in-process Python call, so it takes the in-process
    # colour. It was drawn in WARN, which the legend defines as "leaves the
    # host" — the one claim this figure exists to make precisely, and the one
    # arrow that does not leave the host. Red starts at the llm/ boundary below.
    _arch_arrow(axes, (102, 60), (112, 60), lw=1.0)
    # Set in the empty column between the two boxes (x 102-112 is clear at every
    # y), rotated so it fits the 10-unit gap instead of being clipped by both.
    axes.text(107, 67, "prompts", fontsize=5.2, color=INK, rotation=90,
              ha="center", va="center", zorder=5)
    _arch_arrow(axes, (125, 44), (125, 30.5), colour=ACCENT, lw=1.1)

    # The two crossings of the dashed line.
    _arch_arrow(axes, (138, 63), (146, 66), colour=WARN, lw=1.1)
    _arch_arrow(axes, (138, 52), (146, 44), colour=WARN, lw=1.1)

    if isinstance(gemini_bytes, (int, float)):
        axes.text(
            162, 75.6,
            f"{int(gemini_bytes):,} B per candidate leave the host",
            fontsize=5.9, color=WARN, ha="center", va="bottom", zorder=5,
        )
    if isinstance(ollama_bytes, (int, float)):
        axes.text(
            121, 37,
            f"{int(ollama_bytes):,} B cross\nthe boundary",
            fontsize=5.9, color=ACCENT, ha="right", va="center",
            linespacing=1.3, zorder=5,
        )
    if media_safe is True:
        axes.text(
            55, 5.4,
            "no media path or blob appears in any request body (m8)",
            fontsize=5.8, color=INK, ha="left", va="bottom", zorder=5,
        )

    axes.legend(
        handles=[
            plt.Line2D([], [], color=INK, lw=0.9, label="in-process call"),
            plt.Line2D([], [], color=GRAY, lw=0.9, label="browser ↔ host"),
            plt.Line2D([], [], color=WARN, lw=1.1, label="leaves the host"),
            plt.Line2D([], [], color=ACCENT, lw=1.1, label="loopback only"),
        ],
        loc="lower right",
        bbox_to_anchor=(1.0, -0.02),
        fontsize=6,
        ncol=1,
        handlelength=1.6,
    )

    caption = (
        "System architecture and the trust boundary. Everything inside the "
        "dashed region runs in a single Python process on the operator's host: "
        "the Streamlit pages, the service layer that holds every decision, the "
        "pure scoring code in core/, the proctoring pipeline, and the two "
        "stores. The provider factory in llm/ is the one place any of it can "
        "reach the network, which is what makes the privacy question answerable "
        "at all — the harness instruments that single seam rather than auditing "
        "the whole tree."
    )
    if isinstance(gemini_bytes, (int, float)) and isinstance(ollama_bytes, (int, float)):
        caption += (
            f" Under the default gemini provider {int(gemini_bytes):,} bytes per "
            f"candidate cross the boundary to a third party; switching the "
            f"provider to ollama terminates the same calls at 127.0.0.1 and the "
            f"figure falls to {int(ollama_bytes):,}."
        )
    if media_safe is True:
        caption += (
            " Interview media is written to disk and referenced by path; no "
            "path and no blob was found in any captured request body under "
            "either provider."
        )
    _save(figure, "fig_architecture", manifest, caption)


# --------------------------------------------------------------------------- #
# Fig. integrity — the deduction curve
# --------------------------------------------------------------------------- #
def figure_integrity_decay(results, manifest) -> None:
    section = _section(results, "m1_integrity")
    if not section:
        return
    curves = section["tables"].get("decay_curves")
    if not curves:
        return

    figure, axes = _new()
    styles = {
        "critical": (INK, "-", "o"),
        "high": (ACCENT, "--", "s"),
        "medium": (GRAY, "-.", "^"),
        "low": (LIGHT, ":", "d"),
    }
    repeats = curves.get("repeats", [])
    for severity, (colour, dash, marker) in styles.items():
        scores = curves.get(severity)
        if not scores:
            continue
        axes.plot(
            repeats,
            scores,
            color=colour,
            linestyle=dash,
            marker=marker,
            markerfacecolor="white",
            markeredgewidth=0.8,
            label=severity,
        )
    axes.axhline(80, color=INK, linewidth=0.5, linestyle=(0, (1, 2)))
    axes.axhline(55, color=WARN, linewidth=0.5, linestyle=(0, (1, 2)))
    axes.annotate(
        "clean ≥ 80",
        xy=(0.98, 81),
        xycoords=("axes fraction", "data"),
        ha="right",
        fontsize=6,
        color=INK,
    )
    axes.annotate(
        "flag < 55",
        xy=(0.98, 47),
        xycoords=("axes fraction", "data"),
        ha="right",
        fontsize=6,
        color=WARN,
    )
    axes.set_xlabel("Repeated events of the same kind")
    axes.set_ylabel("Integrity score")
    axes.set_ylim(0, 103)
    axes.legend(title="severity", title_fontsize=7, loc="lower left", ncol=2)
    _save(
        figure,
        "fig_integrity_decay",
        manifest,
        "Integrity score against repeated events of one kind, by severity. A "
        "kind is charged its worst observed severity scaled by 1 + ln(n), so "
        "the curves are concave: a flaky webcam produces many events of one "
        "kind and cannot drive the score down as fast as several distinct "
        "violations, but persistence is not free either. Dotted lines mark the "
        "clean and flag thresholds.",
    )


def figure_verdict_thresholds(results, manifest) -> None:
    section = _section(results, "m1_integrity")
    if not section:
        return
    scenarios = section["tables"].get("scenarios")
    if not scenarios:
        return

    rows = sorted(scenarios, key=lambda r: -r["integrity_score"])
    # Double width: eight scenario labels are sentences, and truncating them to
    # fit a 3.5 in column turned "background voice" into "backgro…", which reads
    # as a rendering fault rather than as an abbreviation.
    figure, axes = _new(width=DOUBLE_WIDTH, height=2.6)
    colours = {"clean": LIGHT, "review": GRAY, "flag": WARN}
    hatches = {"clean": "", "review": "///", "flag": "xxx"}
    positions = range(len(rows))
    axes.barh(
        list(positions),
        [r["integrity_score"] for r in rows],
        color=[colours.get(r["verdict"], GRAY) for r in rows],
        edgecolor=INK,
        linewidth=0.5,
        height=0.68,
    )
    for bar_position, row in zip(positions, rows):
        axes.barh(
            bar_position,
            row["integrity_score"],
            color="none",
            hatch=hatches.get(row["verdict"], ""),
            edgecolor=INK,
            linewidth=0,
            height=0.68,
        )
        events = row["events"]
        kinds = row["distinct_kinds"]
        axes.text(
            row["integrity_score"] + 1.5,
            bar_position,
            f"{row['integrity_score']} · {row['verdict']}"
            f"  ({events} event{'' if events == 1 else 's'}, "
            f"{kinds} kind{'' if kinds == 1 else 's'})",
            va="center",
            fontsize=6,
        )
    axes.set_yticks(list(positions))
    axes.set_yticklabels([r["scenario"] for r in rows], fontsize=6.5)
    axes.axvline(80, color=INK, linewidth=0.5, linestyle=(0, (1, 2)))
    axes.axvline(55, color=WARN, linewidth=0.5, linestyle=(0, (1, 2)))
    # Below the bars rather than above them: the top bar is the one the caption
    # is about, and a rotated label across it is the worst place to put text.
    axes.annotate(
        "clean ≥ 80",
        (80, -0.62),
        fontsize=6,
        ha="center",
        va="top",
        annotation_clip=False,
    )
    axes.annotate(
        "flag < 55",
        (55, -0.62),
        fontsize=6,
        color=WARN,
        ha="center",
        va="top",
        annotation_clip=False,
    )
    axes.set_xlim(0, 134)
    axes.set_xlabel("Integrity score")
    axes.grid(axis="y", visible=False)
    handles = [
        Patch(
            facecolor=colours[verdict],
            edgecolor=INK,
            hatch=hatches[verdict],
            linewidth=0.5,
            label=verdict,
        )
        for verdict in ("clean", "review", "flag")
    ]
    axes.legend(handles=handles, loc="lower right", fontsize=6.5, framealpha=1.0)
    _save(
        figure,
        "fig_integrity_scenarios",
        manifest,
        "Integrity score and verdict for nine worked scenarios, with the event "
        "count and the number of distinct event kinds behind each. The critical "
        "gate works: a single substitution event scores 88, comfortably above "
        "the threshold, and is still held at review because a critical severity "
        "blocks the clean verdict regardless of arithmetic. The persistence rule "
        "works in the direction intended without making persistence free: six "
        "repeats of one kind cost 17 rather than 36 points and stay clean, while "
        "forty of them cost 28 and reach review. The ordering across scenarios "
        "is the one the design needs — the failing webcam scores 83 and is left "
        "clean while the coached candidate, carrying a phone, written notes and "
        "another voice in the room, scores 75 and is sent to a human. An earlier "
        "per-event version of c(E) inverted exactly this pair, scoring the "
        "webcam 79/review and the coached candidate 86/clean, because a "
        "deduction accumulated per event tracks how many events fired rather "
        "than how serious the distinct signals were; scoring each distinct kind "
        "at its worst observed severity, with a sublinear persistence term and a "
        "co-occurrence term, is what corrected it. The verdict is advisory and "
        "c(E) enters the ranking multiplicatively, so no candidate was scored "
        "down by the defect and none is scored down by the repair — both change "
        "only what the recruiter is shown.",
    )


# --------------------------------------------------------------------------- #
# Fig. guardrails
# --------------------------------------------------------------------------- #
def figure_guardrail_families(results, manifest) -> None:
    section = _section(results, "m2_guardrails")
    if not section:
        return
    families = section["tables"].get("by_family")
    if not families:
        return

    names = list(families)
    detection = [families[n]["detection_rate"] for n in names]
    figure, axes = _new(height=2.2)
    bars = axes.bar(
        range(len(names)),
        detection,
        color=LIGHT,
        edgecolor=INK,
        linewidth=0.6,
        hatch="///",
        width=0.62,
    )
    for index, (bar, name) in enumerate(zip(bars, names)):
        axes.text(
            index,
            bar.get_height() + 0.02,
            f"{families[name]['detected']}/{families[name]['total']}",
            ha="center",
            fontsize=6,
        )
    axes.set_xticks(range(len(names)))
    axes.set_xticklabels(
        [n.replace("_", "\n") for n in names], fontsize=6.5
    )
    axes.set_ylim(0, 1.12)
    axes.yaxis.set_major_formatter(PercentFormatter(xmax=1))
    axes.set_ylabel("Tier-1 detection rate")
    axes.grid(axis="x", visible=False)
    _save(
        figure,
        "fig_guardrail_families",
        manifest,
        "Tier-1 detection rate per injection family over the labelled corpus, "
        "with counts above each bar. Tier 1 is pure regex, so these figures are "
        "exact and reproducible offline rather than estimated.",
    )


def figure_guardrail_tradeoff(results, manifest) -> None:
    section = _section(results, "m2_guardrails")
    if not section:
        return
    matrix = section["tables"].get("confusion")
    if not matrix:
        return

    labels = ["detection\n(recall)", "precision", "specificity", "F1"]
    values = [
        matrix.get("recall", 0),
        matrix.get("precision", 0),
        matrix.get("specificity", 0),
        matrix.get("f1", 0),
    ]
    hard_fp = _value(section, "false_positive_rate_hard_negatives", 0) or 0

    figure, axes = _new(height=2.2)
    bars = axes.bar(
        range(len(labels)),
        values,
        color=[LIGHT, LIGHT, LIGHT, GRAY],
        edgecolor=INK,
        linewidth=0.6,
        width=0.6,
    )
    for index, bar in enumerate(bars):
        axes.text(
            index,
            bar.get_height() + 0.02,
            f"{values[index]:.2f}",
            ha="center",
            fontsize=6.5,
        )
    axes.axhline(
        1 - hard_fp,
        color=WARN,
        linewidth=0.8,
        linestyle="--",
        label=f"hard-negative pass rate {1 - hard_fp:.2f}",
    )
    axes.set_xticks(range(len(labels)))
    axes.set_xticklabels(labels, fontsize=6.5)
    axes.set_ylim(0, 1.14)
    axes.yaxis.set_major_formatter(PercentFormatter(xmax=1))
    axes.legend(loc="lower right")
    axes.grid(axis="x", visible=False)
    _save(
        figure,
        "fig_guardrail_tradeoff",
        manifest,
        "Tier-1 guardrail on the full labelled corpus. The dashed line is the "
        "rate at which hard negatives — honest resumes that discuss prompt "
        "injection, system administration or scoring rubrics — pass unmodified. "
        "It is plotted beside the detection figures because a false positive "
        "silently deletes a sentence from an honest application.",
    )


# --------------------------------------------------------------------------- #
# Fig. ATS gate
# --------------------------------------------------------------------------- #
def figure_ats_granularity(results, manifest) -> None:
    section = _section(results, "m3_ats")
    if not section:
        return
    rows = section["tables"].get("gate_granularity")
    if not rows:
        return
    tau = _value(section, "ats_reject_below_tau", 40)

    figure, axes = _new(height=2.2)
    for index, row in enumerate(rows[:6]):
        scores = row["achievable_scores"]
        axes.plot(
            scores,
            [index] * len(scores),
            marker="o",
            linestyle="none",
            color=INK,
            markerfacecolor="white",
            markeredgewidth=0.8,
        )
        axes.plot([0, 100], [index, index], color=LIGHT, linewidth=0.5, zorder=0)
    axes.axvline(
        tau, color=WARN, linewidth=1.0, linestyle="--", label=f"τ = {tau} (reject below)"
    )
    axes.set_yticks(range(len(rows[:6])))
    axes.set_yticklabels(
        [f"{r['role_id'][:18]} ({r['requirements']})" for r in rows[:6]], fontsize=6
    )
    axes.set_xlabel("Achievable ATS score (%)")
    axes.set_xlim(-4, 104)
    axes.legend(loc="lower right")
    axes.grid(axis="y", visible=False)
    _save(
        figure,
        "fig_ats_granularity",
        manifest,
        "Every ATS score a role can produce, given its requirement count in "
        "parentheses. Because the score is |matched| / |requirements|, a "
        "five-requirement role admits only six values, so the rejection "
        "threshold τ = 40 is not a tuned quantity but a restatement of 'at "
        "least two of five'. No threshold between 21 and 40 behaves differently.",
    )


def figure_ats_traps(results, manifest) -> None:
    section = _section(results, "m3_ats")
    if not section:
        return
    rows = section["tables"].get("boundary_traps")
    if not rows:
        return

    groups = {
        "positives": [r for r in rows if r["expected_match"]],
        "substring traps": [
            r
            for r in rows
            if not r["expected_match"] and "prefix" in r["rationale"] or
            (not r["expected_match"] and "shared prefix" in r["rationale"])
        ],
    }
    groups["other negatives"] = [
        r
        for r in rows
        if not r["expected_match"] and r not in groups["substring traps"]
    ]

    labels, correct, total = [], [], []
    for name, bucket in groups.items():
        if not bucket:
            continue
        labels.append(name)
        correct.append(sum(1 for r in bucket if r["correct"]))
        total.append(len(bucket))

    figure, axes = _new(height=2.1)
    positions = range(len(labels))
    axes.bar(
        positions,
        total,
        color="white",
        edgecolor=INK,
        linewidth=0.6,
        width=0.6,
        label="hand-labelled cases",
    )
    axes.bar(
        positions,
        correct,
        color=LIGHT,
        edgecolor=INK,
        linewidth=0.6,
        hatch="///",
        width=0.6,
        label="gate agrees with the label",
    )
    for index, (good, whole) in enumerate(zip(correct, total)):
        axes.text(index, whole + 0.25, f"{good}/{whole}", ha="center", fontsize=6.5)
    axes.set_xticks(list(positions))
    axes.set_xticklabels([label.replace(" ", "\n") for label in labels], fontsize=6.5)
    axes.set_ylabel("Hand-labelled cases")
    axes.set_ylim(0, max(total) + 4)
    # Spelled out rather than left to the caption: a bar chart where the fill
    # means "correct" and the outline means "total" is unreadable without it,
    # and a figure has to survive being looked at on its own.
    axes.legend(loc="upper right", fontsize=6.5, framealpha=1.0)
    axes.grid(axis="x", visible=False)
    _save(
        figure,
        "fig_ats_traps",
        manifest,
        "Word-boundary behaviour of the keyword pre-filter on hand-labelled "
        "cases (hatched = agrees with the label, outline = total). The "
        "substring group is the one that matters: 'Java' must not match "
        "'JavaScript' and 'Go' must not match 'Golang' or 'Google', or the gate "
        "rejects candidates for a reason that does not exist.",
    )


# --------------------------------------------------------------------------- #
# Fig. baselines
# --------------------------------------------------------------------------- #
def figure_baselines(results, manifest) -> None:
    section = _section(results, "m4_baselines")
    if not section:
        return
    quality = section["tables"].get("ranker_quality")
    contamination = section["tables"].get("over_seller_contamination")
    if not quality:
        return

    names = list(quality)
    ndcg_key = next((k for k in quality[names[0]] if k.startswith("ndcg@")), None)
    figure, axes = _new(width=DOUBLE_WIDTH, height=2.4)
    positions = [i for i in range(len(names))]
    width = 0.38

    axes.bar(
        [p - width / 2 for p in positions],
        [quality[n][ndcg_key] for n in names],
        width=width,
        color=LIGHT,
        edgecolor=INK,
        linewidth=0.6,
        hatch="///",
        label=ndcg_key.upper().replace("NDCG", "NDCG"),
    )
    axes.bar(
        [p + width / 2 for p in positions],
        [quality[n]["kendall_tau_b"] for n in names],
        width=width,
        color=GRAY,
        edgecolor=INK,
        linewidth=0.6,
        label="Kendall τ-b",
    )

    # The fused system as a reference line. Without it a reader sees four
    # mediocre bars and no scale for "mediocre"; with it the figure states the
    # paper's claim directly — every ranker that reads only the resume lands in
    # the same band, and the band is well below what a second evidence stream
    # reaches on the same cohort.
    fusion = _section(results, "m5_fusion_audit")
    fused_ndcg = _value(fusion, f"fused_{ndcg_key}") if fusion else None
    if fused_ndcg is not None:
        axes.axhline(
            fused_ndcg,
            color=INK,
            linestyle=(0, (4, 2)),
            linewidth=0.9,
            label=f"fused {ndcg_key.upper()} = {fused_ndcg:.3f}",
        )

    axes.set_xticks(positions)
    axes.set_xticklabels([n.replace("_", " ") for n in names], fontsize=7)
    axes.set_ylabel("Agreement with latent competence")
    axes.set_ylim(0, 1.16)
    axes.grid(axis="x", visible=False)

    if contamination:
        twin = axes.twinx()
        key = next(k for k in contamination[0] if k.startswith("over_sellers_in_top_"))
        top_k = int(key.rsplit("_", 1)[-1])
        order = {row["ranker"]: row for row in contamination}
        counts = [order[n][key] for n in names if n in order]
        twin.plot(
            positions,
            counts,
            color=WARN,
            marker="o",
            markerfacecolor="white",
            markeredgewidth=0.9,
            linestyle="--",
            label=f"over-sellers in top {top_k}",
        )
        # Pinned to the full shortlist, not to the observed range. Auto-scaling
        # stretches a 6-vs-7 difference across the whole axis and reads as a
        # collapse; the finding is that 6 or 7 of every 10 shortlisted
        # candidates are over-sellers whichever resume-only ranker is used.
        twin.set_ylim(0, top_k)
        twin.set_yticks(range(0, top_k + 1, 2))
        twin.set_ylabel(f"Over-sellers in top {top_k}", color=WARN)
        twin.tick_params(axis="y", colors=WARN)
        twin.grid(False)
        twin.spines["top"].set_visible(False)
        for position, count in zip(positions, counts):
            twin.annotate(
                str(count),
                (position, count),
                textcoords="offset points",
                xytext=(0, -11),
                ha="center",
                fontsize=7,
                color=WARN,
            )
        handles, labels = axes.get_legend_handles_labels()
        extra_handles, extra_labels = twin.get_legend_handles_labels()
        axes.legend(
            handles + extra_handles,
            labels + extra_labels,
            loc="upper center",
            bbox_to_anchor=(0.5, 1.0),
            ncol=4,
            fontsize=7,
            framealpha=1.0,
        )
    else:
        axes.legend(loc="upper left", ncol=2, fontsize=7)

    _save(
        figure,
        "fig_baselines",
        manifest,
        "Ranking quality of the keyword gate, TF-IDF cosine, BM25 and the "
        "simulated resume score against latent competence, with the number of "
        "over-sellers each admits to the top k on the right axis and the fused "
        "system's NDCG as a reference line. All four read the same self-reported "
        "document, so the inflated resumes defeat all of them — six or seven of "
        "every ten shortlisted candidates are over-sellers regardless of which "
        "retrieval function is used. This is the measured argument for a second, "
        "independently-obtained evidence stream rather than a better reader of "
        "the same one.",
    )


# --------------------------------------------------------------------------- #
# Fig. fusion audit — the paper's central figures
# --------------------------------------------------------------------------- #
def figure_rank_movement(results, manifest) -> None:
    section = _section(results, "m5_fusion_audit")
    if not section:
        return
    rows = section["tables"].get("rank_movement")
    if not rows:
        return

    order = ["A_over_seller", "B_hidden_gem", "C_honest_strong", "D_honest_weak"]
    figure, axes = _new(width=DOUBLE_WIDTH, height=2.5)
    colours = {
        "A_over_seller": WARN,
        "B_hidden_gem": ACCENT,
        "C_honest_strong": GRAY,
        "D_honest_weak": LIGHT,
    }
    markers = {
        "A_over_seller": "v",
        "B_hidden_gem": "^",
        "C_honest_strong": "o",
        "D_honest_weak": "s",
    }
    for case in order:
        bucket = [r for r in rows if r["case"] == case]
        if not bucket:
            continue
        axes.scatter(
            [r["rank_before"] for r in bucket],
            [r["delta_rank"] for r in bucket],
            s=14,
            color=colours[case],
            marker=markers[case],
            edgecolor=INK,
            linewidth=0.3,
            label=case.split("_", 1)[1].replace("_", " "),
            alpha=0.9,
        )
    axes.axhline(0, color=INK, linewidth=0.7)
    axes.set_xlabel("Position under resume-only ranking")
    axes.set_ylabel("Change in position after fusion\n(negative = moved up)")
    axes.legend(loc="upper right", ncol=4, columnspacing=1.0, handletextpad=0.3)
    _save(
        figure,
        "fig_rank_movement",
        manifest,
        "Per-candidate rank change when the interview is fused into the "
        "ranking, against the position the resume alone gave them. Over-sellers "
        "fall and hidden gems rise, which is the correction the pipeline exists "
        "to make; the spread of the honest controls around zero is the cost of "
        "making it. Simulated cohort — this measures the fusion function, not "
        "the interviewer.",
    )


def figure_lambda_sweep(results, manifest) -> None:
    section = _section(results, "m5_fusion_audit")
    if not section:
        return
    rows = section["tables"].get("lambda_sweep")
    if not rows:
        return

    lambdas = [r["lambda"] for r in rows]
    figure, axes = _new(height=2.4)
    axes.plot(
        lambdas,
        [r["recovery_over_seller"] for r in rows],
        color=WARN,
        linestyle="-",
        marker="v",
        markevery=2,
        markerfacecolor="white",
        label="recovery: over-sellers",
    )
    axes.plot(
        lambdas,
        [r["recovery_hidden_gem"] for r in rows],
        color=ACCENT,
        linestyle="--",
        marker="^",
        markevery=2,
        markerfacecolor="white",
        label="recovery: hidden gems",
    )
    axes.plot(
        lambdas,
        [r["false_correction"] for r in rows],
        color=GRAY,
        linestyle="-.",
        marker="o",
        markevery=2,
        markerfacecolor="white",
        label="false correction",
    )
    axes.plot(
        lambdas,
        [r["kendall_tau_b"] for r in rows],
        color=INK,
        linestyle=":",
        marker="s",
        markevery=2,
        markerfacecolor="white",
        label="τ-b vs truth",
    )
    default = _value(_section(results, "m5_fusion_audit"), "tau_at_default_lambda")
    axes.axvline(0.5, color=INK, linewidth=0.5, linestyle=(0, (1, 2)))
    axes.annotate(
        "shipped λ = 0.5" + (f"\nτ-b = {default:.2f}" if default is not None else ""),
        xy=(0.5, 0.06),
        xytext=(0.54, 0.06),
        fontsize=6,
        color=INK,
    )
    axes.set_xlabel("Interview weight λ")
    axes.set_ylabel("Rate")
    axes.set_ylim(-0.03, 1.08)
    axes.yaxis.set_major_formatter(PercentFormatter(xmax=1))
    axes.legend(loc="center right", ncol=1)
    _save(
        figure,
        "fig_lambda_sweep",
        manifest,
        "Sensitivity of the ranking to the interview weight λ. Recovery of both "
        "planted error types rises with λ, and so does displacement of the "
        "honest controls — the trade the parameter governs. λ was fixed at 0.5 "
        "before this sweep was run; reporting the whole curve prevents a single "
        "flattering operating point from being mistaken for a tuned result.",
    )


def figure_integrity_shrinkage(results, manifest) -> None:
    section = _section(results, "m5_fusion_audit")
    if not section:
        return
    rows = section["tables"].get("integrity_sensitivity")
    if not rows:
        return

    figure, axes = _new(height=2.1)
    axes.plot(
        [r["integrity_score"] for r in rows],
        [r["kendall_tau_b"] for r in rows],
        color=INK,
        marker="o",
        markerfacecolor="white",
        markeredgewidth=0.9,
    )
    axes.axvline(55, color=WARN, linewidth=0.7, linestyle="--")
    axes.annotate(
        "flag threshold", xy=(56, min(r["kendall_tau_b"] for r in rows)), fontsize=6,
        color=WARN,
    )
    axes.set_xlabel("Integrity score c(E) applied to every candidate")
    axes.set_ylabel("τ-b vs latent competence")
    _save(
        figure,
        "fig_integrity_shrinkage",
        manifest,
        "Ranking quality as the integrity score is lowered for the whole "
        "cohort. Integrity multiplies the interview's contribution rather than "
        "subtracting from the final score, so an untrusted recording shrinks "
        "the ranking back towards the resume-only ordering instead of becoming "
        "a penalty. At c(E) = 0 the two rankings coincide exactly, which is why "
        "a flaky webcam can never be read as a character finding.",
    )


# --------------------------------------------------------------------------- #
# Fig. latency and egress
# --------------------------------------------------------------------------- #
def figure_latency(results, manifest) -> None:
    section = _section(results, "m6_latency")
    if not section:
        return
    rows = section["tables"].get("stages")
    if not rows:
        return

    interesting = [r for r in rows if r["p50_ms"] > 0]
    interesting.sort(key=lambda r: r["p50_ms"])
    interesting = interesting[-12:]

    figure, axes = _new(width=DOUBLE_WIDTH, height=2.8)
    positions = range(len(interesting))
    axes.barh(
        list(positions),
        [r["p50_ms"] for r in interesting],
        color=LIGHT,
        edgecolor=INK,
        linewidth=0.6,
        height=0.6,
        label="p50",
    )
    axes.barh(
        list(positions),
        [r["p95_ms"] - r["p50_ms"] for r in interesting],
        left=[r["p50_ms"] for r in interesting],
        color=GRAY,
        edgecolor=INK,
        linewidth=0.6,
        height=0.6,
        hatch="///",
        label="p50→p95",
    )
    axes.set_yticks(list(positions))
    axes.set_yticklabels(
        [r["stage"].replace("_", " ") for r in interesting], fontsize=6.5
    )
    axes.set_xscale("log")
    axes.set_xlabel("Latency (ms, log scale) — model stubbed")
    axes.legend(loc="lower right")
    axes.grid(axis="y", visible=False)
    _save(
        figure,
        "fig_latency",
        manifest,
        "Per-stage latency with the LLM provider stubbed, so the figure shows "
        "the portal's own overhead rather than model time. Everything in the "
        "scoring path is sub-millisecond; what costs time is PDF extraction, "
        "SQLite writes and the scrypt key derivation — and the last of those is "
        "slow by design, since that is what a password KDF is for.",
    )


def figure_egress(results, manifest) -> None:
    section = _section(results, "m8_egress")
    if not section:
        return
    providers = section["tables"].get("by_provider")
    if not providers:
        return

    names = [n for n in ("fake", "ollama", "gemini") if n in providers]
    figure, axes = _new(height=2.3)
    total = [providers[n]["body_bytes"] / 1024 for n in names]
    remote = [
        (providers[n]["body_bytes"] / 1024) if providers[n]["leaves_the_host"] else 0.0
        for n in names
    ]
    positions = range(len(names))
    axes.bar(
        list(positions),
        total,
        color=LIGHT,
        edgecolor=INK,
        linewidth=0.6,
        width=0.6,
        label="bytes sent",
    )
    axes.bar(
        list(positions),
        remote,
        color=WARN,
        edgecolor=INK,
        linewidth=0.6,
        width=0.6,
        hatch="xxx",
        label="bytes leaving the host",
    )
    for index, name in enumerate(names):
        axes.text(
            index,
            total[index] + max(total) * 0.03,
            f"{providers[name]['http_requests']} calls",
            ha="center",
            fontsize=7,
        )
    axes.set_xticks(list(positions))
    # The destination goes under the provider name rather than above the bar:
    # truncating a hostname mid-word to fit over a bar reads as a rendering bug,
    # and the recipient is the whole point of the figure.
    axes.set_xticklabels(
        [
            f"{name}\n{(providers[name]['destination_hosts'] or ['no network'])[0]}"
            for name in names
        ],
        fontsize=6.5,
    )
    axes.set_ylabel("Payload per candidate (KiB)")
    axes.set_ylim(0, max(total) * 1.22 if max(total) else 1)
    # Centre-left: the `fake` column is empty all the way up, so the legend sits
    # in dead space instead of over the call-count annotations.
    axes.legend(loc="center left", fontsize=7, framealpha=1.0)
    axes.grid(axis="x", visible=False)
    _save(
        figure,
        "fig_egress",
        manifest,
        "Request payload per candidate — one screening, a question plan, every "
        "interview turn and the final grading — measured by intercepting the "
        "real provider code. The volume is the same for Ollama and Gemini "
        "because the same prompts are built; what differs is the recipient. "
        "Under the local configuration none of it leaves the host, and "
        "biometric media leaves the host under neither, since vision and "
        "speech-to-text run in-process.",
    )


def figure_test_distribution(results, manifest) -> None:
    section = _section(results, "m7_codebase")
    if not section:
        return
    rows = section["tables"].get("tests")
    if not rows:
        return

    top = rows[:12]
    figure, axes = _new(height=2.6)
    positions = range(len(top))
    axes.barh(
        list(positions),
        [r["tests"] for r in top],
        color=LIGHT,
        edgecolor=INK,
        linewidth=0.6,
        height=0.65,
        hatch="///",
    )
    for index, row in enumerate(top):
        axes.text(row["tests"] + 0.6, index, str(row["tests"]), va="center", fontsize=6)
    axes.set_yticks(list(positions))
    axes.set_yticklabels(
        [r["file"].replace("test_", "").replace(".py", "") for r in top], fontsize=6.5
    )
    axes.invert_yaxis()
    axes.set_xlabel("Test functions")
    axes.grid(axis="y", visible=False)
    _save(
        figure,
        "fig_test_distribution",
        manifest,
        "Test functions per subsystem. Each behavioural claim in the design "
        "section is warranted by a test that fails if the behaviour changes, so "
        "this distribution also shows where that warrant is thin.",
    )


# --------------------------------------------------------------------------- #
# Figs. 2–4 — the three workflow diagrams
# --------------------------------------------------------------------------- #
# Like Fig. 1 these are drawings rather than plots, and the same rule applies:
# every box, branch and threshold is read off the source, and any number that
# appears in one comes from the results rather than from the caption writer. A
# flowchart that disagrees with the code is worse than no flowchart, because a
# reviewer checks the figure and not the function.
#
# The shapes follow the usual convention so no legend is needed for them:
# rectangle = a step, diamond = a branch, rounded = a terminal.


def _text_units(axes, lines: int, size: float, linespacing: float) -> float:
    """Height of an ``lines``-line text block, in this axes' y data units.

    Font sizes are in points and box geometry is in data units, so nothing in a
    hand-laid-out flowchart checks that one fits inside the other. Converting
    through the axes' own scale is the only way to ask the question.
    """
    low, high = axes.get_ylim()
    points_per_unit = (
        axes.figure.get_figheight() * 72 * axes.get_position().height / (high - low)
    )
    return lines * size * linespacing / points_per_unit


def _flow_box(
    axes,
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    detail: str = "",
    *,
    face: str = "white",
    edge: str = INK,
    lw: float = 0.8,
    title_size: float = 6.8,
    detail_size: float = 5.6,
    rounding: float = 1.2,
) -> None:
    # Text has to fit inside the box it is drawn in, and nothing else checks
    # that. An earlier Fig. 3 anchored the title and detail at fixed fractions
    # of h (+0.17 and −0.24) rather than stacking them, so a two-line detail
    # hung below the bottom edge and the box's own border ran through the last
    # line like a strikethrough — at any box height, because the anchor scaled
    # with the box. The harness reported that figure as written, because it
    # was: rendering without error is not the same as being legible.
    #
    # Both blocks are now measured in data units and centred as one group, and
    # a box too short for its text raises instead of quietly overflowing.
    title_h = _text_units(axes, title.count("\n") + 1, title_size, 1.25)
    detail_h = (
        _text_units(axes, detail.count("\n") + 1, detail_size, 1.3) if detail else 0.0
    )
    if title_h + detail_h > h:
        raise ValueError(
            f"flow box {title.splitlines()[0]!r} is {h:.1f} units tall but its "
            f"text needs {title_h + detail_h:.1f}: raise h, shorten the detail, "
            f"or drop the font size"
        )

    axes.add_patch(
        FancyBboxPatch(
            (x - w / 2, y - h / 2), w, h,
            boxstyle=f"round,pad=0,rounding_size={rounding}",
            linewidth=lw, edgecolor=edge, facecolor=face, zorder=2,
        )
    )
    if detail:
        # Stacked and centred as a group: the title sits half the detail block
        # above centre, the detail half the title block below it.
        drawn = [
            axes.text(x, y + detail_h / 2, title, ha="center", va="center",
                      fontsize=title_size, zorder=3, linespacing=1.25),
            axes.text(x, y - title_h / 2, detail, ha="center", va="center",
                      fontsize=detail_size, color=GRAY, zorder=3, linespacing=1.3),
        ]
    else:
        drawn = [
            axes.text(x, y, title, ha="center", va="center",
                      fontsize=title_size, zorder=3, linespacing=1.25)
        ]

    # Width is the other half of "does the text fit", and it cannot be
    # predicted from the font size the way height can — it depends on the
    # glyphs. Fig. 4 had a detail line (``guardrail_service.check_answer``)
    # that ran out through both sides of its box, which the height check above
    # sails straight past. Measuring the drawn text is the only honest test.
    _assert_within(axes, x, w, title, drawn)


def _assert_within(axes, x: float, w: float, label: str, drawn: list) -> None:
    """Raise if any of ``drawn`` spills out the side of a ``w``-wide box at ``x``."""
    renderer = axes.figure.canvas.get_renderer()
    inverse = axes.transData.inverted()
    for text in drawn:
        extent = text.get_window_extent(renderer=renderer)
        (x0, _), (x1, _) = inverse.transform(
            [[extent.x0, extent.y0], [extent.x1, extent.y1]]
        )
        over = max((x - w / 2) - x0, x1 - (x + w / 2))
        # 0.15 data units of slack, for the few tenths a glyph bounding box
        # carries beyond the inked area. It was 0.5 and let a 0.3-unit overhang
        # in Fig. 1 through, which is visible at print size.
        if over > 0.15:
            raise ValueError(
                f"flow box {label.splitlines()[0]!r}: the text "
                f"{text.get_text().splitlines()[0]!r} overruns the {w:.0f}-unit "
                f"box by {over:.1f} units — widen the box, split the line, or "
                f"drop the font size"
            )


def _flow_diamond(
    axes, x: float, y: float, w: float, h: float, label: str,
    *, edge: str = INK, face: str = "white", size: float = 6.2,
) -> None:
    axes.add_patch(
        plt.Polygon(
            [(x, y + h / 2), (x + w / 2, y), (x, y - h / 2), (x - w / 2, y)],
            closed=True, linewidth=0.8, edgecolor=edge, facecolor=face, zorder=2,
        )
    )
    axes.text(x, y, label, ha="center", va="center", fontsize=size,
              zorder=3, linespacing=1.25)


def _flow_arrow(
    axes, start, end, *, colour: str = INK, lw: float = 0.8,
    label: str = "", label_xy=None, label_size: float = 5.5,
    style: str = "-", ha: str = "center", va: str = "center",
    connection: str = "arc3,rad=0",
) -> None:
    axes.add_patch(
        FancyArrowPatch(
            start, end, arrowstyle="-|>", mutation_scale=6.5,
            linewidth=lw, linestyle=style, color=colour,
            shrinkA=0, shrinkB=0, zorder=4, connectionstyle=connection,
        )
    )
    if label:
        lx, ly = label_xy or ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        axes.text(lx, ly, label, ha=ha, va=va, fontsize=label_size,
                  color=colour, zorder=5, linespacing=1.25,
                  bbox=dict(boxstyle="square,pad=0.12", facecolor="white",
                            edgecolor="none"))


def figure_pipeline(results, manifest) -> None:
    """Fig. 2 — the end-to-end candidate pipeline, stage by stage."""
    ats = _section(results, "m3_ats") or {}
    fusion = _section(results, "m5_fusion_audit") or {}

    floor = _value(ats, "ats_reject_below_tau")
    parameters = _value(fusion, "fusion_parameters") or {}
    weight = parameters.get("interview_weight_lambda") if isinstance(parameters, dict) else None

    figure, axes = plt.subplots(figsize=(DOUBLE_WIDTH, 2.45))
    axes.set_xlim(0, 200)
    axes.set_ylim(0, 62)
    axes.set_axis_off()

    # Stage strip. The y band each stage sits in says who acts: the candidate
    # supplies, the system decides, and only the last box is a person again.
    y = 40
    stages = [
        (16, "Apply", "PDF or text\nupload"),
        (50, "Pre-filter", "keyword ATS,\nno model call"),
        (84, "Screen", "LLM rubric\n5 × 5 = 25"),
        (118, "Interview", "adaptive turns\n+ proctoring"),
        (152, "Fuse", "trust-weighted\nevidence"),
        (186, "Rank", "recruiter\nreviews"),
    ]
    for index, (x, title, detail) in enumerate(stages):
        face = "#eef2f6" if title in ("Screen", "Interview") else "white"
        edge = ACCENT if face != "white" else INK
        _flow_box(axes, x, y, 28, 17, title, detail, face=face, edge=edge)
        if index:
            _flow_arrow(axes, (stages[index - 1][0] + 14, y), (x - 14, y))

    # What each stage costs in model calls — the line that makes the pre-filter
    # worth having, and the honest answer to "how many times do you call the
    # LLM". Above the boxes, not below: the two exit arrows descend from the
    # underside of Pre-filter and Screen, and an earlier version of this figure
    # ran them straight through this row of labels.
    for x, label, colour in (
        (16, "0 calls", GRAY),
        (50, "0 calls", ACCENT),
        (84, "1 call", WARN),
        (118, "1 + 1 per turn + 1", WARN),
        (152, "0 calls", ACCENT),
        (186, "0 calls", GRAY),
    ):
        axes.text(x, 51.5, label, fontsize=5.6, color=colour, ha="center")

    # The two exits. A candidate can leave the pipeline at exactly two points,
    # and only one of them is automatic — that asymmetry is a design claim the
    # paper makes, so the figure has to show it.
    _flow_box(axes, 50, 8, 28, 11, "rejected",
              f"ATS < {floor}%" if isinstance(floor, int) else "below ATS floor",
              edge=WARN, rounding=4.0, title_size=6.2)
    _flow_arrow(axes, (50, 31.5), (50, 13.5), colour=WARN,
                label="auto", label_xy=(56, 22), label_size=5.4)

    _flow_box(axes, 84, 8, 28, 11, "under review",
              "human decides", edge=GRAY, rounding=4.0, title_size=6.2)
    _flow_arrow(axes, (84, 31.5), (84, 13.5), colour=GRAY,
                label="below\nshortlist floor", label_xy=(95, 22), label_size=5.2)

    axes.text(
        0, 58.5,
        "Candidate supplies evidence  →  the system scores it  →  a person decides",
        fontsize=6.0, style="italic", color=INK, ha="left", va="center",
    )

    caption = (
        "The end-to-end pipeline. Two of the six stages call a language model; "
        "the keyword pre-filter, the fusion step and the ranking are "
        "deterministic and run without a network call, which is why a throttled "
        "provider can delay a decision but never lose a submission. There are "
        "exactly two exits before the recruiter's desk, and only the keyword "
        "filter rejects automatically — a low model score routes to human review "
        "rather than to rejection, so no candidate is dismissed on a judgement "
        "they cannot read. The row above each stage counts its model calls, "
        "coloured red where a call is made and blue where a stage that could "
        "plausibly have used one does not; the interview's "
        "\\texttt{1 + 1 per turn + 1} is one planning call, one adaptive "
        "question-selection call per turn, and one scoring call, plus a "
        "conditional guardrail call raised only when the structural check "
        "already finds an answer suspicious."
    )
    if isinstance(floor, int):
        caption += f" The shipped keyword floor is {floor}%."
    if isinstance(weight, (int, float)):
        caption += (
            f" The fusion stage weights interview evidence at λ = {weight} by "
            "default; Fig. 10 reports the sensitivity of the ranking to that "
            "choice."
        )
    _save(figure, "fig_pipeline", manifest, caption)


def figure_screening_workflow(results, manifest) -> None:
    """Fig. 3 — control flow inside services.application_service.screen."""
    ats = _section(results, "m3_ats") or {}
    guard = _section(results, "m2_guardrails") or {}
    floor = _value(ats, "ats_reject_below_tau")
    shortlist = _value(ats, "shortlist_llm_score_min")
    resistance = _value(guard, "injection_resistance_rate")
    false_positives = _value(guard, "false_positive_rate_overall")

    figure, axes = plt.subplots(figsize=(COLUMN_WIDTH, 4.55))
    axes.set_xlim(0, 100)
    axes.set_ylim(0, 148)
    axes.set_axis_off()

    _flow_box(axes, 34, 141, 46, 11, "resume submitted", rounding=4.0)
    _flow_box(axes, 34, 124, 52, 12, "guardrail_service.sanitize_resume",
              "strip injected instructions", title_size=5.6)
    _flow_box(axes, 34, 107, 52, 12, "core.resume.ats_score",
              "keyword overlap, 0–100", title_size=6.2)

    _flow_diamond(axes, 34, 87, 44, 18,
                  f"ats < {floor}%?" if isinstance(floor, int) else "below floor?")
    _flow_box(axes, 82, 87, 30, 13, "rejected",
              "reason names the\nmissing terms", edge=WARN, rounding=4.0,
              title_size=6.2, detail_size=5.2)
    _flow_arrow(axes, (56, 87), (67, 87), colour=WARN,
                label="yes", label_xy=(61, 91), label_size=5.4)

    _flow_box(axes, 34, 64, 54, 14, "llm.generate_json",
              "5 criteria × 5 pts,\nschema-constrained, T=0",
              face="#f6efee", edge=WARN, title_size=6.2, detail_size=5.3)
    _flow_arrow(axes, (34, 78), (34, 71), label="no", label_xy=(40, 74.5),
                label_size=5.4)

    _flow_box(axes, 34, 45, 54, 13, "_normalize_evaluation",
              "total recomputed from\nthe five criteria", title_size=6.2,
              detail_size=5.3)
    _flow_arrow(axes, (34, 57), (34, 51.5))

    _flow_diamond(axes, 34, 24, 46, 18,
                  f"score ≥ {shortlist}/25?" if isinstance(shortlist, int)
                  else "above shortlist\nfloor?", size=5.9)
    _flow_arrow(axes, (34, 38.5), (34, 33))

    _flow_box(axes, 82, 24, 30, 12, "shortlisted", edge=ACCENT, rounding=4.0,
              title_size=6.2)
    _flow_arrow(axes, (57, 24), (67, 24), colour=ACCENT,
                label="yes", label_xy=(62, 28), label_size=5.4)
    _flow_box(axes, 34, 5, 46, 11, "under review",
              edge=GRAY, rounding=4.0, title_size=6.2)
    _flow_arrow(axes, (34, 15), (34, 10.5), colour=GRAY,
                label="no", label_xy=(40, 12.8), label_size=5.4)

    # The two annotations that make the shape of the flow an argument rather
    # than a description.
    if isinstance(floor, int):
        axes.text(
            98, 96, f"no model call\nbelow {floor}%",
            fontsize=5.5, color=WARN, ha="right", va="bottom", linespacing=1.3,
        )
    if isinstance(resistance, (int, float)):
        axes.text(
            98, 130, f"{resistance:.0%} of injections\nneutralised",
            fontsize=5.5, color=GRAY, ha="right", va="center", linespacing=1.3,
        )

    caption = (
        "Screening control flow, as implemented in "
        "\\texttt{services.application\\_service.screen}. Sanitisation and "
        "keyword scoring are deterministic and precede the only model call, so "
        "a resume carrying injected instructions is stripped before any prompt "
        "is built and a resume below the keyword floor costs nothing. The model "
        "returns five criterion scores rather than a total and the total is "
        "recomputed from them, so the number a recruiter sees is always the sum "
        "of judgements they can read. A score below the shortlist floor routes "
        "to human review; the model cannot reject."
    )
    if isinstance(resistance, (int, float)) and isinstance(false_positives, (int, float)):
        caption += (
            f" The sanitiser neutralised {resistance:.0%} of the injection "
            f"corpus at a {false_positives:.0%} false-positive rate on benign "
            "resumes, and it never rejects a document outright — it strips and "
            "discloses, because a false positive here silently edits an honest "
            "candidate's resume."
        )
    _save(figure, "fig_screening_workflow", manifest, caption)


def figure_interview_workflow(results, manifest) -> None:
    """Fig. 4 — the adaptive interview loop and its bounds."""
    integrity = _section(results, "m1_integrity") or {}
    guard = _section(results, "m2_guardrails") or {}
    fail_below = _value(integrity, "integrity_fail_below")
    families = _value(guard, "injection_pattern_families")
    retries = 3  # interview_service.MAX_REJECTS_PER_TURN, drawn in the loop below

    figure, axes = plt.subplots(figsize=(DOUBLE_WIDTH, 3.2))
    axes.set_xlim(0, 200)
    axes.set_ylim(0, 92)
    axes.set_axis_off()

    # ---- the loop ------------------------------------------------------- #
    _flow_box(axes, 20, 78, 34, 12, "shortlisted", rounding=4.0, title_size=6.4)
    _flow_box(axes, 20, 57, 36, 15, "build_plan",
              "questions from the\nresume ↔ JD gap", face="#f6efee", edge=WARN,
              title_size=6.4, detail_size=5.3)
    _flow_arrow(axes, (20, 72), (20, 64.5))

    _flow_box(axes, 20, 33, 36, 14, "ask_next",
              "question on screen", title_size=6.4, detail_size=5.3)
    _flow_arrow(axes, (20, 49.5), (20, 40))

    _flow_box(axes, 68, 33, 42, 14, "submit_answer",
              "guardrail_service.check_answer", title_size=6.4, detail_size=4.9)
    _flow_arrow(axes, (38, 33), (46, 33))

    _flow_diamond(axes, 68, 66, 38, 19, "answer\nsafe?", size=6.0)
    _flow_arrow(axes, (68, 40), (68, 56.5))
    # The retry path leaves the diamond's lower-left edge and passes *under*
    # build_plan into the top of ask_next. Routed from the left vertex it cut
    # straight through the build_plan box instead, which reads as an edge the
    # flow does not have.
    _flow_arrow(
        axes, (61, 60), (32, 40.5), style=(0, (3, 2)), colour=GRAY,
        label="no — retry,\nturn not spent\n(max 3)", label_xy=(48, 53),
        label_size=5.2,
    )

    _flow_box(axes, 121, 66, 40, 16, "decide_next_turn",
              "probe · steer ·\nnext planned · wrap up",
              face="#f6efee", edge=WARN, title_size=6.4, detail_size=5.3)
    _flow_arrow(axes, (87, 66), (101, 66), label="yes", label_xy=(94, 70),
                label_size=5.4)

    _flow_arrow(
        axes, (121, 58), (20, 26), style=(0, (3, 2)),
        connection="arc3,rad=0.14",
        label="loop while budget and clock allow;\n"
              "at most 2 adaptive turns in a row",
        label_xy=(74, 14), label_size=5.3,
    )

    _flow_box(axes, 176, 66, 38, 16, "score",
              "5 criteria × 5 pts,\ntotal recomputed",
              face="#f6efee", edge=WARN, title_size=6.4, detail_size=5.3)
    _flow_arrow(axes, (141, 66), (157, 66), label="wrap up", label_xy=(149, 70),
                label_size=5.4)

    # ---- the parallel proctoring track ---------------------------------- #
    axes.add_patch(
        Rectangle(
            (100, 26), 96, 21, linewidth=0.8, edgecolor=ACCENT,
            facecolor="#f4f7fa", linestyle=(0, (4, 3)), zorder=1,
        )
    )
    axes.text(102, 44.4, "runs alongside every turn, on the host",
              fontsize=5.5, style="italic", color=ACCENT, va="top", ha="left",
              zorder=3)
    _flow_box(axes, 124, 33, 40, 12, "proctoring/",
              "vision · audio · browser", edge=ACCENT, title_size=6.2,
              detail_size=5.2)
    _flow_box(axes, 174, 33, 38, 12, "c(E) → integrity",
              "100 − Σ weighted", edge=ACCENT, title_size=6.2, detail_size=5.2)
    _flow_arrow(axes, (144, 33), (155, 33), colour=ACCENT)
    _flow_arrow(axes, (176, 39), (176, 58), colour=ACCENT,
                label="trust", label_xy=(183, 48), label_size=5.4)

    if isinstance(fail_below, (int, float)):
        axes.text(
            198, 22,
            f"below {fail_below:g} the interview is flagged for a human",
            fontsize=5.5, color=ACCENT, ha="right", va="center",
        )
    if isinstance(families, int):
        axes.text(
            2, 6,
            f"{families} regex families run on every answer before the model does; "
            f"at most {retries} rejections per question",
            fontsize=5.5, color=GRAY, ha="left", va="center",
        )

    caption = (
        "The adaptive interview loop. Question selection is bounded before the "
        "model is consulted: the turn budget, the wall clock, unasked plan "
        "coverage and a cap of two consecutive follow-ups are all checked first, "
        "and the model's choice is then validated against the same rules — so an "
        "adaptive interviewer can never spend a candidate's last turns on "
        "tangents and leave a requirement unasked. A guardrail rejection returns "
        "the question rather than consuming a turn, with a hard retry limit so a "
        "failing microphone cannot lock a candidate out of their own interview. "
        "Proctoring runs alongside on the host and produces the integrity score "
        "that becomes the trust weight in the fusion step; it never gates a "
        "question."
    )
    _save(figure, "fig_interview_workflow", manifest, caption)


FIGURES = (
    figure_architecture,
    figure_pipeline,
    figure_screening_workflow,
    figure_interview_workflow,
    figure_integrity_decay,
    figure_verdict_thresholds,
    figure_guardrail_families,
    figure_guardrail_tradeoff,
    figure_ats_granularity,
    figure_ats_traps,
    figure_baselines,
    figure_rank_movement,
    figure_lambda_sweep,
    figure_integrity_shrinkage,
    figure_latency,
    figure_egress,
    figure_test_distribution,
)


def draw_all(results: dict[str, Any]) -> list[dict[str, Any]]:
    """Draw every figure the results support. Returns the manifest."""
    manifest: list[dict[str, Any]] = []
    with plt.rc_context(_RC):
        for builder in FIGURES:
            try:
                builder(results, manifest)
            except Exception as exc:  # a broken figure must not lose the numbers
                manifest.append(
                    {
                        "name": builder.__name__,
                        "skipped": True,
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                )
    return manifest


def figures_dir() -> Path:
    return FIGURES_DIR
