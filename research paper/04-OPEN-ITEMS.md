# OPEN ITEMS

Every marker left in `02-REVISED-PAPER.md`, with what is needed and the exact command or
source that would close it. Nothing in this list has been guessed or filled in.

---

## A. Citations with no supplied source

The project contains no `related_sources_ai_recruitment.md`, so the only bibliographic
material available was `research paper/reference.txt` (entries [20]–[39]) and the older
draft's list in `_extracted.txt`. Where neither covers a named item, the paper carries a
marker rather than a substitute.

| Marker | What is needed | Exact source required |
|---|---|---|
| `[CITE-NEEDED: automated/video interviewing and LLM interview assessment]` | at least one source on automated or video interviewing and on structured interview generation conditioned on a resume | a supplied reference on interview assessment |
| `[CITE-NEEDED: LLM-as-judge validity and human agreement]` | a source on LLM-as-judge methodology, inter-rater agreement for model-based evaluators, and self-preference bias | a supplied reference on LLM-as-judge validity |
| `[CITE-NEEDED: NDCG]` | Järvelin & Kekäläinen, rank-based evaluation measures for IR retrieval | a supplied source; none present |
| `[CITE-NEEDED: Kendall tau-b]` | Kendall, rank correlation and the tau-b tie correction | a supplied source; none present |
| `[CITE-NEEDED: BM25]` | Robertson & Zaragoza, the probabilistic relevance framework (Okapi BM25) | a supplied source; none present |
| `[CITE-NEEDED: TF-IDF]` | a source for log-tf, smoothed-idf, cosine-normalized weighting | a supplied source; none present |
| `[CITE-NEEDED: proctoring and integrity monitoring]` | a source on remote proctoring and on advisory integrity signals | a supplied source; none present |
| `[CITE-NEEDED: scrypt]` | Percival & Josefsson, scrypt | a supplied source; none present |
| `[CITE-NEEDED: faster-whisper]` | the faster-whisper project itself, cited as distinct from Whisper [14] | a supplied source; none present |
| `[CITE-NEEDED: YuNet]`, `[CITE-NEEDED: SFace]`, `[CITE-NEEDED: MediaPipe]` | one source each for the OpenCV face detector, the OpenCV face recogniser, and MediaPipe head pose | a supplied source; none present |
| `[CITE-NEEDED: YOLO11n/ultralytics]` | a source for YOLO11n and for ultralytics (AGPL-3.0) | a supplied source; none present |

Gale–Shapley is cited through the Pudasaini chapter [2], which applies the algorithm; no
separate Gale–Shapley source was supplied. Gemini is named in prose but has no supplied
source and therefore carries no marker in the text; if a citation is wanted, it needs a
Google API reference added to the supplied set.

## B. Bibliographic fields the supplied sources disagree on, or omit

Per the instruction to omit a disputed field and mark it rather than resolve it, these are
in the reference list with an inline marker and must be checked against the publisher record.

| Reference | What is disputed or missing | How to close |
|---|---|---|
| [2] Pudasaini et al. | author initial, year, and page range. `reference.txt` gives "S. Pudasaini", pp. 705-713, 2022; the older draft in `_extracted.txt` gives "P. Pudasaini", a different title, no pages, and 2022; a third variant supplies different pages. | read the Springer record for DOI `10.1007/978-981-16-2126-0_55` |
| [5] Lo et al. | page range appears in `reference.txt` only and is contradicted elsewhere | read the CVPRW 2025 proceedings record for DOI `10.1109/cvprw67362.2025.00402` |
| [9] Human and LLM-Based Resume Matching | no authors in the supplied source | read the Findings of NAACL 2025 record |
| [10] Evaluating Bias in LLMs for Job-Resume Matching | no authors in the supplied source | read the NAACL Industry Track 2025 record |
| [11] FAIRE | no authors and no venue in the supplied source | locate the 2025 record |
| [14] Radford et al. (Whisper) | no arXiv identifier or DOI in the supplied source | add the identifier from the arXiv record |

## C. Repository and authorship

| Marker | What is needed |
|---|---|
| `[REPOSITORY URL]` (Section IV-I) | the public URL of the released harness. Not invented. Insert before submission. |
| Author block (title page) | three authors with affiliations and emails. Currently a marked placeholder. |
| `[AUTHORS MUST CONFIRM THIS IS TRUE]` (Acknowledgment) | the authors must confirm they verified every reported number against the released harness, which is what the Acknowledgment asserts. |

## D. Measurements that would strengthen the paper but are not present

These are **not** in the paper, because no run or supplied file produced them. Each is
stated in the prose as unmeasured rather than estimated.

| Quantity | Exact run or requirement |
|---|---|
| Interview-score validity / agreement with human raters | `python -m metrics.run_all --only m11_live_screening` with ≥3 independent human raters scoring the same recorded interviews; report Fleiss' κ and Spearman's ρ. The harness already emits this as `unavailable` with the same instruction. |
| Real zero-shot LLM resume-only baseline (replacing the simulated one) | 40 live model calls, paced at one start every 6.5 s to stay inside free-tier limits; the free tier did not sustain this run, which is why all four m11 quantities are unavailable |
| Ablation ladder A0–A5 | six pipeline configurations over one candidate set with a real model; with the stub provider every rung returns the same canned JSON and an ablation run would measure nothing |
| Model inference latency, tokens per second, peak RAM/VRAM | `ollama pull llama3.1`, then screen N applications with `LLM_PROVIDER=ollama`, recording p50/p95 per call together with model tag, quantization, context length, CPU/GPU and peak memory. `llm/ollama.py` already sums the token counts and currently discards the durations that would be the denominators. |
| Whisper transcription real-time factor | faster-whisper with a downloaded model over recorded audio of known duration |
| CV and audio detection accuracy | a labelled interview-video corpus with per-frame ground truth; the scoring function is exact, the detectors upstream of it are not measured |
| Rate at which real candidates' answers cross the network | the 12-of-14 figure is one probe journey with a 546-byte generated resume and stubbed replies; a rate over real candidates needs real documents of varying length |
| Shortlist cutoff-crossing rate for recovered candidates | not computed by any supplied module. `m5_fusion_audit.py` defines the relevant set as the top quartile by competence for the ranking metrics but does not report a cutoff-crossing rate for the recovered cases. Would need a small addition to that module, or a stated cutoff from the deployment. |
| Boundary accuracy under a corrected corpus label | would require editing the hand label in `metrics/m3_ats.py:73` and re-running. The 22-of-23 figure in Section V-E is derived arithmetically from the reported confusion matrix, not re-measured. |

## E. Known model-version hazard, stated not resolved

The shipped default model string is `gemini-3.8-flash` (`core/config.py:76`). The two live
provider-contract probes reported in Section V-H were issued against `gemini-2.5-flash`,
and the measurement run is dated 2026-09-27. A hosted-provider result without a model string
and a date is not reproducible, so both are given in the paper. **Action:** re-run
`python -m metrics.run_all --only m9_provider_contract` against whatever model string is
current at submission and update the string and the date in Section V-H and IV-I, or state
explicitly that the shipped default differs from the probed model, which is what the paper
now does.