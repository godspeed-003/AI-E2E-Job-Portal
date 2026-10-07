# DO NOT USE THESE FILES IN THE RESEARCH PAPER

**Status:** quarantined 2026-09-27. Kept in the tree only so the record of what
was produced survives. Superseded by `research paper/results/` and
`research paper/figures/`.

Everything in this directory was produced by `generate_metrics.py` (repo root).
**Seven of the thirteen figures, and six of the fourteen values in
`system_metrics_summary.json`, contain numbers that were drawn from
`random.uniform` / `random.random` or asserted in the source rather than
observed.** They are not measurements of this system.

The script also no longer runs: it imports `seaborn`, which is not in the
environment.

---

## Why this notice exists rather than a deletion

A deleted file leaves no explanation. Someone assembling the paper who globs
`results/metrics/*.png` needs to find out *here* why these cannot be used, and
which of them are salvageable. Git history retains the files either way.

---

## Per-figure verdict

### FABRICATED — must never appear in the paper or any derived document

| file | what makes it fabricated |
|---|---|
| `B_Skill_Match_Ratio.png` | `skill_match_ratio = match_score + random.uniform(-0.1, 0.1)` |
| `C_Processing_Time.png` | `processing_time_sec = <invented base> + random.uniform(0.1, 0.5)` — no stage was ever timed |
| `E_Consistency_Score.png` | `consistency_scores = [base, base, base]` and `consistency_std = 0.0`, hardcoded. The model was never run three times. The plot's error bars are zero **by assignment**, not by observation. |
| `F_Prompt_Sensitivity.png` | `prompt_delta = 0 if random.random() > 0.1 else 0.5`. No second prompt variant was ever sent. |
| `I_Human_vs_AI.png` | `human_score = ai_score_normalized + random.uniform(-1.0, 1.0)`. **No human rater has ever scored a candidate in this project.** The reported correlation of 0.9897 is a correlation between a number and itself plus noise. |
| `J_End_To_End_Time.png` | already-fabricated `processing_time_sec` `+ random.uniform(2.0, 5.5)` |
| `K_Pipeline_Failures.png` | `parsing_failed = random.random() < 0.02`; `llm_failed` gated on `random.random() < 0.3`. No failure was ever observed. |

`E_Consistency_Score.png` deserves particular care: it is the one that looks
most credible. A determinism claim is *plausible* at `temperature=0`, and the
source comment even reasons about why. But the figure does not test the claim —
it copies one score three times and plots a standard deviation of zero. The
shipped harness does test determinism properly, over 1000 repeat calls
(`m1_integrity`) and across pipeline stages (`m6_latency`), and both report
`True`. **Cite those, not this.**

### REAL SOURCE, but unvalidated and superseded

These derive from `data/results/*.json`, which are genuine pipeline outputs.
They are not fabrications. They are still unusable as published figures because
they carry no provenance label, no seed, no environment record, no cohort
description, and were never cross-checked — and because the shipped harness now
measures the same things with all of that attached.

| file | source column | replaced by |
|---|---|---|
| `A_Match_Score_Distribution.png` | `match_score` | `m4_baselines` ranker quality |
| `D_Answer_Scores_Boxplot.png` | `llm_score / max_score` | `m5_fusion_audit` score distributions |
| `G_Shortlist_Rate.png` | shortlist flags | `m3_ats` gate behaviour |
| `H_Score_Gap.png` | top-candidate score deltas | `m5_fusion_audit` rank movement |
| `L_ATS_vs_LLM_Correlation.png` | `ats_score` vs `llm_score` | `m4_baselines` ranker agreement (τ-b) |
| `M_Feedback_Depth.png` | `num_strengths`, `num_weaknesses` | not replaced — see below |

`M_Feedback_Depth.png` counts real extracted bullets and has no replacement in
the shipped harness. If a figure on feedback depth is wanted, it can be measured
honestly — but do not reuse this PNG, because it was rendered from a dataframe
that also carried the fabricated columns, and the figure carries no record of
which rows survived filtering.

---

## `system_metrics_summary.json` — field by field

| field | value | verdict |
|---|---:|---|
| `avg_match_score` | 0.5659 | real source, unvalidated |
| `median_match_score` | 0.76 | real source, unvalidated |
| `total_candidates` | 27 | real source |
| `shortlisted` | 17 | real source |
| `shortlist_rate` | 0.6296 | real source |
| `avg_top_gap` | 0.0580 | real source, unvalidated |
| `ats_llm_score_correlation` | 0.7441 | real source, unvalidated |
| `avg_processing_time_sec` | 2.5455 | **FABRICATED** |
| `avg_total_pipeline_time` | 6.3123 | **FABRICATED** |
| `avg_consistency_std` | 0.0 | **ASSERTED, not measured** |
| `human_ai_mae` | 0.4237 | **FABRICATED** |
| `human_ai_correlation` | 0.9897 | **FABRICATED** |
| `error_rate_overall` | 0.0 | **FABRICATED** |

`detailed_candidate_metrics.csv` (27 rows) mixes real and fabricated columns in
the same table with no marking. Do not use it as a data appendix.

---

## What to use instead

| you want | use |
|---|---|
| ranking quality, baselines | `research paper/results/` → `m4_baselines`, `m5_fusion_audit` |
| stability / uncertainty | `m10_stability` (200 cohort draws) |
| determinism | `m1_integrity`, `m6_latency` |
| latency | `m6_latency` (51 measured stages, model stubbed — stated) |
| failure behaviour | `m8_egress`, `m9_provider_contract` |
| human agreement | **nothing. It is unmeasured and declared `unavailable`.** Needs 3–5 raters, Fleiss' κ, Pearson/Spearman. A self-agreement or model-vs-model figure is not a substitute. |

Full index: `research paper/METRICS-PROVENANCE.md`.
Figures ready for the paper: `research paper/figures/` (17 PDF+PNG pairs).
