# CHANGE LOG

Columns: review item | what changed | where.

Every row was verified against the source or by a run of the current system; see
`01-CHECKPOINT-AND-PHASE0-VERIFICATION.md` for the commands.

---

## PHASE 0 — verification (not a review item; recorded because edits depend on it)

| # | Finding | Where |
|---|---|---|
| 0a | `ats_score(text, [])` returns **0**, not 100. The manuscript said 100 and drew the conclusion that an empty requirement set "cannot cause a rejection". With τ_ats = 40 it can, and does, reject every applicant for that role. | III-B, equation (1) |
| 0b | The `PostgreSQL`/`SQL` case is a **mislabel, not a matcher failure**. A direct call returns 0, so the matcher behaved as specified. The manuscript described this case backwards. | V-E |
| 0c | 0.62 is a **control displacement rate** at a 5-position tolerance; the harness's own annotation calls it an upper bound and supplies a decomposition in which the controls' internal tau-b **rises**. | V-B, VI-A |
| 0d | The 57 intercepted bodies **reconcile exactly** (0+14+14+14+14+1). The manuscript said the count could not be reconciled. | V-G |
| 0e | Only **4 of 5** prompt templates are loaded by the shipped pipeline; the fifth is reachable only from untracked legacy code. | III-A |
| 0f | Cohort fixes integrity at 100; the collapse property is algebraic, with a separate measured check. | IV-A, V-C |
| 0g | s_min = **15**, previously never stated. | III-C |
| 0h | 25 per case / 100 total confirmed; "26 traps" is the boundary corpus, a different artefact from the cohort and from the 26 rubric values. | IV-A, IV-E |
| 0i | Shipped default provider is **hosted** (`gemini`, model string `gemini-3.8-flash`); no local-model inference was run at any point. | III-H, IV-I, V-G |
| 0j | Across-seed intervals, exact sign tests, and per-case bootstrap CIs all **already exist**; no re-run needed. | V-B |

---

## PHASE 1 — critical fixes (items 1–9)

| Item | What changed | Where |
|---|---|---|
| 1 | Added the explicit circularity statement (cohort inflation and the unbiased-noisy interview are assumptions of the generator; the results test the fusion function, not the interviewer) in all three required places. Deleted the abstract's closing sentence "these findings support treating interviews as independent measurements" and replaced it with a statement about what the fusion does under the stated assumptions. | Abstract; I (new paragraph 3); IV-A; VI-B |
| 2 | Every use of "independent" applied to the interview replaced by "an additional evidence source". Added to III-D the two structural facts: the question plan is conditioned on the resume and on the requirements the pre-filter did not match, and the same provider/model family screens and interviews. Carried both into Threats to Validity. | I; III-D; VI-B |
| 3 | One name and one definition for the metric everywhere: **control displacement rate — the share of honest controls displaced by more than five positions**. Deleted "false correction rate" and "62% of the rank changes it made were not corrections". Added the Phase 0c decomposition: 16 honest-strong up, 15 honest-weak down, control-internal tau-b 0.7861→0.8596, 1148/1225 pairs preserved, 0.0629 inverted. Deleted "high-recall but imprecise" — the decomposition contradicts it — and replaced the phrase with an explicit statement that the characterization is not supported and is not made. | Abstract; I; V-B (new Table VI); VI-A; VIII |
| 4 | Added "IV. EVALUATION METHODOLOGY", nine subsections: the generator (bands, inflation +0.55, deflation −0.45, noise 0.08 both views, seed, integrity fixed at 100, scores scaled to 0–25); the simulated model score baseline; TF-IDF and BM25 definitions with k1 = 1.5, b = 0.75, smoothed idf log(1+N/df), log-tf, L2 norm, tokenizer; the relevant set as the top quartile by competence (25); NDCG@10, Kendall's tau-b, recovery rate, mean rank shift, control displacement tolerance; the guardrail and boundary corpora with who wrote them and the hard negatives; the egress interception method; the latency method with warm-up, repeats, p50/p95; the five provenance labels with counts. | IV-A … IV-H |
| 5 | Reordered to the required structure and rewrote the Introduction roadmap to match exactly. Removed the stray III-H pointer and the old self-references. Merged Future Scope into VII with no duplication in the Conclusion. | I roadmap; II–VIII |
| 6 | Stated in Abstract, Introduction, III-H, IV-I, V-G, VI-B, VII and VIII that the pipeline supports a local provider **by configuration**, that the shipped default is hosted, and that no local-model inference was run. Replaced "reduced the bytes leaving the host from 41798 to zero" with wording that says the same payload is directed to a loopback address and that zero is a consequence of that routing. | Abstract; I; III-H; IV-F, IV-I; V-G; VI-B; VIII |
| 7 | Added probe details beside the 12-of-14 and 2-of-14 figures (one journey, one generated 546-byte resume, same bytes under all configurations, stubbed responses). Added the reconciliation of 57. Added the tier-2 count with the statement that it is a stub artefact rather than a measured rate. Deleted the sentence admitting the count was unreconciled. | V-G; VI-B |
| 8 | Added four short subsections to II: E automated/video interviewing, F LLM-as-judge and human agreement, G rank fusion and IR evaluation metrics, H proctoring and integrity monitoring. Each states plainly that no supplied source exists rather than making claims from memory. The research gap is now stated strictly as "in the sources reviewed here". | II-E … II-H; II-I |
| 9 | Cited a supplied source where one exists (PyMuPDF, Streamlit, Whisper; Gale–Shapley via the Pudasaini paper) and inserted `[CITE-NEEDED: …]` for NDCG, Kendall tau-b, BM25, TF-IDF, faster-whisper, YuNet, SFace, MediaPipe, YOLO11n/ultralytics, scrypt, and the interview-assessment, LLM-as-judge and proctoring topics. All are listed in `04-OPEN-ITEMS.md`. | II-G; III-H; Open Items |

## PHASE 2 — correctness and evidence (items 10–17)

| Item | What changed | Where |
|---|---|---|
| 10 | Section III-B now matches the code: equation (1) returns **0** for an empty requirement set, with the rejection consequence stated. | III-B |
| 11 | Section V-E rewritten to describe the real failures: one genuine matcher defect (C inside C++) and one **mislabel** (PostgreSQL/SQL), with the label reported as a label problem rather than a matcher failure. Accuracy is still 0.9231 as labelled, with the disagreement disclosed and the 22-of-23 corrected-label figure given. | V-E |
| 12 | Accuracy reported separately for the 23 decidable-lexical cases (0.9130) and for the three semantic probes, with the statement that the authors labelled the semantic probes "no match" for a literal matcher so they do not test the matcher. 0.9231 is no longer presented as general matcher accuracy. | IV-E; V-E |
| 13 | Scope retained and tightened: "a detection rate of 0.9032 on this self-authored corpus". "Resistance", "resistance rate" and "security" removed from every mention. Stated that the authors wrote the corpus. False positives reported with equal prominence (0.2222 overall, 0.40 on hard negatives). | IV-E; V-D; VI-B |
| 14 | Added the integrity-sensitivity table (new Table VIII) and stated that the headline audit fixes integrity at 100, that the collapse-to-resume property is algebraic from (4)–(5), and that the measured check is a check of the implementation. | V-C; VI-B |
| 15 | Added recovery rate with bootstrap interval and exact sign test (new Table V), tau-b and NDCG with across-seed intervals (new Table VII), and sign tests for rank movement per case. The Abstract now quotes the across-seed figures alongside the single draw. Precision@10 and MRR are omitted as point estimates with the reason given. | Abstract; V-A; V-B |
| 16 | Stated that the audit used one role (the five-requirement data-engineer role) and that "recovery" counts any movement in the direction implied by the planted error, not crossing a shortlist cutoff. The cutoff-crossing rate is **not** in any supplied file, so it is not reported and appears in Open Items. | IV-C; IV-D; V-A; V-B |
| 17 | Prompt template count corrected to four loaded. Figs. 3 and 4 now cited where the screening and interview flows are described. s_min = 15 supplied. | III-A; III intro, III-D; III-C |

## PHASE 3 — formatting and mechanics (items 18–25)

| Item | What changed | Where |
|---|---|---|
| 18 | All five equations re-keyed in LaTeX and numbered (1)–(5): S_ats, d, c(E), v, S_final. Each is referenced in the text as "(1)", "(2) and (3)", "(4) and (5)". No encoding-damaged glyphs remain (`𝑓ఏ`, `𝑖ଵ…𝑖௞`, stray Devanagari subscripts all gone — verified by scan). Display-math delimiters are the only `$$` in the file and each maps to one `equation` float in the LaTeX build. | III-B, III-E, III-F |
| 19 | Deleted the "Commented [A1]" copyright box and the surrounding template text. The author block is retained as a clearly marked placeholder. The reference list was rebuilt (Phase 5), so the stray duplicate list is gone. | Title block; References |
| 20 | Caption added under every figure (Figs. 1–11) and above every table (Tables I–X). Captions are taken from the manifest in `results.json` and shortened to under 60 words. Every ranking caption and ranking table caption carries "simulated" — verified programmatically (Tables II, V, VI, VII, VIII; Figs. 5, 6, 7). | Throughout |
| 21 | The four design decisions restored as a numbered list. Algorithm 1 rewritten so each numbered line is on its own line with aligned comments, and the `S_ats` comment corrected to "0 if J lists no requirements". | III-A; III-G |
| 22 | Number style made consistent: no commas in four-or-more-digit numbers anywhere (41798, 40870, 39778, 10423, 8361, 18000, 1225, 6492) — verified by scan, zero matches; leading zeros on decimals; em-dashes for ranges. | Throughout |
| 23 | Duplication removed: the Li and Gan figures appear in II only, and the Introduction cites them without numbers. The Introduction results paragraph shortened. Abstract, Introduction and Conclusion overlap trimmed; the Conclusion's future-work content is reduced to one clause pointing to Section VII. | I; II-B, II-C; VIII |
| 24 | Every interpretive sentence moved out of Results into Discussion. Results now state what was measured and found. The BM25-beats-simulated-LLM reading, the upper-bound reading of the displacement rate, the cost of scrypt, and the interpretation of the λ sweep are all in VI-A. | V; VI-A |
| 25 | Added the reproducibility paragraph: commit 11b9495, Python 3.12.10, Windows 11 / AMD64 / Intel Family 6 Model 158, the harness provider override, the live model string `gemini-2.5-flash`, and `[REPOSITORY URL]`. No URL invented. | IV-I |

## PHASE 4 — missing sections (items 26–28)

| Item | What changed | Where |
|---|---|---|
| 26 | Added "VI. DISCUSSION AND THREATS TO VALIDITY": a results-reading subsection, then eleven short threat paragraphs covering construct validity via assigned ground truth, the unbiased-noise assumption, non-independence of interview and resume, synthetic-resume realism, self-authored corpora, small n, single role, no human raters and self-preference, fixed integrity, stub dependence of the egress and latency results, and non-transferability of latency. | VI-A; VI-B |
| 27 | Added "Ethics and Regulatory Considerations", drawn only from `SECURITY-AND-DATA-SAFETY.md` and the `m8_egress` notes: identity matching and biometric data, recorded consent, advisory-only integrity output with a human final decision, the processor relationship and the high-risk employment classification under EU regulation, the encryption-at-rest and unscheduled-retention gaps. **No fairness claim**; fairness is declared unaudited. | VI-C |
| 28 | Added an Acknowledgment disclosing AI assistance (implementation of the harness and figure/table generation, drafting and editing, analysis support) and stating that the authors verified all reported numbers against the released harness, marked `[AUTHORS MUST CONFIRM THIS IS TRUE]`. | Acknowledgment |

## PHASE 5 — citation numbering

| Item | What changed | Where |
|---|---|---|
| 5 | The manuscript had **no reference list at all**: both the `.docx` and the `.pdf` end at the word "REFERENCES" while the body cites [1]–[11]. One list was rebuilt from the supplied bibliography and numbered by first appearance, giving [1]–[14] with no gaps. Verified programmatically: every citation resolves to a reference and every reference is cited at least once. Disputed fields were omitted and marked `[AUTHOR TO CONFIRM]` rather than filled in. The superseded entries with unusable venue strings ("IEEE Trans.", "in IEEE Conf.") were not reused. | References |

---

## Corrections made to facts the original manuscript got right in form but wrong in substance

| Original claim | Corrected to | Basis |
|---|---|---|
| "One observed live call billed 603 reasoning tokens" | Two probes billed 603 in total, against 324 visible output tokens; two observations, not a constant | `m9_provider_contract__live_probes.csv`: 371 + 232 |
| live probes implicitly attributed to the shipped model | stated as `gemini-2.5-flash`, distinct from the shipped default `gemini-3.8-flash` | `core/config.py:76` vs the m9 live probes |
| Table III latency values | refreshed from the current `m6` table with its machine and commit named | `m6_latency__stages.csv`, `results.json.environment` |
| "26 traps" adjacent to cohort counts | separated explicitly from both the 100-candidate cohort and the 26 attainable rubric values | `m3_ats.py`, `m5` tie-break data |
| "The 57 intercepted request bodies ... a full per-configuration reconciliation was not included" | exact reconciliation by configuration | `m8_egress.py:270-276, 403-411, 468-473, 563-564` |

**Note on scope of edits.** No file under `D:\job` was modified other than the new
deliverables in `research paper/`. The project's own `results/` files, source tree and
`D:\job\git\` were read only. The verification venv and script live outside the repository
at `D:\tmp\papervenv`.