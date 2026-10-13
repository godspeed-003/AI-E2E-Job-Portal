# Clarifications for Revision Report (1)

**Re:** `Revision_Report (1).docx` against `AI_Resume_Screening_IEEE_Paper (2).docx`
**From:** the implementation side
**Date:** 2026-10-13
**Authoritative run:** commit `c51b84e`, measured `2026-10-13T12:05:18Z`

Your Table 1 row 1 correctly records the four items settled on 2026-10-11. This
document closes as many of the remaining 21 checklist rows as can be answered
from the system, supplies the eleven missing citations as verified records, and
flags **three things that have changed since your draft** and need a paper edit.

---

## Read this first — three changes since your version

### 1. The provenance counts moved, and in the honest direction

> `AI_Resume_Screening_IEEE_Paper (2).docx` cites **243 quantities, 16 unavailable**.

| | paper (2) | now | |
|---|---:|---:|---|
| total reported quantities | 243 | **241** | ⚠ change |
| measured | 131 | **128** | ⚠ change |
| simulated | 37 | 37 | same |
| derived | 24 | 24 | same |
| specification | 35 | 35 | same |
| **unavailable** | 16 | **17** | ⚠ change |

This is a correction, not a regression. One quantity that was being published as
a measured `False` is now correctly declared unmeasured, and one that was being
computed from an unusable sample was dropped. See item 3 below for the cause.
**Use 241 / 128 / 37 / 24 / 35 / 17.**

### 2. The latency table is from a different run than every other number

Table 7 in your version does not come from the same run as Tables 4–6. Values
on this hardware drift 3–4% between runs — scrypt has measured 118.31, 119.35
and 122.28 ms on three different days — so a mixed-run table invites exactly the
question your row 11 anticipates. §E below gives the complete table from the one
authoritative run so every number in the paper can cite a single commit.

### 3. The provider contract can no longer be stated the way §VII-C states it

Two separate problems, both ours:

- The live run in your version was measured on **`gemini-2.5-flash`** on
  **2026-09-24**. The codebase's default is now **`gemini-3.8-flash`**, so the
  paper reports a contract measured against a model it no longer uses.
- Re-running on `gemini-3.8-flash` today, **twice**, both shipped-schema probes
  returned **HTTP 503 "This model is currently experiencing high demand"** and a
  read timeout. The model is at capacity, so the "accepted" leg cannot be
  measured right now.

Crucially, the two **control** probes did reach the API both times and were
rejected with exactly the right errors:

```
pre-fix schema       → HTTP 400  "response_schema.required[0]: property is not defined"
unsanitised schema   → HTTP 400  "Unknown name \"additionalProperties\""
```

So the sanitiser's necessity is still measured and still holds. What cannot
currently be shown is that the shipped schema is accepted.

**This also exposed a defect in our own harness, now fixed.** It scored a 503 as
`accepted = False`, which made `shipped_schemas_accepted_live` report `False` —
a provider capacity problem presenting as a contract regression, and it would
have gone into the paper as evidence the sanitiser had stopped working. The
harness now separates "the API refused the request" (HTTP 4xx) from "the request
never reached the model" (5xx, timeout, no status), and reports the latter as
`unavailable`. The symmetric case mattered more: a 503 on a *control* probe
would have read as "correctly rejected" and credited the sanitiser with a
refusal that never happened.

**What to write in §VII-C.** Suggested wording:

> The two control probes were rejected as expected on 2026-10-13 against
> `gemini-3.8-flash` (HTTP 400, "property is not defined" and "Unknown name
> additionalProperties"), confirming that both branches of schema sanitisation
> are load-bearing. Acceptance of the shipped schema was last confirmed on
> 2026-09-24 against `gemini-2.5-flash`; two attempts to re-confirm it against
> the current default model returned HTTP 503 (model at capacity), so we report
> it as unmeasured for that model rather than inferring it.

And it belongs in the reproducibility discussion: a hosted provider's capacity
is part of the measurement apparatus. Schema validation happens *before* the
capacity check — which is why a 400 returns in 0.4 s while a 503 takes 2 s — so
the controls remain verifiable when the real calls are not.

---

## A. Facts you asked for (rows 10, 11, 12, 14)

| row | item | value |
|---|---|---|
| 10 | CPU | **Intel Core i3-9100F @ 3.60 GHz**, 4 cores / 4 threads |
| 10 | installed RAM | **8 GB** (7.96 GiB usable; 8 542 040 064 bytes) |
| 10 | OS | Windows 11 Pro, build 10.0.26200 |
| 10 | Python | 3.12.10, CPU-only (`torch 2.14.0+cpu`) |
| 11 | **commit to cite for all results** | **`c51b84e`** |
| 11 | date of the latency run | **2026-10-13** (`2026-10-13T12:05:18Z`) |
| 12 | repository URL | `https://github.com/godspeed-003/AI-E2E-Job-Portal` |
| 14 | live provider-contract run | **2026-10-13** for the controls; the acceptance probe was last measured 2026-09-24 (see item 3) |

On row 11 — **cite `c51b84e`, not `11b9495`.** The `research paper/results/`
tree in the repository was regenerated at `c51b84e`, so that is the commit whose
files a reviewer can download and reproduce. `11b9495` predates the microphone
fix and the harness correction above, and its files are no longer in the tree.

Hardware caveat worth one clause in §VI-A: this is a 4-thread desktop CPU with
8 GB of RAM and no GPU. It bounds the portal's own overhead, which is the claim
being made, but it is not a server and the latency figures should not be read as
a deployment capacity estimate.

---

## B. Row 13 resolved — the four interval widths map as follows

> *"map these widths to precision and MRR, resume-only and fused"*

From `research paper/results/tables/m10_stability__across_seed_intervals.csv`,
200 cohort regenerations:

| quantity | shipped draw | mean | 95% interval | **width** |
|---|---:|---:|---|---:|
| Precision@10, resume-only | 0.3000 | 0.3145 | [0.1000, 0.5000] | **0.4000** |
| Precision@10, fused | 0.5000 | 0.4935 | [0.3000, 0.7000] | **0.4000** |
| MRR, resume-only | 0.5000 | 0.5578 | [0.1429, 1.0000] | **0.8571** |
| MRR, fused | 1.0000 | 0.9087 | [0.3333, 1.0000] | **0.6667** |

For contrast, the quantities the paper *does* report as point estimates:

| | width |
|---|---:|
| τ-b, fused | 0.1007 |
| NDCG@10, fused | 0.0593 |

**That is the whole argument for omitting them**, and it is worth stating as a
ratio rather than a judgement: the precision and MRR intervals are **4× to 14×
wider** than the τ-b and NDCG intervals on the same 0–1 scale. MRR for the
resume-only ranker spans 0.14 to 1.00 across draws — it is not measuring the
ranker.

Suggested sentence for §VI-D:

> Precision@10 and MRR are omitted as point estimates. Across 200 cohort
> regenerations their 95% intervals span 0.400, 0.400, 0.857 and 0.667 for
> resume-only and fused precision and MRR respectively — four to fourteen times
> the width of the τ-b and NDCG@10 intervals on the same scale — so a single
> draw does not identify them. The shipped draw's values (0.30 → 0.50 precision,
> 0.50 → 1.00 MRR) fall inside those intervals but should not be read as
> estimates of the effect.

Note the last clause: all ten quantities have `shipped_inside_ci = True`, so the
shipped draw is not anomalous — it is simply uninformative for these two
metrics.

---

## C. Row 11, second half — the single-run latency table

Replace Table 7 with this. One run, commit `c51b84e`, `2026-10-13T12:05:18Z`,
provider stubbed (`LLM_PROVIDER=fake`), so no network or model time is included.
Milliseconds.

| Stage | n | p50 | p95 |
|---|---:|---:|---:|
| PDF text extraction (9 pages) | 10 | 50.614 | 51.275 |
| Sanitization (hostile input) | 40 | 0.104 | 0.239 |
| Keyword score (5 requirements) | 40 | 0.079 | 0.079 |
| Keyword score (long resume) | 40 | 0.273 | 0.309 |
| Integrity score, 0 events | 40 | 0.001 | 0.001 |
| Integrity score, 10 events | 40 | 0.005 | 0.006 |
| Integrity score, 50 events | 40 | 0.018 | 0.019 |
| Integrity score, 200 events | 40 | 0.065 | 0.066 |
| Rank 100 candidates | 40 | 0.425 | 0.447 |
| Scrypt hash | 8 | 122.277 | 123.732 |
| Scrypt verify | 8 | 122.765 | 124.773 |
| Register candidate | 6 | 123.328 | 123.996 |
| Screen (provider stubbed) | 15 | 0.502 | 0.604 |

Two sentences that make this table an argument rather than a list, if you have
the space:

> The dominant cost in the entire offline pipeline is scrypt password hashing at
> roughly 122 ms, deliberately: a key-derivation function that is cheap for the
> server is cheap for an attacker holding leaked hashes. Everything the paper
> actually contributes — the pre-filter, integrity scoring, fusion and ranking
> 100 candidates — completes in under 0.6 ms combined, so the system's own logic
> is not the bottleneck at any plausible cohort size.

And the scaling observation: integrity scoring is sub-linear in events — 0.005 ms
at 10 events against 0.065 ms at 200, i.e. 13× the cost for 20× the events,
because the deduction is computed per distinct event *kind* rather than per
event.

**On the discrepancy you flagged:** the 118.31 / 119.35 / 122.28 ms scrypt
values are three different runs on the same machine, not an error in either
document. Run-to-run variance on this hardware is 3–4%. Please take all
latency values from one run and name it.

---

## D. Rows 3–9 and 15 — the eleven missing citations

All supplied in **`research paper/REFERENCES-TO-ADD.md`**, in IEEE format and as
BibTeX, ready to paste.

Nine of eleven were **verified against Crossref or the arXiv API today** and are
marked as such. Two could not be verified and are marked clearly — EUR-Lex
returned HTTP 202 with an empty body to three different request forms, so the
GDPR and EU AI Act entries carry the standard citation form with an explicit
*verify against EUR-Lex before submission* note. I have not asserted an official
title I could not read.

Row 3 is partly unmet and I want to be straight about which part. The four
**methodological** references it asks for — NDCG, Kendall's τ-b, BM25, TF-IDF —
are supplied and verified. The **literature survey** it asks for (automated and
video interviewing, LLM interview assessment, remote proctoring, LLM-as-judge
validity and human agreement) is not something I can supply from the repository,
and padding it with plausible-looking titles is the one thing neither of us
should do. That row stays open.

---

## E. Row-by-row status

| row | item | status |
|---|---|---|
| 1 | author block: dept, institution, city, country | **author only** |
| 2 | author block: e-mail ×4 | **author only** |
| 3 | §II literature + metric sources | **metric sources supplied**; survey still open |
| 4 | faster-whisper citation | ✅ supplied, verified |
| 5 | YuNet citation | ✅ supplied, verified |
| 6 | SFace citation | ✅ supplied, verified |
| 7 | MediaPipe citation | ✅ supplied, verified |
| 8 | YOLO11n / ultralytics citation | ✅ supplied (software; no paper exists for v11) |
| 9 | scrypt citation | ✅ supplied, verified |
| 10 | CPU model + RAM | ✅ §A |
| 11 | one commit, latency date, re-read values | ✅ §A and §C — cite `c51b84e` |
| 12 | repository URL | ✅ §A |
| 13 | map the four widths | ✅ §B |
| 14 | live provider-contract date | ✅ §A, **with the caveat in item 3** |
| 15 | GDPR + EU AI Act | ⚠ supplied, **unverified** — see §D |
| 16 | acknowledgments + AI disclosure | **author only** |
| 17 | reference [1] publisher record | **author only** |
| 18 | reference [2] year/pages | **author only** |
| 19 | reference [5] pages + DOI | partial — see below |
| 20 | reference [20] author list, venue, DOI | **author only** |

**On row 19.** I can narrow it but not close it. Crossref returns
`10.1109/cvprw67362.2025.00402` for the Lo et al. CVPRW 2025 entry with pages
**4184–4193**. That is one of the three ranges in the project files and it is
the one with a DOI behind it. The 4223–4232 and 4193–4202 variants appear only
in project documents and in the supplied PDF respectively. My recommendation is
to cite the Crossref record and verify the page range against IEEE Xplore — the
PDF's own pagination can differ from the proceedings pagination, which is the
most likely source of the conflict.

---

## F. On the manuscript

Tables 2, 4, 5 and 6 agree with `results.json` exactly; I checked every value.
Table 7 is the only one from a different run, and §C replaces it.

The condensation to 8–9 pages reads well, and dropping the figures rather than
the limitations was the right trade. Two things to keep exactly as they are:

- **Table 5's "Outcome" column.** Reporting `9/25 within tolerance` for honest
  strong candidates next to `25/25 moved down` for over-sellers is what makes
  the displacement result honest. Do not compress that column.
- **Both Table 4's 0.5572 and Table 6's 0.6087.** They are the same screening
  scores under two tie-breaks, and keeping both with the explanation is more
  informative than picking one. The underlying fact — a 0–25 integer rubric
  cannot separate 100 candidates, so the tie-break does the ranking — deserves
  its sentence in the discussion if it is not already there.

One addition to consider for §VIII, given item 3: the paper's deployability
argument is currently supported by the egress measurement. Item 3 adds a second,
independent support for it — the hosted provider's capacity and model lifecycle
are part of the measurement apparatus, and a local model removes both. The
pinned default model becoming uncallable within three days, and the current one
returning 503 under load, are concrete reproducibility costs of the hosted path.

---

## Reproducing anything above

```bash
python -m metrics.run_all
```

Everything lands in `research paper/results/` with a provenance label per
quantity; `unavailable.md` states what each unmeasured quantity would require.
For the live provider probes specifically:

```bash
METRICS_LIVE_PROVIDER=gemini python -m metrics.run_all --only m9_provider_contract --no-figures
```

Expect the controls to pass and the acceptance probe to be `unavailable` while
`gemini-3.8-flash` is returning 503.
