"""Visual design system.

Streamlit's defaults are fine for a notebook and wrong for a hiring product, so
everything here exists to make the app look deliberate: one set of tokens, one
CSS injection, and a handful of small HTML components that every page reuses.

Two rules kept throughout:

* Tokens mirror ``.streamlit/config.toml`` exactly, so custom HTML and native
  widgets never disagree about a colour or a radius.
* Anything interpolated into HTML goes through :func:`esc`. Candidate names,
  emails and resume snippets are user input and must not be able to inject
  markup into a recruiter's dashboard.
"""

from __future__ import annotations

import html
from typing import Iterable, Literal

import streamlit as st

# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #

BG = "#0B0F1A"
SURFACE = "#141A2A"
SURFACE_2 = "#181F31"
BORDER = "#232B3E"
BORDER_SOFT = "#1B2233"
TEXT = "#E8ECF5"
MUTED = "#94A0BC"
FAINT = "#5D6883"

PRIMARY = "#6366F1"
PRIMARY_DIM = "#4F46E5"
ACCENT = "#8B93FF"

SUCCESS = "#22C55E"
WARNING = "#F59E0B"
DANGER = "#EF4444"
INFO = "#38BDF8"
NEUTRAL = "#64748B"

Tone = Literal["primary", "success", "warning", "danger", "info", "neutral"]

_TONE_COLORS: dict[str, str] = {
    "primary": PRIMARY,
    "success": SUCCESS,
    "warning": WARNING,
    "danger": DANGER,
    "info": INFO,
    "neutral": NEUTRAL,
}


def esc(value: object) -> str:
    """HTML-escape anything before it goes into a component."""
    return html.escape(str(value if value is not None else ""), quote=True)


def tone_color(tone: str) -> str:
    return _TONE_COLORS.get(tone, NEUTRAL)


# --------------------------------------------------------------------------- #
# Status vocabulary — one mapping so a status looks identical everywhere
# --------------------------------------------------------------------------- #

STATUS_TONES: dict[str, tuple[str, str]] = {
    # applications
    "draft": ("neutral", "Draft"),
    "under_review": ("info", "Under review"),
    "shortlisted": ("success", "Shortlisted"),
    "rejected": ("danger", "Not selected"),
    "hired": ("success", "Hired"),
    "withdrawn": ("neutral", "Withdrawn"),
    # interviews
    "pending": ("warning", "Awaiting interview"),
    "in_progress": ("info", "In progress"),
    "completed": ("success", "Completed"),
    "expired": ("danger", "Window closed"),
    "abandoned": ("neutral", "Abandoned"),
    "scored": ("success", "Scored"),
    # integrity verdicts — the exact strings compute_integrity_score emits, not
    # near-misses: "clear"/"flagged" would fall through to a grey pill labelled
    # from the raw value, so a flagged session would read as unremarkable.
    "clean": ("success", "Clean"),
    "review": ("warning", "Needs review"),
    "flag": ("danger", "Flagged"),
}


def status_meta(status: str) -> tuple[str, str]:
    """Return ``(tone, label)`` for a raw DB status string."""
    key = (status or "").strip().lower()
    if key in STATUS_TONES:
        return STATUS_TONES[key]
    return "neutral", key.replace("_", " ").title() or "Unknown"


# --------------------------------------------------------------------------- #
# Global stylesheet
# --------------------------------------------------------------------------- #

_CSS = f"""
<style id="portal-theme">
:root {{
  --bg: {BG};
  --surface: {SURFACE};
  --surface-2: {SURFACE_2};
  --border: {BORDER};
  --border-soft: {BORDER_SOFT};
  --text: {TEXT};
  --muted: {MUTED};
  --faint: {FAINT};
  --primary: {PRIMARY};
  --primary-dim: {PRIMARY_DIM};
  --accent: {ACCENT};
  --success: {SUCCESS};
  --warning: {WARNING};
  --danger: {DANGER};
  --info: {INFO};
  --neutral: {NEUTRAL};
  --radius: 14px;
  --radius-sm: 9px;
  --shadow: 0 1px 2px rgba(0,0,0,.32), 0 8px 24px -12px rgba(0,0,0,.55);
}}

/* Roomier page, but never edge-to-edge on a wide monitor. */
.block-container {{ padding-top: 2.1rem; padding-bottom: 4rem; max-width: 1280px; }}

/* The default Streamlit chrome reads as "someone's script", not a product. */
#MainMenu, footer {{ visibility: hidden; }}
header[data-testid="stHeader"] {{ background: transparent; }}

h1, h2, h3, h4 {{ letter-spacing: -0.02em; font-weight: 650; }}
h1 {{ font-size: 1.95rem; }}
a {{ text-decoration: none; }}
a:hover {{ text-decoration: underline; }}

/* Primary buttons get the gradient; secondary stay quiet. */
.stButton > button, .stFormSubmitButton > button, .stDownloadButton > button {{
  font-weight: 600;
  transition: transform .08s ease, box-shadow .18s ease, border-color .18s ease;
}}
.stButton > button:hover, .stFormSubmitButton > button:hover {{
  transform: translateY(-1px);
}}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {{
  background: linear-gradient(135deg, var(--primary) 0%, #7C5CFF 100%);
  border: 1px solid rgba(255,255,255,.14);
  box-shadow: 0 6px 18px -8px rgba(99,102,241,.9);
}}

/* Tabs read as segmented control rather than browser tabs. */
.stTabs [data-baseweb="tab-list"] {{
  gap: .25rem; background: var(--surface); padding: .3rem;
  border-radius: var(--radius-sm); border: 1px solid var(--border);
}}
.stTabs [data-baseweb="tab"] {{
  border-radius: 7px; padding: .35rem .85rem; color: var(--muted);
}}
.stTabs [aria-selected="true"] {{ background: var(--surface-2); color: var(--text); }}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{
  display: none;
}}

[data-testid="stMetric"] {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 1rem 1.1rem;
}}
[data-testid="stMetricLabel"] {{ color: var(--muted); font-size: .78rem;
  text-transform: uppercase; letter-spacing: .07em; }}

[data-testid="stSidebarNav"] {{ padding-top: .4rem; }}
[data-testid="stExpander"] details {{
  border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--surface);
}}

/* ---- components ---- */

.p-pill {{
  display: inline-flex; align-items: center; gap: .4rem;
  padding: .2rem .62rem; border-radius: 999px;
  font-size: .76rem; font-weight: 600; line-height: 1.5;
  white-space: nowrap;
}}
.p-pill .dot {{ width: 6px; height: 6px; border-radius: 50%; background: currentColor; }}

.p-hd {{ display: flex; align-items: flex-start; gap: .9rem; margin-bottom: 1.25rem; }}
.p-hd .ico {{
  width: 42px; height: 42px; flex: 0 0 42px; border-radius: 12px;
  display: grid; place-items: center; font-size: 1.25rem;
  background: linear-gradient(135deg, rgba(99,102,241,.22), rgba(124,92,255,.10));
  border: 1px solid rgba(139,147,255,.28);
}}
.p-hd h1 {{ margin: 0; font-size: 1.6rem; line-height: 1.25; }}
.p-hd p {{ margin: .18rem 0 0; color: var(--muted); font-size: .92rem; }}

.p-card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 1.05rem 1.15rem;
  box-shadow: var(--shadow); margin-bottom: .8rem;
}}
.p-card.hover:hover {{ border-color: rgba(139,147,255,.42); }}
.p-card .row {{ display: flex; align-items: center;
  justify-content: space-between; gap: .8rem; }}
.p-card .title {{ font-weight: 640; font-size: 1.02rem; }}
.p-card .sub {{ color: var(--muted); font-size: .85rem; margin-top: .15rem; }}
.p-card .body {{ margin-top: .7rem; font-size: .9rem; color: var(--text); }}

.p-tile {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: .9rem 1rem; height: 100%;
  border-top: 2px solid var(--tile-accent, var(--border));
}}
.p-tile .lbl {{ color: var(--muted); font-size: .74rem;
  text-transform: uppercase; letter-spacing: .08em; }}
.p-tile .val {{ font-size: 1.62rem; font-weight: 660; line-height: 1.2;
  margin-top: .28rem; }}
.p-tile .hint {{ color: var(--faint); font-size: .78rem; margin-top: .2rem; }}

.p-kv {{ display: flex; justify-content: space-between; gap: 1rem;
  padding: .42rem 0; border-bottom: 1px dashed var(--border-soft);
  font-size: .88rem; }}
.p-kv:last-child {{ border-bottom: none; }}
.p-kv .k {{ color: var(--muted); }}
.p-kv .v {{ font-weight: 560; text-align: right; }}

.p-empty {{
  text-align: center; padding: 2.4rem 1rem; border-radius: var(--radius);
  border: 1px dashed var(--border); background: rgba(20,26,42,.55);
}}
.p-empty .ico {{ font-size: 1.9rem; opacity: .75; }}
.p-empty .t {{ font-weight: 620; margin-top: .5rem; }}
.p-empty .b {{ color: var(--muted); font-size: .88rem; margin-top: .25rem; }}

.p-hero {{
  border-radius: 20px; padding: 2.3rem 2.1rem;
  background:
    radial-gradient(900px 380px at 8% -20%, rgba(99,102,241,.30), transparent 62%),
    radial-gradient(700px 340px at 105% 10%, rgba(56,189,248,.16), transparent 60%),
    linear-gradient(160deg, #101728 0%, #0B0F1A 100%);
  border: 1px solid rgba(139,147,255,.24);
}}
.p-hero .eyebrow {{
  display: inline-flex; align-items: center; gap: .45rem;
  font-size: .74rem; font-weight: 640; letter-spacing: .13em;
  text-transform: uppercase; color: var(--accent);
  border: 1px solid rgba(139,147,255,.3); border-radius: 999px;
  padding: .22rem .7rem; background: rgba(99,102,241,.12);
}}
.p-hero h1 {{ font-size: 2.35rem; margin: .85rem 0 .5rem; line-height: 1.1; }}
.p-hero h1 .grad {{
  background: linear-gradient(100deg, #A5B4FC, #7DD3FC 55%, #C4B5FD);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}}
.p-hero p {{ color: #B9C3DA; font-size: 1rem; max-width: 46ch; margin: 0; }}
.p-hero ul {{ list-style: none; padding: 0; margin: 1.35rem 0 0;
  display: flex; flex-wrap: wrap; gap: .5rem; }}
.p-hero li {{
  font-size: .82rem; color: #CBD5E9; padding: .34rem .72rem;
  border: 1px solid var(--border); border-radius: 999px;
  background: rgba(20,26,42,.75);
}}

.p-timeline {{ position: relative; margin: .3rem 0 0; padding-left: 1.15rem; }}
.p-timeline::before {{ content: ""; position: absolute; left: 4px; top: .35rem;
  bottom: .35rem; width: 2px; background: var(--border); }}
.p-ev {{ position: relative; padding: .42rem 0 .42rem .35rem; font-size: .86rem; }}
.p-ev::before {{ content: ""; position: absolute; left: -1.15rem; top: .78rem;
  width: 9px; height: 9px; border-radius: 50%;
  background: var(--ev-color, var(--neutral));
  box-shadow: 0 0 0 3px var(--bg); }}
.p-ev .t {{ color: var(--faint); font-variant-numeric: tabular-nums;
  margin-right: .5rem; font-size: .8rem; }}
.p-ev .d {{ color: var(--muted); }}

/* Sandbox must be impossible to mistake for production. */
.p-sandbox {{
  display: flex; align-items: center; gap: .6rem;
  border-radius: var(--radius-sm); padding: .55rem .85rem;
  margin-bottom: 1rem; font-size: .86rem; font-weight: 560;
  color: #FFE9B8; background: rgba(245,158,11,.12);
  border: 1px solid rgba(245,158,11,.4);
  background-image: repeating-linear-gradient(135deg,
    rgba(245,158,11,.08) 0 10px, transparent 10px 20px);
}}

.p-hud {{
  display: flex; flex-wrap: wrap; gap: .5rem; align-items: center;
  padding: .55rem .7rem; border-radius: var(--radius-sm);
  background: var(--surface); border: 1px solid var(--border);
}}
.p-hud .item {{ display: inline-flex; align-items: center; gap: .38rem;
  font-size: .8rem; color: var(--muted); }}
.p-hud .item b {{ color: var(--text); font-weight: 620;
  font-variant-numeric: tabular-nums; }}

.p-q {{
  border-radius: var(--radius); padding: 1.1rem 1.25rem;
  background: linear-gradient(150deg, rgba(99,102,241,.14), rgba(20,26,42,.9));
  border: 1px solid rgba(139,147,255,.3);
}}
.p-q .who {{ font-size: .74rem; letter-spacing: .1em; text-transform: uppercase;
  color: var(--accent); font-weight: 640; }}
.p-q .txt {{ font-size: 1.08rem; line-height: 1.55; margin-top: .45rem; }}

.p-ans {{
  border-radius: var(--radius); padding: .85rem 1.05rem; margin-top: .55rem;
  background: var(--surface-2); border: 1px solid var(--border);
  font-size: .92rem; line-height: 1.55;
}}
.p-ans .who {{ font-size: .74rem; letter-spacing: .1em; text-transform: uppercase;
  color: var(--muted); font-weight: 640; margin-bottom: .3rem; }}
</style>
"""


def inject() -> None:
    """Add the stylesheet once per rerun. Call at the top of every page."""
    st.markdown(_CSS, unsafe_allow_html=True)


def html_block(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def hide_sidebar() -> None:
    """Collapse the sidebar out of existence for this rerun.

    Used by the signed-out screens. ``st.navigation`` is sticky on the frontend:
    a run that does not call it leaves the previous run's page list on screen, so
    after a sign-out the nav of the account that just left would linger. Hiding
    the whole sidebar is one line and does not depend on Streamlit internals.
    """
    st.markdown(
        "<style>"
        'section[data-testid="stSidebar"],'
        '[data-testid="stSidebarCollapsedControl"]{display:none!important}'
        "</style>",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Components — each returns markup so pages can compose before rendering
# --------------------------------------------------------------------------- #


def pill(label: str, tone: str = "neutral", *, dot: bool = True) -> str:
    color = tone_color(tone)
    marker = '<span class="dot"></span>' if dot else ""
    return (
        f'<span class="p-pill" style="color:{color};'
        f"background:{color}1F;border:1px solid {color}55;\">"
        f"{marker}{esc(label)}</span>"
    )


def status_pill(status: str) -> str:
    tone, label = status_meta(status)
    return pill(label, tone)


def page_header(title: str, subtitle: str = "", icon: str = "") -> None:
    icon_html = f'<div class="ico">{esc(icon)}</div>' if icon else ""
    sub_html = f"<p>{esc(subtitle)}</p>" if subtitle else ""
    html_block(
        f'<div class="p-hd">{icon_html}<div><h1>{esc(title)}</h1>{sub_html}</div></div>'
    )


def metric_tile(label: str, value: object, hint: str = "", tone: str = "primary") -> str:
    return (
        f'<div class="p-tile" style="--tile-accent:{tone_color(tone)}">'
        f'<div class="lbl">{esc(label)}</div>'
        f'<div class="val">{esc(value)}</div>'
        + (f'<div class="hint">{esc(hint)}</div>' if hint else "")
        + "</div>"
    )


def metric_row(tiles: Iterable[str]) -> None:
    """Render pre-built tiles in equal columns."""
    items = list(tiles)
    if not items:
        return
    for column, markup in zip(st.columns(len(items)), items):
        with column:
            html_block(markup)


def score_ring(
    value: float, maximum: float = 100.0, *, label: str = "", size: int = 108
) -> str:
    """SVG donut. Colour is derived from the ratio, so good/bad reads instantly."""
    maximum = maximum or 1
    ratio = max(0.0, min(1.0, float(value) / float(maximum)))
    color = SUCCESS if ratio >= 0.7 else WARNING if ratio >= 0.45 else DANGER
    radius = size / 2 - 9
    circumference = 2 * 3.14159 * radius
    offset = circumference * (1 - ratio)
    shown = f"{value:g}"
    return f"""
<div style="display:flex;flex-direction:column;align-items:center;gap:.35rem">
  <svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">
    <circle cx="{size/2}" cy="{size/2}" r="{radius}" fill="none"
            stroke="{BORDER}" stroke-width="9"/>
    <circle cx="{size/2}" cy="{size/2}" r="{radius}" fill="none"
            stroke="{color}" stroke-width="9" stroke-linecap="round"
            stroke-dasharray="{circumference:.2f}"
            stroke-dashoffset="{offset:.2f}"
            transform="rotate(-90 {size/2} {size/2})"/>
    <text x="50%" y="49%" text-anchor="middle" dominant-baseline="middle"
          fill="{TEXT}" font-size="{size*0.26:.0f}" font-weight="680">{shown}</text>
    <text x="50%" y="66%" text-anchor="middle" dominant-baseline="middle"
          fill="{MUTED}" font-size="{size*0.115:.0f}">/ {maximum:g}</text>
  </svg>
  {f'<div style="color:{MUTED};font-size:.8rem">{esc(label)}</div>' if label else ""}
</div>"""


def card(
    title: str,
    *,
    subtitle: str = "",
    badge: str = "",
    body: str = "",
    hover: bool = False,
) -> str:
    """A list row. ``badge`` and ``body`` may contain markup from this module."""
    return (
        f'<div class="p-card{" hover" if hover else ""}">'
        f'<div class="row"><div>'
        f'<div class="title">{esc(title)}</div>'
        + (f'<div class="sub">{esc(subtitle)}</div>' if subtitle else "")
        + "</div>"
        + (f"<div>{badge}</div>" if badge else "")
        + "</div>"
        + (f'<div class="body">{body}</div>' if body else "")
        + "</div>"
    )


def kv(pairs: Iterable[tuple[str, object]]) -> str:
    rows = "".join(
        f'<div class="p-kv"><span class="k">{esc(k)}</span>'
        f'<span class="v">{esc(v)}</span></div>'
        for k, v in pairs
    )
    return f'<div class="p-card">{rows}</div>'


def empty_state(title: str, body: str = "", icon: str = "○") -> None:
    html_block(
        f'<div class="p-empty"><div class="ico">{esc(icon)}</div>'
        f'<div class="t">{esc(title)}</div>'
        + (f'<div class="b">{esc(body)}</div>' if body else "")
        + "</div>"
    )


def sandbox_banner(detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    html_block(
        '<div class="p-sandbox">🧪 <span>Sandbox mode: this data is disposable and '
        f"hidden from real reporting{esc(suffix)}</span></div>"
    )


def hero(
    eyebrow: str, headline: str, gradient_tail: str, blurb: str, features: Iterable[str]
) -> None:
    chips = "".join(f"<li>{esc(item)}</li>" for item in features)
    html_block(
        f'<div class="p-hero"><span class="eyebrow">{esc(eyebrow)}</span>'
        f'<h1>{esc(headline)} <span class="grad">{esc(gradient_tail)}</span></h1>'
        f"<p>{esc(blurb)}</p><ul>{chips}</ul></div>"
    )


def hud(items: Iterable[tuple[str, object]]) -> str:
    body = "".join(
        f'<span class="item">{esc(label)} <b>{esc(value)}</b></span>'
        for label, value in items
    )
    return f'<div class="p-hud">{body}</div>'


def question_block(text: str, *, who: str = "AI interviewer") -> str:
    return (
        f'<div class="p-q"><div class="who">{esc(who)}</div>'
        f'<div class="txt">{esc(text)}</div></div>'
    )


def answer_block(text: str, *, who: str = "You") -> str:
    return (
        f'<div class="p-ans"><div class="who">{esc(who)}</div>{esc(text)}</div>'
    )


def timeline(events: Iterable[tuple[str, str, str]]) -> str:
    """``events`` are ``(timestamp_label, description, tone)`` triples."""
    rows = "".join(
        f'<div class="p-ev" style="--ev-color:{tone_color(tone)}">'
        f'<span class="t">{esc(stamp)}</span><span class="d">{esc(text)}</span></div>'
        for stamp, text, tone in events
    )
    return f'<div class="p-timeline">{rows}</div>' if rows else ""
