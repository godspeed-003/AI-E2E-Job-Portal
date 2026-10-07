# Metrics Provenance

Every number this project reports, where it came from, and what it is allowed to
support.

**Measured at:** 2026-09-27T03:27:43Z · commit `11b9495`
**Machine:** Windows 11, AMD64, Intel64 Family 6 Model 158 Stepping 10, CPU-only
**Python:** 3.12.10
**Harness failures:** none (all 11 modules completed)

Reproduce the entire set:

```bash
.venv/Scripts/python -m metrics.run_all
```

Outputs land in `research paper/results/` (`results.json`, `provenance.csv`,
`tables/` — 54 CSVs, `unavailable.md`) and `research paper/figures/` (17 PDF+PNG
pairs at IEEE column widths).

---

## 1. The five labels

Every quantity carries exactly one provenance label, **assigned by the module
that produced it, not by an author at writing time**. This is the point of the
harness. A label cannot be upgraded by rewording a sentence.

| label | meaning | may support |
|---|---|---|
| `measured` | observed by running the shipped code on this machine | a claim about the system's behaviour |
| `derived` | arithmetic over measured values (ratios, deltas, rates) | the same claim, at one remove |
| `specification` | read out of the source — a constant, not a result | a description of the design |
| `simulated` | produced against a constructed cohort or the `fake` provider | a claim about *the method*, never about real candidates |
| `unavailable` | **not measured.** Carries a `needs` field. Never has a value. | nothing |

### The two rules that make the labels worth having

1. **`unavailable` quantities never carry a number.** Not a placeholder, not a
   zero, not an estimate. The field is `null` and a sibling `needs` field states
   in prose what would have to exist to measure it.
2. **`simulated` must appear in the table and in the caption, not only in the
   prose.** A reader skimming a results table must not be able to mistake a
   synthetic cohort for a real one.

---

## 2. What was produced

**243 quantities across 11 modules.**

| label | count |
|---|---:|
| `measured` | 131 |
| `simulated` | 37 |
| `specification` | 35 |
| `derived` | 24 |
| `unavailable` | **16** |

Plus 10 further gaps declared at the top level of `results.json` rather than
inside a module (`unavailable_declared`), which are the cross-cutting ones: the
interview-validity re-run, end-to-end error recovery, model inference latency,
tokens/sec, peak RAM+VRAM, Whisper latency, the A0–A5 ablation ladder,
encryption at rest, automatic retention, and `human_ai_correlation`.

That last one is declared unavailable **deliberately and permanently as far as
this repository is concerned** — see §6.

### Per module

| module | n | breakdown | tables |
|---|---:|---|---:|
| `m1_integrity` | 20 | meas 13 · spec 6 · unav 1 | 6 |
| `m2_guardrails` | 21 | meas 7 · deri 10 · spec 3 · unav 1 | 8 |
| `m3_ats` | 18 | meas 5 · deri 4 · spec 8 · unav 1 | 6 |
| `m4_baselines` | 14 | meas 1 · **simu 9** · spec 3 · unav 1 | 4 |
| `m5_fusion_audit` | 30 | meas 6 · **simu 16** · deri 2 · spec 4 · unav 2 | 9 |
| `m10_stability` | 14 | **simu 12** · deri 1 · spec 1 | 4 |
| `m7_codebase` | 22 | meas 18 · deri 1 · spec 3 | 7 |
| `m8_egress` | 27 | meas 18 · deri 5 · spec 2 · unav 2 | 5 |
| `m9_provider_contract` | 14 | meas 12 · spec 2 | 4 |
| `m11_live_screening` | 4 | **unav 4** | 0 |
| `m6_latency` | 59 | meas 51 · deri 1 · spec 3 · unav 4 | 2 |

Read that table as a map of what the project can and cannot claim. The three
modules dominated by `simulated` (`m4`, `m5`, `m10`) are the ones carrying the
headline ranking results — **all 37 simulated quantities live there.** The
modules dominated by `measured` (`m1`, `m7`, `m8`, `m9`, `m6`) are properties of
the artefact itself. `m11` is entirely unavailable, which is the honest record of
a module that was written to close a gap and could not run.

---

## 3. How each module obtains its numbers

### `m1_integrity` — measured
Calls `proctoring.rules.compute_integrity_score` directly over constructed event
sets. No model, no network, no randomness, so results are exact and reproducible
to the integer on any machine. Includes the 39-sequence monotonicity property
(adding an event must never raise the score), a 1000-call determinism check, and
an order-independence check (same event set, shuffled).

### `m2_guardrails` — measured + derived
Calls `services.guardrail_service.strip_injections`, `sanitize_resume` and
`check_answer(use_model=False)` over a labelled corpus: 31 attacks, 18 benign
inputs of which 5 are hard negatives. Tier 1 is pure regex, so exact. Rates
(resistance, false-positive, precision/recall/F1) are `derived` from those
counts. Tier 2 accuracy is `unavailable` — it needs a labelled off-topic corpus
and a model run.

### `m3_ats` — measured + specification
Calls `core.resume.ats_score` against the real role catalogue. Constants
(`MIN_RESUME_WORDS`, `ats_reject_below`, `MAX_UPLOAD_BYTES`, supported suffixes)
are `specification` — read from source, not observed.

### `m4_baselines` — **simulated**
Runs four resume-only scorers over the 100-candidate synthetic cohort
(seed `20260923`). Three are hand-implemented in `metrics/m4_baselines.py`:

- **TF-IDF** — log tf, smoothed idf `log(1 + N/df)`, L2-normalised cosine
  ("ltc" weighting)
- **BM25** — Okapi, `k₁ = 1.5`, `b = 0.75`, same smoothed idf
- **ATS** — literal requirement coverage, the shipped pre-filter

Smoothed idf is a deliberate deviation from textbook BM25: the raw form goes
negative for terms appearing in more than half the documents, which in a
single-role cohort is most of the vocabulary.

**These are not library calls.** `scikit-learn`, `rank_bm25`,
`sentence_transformers` and `scipy` are all absent from the environment. Verify:

```bash
.venv/Scripts/python -c "import importlib.util as u; print([n for n in ('sklearn','rank_bm25','sentence_transformers','scipy','seaborn') if u.find_spec(n) is None])"
```

The paper's methods section must state that these are own implementations with
the parameters above. A reader will otherwise assume library defaults.

The fourth scorer is named `resume_llm_simulated` and is exactly that — it is
**not** a real zero-shot LLM baseline. `sentence_bert_baseline` is `unavailable`
(not implemented, dependency absent).

### `m5_fusion_audit` — **simulated**
The correction audit. 100 candidates with concealed competence vectors, 26 of
them designed traps (over-sellers whose resumes overstate competence, hidden gems
whose resumes understate it). Compares resume-only ranking against
trust-weighted fusion (`core.ranking`) using the concealed vector as ground
truth.

The six `measured` quantities here are the ranking function's own properties
(determinism, tiebreak stability, the trust→0 collapse). The 16 `simulated` ones
are the ranking-quality results.

### `m10_stability` — **simulated**
Re-draws the cohort under **200 seeds** and reports the across-draw distribution.
This module exists to stop single-draw numbers from being published as point
estimates, and it names four quantities that fail that test:
`precision_resume_only`, `precision_fused`, `mrr_resume_only`, `mrr_fused`.

It also reports `lambda_argmax_is_stable_across_draws = False`, which is why no
λ value may be recommended.

### `m7_codebase` — measured
Static inspection: file and line counts, `def test_*` definitions, AST expansion
of `@pytest.mark.parametrize` argument lists, and a schema walk for tables,
columns, foreign keys, cascade constraints, PII columns and biometric-media
columns.

Two test counts are emitted and **must be quoted together or not at all**:
`test_functions = 358` (definitions) and `test_cases = 371` (what pytest
collects). The 13-case difference is parametrize expansion across four files,
verified equal to `pytest --collect-only` on 2026-09-27.

### `m8_egress` — measured
Replaces the `requests` session the provider modules use with an interceptor,
then drives a complete candidate journey three times, once per provider,
capturing every request body byte-for-byte before it is sent or discarded.

This is a transport-level measurement, not a reading of the code. It therefore
includes prompt scaffolding, JSON schemas, rubric text and retry traffic that a
design-document analysis would miss.

`encryption_at_rest` and `automatic_retention_policy` are `unavailable` here —
not implemented, and not scheduled, respectively.

### `m9_provider_contract` — measured
Verifies the Gemini JSON-Schema allow-list against the live API three ways: the
shipped sanitised schema is accepted, the pre-fix schema is rejected, the
unsanitised schema is rejected. A measured three-way discrimination, which is
what makes the sanitiser demonstrably load-bearing rather than merely present.

Also records `model_call_sites = 5` against
`schema_constrained_call_sites = 2`.

### `m11_live_screening` — **entirely unavailable**
All four quantities. Written specifically to replace `resume_llm_simulated` with
a real zero-shot LLM baseline; blocked by the hosted free-tier quota (§5.3 of
the revision guide). The module is committed in its non-running state on purpose
— it documents the gap and will produce the numbers when the quota allows.

To attempt it:

```bash
METRICS_LIVE_PROVIDER=gemini .venv/Scripts/python -m metrics.run_all --only m11_live_screening --no-figures
```

Do not pipe that through `tail` — the failure mode is in the early output.

### `m6_latency` — measured, with the model stubbed
40 repeats per stage, `LLM_PROVIDER=fake`, so **no network time and no model
inference time is included**. These figures bound the portal's own overhead.
They are not model latencies and must never be presented as end-to-end latency.

Four quantities are `unavailable` here and all four need a local model run:
`model_inference_latency`, `tokens_per_second`, `peak_ram_and_vram`,
`whisper_transcription_latency`.

---

## 4. Isolation

The harness never touches development or production state. Every run creates a
throwaway root under the OS temp directory and overrides the environment for the
duration:

```
DATABASE_PATH  → <temp>/portal-metrics-<random>/metrics.db
MEDIA_DIR      → <temp>/portal-metrics-<random>/media
UPLOAD_DIR     → <temp>/portal-metrics-<random>/uploads
LLM_PROVIDER   → fake
STT_PROVIDER   → disabled
TTS_PROVIDER   → disabled
PROCTORING_ENABLED → false
```

Recorded in `results.json` under `environment.environment_overrides` and
`environment.throwaway_root`, so a reader can confirm the measurements did not
run against `data/app.db`.

The two modules that need a live provider (`m9`, `m11`) override
`LLM_PROVIDER` explicitly and say so in their own output. No credential value is
ever written to any results file — only the provider *name*.

---

## 5. Cross-checks that caught real errors

These are recorded because the harness was wrong twice and both times the
paper material was right. A measurement that disagrees with a document is not
automatically the trustworthy one.

**`test_functions` 358 vs pytest's 371.** Both correct, measuring different
things. Localised to 13 parametrize expansions across
`test_app_routing.py` (+2), `test_audio_proctoring.py` (+3), `test_auth.py`
(+3), `test_screening.py` (+5). Fixed by emitting both under unambiguous names
with an AST expansion that now returns exactly 371.

**`resume_sanitisation_discloses_flags = False`.** This would have entered the
paper as a data-handling defect that does not exist. The predicate asked
`all(v.flags for v in sanitised)` over all 31 attacks, so the 3 tier-1 evasions
— where nothing is stripped and therefore correctly nothing is flagged — dragged
it to `False`. Direct probe:

```
attacks               31
text modified         28   → 28 carry flags   (100%)
text unchanged         3   →  0 carry flags   (correct)
```

Disclosure holds perfectly. Fixed by scoping the predicate to rewritten resumes
and adding a converse guard (`flags_only_when_it_rewrites`) so the property
cannot pass vacuously by flagging everything.

**Four figures rendered cleanly while carrying visible defects** — text running
out through box sides, a label struck through by its own border, an arrow routed
across a box it has no edge to, and an arrow drawn in the colour the legend
reserves for "leaves the host" on a call that does not leave the host.
`metrics/figures.py::_assert_within` now measures rendered glyph extents and
raises rather than overflowing. Rendering without error is not the same as being
correct.

---

## 6. Quarantine: `generate_metrics.py`

**Nothing this script produced may be used.** It sits at the repository root and
is now guarded — running it raises `SystemExit` before any import completes.

```python
item['human_score']         = item['ai_score_normalized'] + random.uniform(-1.0, 1.0)
item['processing_time_sec'] = base_parsing_time + llm_processing_time + random.uniform(0.1, 0.5)
item['parsing_failed']      = True if random.random() < 0.02 else False
item['prompt_delta']        = 0 if random.random() > 0.1 else 0.5
item['consistency_std']     = 0.0   # asserted, never observed
```

It draws numbers from `random` and reports them as measurements. From the first
line it reported `human_ai_correlation = 0.9897270711602314` — the correlation
between a number and itself plus noise, in a project where **no human rater has
ever scored a candidate**.

The contamination is wider than that one figure. Of the 13 PNGs it wrote to
`results/metrics/`, **7 are fabricated**:

| fabricated | cause |
|---|---|
| `B_Skill_Match_Ratio.png` | `± random.uniform(-0.1, 0.1)` |
| `C_Processing_Time.png` | invented base `+ random.uniform(0.1, 0.5)` — nothing was timed |
| `E_Consistency_Score.png` | `[base, base, base]`, `std = 0.0` hardcoded; model never run 3× |
| `F_Prompt_Sensitivity.png` | `random.random() > 0.1`; no second prompt was ever sent |
| `I_Human_vs_AI.png` | `± random.uniform(-1.0, 1.0)`; no human rater exists |
| `J_End_To_End_Time.png` | fabricated time `+ random.uniform(2.0, 5.5)` |
| `K_Pipeline_Failures.png` | `random.random() < 0.02`; no failure was observed |

The remaining 6 (`A`, `D`, `G`, `H`, `L`, `M`) derive from real
`data/results/*.json` output but carry no provenance, seed, or cohort record and
are superseded by the harness.

And 6 of the 14 fields in `system_metrics_summary.json` are fabricated:
`avg_processing_time_sec`, `avg_total_pipeline_time`, `avg_consistency_std`
(asserted, not measured), `human_ai_mae`, `human_ai_correlation`,
`error_rate_overall`. `detailed_candidate_metrics.csv` mixes real and fabricated
columns in one table with no marking — it is not usable as a data appendix.

`E_Consistency_Score.png` is worth singling out because it is the most credible-
looking of the seven. Determinism at `temperature=0` is a *plausible* claim, and
the source even reasons about why. But the figure does not test it: it copies one
score three times, assigns a standard deviation of zero, and plots the error
bars. The shipped harness tests determinism properly — 1000 repeat calls in
`m1_integrity`, every offline stage in `m6_latency`, both `True`. Cite those.

Full per-file verdict, kept next to the files themselves so it is found by
anyone globbing that directory: `results/metrics/DO-NOT-USE.md`.

`human_ai_correlation` is declared `unavailable` at the top level of
`results.json`, and its `needs` field states the real requirement: 3–5
independent raters, Fleiss' κ for inter-rater agreement, Pearson/Spearman against
the model. A self-agreement figure, a model-vs-model figure, or a
single-author-as-rater figure is **not** a substitute.

---

## 7. Reproducibility hazards found while measuring

Recorded here because a hosted-provider measurement without these caveats is not
reproducible even in principle.

**The pinned hosted model became uncallable mid-study.** Default shipped as
`gemini-2.5-flash`; within three days it returned HTTP 404. The provider's own
`/v1beta/models` discovery endpoint kept advertising it after it stopped serving.
A model name can be documented, discoverable, and dead at the same time.
→ Every hosted measurement must carry the exact model string **and the date**.

**Reasoning-token overhead is nondeterministic and invisible.**
`reasoning_tokens_per_call = 603` is **one observation**. A separate probe on the
same model measured 125 thought tokens for an 8-token answer; an earlier probe
measured 0. These tokens bill against the output budget and are not returned in
the response body. Any cost projection built on 603 is unsound.

**The free tier will not sustain a validation cohort.** A 40-call run could not
complete in one day. This is why `m11_live_screening` is unavailable — a
methodological constraint worth stating, not an omission to hide.

**The providers do not report the same telemetry.**
`gemini_reports_generation_time = False`; only Ollama returns
`prompt_eval_duration` / `eval_duration`. Like-for-like latency comparison must
use client wall-clock for both, which is what `m6_latency` does.

---

## 8. Rules for using these numbers in the paper

1. If it is not in `results.json`, it does not go in the paper.
2. Every `simulated` quantity is labelled `simulated` in the table **and** in
   the caption.
3. `unavailable` quantities appear in Limitations with their `needs` text. They
   never appear with a number.
4. `precision@10` and `MRR` are omitted or given with across-seed intervals —
   `m10_stability` names them as unstable from one draw.
5. No recommended value of λ, anywhere.
6. `false_correction_rate = 0.62` appears in the same paragraph as
   `recovery_rate = 1.00`. Never one without the other.
7. The guardrail false-positive rate (0.40 on hard negatives) appears with the
   same prominence as the resistance rate (0.9032).
8. `injection_resistance_rate` is always scoped to the five implemented pattern
   families and the authored corpus. It never supports "secure against prompt
   injection".
9. `test_functions` and `test_cases` are quoted together or neither.
10. Every measurement states which provider and model produced it.
11. Latency figures state that the model was stubbed.

---

## 9. File map

| path | contents |
|---|---|
| `metrics/__init__.py` | `Section`, `Measurement`, the five labels |
| `metrics/run_all.py` | orchestration, isolation, report writers |
| `metrics/corpora.py` | attack/benign corpora, synthetic cohort generator |
| `metrics/stats.py` | confusion matrix, NDCG, Kendall τ-b, bootstrap |
| `metrics/figures.py` | IEEE-width figure generation + fit assertions |
| `metrics/m1…m11_*.py` | the 11 measurement modules |
| `research paper/results/results.json` | every quantity, with label and source |
| `research paper/results/provenance.csv` | 243 rows, flat, one per quantity |
| `research paper/results/tables/` | 54 CSVs, the raw per-case rows |
| `research paper/results/unavailable.md` | 16 module gaps + 10 declared |
| `research paper/figures/` | 17 PDF+PNG pairs |
| `research paper/algorithms/` | 4 LaTeX algorithm floats |
| `generate_metrics.py` | **quarantined — see §6** |
| `results/metrics/` | **its output — see `DO-NOT-USE.md` there** |
