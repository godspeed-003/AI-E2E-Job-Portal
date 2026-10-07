# Figures

17 figures, each written as a paired `.pdf` and `.png`. **Use the PDFs in the
LaTeX build** — they are vector, and the PNGs exist only for previewing in a
browser or pasting into a chat.

Regenerate all of them:

```bash
.venv/Scripts/python -m metrics.run_all
```

Generator: `metrics/figures.py`. Manifest with full captions:
`research paper/results/results.json` → `figures[]`.

---

## IEEE conformance

| | |
|---|---|
| single-column width | 3.5 in (88.9 mm) |
| double-column width | 7.16 in |
| minimum type size | 8 pt, enforced |
| palette | `INK #1a1a1a`, `GRAY #7a7a7a`, `LIGHT #c8c8c8`, `ACCENT #2b5d8a`, `WARN #a33b2a` |

The palette is print-safe and legible in greyscale — `ACCENT` and `WARN` differ
in lightness as well as hue, so a reviewer printing on a mono laser still sees
the distinction.

`\includegraphics[width=\columnwidth]{...}` for the single-column figures and
`\includegraphics[width=\textwidth]{...}` inside `figure*` for the double-column
ones. Widths are already correct, so do not rescale — rescaling is what breaks
the 8 pt floor.

---

## Inventory

`†` = the figure's data is `simulated` (synthetic cohort, seed `20260923`). The
caption must say so, and so must the table it sits beside.

| # | name | span | drawn from |
|---:|---|---|---|
| 1 | `fig_architecture` | double | `m8_egress` — trust boundary, measured byte counts |
| 2 | `fig_pipeline` | double | `m8_egress`, `m9_provider_contract` — per-stage model-call counts |
| 3 | `fig_screening_workflow` | single | `services.application_service.screen` control flow |
| 4 | `fig_interview_workflow` | double | `services.interview_service` control flow |
| 5 | `fig_integrity_decay` | single | `m1_integrity` |
| 6 | `fig_integrity_scenarios` | double | `m1_integrity` |
| 7 | `fig_guardrail_families` | single | `m2_guardrails` |
| 8 | `fig_guardrail_tradeoff` | single | `m2_guardrails` |
| 9 | `fig_ats_granularity` | single | `m3_ats` |
| 10 | `fig_ats_traps` | single | `m3_ats` |
| 11 | `fig_baselines` † | double | `m4_baselines` |
| 12 | `fig_rank_movement` † | double | `m5_fusion_audit` |
| 13 | `fig_lambda_sweep` † | single | `m5_fusion_audit`, `m10_stability` |
| 14 | `fig_integrity_shrinkage` † | single | `m5_fusion_audit` |
| 15 | `fig_latency` | double | `m6_latency` (model stubbed — say so) |
| 16 | `fig_egress` | single | `m8_egress` |
| 17 | `fig_test_distribution` | single | `m7_codebase` |

### The four the draft has slots for

The draft's `[REQUIRED FIGURE]` markers in Section III map to figures 1–4.
Figures 5–17 are new and are what Section V (Results) should be built around.

### Figure 13 carries a hard constraint

`fig_lambda_sweep` is a **sensitivity analysis**. Its peak sits at λ = 0.95
against the shipped default of 0.5, and `m10_stability` reports
`lambda_argmax_is_stable_across_draws = False`. The caption must state that the
sweep optimises agreement with a *synthetic* cohort's concealed ground truth and
that **no value of λ is recommended**. Tuning a hiring weight against synthetic
ground truth and shipping the result is exactly the overfitting the paper warns
about elsewhere.

---

## Why the generator asserts text fits

All four architecture and workflow figures rendered without error while carrying
visible defects:

- text running out through the sides of its box
- a label struck through by its own box border
- an arrow routed across a box it has no edge to
- in `fig_architecture`, an arrow drawn in the colour the legend reserves for
  "leaves the host", on a call that does not leave the host

Matplotlib does not complain about any of these. **Rendering cleanly is not the
same as being correct.**

`metrics/figures.py::_assert_within` now measures rendered glyph extents against
the box that owns them and raises rather than overflowing. If you add a figure
and it raises, the assertion is right and the layout is wrong — shorten the
label or widen the box; do not relax the assertion.

---

## Blocked figures

Slots the draft asks for that cannot be filled, and why. Each corresponds to an
`unavailable` quantity in `results.json` with a `needs` field.

| figure | blocked on |
|---|---|
| human-agreement scatter / Bland–Altman | 3–5 human raters. **No substitute is acceptable** — not self-agreement, not model-vs-model, not one author as rater. |
| ablation ladder A1/A2/A3/A5 | the ladder has not been run |
| local-vs-hosted latency comparison | no local model run (tokens/sec, RAM, VRAM all unavailable) |
| CV / audio detection ROC | no labelled media |
| fairness / subgroup plots | no demographic data, and explicitly out of scope |

See `research paper/results/unavailable.md` for the full text of what each one
needs.

---

## Captions

Every caption is in `results.json` → `figures[].caption`, already written for
the body text and already carrying its provenance qualifier. They range from 193
to 1361 characters; the long ones are long because they carry the measured
numbers inline so the figure can be read without the surrounding text.

Copy them from the JSON rather than rewriting them — the numbers in them are
the measured values and will drift if retyped.
