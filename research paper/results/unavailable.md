# Claims this machine cannot support

Written by `metrics.run_all`. Each entry names the run that would
produce the number. Nothing here has been estimated, and nothing here
should appear in the paper as a result — this is the limitations
section in draft form.

## From the measurement modules

### m1_integrity — Integrity scoring c(E) — exhaustive characterisation

**`detection_accuracy_of_cv_backends`**

- Relevant code: `proctoring/vision.py, proctoring/audio.py`
- Needs: Labelled interview video. The scoring function above is exact; what is unmeasured is whether YuNet/SFace/MediaPipe/YOLO11n raise the right events from a real recording. That needs a recorded corpus with per-frame ground truth and cannot be simulated.

### m2_guardrails — Tier-1 prompt-injection resistance (regex, offline, exact)

**`tier2_model_moderation_accuracy`**

- Relevant code: `services.guardrail_service._model_verdict`
- Needs: A model run over a labelled off-topic corpus. Tier 2 fails open by design (flags ('low_overlap','moderation_unavailable') and accepts), so its accuracy bounds a *usability* claim, not a security one — the security claim rests on tier 1, measured above.

### m3_ats — ATS keyword pre-filter — matching correctness and gate behaviour

**`end_to_end_screening_quality`**

- Relevant code: `services.application_service.screen`
- Needs: A resume corpus with human relevance labels, plus a model run. The stored data/results records are illustrative and were written by hand; measuring precision/recall of screening against them would be measuring the fixture, not the system.

### m4_baselines — Lexical baselines (ATS, TF-IDF, BM25) on self-reported evidence

**`sentence_bert_baseline`**

- Relevant code: `not implemented`
- Needs: sentence-transformers plus a model download (~90 MB for MiniLM). Left out deliberately: it is not a dependency of the portal, and an embedding baseline reads the same self-reported document as the lexical ones, so it would move the numbers without changing the structural finding. Worth adding for the paper's related-work comparison; state it as an addition, not as a result you have.

### m5_fusion_audit — Correction audit — resume-only vs fused ranking against ground truth

**`interview_score_validity`**

- Relevant code: `services.interview_service.score`
- Needs: A model run: N candidates through the full adaptive interview with LLM_PROVIDER=ollama, each transcript independently scored by >=3 human raters, then Fleiss' kappa between raters and Pearson/Spearman between the human mean and the rubric total. Until that exists, the cohort's 'unbiased but noisy' interview model is an assumption, and every recovery rate above is conditional on it.

**`ablation_ladder_A0_to_A5`**

- Relevant code: `not implemented`
- Needs: Six pipeline configurations (A0 resume-only, A1 +static interview, A2 +adaptive, A3 +structured pros/cons, A4 full, A5 +integrity) run over the same candidate set with a real model. A0 and A4-minus-the-model are the two rows this harness can produce offline; the other four need generation.

### m8_egress — Data egress per candidate, by provider (bytes and destination)

**`encryption_at_rest`**

- Relevant code: `not implemented`
- Needs: SQLite is unencrypted and MEDIA_DIR holds plain video files, so the database-file protections rest entirely on filesystem permissions. Options worth naming in the paper: SQLCipher for the database, an OS-level encrypted volume for media, or a retention job that deletes recordings after the hiring decision. State this as a limitation — it is a real gap, not a configuration choice.

**`automatic_retention_policy`**

- Relevant code: `services.proctor_service.purge_snapshots, services.recording_service, services.auth_service.purge_expired_sessions`
- Needs: Three purge functions exist and are correct, but nothing calls them on a schedule: deletion is manual. A defensible retention claim needs a documented period and a job that enforces it. Until then the honest sentence is 'deletion primitives exist; retention is the operator's responsibility'.

### m11_live_screening — Real-model screening — is the screener fooled the way the cohort assumes?

**`real_model_is_fooled_by_resume_inflation`**

- Relevant code: `metrics.m11_live_screening._run_live`
- Needs: whether a real screener scores an inflated resume above an honest one from the same competence band — the assumption every ranking result in this harness is built on. Costs 40 model calls, paced at one start every 6.5s to stay inside a free-tier rate limit (about 4 minutes): `METRICS_LIVE_PROVIDER=gemini .venv/Scripts/python -m metrics.run_all --only m11_live_screening`. Sends generated resume text only — no real candidate data exists in this project's throwaway database. Attempted on 2026-09-26 and not completed: the free-tier daily request allowance on both configured keys was exhausted partway through, and every remaining call returned HTTP 429. A 40-call validation run is therefore not reliably completable in one day on the free hosted tier — which is a measured argument for the local-Ollama path, not merely an inconvenience. Re-run with restored quota, or point LLM_PROVIDER at a local model.

**`real_score_vs_ground_truth_competence`**

- Relevant code: `metrics.m11_live_screening._run_live`
- Needs: the screening stage's rank correlation with a known truth, using a real model instead of the generator's synthetic score. Costs 40 model calls, paced at one start every 6.5s to stay inside a free-tier rate limit (about 4 minutes): `METRICS_LIVE_PROVIDER=gemini .venv/Scripts/python -m metrics.run_all --only m11_live_screening`. Sends generated resume text only — no real candidate data exists in this project's throwaway database. Attempted on 2026-09-26 and not completed: the free-tier daily request allowance on both configured keys was exhausted partway through, and every remaining call returned HTTP 429. A 40-call validation run is therefore not reliably completable in one day on the free hosted tier — which is a measured argument for the local-Ollama path, not merely an inconvenience. Re-run with restored quota, or point LLM_PROVIDER at a local model.

**`simulated_baseline_is_representative_of_a_real_screener`**

- Relevant code: `metrics.m11_live_screening._run_live`
- Needs: whether M4's `resume_llm_simulated` baseline stands in fairly for a real screener, or is only a statement about our generator. Costs 40 model calls, paced at one start every 6.5s to stay inside a free-tier rate limit (about 4 minutes): `METRICS_LIVE_PROVIDER=gemini .venv/Scripts/python -m metrics.run_all --only m11_live_screening`. Sends generated resume text only — no real candidate data exists in this project's throwaway database. Attempted on 2026-09-26 and not completed: the free-tier daily request allowance on both configured keys was exhausted partway through, and every remaining call returned HTTP 429. A 40-call validation run is therefore not reliably completable in one day on the free hosted tier — which is a measured argument for the local-Ollama path, not merely an inconvenience. Re-run with restored quota, or point LLM_PROVIDER at a local model.

**`real_llm_resume_only_tau_b`**

- Relevant code: `metrics.m11_live_screening._run_live`
- Needs: the real-model replacement for the simulated resume-only baseline. Costs 40 model calls, paced at one start every 6.5s to stay inside a free-tier rate limit (about 4 minutes): `METRICS_LIVE_PROVIDER=gemini .venv/Scripts/python -m metrics.run_all --only m11_live_screening`. Sends generated resume text only — no real candidate data exists in this project's throwaway database. Attempted on 2026-09-26 and not completed: the free-tier daily request allowance on both configured keys was exhausted partway through, and every remaining call returned HTTP 429. A 40-call validation run is therefore not reliably completable in one day on the free hosted tier — which is a measured argument for the local-Ollama path, not merely an inconvenience. Re-run with restored quota, or point LLM_PROVIDER at a local model.

### m6_latency — Per-stage latency with the model stubbed (portal overhead only)

**`model_inference_latency`**

- Relevant code: `llm.ollama.OllamaProvider.generate_json`
- Needs: An Ollama run: `ollama pull llama3.1` then screen N applications with LLM_PROVIDER=ollama, recording p50/p95 per call alongside the model tag, quantisation, context length, CPU/GPU and peak RAM/VRAM. Without those five facts beside it a latency number is not a result. Report separately for screening (one long prompt) and interview turns (many short ones) — they have different shapes.

**`tokens_per_second`**

- Relevant code: `llm.ollama`
- Needs: Ollama returns prompt_eval_count, eval_count, prompt_eval_duration and eval_duration. llm/ollama.py already sums the two counts into LLMResult.tokens but drops both durations, which are the denominators. Keeping them would make throughput measurable with no new dependency — a small, worthwhile change, and the paper should not claim tokens/sec until it is made.

**`peak_ram_and_vram`**

- Relevant code: `not instrumented`
- Needs: `ollama ps` during a run for model residency, plus psutil RSS sampling for the Streamlit process and nvidia-smi if a GPU is used. Relevant to the paper's deployability claim: the argument for local inference is that a hiring team can run it on hardware they own, and that argument needs a memory figure.

**`whisper_transcription_latency`**

- Relevant code: `services.recording_service`
- Needs: faster-whisper with a downloaded model over recorded audio of known duration, reported as a real-time factor (audio seconds per wall second) rather than absolute milliseconds.

## Additional, declared in `metrics.run_all.UNAVAILABLE`

**`end_to_end_error_recovery`**

The headline claim 'the pipeline recovers X% of planted resume misrepresentations'. The fusion audit supports the conditional form — given interview evidence with residual error sigma, the fusion recovers X% — and sigma has to be stated. The unconditional form needs real interviews conducted by the real model against a cohort whose true competence is known, which means human-labelled ground truth.

**`human_ai_correlation`**

DO NOT CITE the 0.9897 figure in results/metrics/. generate_metrics.py produced it from `item['human_score'] = item['ai_score_normalized'] + random.uniform(-1.0, 1.0)` — a correlation between a random number and itself. No human rater has scored a candidate in this project. See research paper/METRICS-PROVENANCE.md.

