# AI-Based Resume Screening and AI-Interview-Assisted Evaluation Framework with Proctoring System

**[AUTHOR BLOCK — PLACEHOLDER. Replace before submission.]**

Author Name, Affiliation, author@email.com
Author Name, Affiliation, author@email.com
Author Name, Affiliation, author@email.com

---

## Abstract

Automated resume screening ranks candidates on self-reported documents and offers no mechanism for revising that judgment when later evidence contradicts it. This paper investigates whether an adaptive artificial intelligence (AI) interview stage can correct the ranking errors introduced by resume-only screening, and what data exposure it entails. We present a locally deployable pipeline integrating a deterministic applicant tracking system (ATS) keyword pre-filter, large language model (LLM) resume scoring, an adaptive interview, proctoring-based integrity scoring, and trust-weighted score fusion. A controlled audit is performed on a 100-candidate synthetic cohort with concealed competence values and planted over-sellers, hidden gems, and two honest control groups, while transport-level interception quantifies data egress. Across four resume-only rankers, over-sellers occupied six or seven of the top ten. Fusing the interview stage raised normalized discounted cumulative gain at rank ten (NDCG@10) from 0.61 to 0.94 and Kendall's tau-b from 0.17 to 0.55 on the shipped draw; across 200 cohort regenerations the mean tau-b gain was 0.38 (95% interval [0.33, 0.44]) and the mean NDCG@10 gain was 0.29 ([0.13, 0.45]), positive in every regeneration. The control displacement rate was 0.62, an upper bound rather than an error rate, since 93.71% of honest control pairs preserved their relative order and the controls' internal agreement with concealed competence rose from 0.79 to 0.86. Candidate answers appeared in 12 of 14 model calls against two for resume text. All ranking results are simulated and test the fusion function, not the interviewer; interviewer validity, fairness, and encryption at rest are unmeasured.

**Index Terms**—Automated hiring, resume screening, large language models, adaptive interviewing, algorithmic auditing.

---

## I. INTRODUCTION

Large employers rely on automated screening to reduce a large applicant pool to a shortlist that human reviewers can feasibly read. Applicant tracking systems (ATS) have traditionally performed this reduction by matching keywords or applying predefined criteria to resume text [1]. Subsequent systems adopted semantic representations, first through word embeddings combined with stable matching [2] and later through context-aware transformer models for resume classification and resume-job matching [3]. More recently, large language model (LLM) agents have been used to summarize and grade resumes at scale [4], and multi-agent designs have added explanations to the resulting scores [5]. Because hiring decisions affect people's livelihoods, the correctness, robustness, and data handling of these systems matter well beyond engineering convenience.

Despite this progress, resume screening and interview assessment are still usually treated as separate filters. A resume-based score is computed once and gates later stages, yet nothing allows subsequent evidence to revise it. This limitation is consequential because a resume is a self-reported document: one that overstates competence and one that understates it will each be ranked incorrectly, and a screener that reads only that document cannot detect either error. Moreover, evaluations of screening systems typically report accuracy or efficiency for a single component. Li *et al.* report 73.3% competence-level accuracy and 79.2% resume-job matching accuracy, with an inter-annotator kappa of 61% [3]. Gan *et al.* report screening roughly elevenfold faster than manual review and an F1 score of 87.7% on resume-sentence classification [4]. Neither study examines whether a later stage corrects errors made by an earlier one. Fairness and robustness research likewise analyzes screening in isolation. Fabris *et al.* survey bias across the algorithmic hiring pipeline and characterize whether algorithmic hiring is less biased than human-driven alternatives as an open question [6]. Baxi *et al.* show that prompt-injection text embedded in a resume can improve an applicant's LLM-assigned ranking under some conditions, and that this advantage weakens as the tactic becomes widespread [7]. That finding concerns screening alone, not a pipeline in which a subsequent interview could counteract an inflated score.

This paper addresses that gap. We investigate whether an adaptive AI interview stage can correct ranking errors introduced by resume-only screening, and what data exposure such a pipeline entails. The contributions are as follows.

1. We implement a locally deployable hiring pipeline comprising a deterministic keyword pre-filter, LLM resume scoring, an adaptive interview, proctoring-based integrity scoring, and trust-weighted score fusion. The fusion step allows an untrusted interview to stop benefiting a candidate without penalizing that candidate.
2. We design a controlled audit on a synthetic cohort with concealed competence values. The cohort includes planted over-sellers, hidden gems, and two honest control groups, so that genuine correction can be distinguished from rank noise.
3. We compare four resume-only rankers on the same cohort and report how many over-sellers each admits to the shortlist.
4. We quantify data egress by intercepting the transport layer, comparing a hosted model provider with a local one.
5. We release a measurement harness that labels every reported quantity by provenance and states explicitly which quantities could not be measured.

In the audit, over-sellers occupied six or seven of the top ten positions under all four resume-only rankers. Fusing the interview stage raised NDCG@10 from 0.61 to 0.94 and Kendall's tau-b from 0.17 to 0.55 on the shipped cohort draw, and every planted hidden gem moved up in rank. Its cost is stated with the same prominence as its benefit: the control displacement rate was 0.62, an upper bound rather than an error rate, because 93.71% of honest control pairs preserved their relative order. At the transport layer, candidate answers appeared in 12 of 14 model calls against two for resume text.

Two properties of the reported ranking results bound what they mean. The cohort's resume inflation and its unbiased-but-noisy interview are assumptions of the generator, not findings; the results therefore test the fusion function against a constructed ground truth, not the validity of the interviewer. And the interview is an additional evidence source rather than a measurement separable from the resume: its question plan is conditioned on the same resume and the same screening gaps, and the same model family may screen and interview. All ranking results are simulated.

The remainder of this paper is organized as follows. Section II reviews related work. Section III describes the proposed system. Section IV presents the evaluation methodology, including the synthetic cohort, the metrics, the corpora, and the provenance labels. Section V reports the results. Section VI discusses threats to validity and gives the ethics and regulatory considerations. Section VII states the limitations and future scope, and Section VIII concludes.

---

## II. RELATED WORK

### A. Keyword and Rule-Based Screening

Early applicant tracking systems treated screening as document scoring. Resumes were parsed into fields and compared with job requirements through largely deterministic logic [1]. This design is fast, and its criteria are explicit and reproducible. Its main weakness is that it cannot recognize that two differently worded descriptions refer to the same competency. The weakness is also structural: a keyword score measures the presence of resume terms rather than substantiated competencies, so a resume that lists every requirement scores well regardless of whether the candidate can support those claims.

### B. Semantic and Embedding-Based Matching

Later work represented resumes and job descriptions in a shared vector space to close the semantic gap. Pudasaini *et al.* combined word embeddings with the Gale-Shapley stable-matching procedure, which recasts screening as a matching problem over the preferences of both sides rather than a one-directional ranking [2]. Vanetik and Kogan address job vacancy ranking by combining sentence embeddings, keywords, and named entities [8]. Li *et al.* used context-aware transformer models to condition a representation on its surrounding resume section and the paired job description. They evaluated on 6492 annotated resumes for a single occupation and reported 73.3% competence-level accuracy and 79.2% resume-job matching accuracy. Their inter-annotator kappa of 61% shows that expert judgment itself does not fully converge [3]. This indicates disagreement, and therefore uncertainty, in the human-labelled target. These methods capture relatedness better than literal matching, but embedding similarity measures how related two texts are, not whether the candidate can do the job. Better similarity therefore does not by itself show better hiring decisions. Like the keyword approach, these methods operate primarily on self-reported resume content.

### C. LLM-Based Screening and Evaluation

The most recent systems replace fixed similarity with generative evaluation. Gan *et al.* describe an LLM-agent framework that summarizes and grades resumes, reporting screening roughly eleven times faster than manual review and an F1 of 87.7% on resume-sentence classification after fine-tuning [4]. Lo *et al.* decompose screening across specialized agents and, in some configurations, ground scores in retrieved role-specific criteria to make outputs explainable [5]. Human and LLM-Based Resume Matching compares human ratings with LLM matching scores [9], which is directly relevant to validating a model against human judgment. These systems produce natural-language justifications alongside scores. That output is richer than a similarity value, but it introduces an additional evaluation requirement, since a fluent natural-language justification is not necessarily a faithful account of the process that produced the score. The reported evaluations concern efficiency, classification accuracy, or agreement for the screening step. In these evaluations, the model's input is primarily the resume.

### D. Fairness, Bias, and Robustness

As these systems have scaled, so has scrutiny of their behavior. Fabris *et al.* survey fairness and bias across the algorithmic hiring pipeline, covering bias sources, mitigation techniques, and legal frameworks. They also state that whether algorithmic hiring is less biased than the human-driven alternatives remains an open question [6]. Empirical studies probe LLM matching directly. One evaluates the effect of gender, race, and education on job-resume matching [10], and FAIRE offers a benchmark-style test of how identity-related changes affect resume evaluation [11]. Robustness has been studied separately. Baxi *et al.* show that self-promotional prompt-injection text embedded in a resume can improve an applicant's LLM-assigned ranking when few applicants use it and quality is otherwise homogeneous. The advantage weakens as the tactic spreads, and in heterogeneous settings it can occasionally invert the ranking of weaker and stronger candidates [7]. This work matters because it shows that resumes can constitute an attack surface, not only an unreliable signal.

### E. Automated and Video Interviewing

The sources reviewed here contain no study of automated or video-based interviewing, of structured question generation conditioned on a resume, or of an interview stage used to revise an earlier document-based ranking. We therefore make no claim about what that literature does or does not establish, and we cite no substitute for it. **[CITE-NEEDED: automated/video interviewing and LLM interview assessment]** The design reported in Section III-D therefore cannot be positioned against prior interview-assessment work; the closest comparisons available to us are the resume-screening studies in Sections II-A to II-C, all of which stop at the document. Establishing where this work sits in the interview-assessment literature requires a source set we were not supplied, and is listed as an open item rather than filled from memory.

### F. LLM-as-Judge and Human Agreement

One supplied source compares human ratings with LLM resume-matching scores [9], which is the only human-agreement evidence available to us. We are not aware of a supplied source on LLM-as-judge methodology generally, on inter-rater agreement statistics for such systems, or on the known self-preference and position biases of model-based evaluators. **[CITE-NEEDED: LLM-as-judge validity and human agreement]** This gap has a direct consequence for this paper. Every quantity that would place our model-based interview score against human judgment is absent from the released harness, which records them as unavailable and names the run that would produce them. We therefore report agreement between two rankings computed from the same constructed cohort, and we report no agreement with human raters at all. Section VI records the absence as a threat rather than working around it.

### G. Rank Fusion and Information-Retrieval Evaluation

Fusion of multiple evidence sources into one ranking, and the metrics used to evaluate rankings, are established separately from hiring. Our evaluation borrows normalized discounted cumulative gain at a cut-off, Kendall's tau-b for rank agreement, and Okapi BM25 as a lexical ranker. None of these originates in a supplied source. **[CITE-NEEDED: NDCG] [CITE-NEEDED: Kendall tau-b] [CITE-NEEDED: BM25] [CITE-NEEDED: TF-IDF]** We therefore describe their definitions in Section IV rather than attributing them, and we note two properties that bear on how our numbers should be read. First, our fusion is not reciprocal-rank fusion and not a weighted sum of normalized scores; it interpolates toward a prior, which is why a trust value of zero collapses the fused score exactly onto the resume score rather than merely approximating it. Second, NDCG@10 depends on a relevant set we define ourselves, so its absolute value is a statement about our constructed cohort and not a transferable quality score.

### H. Proctoring and Integrity Monitoring

No supplied source addresses remote proctoring, webcam-based face detection and identity matching during an interview, or the design of an integrity signal that is advisory rather than disqualifying. **[CITE-NEEDED: proctoring and integrity monitoring]** We can therefore say only what we measured: that our integrity function is deterministic, order-independent, and monotone, and that it enters the ranking as a weight rather than as a penalty. We make no claim that it detects misconduct, and the ethics discussion in Section VI-B rests on that limitation rather than on any comparison with prior proctoring systems.

### I. Critical Synthesis and Research Gap

Across the themes above, capability has moved from literal matching to contextual and generative evaluation, but the evidence supplied is not directly comparable. The studies use different datasets, tasks, and metrics, from competence classification [3] to sentence classification [4] to injection-induced rank change [7]. The reviewed evaluations share three limitations. First, the reviewed studies generally examine one component in isolation: screening accuracy is reported separately from robustness, and neither is examined within a pipeline. Second, the reviewed screening approaches, from keyword scoring to LLM agents, primarily evaluate resume content, and none of the reviewed studies evaluates whether subsequent interview evidence can revise the resulting ranking. Third, data handling is not the focus of these studies; none of the sources reviewed measures transport-level data egress, that is, what candidate data leaves the deploying organization.

In the sources reviewed here, resume screening and interview assessment are evaluated as separate problems, and none tests whether a subsequent interview stage can revise rankings produced by an earlier resume-based stage. The robustness finding in [7] is reported for screening alone, not for a pipeline in which a subsequent interview could revise the resulting ranking. This paper addresses that gap. It treats screening and adaptive interviewing as stages of one auditable ranking process, plants over-sellers, hidden gems, and honest controls in a synthetic cohort with concealed competence values so that rank revisions reflecting planted errors can be distinguished from rank noise, and measures transport-level data egress for hosted and local model providers. Fairness auditing is outside the scope of this study, so the fairness literature [6], [10], [11] is discussed as contextual background rather than as evidence that the proposed system is fair.

---

## III. PROPOSED SYSTEM

This section describes an end-to-end hiring pipeline built so that a later evidence source can revise an earlier ranking. The pipeline has five processing stages: keyword pre-filter (preceded by ingestion and sanitization), model screening, adaptive interview, integrity scoring, and trust-weighted fusion into a ranked report. Every decision is made in a service layer that is independent of the user interface, so the same code drives the web application and the measurement harness. The architecture is shown in Fig. 1, the stage flow in Fig. 2, the screening control flow in Fig. 3, and the interview loop in Fig. 4. Once a resume has been ingested, a candidate can be removed automatically in only one place, the keyword pre-filter. A low model score routes the candidate to human review, not to rejection.

**Fig. 1.** System architecture and the host trust boundary. Everything inside the dashed region runs in a single process on the operator's machine; the provider factory is the one place any of it can reach the network, which is what makes the privacy question answerable at all.

**Fig. 2.** The end-to-end pipeline. Two of the six stages call a language model; the keyword pre-filter, fusion, and ranking are deterministic and run without a network call, so a throttled provider can delay a decision but never lose a submission. The row above each stage counts its model calls.

**Fig. 3.** Screening control flow. Sanitization and keyword scoring are deterministic and precede the only model call in that stage, so a resume carrying injected instructions is stripped before any prompt is built. The model returns five criterion scores rather than a total, and the total is recomputed from them.

**Fig. 4.** The adaptive interview loop. Question selection is bounded before the model is consulted: the turn budget, the wall clock, unasked plan coverage, and a cap of two consecutive follow-ups are all checked first and then re-validated against the model's choice. A guardrail rejection returns the question rather than consuming a turn.

### A. System Architecture and Design Decisions

The system is a Python application with four kinds of module. Pages render, services decide, core modules hold pure scoring code, and a provider layer is the only path by which data can reach a model. State is stored in SQLite (11 tables, write-ahead logging, enforced foreign keys with cascading deletes). Fig. 1 marks a host trust boundary. The application runs as a single process on the operator's machine, and the provider factory is the single point at which a request can leave the host. Whether a given request crosses the boundary depends on the configured provider endpoint.

Four design decisions shape the rest of the section.

1. **Provider abstraction.** Four provider types are supported: a hosted Gemini API, a local Ollama daemon, an OpenAI-compatible endpoint, and an offline fake used for testing. The first three are reached over plain REST, and the fake makes no network request. The provider is selected through configuration, and the same prompts are built in every case.
2. **Deterministic first.** Sanitization, keyword scoring, integrity scoring, fusion, and ranking make no model call. A throttled provider can therefore delay a decision but not lose a submission.
3. **Recomputed totals.** The model supplies five criterion scores and prose. Totals are recomputed from the clamped criteria, so a model-reported total cannot change an outcome.
4. **Prompts in files.** Prompt templates are stored as text files and can be inspected without reading source code. Four of the five templates in the released tree are loaded by the shipped pipeline: the resume-evaluation, question-plan, next-turn, and interview-score templates. A fifth, `evaluation_prompt.txt`, remains in the template directory and is reachable only from a legacy module that nothing in the shipped pipeline imports; we report it here because it is present in the released artefact, not because it is on any code path we measured.

### B. Stage 1: Ingestion, Sanitization, and Keyword Pre-filter

Resume text is extracted with PyMuPDF [12] for PDF, python-docx for DOCX, or by direct read for text. It is then normalized. Text below a 40-word floor is refused as unparseable and is not scored. No structured field extraction of skills, roles, or years of experience is performed. The cleaned text is passed to the model in full, truncated at 18000 characters. This avoids adding a parser as a second error source that could not be separated from screening error.

Sanitization strips instruction-like text using five regular-expression families: instruction override, persona hijack, chat markup, score demand, and verdict demand. It never rejects a resume. It removes the matched text and records a flag on the application, so a recruiter sees what was changed. This conservative handling is deliberate, because a pattern match cannot separate an attack from a security engineer describing their work.

The pre-filter score is the share of a role's listed requirements found literally in the resume, using word-boundary matching:

$$
S_{\mathrm{ats}} =
\begin{cases}
\operatorname{round}\!\left(100\cdot\dfrac{|M|}{|R|}\right), & |R| \geq 1, \\[2mm]
0, & |R| = 0,
\end{cases}
\tag{1}
$$

where $M \subseteq R$ is the set of requirements found literally and $R$ is the role's requirement list. When a role lists no requirements the ratio in (1) is undefined, and the implemented function returns 0 rather than 100. We state this because it is a consequential convention rather than a neutral one: an empty requirement set yields $S_{\mathrm{ats}} = 0$, and since $0 < \tau_{\mathrm{ats}} = 40$, such a role rejects every applicant automatically and without a model call. A deployer must therefore populate the requirement list before publishing a role. The harness records this behaviour as a measured property and names it accordingly.

A resume with $S_{\mathrm{ats}} < \tau_{\mathrm{ats}} = 40$ is rejected without a model call. The rejection reason names the missing requirements. The filter is intentionally lexical, so every automatic rejection can be explained to the candidate as a list of terms.

### C. Stage 2: Model Screening

For resumes that pass the pre-filter, one schema-constrained model call scores five criteria from 0 to 5: skill match, experience, projects, communication, and culture fit. The prompt marks the resume as untrusted data. It also lists the requirements that the literal search did not match, so the model can credit genuine equivalents. The screening score is the sum of the clamped criteria, giving $S_{\mathrm{resume}} \in \{0,\dots,25\}$.

This score gates entry to the interview. A candidate with $S_{\mathrm{resume}} \ge s_{\min}$, where $s_{\min} = 15$ is the shortlist floor, is shortlisted and proceeds to the interview stage. A candidate with $S_{\mathrm{resume}} < s_{\min}$ is routed to human review. The pipeline neither rejects nor interviews that candidate automatically.

### D. Stage 3: Adaptive Interview

On shortlisting, a question plan of at most 12 questions is generated from the resume and job description. Each question is tagged by source: resume, job-description gap, culture, or behavioral. Requirements for which the resume provides no evidence are converted into job-description-gap questions. The interview therefore probes claims the document does not substantiate and gives the candidate an opportunity to demonstrate competence that the resume does not show.

Two properties of this stage limit what the interview can be said to contribute, and we state them here rather than in Section VI because they are properties of the design. First, the question plan is conditioned on the resume and on the requirements the literal pre-filter did not match, so the interview is constructed from the same document whose ranking it is asked to revise. The interview is therefore an additional evidence source about the candidate, not an independent one. Second, the same provider and the same model family are used for screening and for interviewing, so a systematic error in one stage is not guaranteed to cancel in the other. We treat the consequences in Section VI-B.

After each answer, a turn policy chooses one of four actions: probe, steer, next planned, or wrap up. Bounds are checked before the model is consulted. The turn budget, the wall clock, and the unasked plan are enforced first, and at most two adaptive turns may occur in a row. Within the turn budget and wall clock, the model cannot end the interview while planned questions remain, and a planned question is asked in its exact wording. A steer is triggered by a keyword match against watch topics, so the decision to follow the candidate needs no model call.

Each answer passes a two-tier guardrail. Tier 1 is structural and pattern-based. Tier 2, a model check, runs only when word overlap with the question falls below 0.06, and it fails open with a flag if the provider is unavailable. A rejected answer returns the question without spending the turn. After three rejections the answer is accepted with a forced-accept flag, so a guardrail cannot lock a candidate out. At the end, one call scores five criteria (technical depth, problem solving, communication, culture fit, practical impact), each from 0 to 5. The total $S_{\mathrm{interview}} \in \{0,\dots,25\}$ is again recomputed from the clamped criteria. The deadline is the earlier of the session duration and the window close, and a refresh resumes the interview without spending the single attempt.

### E. Stage 4: Integrity Scoring

Proctoring runs in-process alongside the interview. It combines computer vision (face detection, head pose, identity matching, and optional object detection), audio loudness cues, and browser signals. Its output is a set of discrete events $E$. Each event has one of 13 kinds and one of four severities, whose weights $w_s$ are 12 (critical), 6 (high), 3 (medium), and 1 (low). The integrity score is computed as

$$
d = \Bigl(\sum_{k \in K} w_{s_k}\bigl(1 + \gamma \ln |E_k|\bigr)\Bigr)\cdot \min\bigl(\kappa,\; 1 + \sigma(|K| - 1)\bigr),
\tag{2}
$$

$$
c(E) = \operatorname{clamp}\bigl(\operatorname{round}(100 - d),\; 0,\; 100\bigr),
\tag{3}
$$

with $\gamma = 1$, $\sigma = 0.25$, and $\kappa = 2$. Here $K$ is the set of distinct kinds observed, $E_k$ is the set of events of kind $k$, and $s_k$ is the worst severity observed for kind $k$. Each distinct kind is thus charged the weight of its most severe event, multiplied by $1 + \gamma \ln |E_k|$, which grows logarithmically with the number of events of that kind, so repetition raises the charge sublinearly. The sum over kinds is then multiplied by a factor that increases with the number of distinct kinds and is capped at $\kappa$. The quantity $d$ is the total deduction, and $c(E)$ is the integrity score on a 0-100 scale.

The verdict is flag below 55, clean at 80 or above with no critical event, and review otherwise. Any critical event precludes a clean verdict. A single critical event, which by the arithmetic of (2) and (3) alone leaves a score of 88, is therefore held at review. The function is deterministic and order-independent, so it can be recomputed from stored events without the video. It reports evidence for a human reviewer and is not an automated finding of misconduct.

### F. Stage 5: Trust-Weighted Fusion and Ranking

Integrity does not gate the final score and does not multiply it. It determines how much weight the interview receives. Let the trust be $t = c(E)/100$, let $\lambda \in [0,1]$ be the interview weight (default 0.5), and note that $S_{\mathrm{resume}}$ and $S_{\mathrm{interview}}$ are both expressed on the 0-25 rubric scale. Then

$$
v = t\,S_{\mathrm{interview}} + (1 - t)\,S_{\mathrm{resume}},
\tag{4}
$$

$$
S_{\mathrm{final}} = (1 - \lambda)\,S_{\mathrm{resume}} + \lambda\,v.
\tag{5}
$$

Equivalently, $S_{\mathrm{final}} = S_{\mathrm{resume}} + \lambda t\,(S_{\mathrm{interview}} - S_{\mathrm{resume}})$, so the effective interview weight is $\lambda t$. As $t \to 0$, (4) and (5) together collapse $S_{\mathrm{final}}$ onto $S_{\mathrm{resume}}$, not onto zero: an untrusted interview can stop benefiting a candidate but cannot numerically penalize one. This is the property that makes an integrity signal produced by a computer-vision heuristic advisory rather than disqualifying, and it is algebraic in (4) and (5); Section V-C reports the measured check of it. It is a statement about scores, not about positions: a candidate's place in the list can still change when the candidates around them are re-scored, and no fusion function can promise otherwise.

Candidates are ordered by $(-S_{\mathrm{final}},\; -S_{\mathrm{resume}},\; -S_{\mathrm{ats}},\; \text{application id})$. The final key, the application id, makes the ordering total and deterministic, which is required because the screening rubric admits 26 distinct integer values and 97 of the 100 candidates in the audit cohort share a screening score with at least one other.

### G. Algorithm

**Algorithm 1. End-to-end candidate evaluation**

*Input:* resume file F, role J (with requirement list), floor $\tau_{\mathrm{ats}}$, shortlist floor $s_{\min}$, interview weight $\lambda$. *Output:* outcome, and $S_{\mathrm{final}}$ for interviewed candidates.

```
 1:  R      <- normalize(extract(F))                     ▷ PyMuPDF / docx / read
 2:  if |words(R)| < 40 then return REFUSED(unparseable)
 3:  R'     <- sanitize(R)                               ▷ strip and flag, never reject
 4:  S_ats  <- keyword_score(R', J)                      ▷ 0 if J lists no requirements
 5:  if S_ats < tau_ats then return REJECTED(missing terms)
 6:  S_res  <- SUM clamp(model_criteria(R', J), 0, 5)   ▷ five criteria, <= 25
 7:  if S_res < s_min then return UNDER_REVIEW           ▷ human review, no interview
 8:  plan <- build_plan(R', J);  E <- {};  transcript <- {}
 9:  while turn budget and clock allow:
10:      q <- next_turn(plan, transcript)                 ▷ probe, steer, next, wrap up
11:      if q = WRAP_UP then break
12:      a <- accept_answer(q)                            ▷ <=3 rejections, then force
13:      transcript <- transcript UNION {(q, a)}
14:      E <- E UNION proctor_events()
15:  S_int <- SUM clamp(model_interview_criteria(transcript), 0, 5)
16:  t <- c(E)/100;   v <- t*S_int + (1-t)*S_res
17:  S_final <- (1-lambda)*S_res + lambda*v
18:  return S_final                                      ▷ then ordered by ranking key
```

### H. Implementation

The system is written in Python 3.12 with Streamlit 1.63 [13] for the interface and 17 pinned, free and open-source dependencies. Model calls use plain REST, with no vendor SDK. Speech recognition uses faster-whisper, which re-implements the Whisper architecture [14], and vision uses OpenCV (YuNet and SFace) and MediaPipe [CITE-NEEDED: faster-whisper] [CITE-NEEDED: YuNet] [CITE-NEEDED: SFace] [CITE-NEEDED: MediaPipe]. Object detection uses YOLO11n through ultralytics, which is AGPL-3.0 licensed and disabled by default [CITE-NEEDED: YOLO11n/ultralytics]. Passwords are hashed with scrypt [CITE-NEEDED: scrypt]. Recorded media, where retained, stays on the operator's local disk and is not written to the database, which stores derived event records. The test suite collects 371 cases and runs offline with the fake provider. Table I summarizes the modules.

**TABLE I. MODULES, INPUTS, OUTPUTS, AND TECHNIQUES**

| Module | Input | Output | Technique | Model calls |
|---|---|---|---|---|
| Sanitizer | resume text | cleaned text, flags | 5 regex families | none |
| Pre-filter | text, requirements | $S_{\mathrm{ats}}$, missing list | literal word-boundary match | none |
| Screening | text, role | $S_{\mathrm{resume}}$ (0-25) | 5-criterion rubric, schema-constrained | 1 |
| Interview | plan, answers | transcript, $S_{\mathrm{interview}}$ | bounded turn policy, 2-tier guardrail | plan, turns, guardrail (tier 2), score |
| Integrity | events | $c(E)$, verdict | per-kind worst severity, log persistence | none |
| Fusion | three scores | $S_{\mathrm{final}}$, rank | trust-weighted convex mix | none |

### I. Link to Evaluation

Section IV evaluates the pipeline on a 100-candidate synthetic cohort generated from a fixed seed. Concealed competence values are assigned, and over-sellers, hidden gems, and two honest control groups are planted by construction. Four resume-only rankers (keyword, TF-IDF, BM25, and a simulated model score) are compared with the fused ranking. Ranking metrics, control-case rank displacement, integrity sensitivity, offline-stage latency, and transport-level data egress under hosted and local providers are also reported.

---

## IV. EVALUATION METHODOLOGY

This section specifies the generator, the metrics, the corpora, and the measurement procedures, and states which provenance label each reported quantity carries. Every value quoted here is read from the released harness and every constant is read from the implementation.

### A. The Synthetic Cohort

Ground truth is assigned first and observations second. Each candidate is drawn with a latent competence value, and the resume and the interview are then generated as noisy, biased views of that value, with the bias assigned by case. No ranker sees the competence value; only the scorer that computes the ground-truth ranking does. The generator is parameterised as follows, with a fixed seed of 20260923 so a reader regenerates the identical cohort.

Competence is drawn uniformly within a per-case band, resampled per case because a case that requires low competence is otherwise meaningless: over-sellers and honest-weak candidates are drawn from the band [0.05, 0.40] and hidden gems and honest-strong candidates from [0.60, 0.95]. The resume view is $\operatorname{clamp}(\text{competence} + b_{\mathrm{case}} + \mathcal{N}(0, 0.08^2), 0, 1)$ where $b_{\mathrm{case}}$ is $+0.55$ for an over-seller, $-0.45$ for a hidden gem, and $0$ for both honest control groups. The interview view is $\operatorname{clamp}(\text{competence} + \mathcal{N}(0, 0.08^2), 0, 1)$: **unbiased but noisy**. This is the single most consequential assumption in the paper, and it is an assumption of the generator rather than a finding. Both views are scaled to the shipped rubrics and rounded to integers, the screening rubric to 0-25 and the interview rubric to 0-25, so the tie structure matches what the portal produces. The keyword score is derived from the resume's claims rather than its competence, $\operatorname{round}(\operatorname{clamp}(100\,\text{resume view} + \mathcal{N}(0, 6^2), 0, 100))$, which is why an over-seller passes the lexical gate.

Integrity is fixed at 100 for every candidate in the cohort. We state this plainly because it bounds what the ranking results mean: the headline audit is run at full trust, and any behaviour of the fusion under a degraded trust value is reported separately as the sensitivity sweep in Section V-B. Mixing a guessed integrity distribution into a headline ranking result would introduce an unmeasured quantity into a measured one.

The cohort contains 25 candidates in each of the four planted cases, 100 in total. The four resume-only baselines are run on this same cohort so that the comparison is like-for-like: the number of requirements a candidate claims in the generated resume text is driven by their screening score rather than their competence, document length is drawn independently of both, and a set of plausible but irrelevant distractor skills is added so that length and relevance are not perfectly correlated.

### B. The Simulated Model Score Baseline

One of the four resume-only rankers, reported as the simulated model score, is not a real language-model screening run. Its screening scores are read from the cohort's concealed screening views rather than obtained by prompting a model. We label it simulated in every table, caption, and sentence in which it appears. It is included because the question in Section V-A is whether a lexical or model-based resume-only method keeps over-sellers out of the shortlist, and this row is the model-shaped stand-in for the screener the pipeline itself would use. It is not evidence about how any real model scores a resume, and a real zero-shot baseline has not been run.

### C. Lexical Baselines

Both lexical baselines are implemented directly in the released harness rather than taken from a library, so their parameters are stated here because a reader would otherwise assume library defaults. The tokenizer lowercases and splits on non-alphanumeric characters, keeping the characters that carry meaning in technical keywords. Term frequency is logarithmic, inverse document frequency is smoothed as $\log(1 + N/\mathrm{df})$, and vectors are L2-normalized, giving the standard log-tf, smoothed-idf, cosine-norm weighting. BM25 is Okapi with $k_1 = 1.5$ and $b = 0.75$ over the same smoothed idf and the same tokenizer. Smoothed idf is a deliberate choice: the unsmoothed BM25 idf becomes negative for terms appearing in more than half the documents, which in a single-role cohort is most of the vocabulary.

All four resume-only rankers are run against one role, the five-requirement data-engineer role (Python, Java, APIs, Databases, System design), chosen because it is the role with the most requirements in the catalogue and therefore gives the lexical rankers the richest query. An embedding baseline using sentence-transformers is deliberately excluded: it is not a dependency of the system, and listing a baseline with no number beside it would misrepresent the comparison. The harness records it as unavailable and names the dependency and the model download that would be required.

### D. Metrics

The relevant set is the top quartile of the cohort by concealed competence, 25 candidates, defined on the truth and never on any score a ranker produces. NDCG@10 is computed over that set against the full competence vector as graded relevance; it is therefore a statement about our constructed cohort rather than a transferable quality score. Kendall's tau-b measures rank agreement between a produced ordering and the concealed competence ordering, and is reported because it tolerates the many ties that a 0-25 integer rubric produces. Recovery rate is the share of candidates in a planted case that moved in the direction the planted error implies: an over-seller downward, a hidden gem upward. Mean rank shift is the mean signed change in position, where a negative value is an improvement. The control displacement rate is the share of honest controls displaced by more than five positions, where five is 5% of the cohort and is stated rather than tuned. Precision@10 and mean reciprocal rank are computed but are not reported as point estimates, because across 200 cohort regenerations their intervals are wide enough (widths 0.40, 0.40, 0.86, and 0.67 respectively) that a single draw would misrepresent them.

### E. The Labelled Corpora

Two corpora are hand-labelled rather than generated, because their labels are judgements a machine cannot make. Whether "act as a system administrator" in a resume is an attack or a job title is a decision, and the decision has to be recorded by a person before a number can mean anything. Both corpora were written by hand by the authors.

The guardrail corpus contains 31 attack strings across the five implemented pattern families and 18 benign strings, 10 of which are hard negatives: prose that discusses instructions, administration, or scoring without attempting any of them, such as a security engineer describing prompt-injection work. The hard negatives are included because a guardrail tested only against easy negatives reports a zero false-positive rate and then edits an honest candidate's resume in production.

The boundary corpus contains 26 cases pairing a resume fragment with a single requirement and a hand label for whether a literal matcher should match it, each with a written justification, since a label without a reason is an opinion. Three of the 26 probe synonym, abbreviation, and conceptual equivalence. Because the matcher is literal by design, the corpus defines the correct answer in those three as no match, and they are therefore counted separately: they measure the price of having no synonym expansion, not matching correctness. We report accuracy over the 23 remaining cases, which are decidable from character content alone, as well as over all 26.

### F. Egress Measurement

Data egress is measured at the transport layer rather than inferred from the design. The harness replaces the HTTP session the provider modules use with an interceptor and then drives complete candidate journeys through the real services, capturing every request body byte-for-byte before it is sent or discarded, so the numbers describe what was on the wire including prompt scaffolding, JSON schemas, rubric text, and accumulated transcript. Three provider configurations are driven: the hosted API, a local daemon endpoint, and the offline fake. Two resume sizes are then used to recover a slope and a fixed overhead rather than a single byte count, and one additional call is made to exercise the tier-2 moderation path in isolation. Every captured body in the whole run is scanned for media file paths, media extensions, data URIs, and base64 blobs, so the "media never leaves the host" statement is computed over all of them rather than asserted.

The hosted and local configurations are driven through the real provider objects with requests intercepted and replies stubbed. The local configuration therefore measures the payload and its routing to a loopback address; it is not a measurement of any model's output, and no inference was performed by a local model at any point in this work.

### G. Latency Measurement

Offline per-stage latency is measured with the provider stubbed, so every figure is the system's own overhead and none includes model inference. Each stage is warmed up for three iterations to load lazily imported modules and warm the page cache, then timed over 40 repetitions with a nanosecond-resolution monotonic clock, and reported at the 50th and 95th percentiles rather than as a mean. Three stages deviate and are timed as run: PDF extraction over 10 repetitions, the scrypt operations over 8, candidate registration over 6, and the end-to-end service stages over 15. Separately from the timing trials, four deterministic scoring functions are each called 50 times on a fixed input; all 50 calls return one distinct result, which is why every other number in this paper is reproducible.

### H. Provenance Labels

Every quantity the harness reports carries one of five labels, assigned by the harness rather than by the authors at writing time. **Measured** quantities were observed by running the shipped code. **Derived** quantities were computed from measured values, such as ratios and deltas. **Specification** quantities were read out of the source and are constants rather than results. **Simulated** quantities were produced against a constructed cohort or a stubbed provider. **Unavailable** quantities were not measured at all; they are emitted with a machine-readable statement of what would be required to measure them and never carry a value. The released set comprises 243 quantities: 131 measured, 24 derived, 37 simulated, 35 specification, and 16 unavailable.

### I. Reproducibility

All measurements were taken at commit 11b9495 on Python 3.12.10, Windows 11 on AMD64 with an Intel64 Family 6 Model 158 Stepping 10 processor, CPU-only. The measurement harness ran with the provider set to the offline fake, a throwaway database, and proctoring disabled, so no reported offline figure depends on local state a reader cannot reproduce. Two classes of measurement in this paper were not taken offline and are labelled individually: the provider-contract probes in Section V-H, which were issued live against the hosted Gemini API using model string `gemini-2.5-flash`, and the two local-deployment figures in Section V-G, which are loopback routing measurements with stubbed responses and no model inference. The harness is released with the code at **[REPOSITORY URL]** and is run with a single command that writes its results, its labelled tables, and its figures to a fixed directory.

---

## V. RESULTS

This section reports measurements only; interpretation is deferred to Section VI. Four independent evaluation sources are used: a 100-candidate synthetic cohort (seed 20260923, 25 candidates in each of four planted cases, competence values concealed from all rankers), used for the ranking and fusion audit; 26 hand-labelled boundary cases, used to characterize the keyword pre-filter; 31 attack strings and 18 benign strings, used to characterize the prompt-injection guardrail; and offline benchmarks of the implemented codebase, executed with the provider stubbed except where a live call is stated explicitly. Ranking and fusion quantities are computed on the synthetic cohort and are simulated; they are not claims about real candidates. Quantities requiring a local model run, human raters, demographic labels, or labelled video or audio media were not measured and are not reported.

### A. Comparison of Resume-Only Rankers

Four resume-only rankers were scored against concealed competence on the synthetic cohort (Table II, Fig. 5). BM25 reached an NDCG@10 of 0.6234, followed by TF-IDF at 0.5846, the simulated model score at 0.5572, and the keyword score at 0.4843. Over-sellers occupied seven of the top ten positions under the keyword score and under TF-IDF, and six under BM25 and under the simulated model score. The fused ranking reached an NDCG@10 of 0.9440 on the same cohort and is included in Table II for reference. Kendall's tau-b for the four resume-only rankers was 0.1240, 0.1026, 0.0949, and 0.1681 respectively. Precision@10 and mean reciprocal rank are not reported as point estimates because their across-regeneration intervals are too wide to characterize a single draw.

**TABLE II. RESUME-ONLY RANKERS ON THE SIMULATED COHORT (SIMULATED)**

| Ranker | NDCG@10 | Kendall's tau-b | Over-sellers in top 10 |
|---|---|---|---|
| Keyword (ATS) | 0.4843 | 0.1240 | 7 |
| TF-IDF (cosine) | 0.5846 | 0.1026 | 7 |
| BM25 | 0.6234 | 0.0949 | 6 |
| Simulated model score | 0.5572 | 0.1681 | 6 |
| Fused (for reference) | 0.9440 | 0.5471 | — |

**Fig. 5.** Ranking quality of the keyword gate, TF-IDF cosine, BM25, and the simulated resume score against concealed competence, with the number of over-sellers each admits to the top ten on the right axis and the fused system's NDCG as a reference line. All four rankers read the same self-reported document. Simulated cohort: these are generated documents scored against a constructed ground truth, not measurements on real candidates.

### B. Rank Correction by Fusion

On the shipped cohort draw, fusion raised NDCG@10 from 0.6087 to 0.9440 and Kendall's tau-b from 0.1701 to 0.5471 (Fig. 6). All 25 planted hidden gems moved up in rank and all 25 over-sellers moved down, a recovery rate of 1.00 over the 50 planted errors, with a mean gain of 21.4 positions for a hidden gem and a mean loss of 24.48 positions for an over-seller. Per-case movement, with 95% bootstrap intervals and exact two-sided sign tests, is given in Table V.

**TABLE V. PER-CASE RANK MOVEMENT UNDER FUSION (SIMULATED)**

| Case | n | Mean shift | 95% bootstrap CI | Moved correct way | Direction significant |
|---|---|---|---|---|---|
| Over-seller (falls) | 25 | +24.48 places | [+21.48, +27.52] | 25/25 | p = 5.96e-08 |
| Hidden gem (rises) | 25 | −21.40 places | [−24.52, −18.24] | 25/25 | p = 5.96e-08 |
| Honest strong | 25 | −10.60 places | [−13.68, −7.64] | 9/25 within tolerance | p = 5.96e-08 (upward) |
| Honest weak | 25 | +7.52 places | [+4.60, +10.48] | 10/25 within tolerance | p = 9.11e-04 (downward) |

Of the 50 honest controls, 31 moved more than the five-position tolerance, a control displacement rate of 0.62. The displacement decomposes by case: 16 of the 25 honest-strong controls moved up, a mean of 15.25 places, and 15 of the 25 honest-weak controls moved down, a mean of 12.27 places; the smallest displacement counted was six places. Among the controls themselves, Kendall's tau-b between their ordering and their concealed competence rose from 0.7861 before fusion to 0.8596 after it, and 1148 of 1225 control pairs (0.9371) preserved their relative order, leaving 77 pairs (0.0629) inverted. Table VI gives the decomposition.

**TABLE VI. CONTROL DISPLACEMENT DECOMPOSITION (SIMULATED)**

| Quantity | Value |
|---|---|
| Honest controls | 50 |
| Displaced beyond the 5-position tolerance | 31 |
| Control-internal tau-b before fusion | 0.7861 |
| Control-internal tau-b after fusion | 0.8596 |
| Control pairs compared | 1225 |
| Control pairs order preserved | 1148 (0.9371) |
| Control pairs order inverted | 77 (0.0629) |

To assess dependence on the specific cohort draw, the cohort was regenerated 200 times using seed 20260923 and its 199 successive integer seeds, and both rankings were recomputed on each regeneration. This 200-draw set characterizes the variability of the shipped figures rather than their point value. Across the 200 regenerations the mean tau-b of the fused ranking was 0.5812 with a 95% interval of [0.5308, 0.6315], the mean tau-b gain over resume-only ranking was 0.3826 [0.3333, 0.4388], and the mean NDCG@10 gain was 0.2855 [0.1257, 0.4514]. The tau-b gain was positive in 200 of 200 draws and the NDCG@10 gain was positive in 200 of 200. All ten headline quantities for the shipped seed fall inside their own across-seed intervals. Table VII gives the intervals in full.

**TABLE VII. ACROSS-SEED INTERVALS OVER 200 COHORT REGENERATIONS (SIMULATED)**

| Quantity | Shipped draw | Mean | 95% interval | Range |
|---|---|---|---|---|
| tau-b, resume-only | 0.1701 | 0.1986 | [0.1268, 0.2744] | [0.0796, 0.2921] |
| tau-b, fused | 0.5471 | 0.5812 | [0.5308, 0.6315] | [0.5075, 0.6469] |
| tau-b gain | 0.3770 | 0.3826 | [0.3333, 0.4388] | [0.3156, 0.4501] |
| NDCG@10, resume-only | 0.6087 | 0.6699 | [0.5008, 0.8357] | [0.4222, 0.8882] |
| NDCG@10, fused | 0.9440 | 0.9553 | [0.9207, 0.9800] | [0.9029, 0.9852] |
| NDCG@10 gain | 0.3353 | 0.2855 | [0.1257, 0.4514] | [0.0672, 0.4916] |

The interview-weight sweep from 0 to 1 in steps of 0.05, run on the shipped cohort, is shown in Fig. 7. Tau-b reached 0.7903 at a weight of 0.95, against 0.5471 at the shipped weight of 0.5. Repeated on each of the 200 regenerations, the maximizing weight landed on five distinct grid values, took 0.95 in 132 of 200 draws, and never fell below 0.8. The shipped weight of 0.5 was fixed before this sweep was run and was not retuned in light of it.

**Fig. 6.** Per-candidate rank change when the interview is fused into the ranking, against the position the resume alone gave them, for the shipped cohort draw. Over-sellers fall and hidden gems rise; the spread of the honest controls around zero is the cost of making that correction. Simulated cohort: this measures the fusion function on a constructed ground truth, not the validity of the interviewer.

**Fig. 7.** Sensitivity of the ranking to the interview weight lambda, swept from 0 to 1 in steps of 0.05 on the shipped cohort draw. Recovery of both planted error types rises with lambda, and so does displacement of the honest controls; the parameter governs the trade. Lambda was fixed at 0.5 before this sweep was run, so the curve is a sensitivity analysis over a simulated cohort and carries no recommended value.

### C. Integrity Scoring

An event-free session scored 100 with a clean verdict, and a single critical event scored 88 at review (Fig. 8). Six repeated no-face events scored 83 at clean; forty repeated no-face events scored 72 at review. A session with a phone, written notes, a background voice, and two glances away, five events across four distinct kinds, scored 75 at review. One glance away scored 97; three glances and one blur, four events across two kinds, scored 88; a second person in frame twice scored 90; four tab switches and two pastes scored 76 at review. The score was non-increasing over 39 cumulative event additions, identical over 1000 repeated calls on a fixed input, and identical across every ordering of a fixed four-event set.

**TABLE VIII. INTEGRITY SENSITIVITY OF THE FUSED RANKING (SIMULATED)**

| Integrity score applied to whole cohort | Kendall's tau-b | Over-seller recovery |
|---|---|---|
| 0 | 0.1701 | 0.00 |
| 25 | 0.2521 | 0.92 |
| 50 | 0.3382 | 1.00 |
| 55 | 0.3636 | 1.00 |
| 75 | 0.4428 | 1.00 |
| 90 | 0.5099 | 1.00 |
| 100 | 0.5471 | 1.00 |

With integrity set to 0 for the whole cohort, every candidate's fused score equalled their resume-only score exactly, the largest absolute difference being 0.0, and the fused ordering coincided with the resume-only ordering on every position. Across 2100 candidate-by-integrity pairs, covering 100 candidates at 21 integrity levels from 0 to 100, the fused score never left the closed interval between the candidate's resume-only score and their fully trusted fused score. The headline audit in Section V-B fixes integrity at 100; the rows above are the sensitivity sweep around that fixed point.

**Fig. 8.** Integrity score and verdict for nine worked scenarios, with the event count and the number of distinct event kinds behind each. A single critical event scores 88 and is still held at review, because a critical severity blocks the clean verdict regardless of arithmetic. Six repeats of one kind cost 17 points and stay clean; forty of them cost 28 and reach review. Measured, not modelled: the scoring function is exhaustive over its own inputs, while the accuracy of the detectors that raise the events is not measured.

### D. Prompt-Injection Guardrail

The tier-1 guardrail was evaluated on 31 attack strings across five pattern families and 18 benign strings, 10 of which were hard negatives (Fig. 9). It flagged 28 of 31 attacks, a detection rate of 0.9032 on this self-authored corpus. Per family, detection was 7 of 9 for instruction override, 4 of 5 for persona hijack, and 7 of 7, 5 of 5, and 5 of 5 for chat markup, score demand, and verdict demand. The three evasions were a leetspeak variant, a hyphenated variant, and a polite paraphrase with no imperative verb. Four benign strings were flagged, all four of them hard negatives, giving a false-positive rate of 0.2222 overall and 0.40 on hard negatives. Precision was 0.875, recall 0.9032, F1 0.8889, specificity 0.7778, and accuracy 0.8571. The tier-2 escalation rate on benign input was 0.7778.

Sanitization does not reject a hostile resume: matched instruction-like text is removed from the resume, the removal is recorded as a flag on the application, and the application proceeds through the remaining screening steps. No hostile resume among the 31 tested was rejected by sanitization. All 28 resumes whose text was rewritten carried their corresponding flags, and no resume was flagged without also being rewritten.

**Fig. 9.** Tier-1 guardrail over the full labelled corpus. Bars are detection rates per injection family; the dashed line is the rate at which hard negatives, honest resumes that discuss prompt injection, system administration, or scoring rubrics, pass unmodified. The two are plotted together because a false positive silently deletes a sentence from an honest application.

### E. Keyword Pre-Filter

On the 26 hand-labelled boundary cases, the pre-filter matched the assigned label in 24, an accuracy of 0.9231, with a table confusion of 11 true positives, 1 false positive, 1 false negative, and 13 true negatives. Two cases did not match their label. The first is a genuine matcher defect: for the resume fragment "C++ only, no plain C work" against the single-character requirement "C", the matcher returns a match and scores 100, where the hand label is no match; the boundary guard is not excluding C inside C++. The second is a labelling error rather than a matcher failure. For "Expert in PostgreSQL" against the requirement "SQL", the hand label expects a match, but the implemented matcher returns no match, and a direct call to the function confirms a score of 0. The label was written on the reasoning that SQL appears at a suffix boundary inside PostgreSQL; the boundary guard correctly declines it, so the matcher behaves as specified and the label is wrong. We report the accuracy as the authors labelled the corpus, 0.9231, and record the disagreement here rather than silently correcting the denominator.

Three further cases in the corpus probe synonym, abbreviation, and conceptual equivalence: "relational databases" for "SQL", "ML" for "machine learning", and "Infrastructure as Code" for "Terraform". For a literal keyword matcher the corpus defines the correct answer in each of these three as no match, and the pre-filter returned no match in all three, so all three were scored correctly. These cases therefore measure the price of having no synonym expansion rather than matching correctness. Restricted to the 23 cases decidable from literal character content alone, accuracy was 0.9130 (21 of 23), and both of the two disagreements above fall inside that subset; under a corrected label for the PostgreSQL case the subset accuracy would be 22 of 23. A five-requirement role admits six score values, 0, 20, 40, 60, 80, and 100, and the threshold of 40 corresponds to at least two matched requirements.

### F. Latency

Offline per-stage latency was measured with the provider stubbed; no model inference is included in any figure in this subsection (Table IX, Fig. 10). Each stage was warmed up for three iterations and then timed over 40 repetitions, except PDF extraction (10), the scrypt operations (8), candidate registration (6), and the end-to-end service stages (15). Scrypt hashing and verification, at 119.35 ms and 119.31 ms at p50, and candidate registration, at 120.92 ms, were the slowest stages, and candidate registration was the dominant stage. The scoring stages ran below 0.5 ms at p50: keyword scoring 0.097 ms, sanitization 0.106 ms, integrity scoring 0.0053 ms at 10 events and 0.0676 ms at 200 events, and ranking 100 candidates 0.373 ms. PDF text extraction on a 9-page document took 51.03 ms at p50. These figures are single-machine, CPU-only, and specific to the hardware named in Section IV-I.

**TABLE IX. OFFLINE STAGE LATENCY (ms), PROVIDER STUBBED**

| Stage | n | p50 | p95 |
|---|---|---|---|
| PDF text extraction (9 pages) | 10 | 51.03 | 51.34 |
| Sanitization (hostile input) | 40 | 0.106 | 0.108 |
| Keyword score (5 requirements) | 40 | 0.097 | 0.148 |
| Integrity score, 10 events | 40 | 0.0053 | 0.0231 |
| Integrity score, 200 events | 40 | 0.0676 | 0.0680 |
| Rank 100 candidates | 40 | 0.373 | 0.513 |
| Scrypt hash | 8 | 119.35 | 120.87 |
| Scrypt verify | 8 | 119.31 | 120.97 |
| Register candidate | 6 | 120.92 | 122.95 |
| Apply (no model call) | 15 | 0.452 | 0.504 |
| Screen (provider stubbed) | 15 | 0.506 | 0.730 |

Separately from the timing trials, four deterministic scoring functions, the keyword pre-filter, sanitization, integrity scoring, and ranking, were each called 50 times on a fixed input and returned identical output on every call. This determinism check does not extend to every offline stage: password hashing is salted by design and is expected to return different output on each call, so its timing is reported without a corresponding determinism claim.

**Fig. 10.** Per-stage latency with the provider stubbed, so the figure shows the system's own overhead rather than model time. Everything in the scoring path is sub-millisecond; what costs time is PDF extraction, SQLite writes, and the scrypt key derivation, the last of which is slow by design.

### G. Data Egress

Transport-level interception recorded every outbound request body issued during one complete candidate journey of 14 model calls under each of the hosted and local provider configurations (Table X, Fig. 11). The probe used a single generated resume of 546 bytes, the same bytes under all three configurations, and the local configuration's responses were stubbed: no local model performed inference at any point. The hosted provider issued 14 requests carrying 41798 bytes to one external, non-loopback host. The local provider issued 14 requests carrying 40870 bytes to a loopback address, so that its traffic was captured but did not leave the host. The fake provider issued no requests. The 0.9778 ratio between the local and hosted payload sizes is reported only as a comparison of request size.

**TABLE X. EGRESS PER CANDIDATE JOURNEY**

| Provider | Requests | Request bytes | Non-loopback egress | Bytes crossing network |
|---|---|---|---|---|
| Gemini (hosted) | 14 | 41798 | Yes | 41798 |
| Ollama (local) | 14 | 40870 | No (loopback only) | 0 |
| Fake | 0 | 0 | No | 0 |

Resume text appeared in 2 of the 14 calls, and candidate answers appeared in 12 of 14. Comparing the two resume sizes used in this measurement, 546 and 5469 bytes, the request-body size increased at a measured slope of 2.0 request bytes per resume byte, with a fixed overhead of 39778 bytes per candidate independent of resume length; both figures are derived from this two-point comparison rather than from a larger sweep of resume sizes.

Across the full egress run, 57 request bodies were intercepted in total, and the count reconciles exactly by configuration: 0 for the fake provider, 14 for the hosted journey, 14 for the local journey, 14 for the larger-resume run used to measure the slope, 14 for the per-call breakdown run, and 1 for the separate tier-2 moderation call. Six of the 14 calls in the journey are tier-2 guardrail calls. That count is a property of the stub rather than a measured rate: the stub's canned question bears no lexical relation to its canned answer, so word overlap falls below the 0.06 threshold and tier 2 fires on most turns. No media file path, media file extension, data URI, or base64-encoded blob appeared in any of the 57 intercepted request bodies.

Seven of the 14 calls in the journey were schema-constrained. This does not contradict the two-of-five figure in Section V-H: that figure counts code locations, and this one counts how many times those locations fire during one journey. Five call sites exist in the implementation and two of them pin the response with a schema; the remaining three ask for JSON in the prompt and rely on extraction.

**Fig. 11.** Request payload per candidate, one screening call, a question plan, every interview turn and the final grading, measured by intercepting the real provider code. The volume is the same for the local and hosted configurations because the same prompts are built; what differs is the recipient. Under the local configuration none of it leaves the host, and biometric media leaves the host under neither, since vision and speech-to-text run in-process.

### H. Provider Contract and Implementation Inventory

Two schemas were shipped to the hosted provider, using 11 allowed keywords. Sanitization preserved every required field and dropped no keyword the schemas use. In a live run against `gemini-2.5-flash`, both shipped schemas were accepted with every required field present, and both the pre-fix schema and an unsanitized schema were rejected with HTTP 400. The mean hosted round trip over the two probes was 4.146 s (5.309 s and 2.983 s). The two probes billed 603 reasoning tokens in total against 324 tokens of visible output; this is two observations, not a constant, and is not reported as an average. The hosted response carried no generation-time field, so the only provider reporting a server-side generation duration is the local one. Five model call sites exist in the implementation, of which two are schema-constrained.

The source comprises 33 files with 10423 lines, of which 8361 are code. The test suite has 15 files containing 358 test functions and 371 collected cases, the difference being parametrized expansions, giving a test-to-source-code-line ratio of 0.428. The database has 11 tables, 121 columns, 7 foreign keys with 7 cascading deletes, 22 personal-data columns, and 4 biometric-media columns. The system defines 13 proctoring event kinds and 5 guardrail pattern families, and has 17 pinned dependencies.

---

## VI. DISCUSSION AND THREATS TO VALIDITY

### A. What the Results Show About the Fusion Function

Read together, Table II and Table V describe a specific situation rather than a general advantage. No resume-only method examined kept over-sellers out of the shortlist: four methods, lexical and model-shaped alike, admitted six or seven candidates whose planted resumes overstated their concealed competence into ten slots. BM25, the strongest of the four at NDCG@10 0.6234, outscored the simulated model screener, which is worth stating plainly because it cuts against the intuition that a model-shaped ranker should dominate a lexical one on this task. What the numbers show is not that the pipeline screens better, but that the shortlist was occupied by the wrong candidates regardless of how it was ranked, and that a second evidence source reorganized it.

The direction of that reorganization is stable. The tau-b gain was positive in 200 of 200 cohort regenerations and the NDCG@10 gain was positive in 200 of 200, with the shipped draw inside its own interval for all ten headline quantities. Both planted error types moved in the intended direction for every member of their case, with exact sign-test probabilities of 5.96e-08.

The cost figure needs the same prominence as the benefit, but it needs its own decomposition to be read correctly. A control displacement rate of 0.62 says that 31 of 50 honest candidates moved more than five positions. It does not by itself say that the fusion misjudged them, and the harness says so in its own annotation: half the cohort is deliberately mis-scored by the resume, so when those candidates are correctly demoted the honest controls must move to let them past. The decomposition separates the two mechanisms. The controls' own ordering became more faithful to their concealed competence, tau-b rising from 0.7861 to 0.8596, and 93.71% of control pairs preserved their relative order. Both displaced groups moved in the direction of their own competence: 16 honest-strong controls up, 15 honest-weak controls down. The defensible cost figure is therefore the 0.0629 of pairs whose order inverted, which is invariant to how many errors were planted, where the displacement rate is not. We describe the fusion as recovering planted errors while displacing a majority of honest candidates in absolute position, most of which displacement is the bookkeeping consequence of correcting others rather than a re-judgement of them. The characterization of the stage as having high recall at the cost of precision is not supported by this decomposition and is not made.

The interview-weight sweep is reported as a sensitivity analysis and nothing more. The maximizing weight never fell below 0.8 across 200 regenerations, which indicates the interview signal carries information about the concealed competence vector in this generator. The argmax itself is unstable, landing on five grid values, so no single-draw maximum can be read as a tuned setting. Tuning a hiring weight against synthetic ground truth and shipping the result would be precisely the overfitting this paper warns about elsewhere. The shipped default of 0.5 was fixed before the sweep was run.

### B. Threats to Validity

**Circularity between the generator and the result.** The cohort's resume inflation and its unbiased-but-noisy interview are assumptions of the generator, not measurements. The results test whether the fusion function recovers a planted resume error given interview evidence of the stated quality; they do not test whether the interviewer produces evidence of that quality. Every rate in Section V-B is conditional on the noise parameter of 0.08 set in Section IV-A, and the unconditional form of the claim is recorded as unmeasured.

**The assumption that interview noise is unbiased.** The generator models the interview as an unbiased noisy view of competence. A real interviewer may be biased rather than merely noisy, and the most obvious case is the one the pipeline exists to address: an interviewer persuaded by a confident over-seller would reproduce the very error the fusion is meant to correct, and the correction would then be anti-correlated with its own target. The cohort contains no such case.

**Non-independence of interview and resume.** As stated in Section III-D, the question plan is conditioned on the same resume and on the requirements the literal pre-filter did not match, and the same provider and model family screen and interview. The interview is therefore an additional evidence source rather than a measurement separable from the resume, and its agreement with concealed competence cannot be attributed to the interview alone.

**Synthetic-resume realism.** Resumes in the cohort are generated from templates that turn a claimed skill set into bland prose, with distractor skills added so length and relevance are uncorrelated. Real resumes contain chronology, self-presentation, gaps, and formatting that carry signal no template reproduces. The lexical baselines read documents whose vocabulary is controlled by construction, which flatters or penalizes them in ways a real corpus would not.

**Self-authored corpora.** The guardrail corpus of 31 attacks and 18 benign strings and the 26-case boundary corpus were both written by the authors, who also wrote the patterns being tested. The detection rate of 0.9032 and the boundary accuracy of 0.9231 are therefore regression figures against a corpus designed to exercise the implementation, not estimates of behaviour on adversarial input in the wild. No adaptive adversary was modelled.

**Small n.** Twenty-five candidates per planted case, and 50 planted errors in total. The sign tests are exact and the intervals are bootstrap or across-seed, but every quantity here rests on a cohort three orders of magnitude smaller than a real applicant pool, and cohort composition is a design choice rather than a sample.

**Single role.** All ranking baselines were run against one five-requirement role, the data-engineer role, chosen because it gives the lexical rankers the richest query. Whether the over-seller penetration rates generalize to roles with three or four requirements, or to non-technical roles, is untested. The gate granularity measurement shows that the reachable keyword scores, and therefore the meaning of the rejection threshold, differ by role.

**No human raters, and self-preference.** Every quantity that would place the model's interview score against human judgment is absent, and the harness records the run that would produce it. Two consequences follow. We cannot state that the interviewer is accurate. We also cannot exclude self-preference, in which the same model family that screened a candidate rates that candidate's interview; the design does not test for it and has no independent rater to test against.

**Fixed integrity in the headline audit.** The ranking results in Section V-B are computed at integrity 100 for every candidate. This is a deliberate exclusion of a guessed quantity from a headline result, and it means the headline figures describe the fusion under full trust. Table VIII reports the degraded-trust behaviour separately; it is a sensitivity sweep, not a substitute for a cohort whose integrity is measured per candidate.

**Stub dependence of the egress and latency results.** Both were measured with responses stubbed. The egress numbers describe the payload the pipeline constructs and where it is routed, which is a property of the request-building code and the configured endpoint; they say nothing about what any model returns. The latency numbers describe the system's own overhead and exclude model inference entirely. The 12-of-14 answer-carrying figure describes one probe journey with a 546-byte generated resume, not a rate over real candidates with real documents of varying length.

**Non-transferability of latency.** Every timing figure is single-machine, CPU-only, and specific to the hardware named in Section IV-I. A re-run on different hardware, or with a real model in the path, will produce different numbers, and the security-relevant constant in particular, the scrypt work factor, is hardware-dependent.

### C. Ethics and Regulatory Considerations

The pipeline processes identity-matching and biometric data. Face detection, head pose, and identity matching run against webcam input, answer audio is transcribed locally, and the resulting derived records are persisted in a database that holds 22 personal-data columns, 4 of which reference biometric media. Consent is recorded before a session proceeds. The integrity score and its associated events are advisory: they are reported to the recruiter as evidence for human review and are never an automated determination of misconduct, and because the integrity value enters the fusion as a mixing weight rather than as a penalty, a candidate's score cannot be driven down by proctoring noise. The final disposition of every candidate remains a human decision, and the only automatic rejection in the pipeline is the keyword pre-filter, which is deliberately lexical so that every such rejection can be explained as a named list of missing requirements.

Under the hosted provider, the candidate's resume text and every verbatim answer reach one third-party processor, making that provider a processor under the data-protection framework that governs personal data and placing the deployment in the high-risk employment category of the EU regulatory framework for artificial intelligence. Under the local configuration the same payload reaches no party other than the operator. Two gaps are material and are named here rather than in the limitations alone: the database is not encrypted at rest, so filesystem permissions are the only control on 22 personal-data columns and 4 biometric-media columns; and while deletion functions exist and cascade correctly, nothing schedules them, so retention is an operator action rather than a property of the system. We make no fairness claim. No demographic attribute is collected, recorded, or evaluated anywhere in the pipeline, so the system is neither audited for nor demonstrated to be fair, and fairness auditing remains future work requiring demographic data this study does not have.

---

## VII. LIMITATIONS AND FUTURE SCOPE

This study showed that fusing an interview stage into resume-only ranking revises planted errors on a synthetic cohort, but did not establish that a real interviewer supplies evidence of the assumed quality. Closing that gap, and the related gaps below, requires five extensions.

First, interviews conducted by a model should be recorded and scored independently by at least three human raters, with inter-rater agreement and model-to-human correlation reported, to replace the assumed interview-noise model with a measured one.

Second, the planned ablation ladder should be executed with a real zero-shot language-model baseline and an embedding-based baseline, both run against a local model, so that each stage's contribution to the observed correction can be attributed rather than assumed. With a stubbed provider every rung would return the same canned response, so an ablation run now would measure nothing.

Third, local deployment should be characterized by inference latency, throughput, and peak memory, reported alongside the specific model, quantization, and hardware used, since none of these was measured in the present study. The provider layer already discards the durations the local provider returns, so this needs no new dependency.

Fourth, encryption at rest and a scheduled data-retention job should be implemented to close the data-protection gaps identified here; until then, the local-deployment egress result characterizes network exposure only, not storage-level protection.

Fifth, the synthetic generator should be extended to encode protected attributes, enabling a disaggregated fairness study, and the evaluation should be repeated on both technical and non-technical roles and on real resumes obtained under a documented licence.

Each of these is a precondition for stronger evidence, not a claim already supported by this work. The present results are confined to a constructed synthetic audit; extending them along the five directions above is necessary before any claim of readiness for supervised use on real candidate data would be warranted, including in organizations that process such data on their own hardware.

---

## VIII. CONCLUSION

Resume-only screening ranks candidates on a self-reported document and cannot revise its judgment when later evidence contradicts it. This work asked whether an adaptive interview stage can revise the resulting ranking errors, and what such a pipeline exposes about candidate data. We built a locally deployable pipeline in which the interview is an additional evidence source, combined with the resume score through trust-weighted fusion, so that low-integrity interview evidence contributes less to the final score without lowering the candidate's resume score.

On the simulated cohort, four resume-only rankers let six or seven over-sellers into the top ten. Fusion raised NDCG@10 from 0.61 to 0.94 and Kendall's tau-b from 0.17 to 0.55 on the shipped draw, with the gain positive in every one of 200 cohort regenerations, and every planted hidden gem moved up in rank. Its cost is stated beside its benefit: the control displacement rate was 0.62, but 93.71% of honest control pairs kept their relative order and their internal agreement with concealed competence rose, so most of that displacement is the consequence of correcting others rather than a re-judgement of them. At the transport layer, candidate answers appeared in 12 of 14 model calls against 2 for resume text, and directing the same payload to a loopback address under the local configuration reduced measured non-loopback egress from 41798 bytes to zero as a consequence of that routing. These ranking results are simulated: they characterize what the fusion function does with interview evidence of a stated quality, and they do not establish that the interviewer supplies evidence of that quality. The extensions in Section VII are the preconditions for making that stronger claim.

---

## ACKNOWLEDGMENT

The authors used AI-assisted tools in preparing this work. Assistance covered implementation of the measurement harness and the figure and table generation, drafting and editing support across all sections, and analysis support in deriving and checking the reported quantities. The authors verified every number reported in this paper against the released harness and the source at the stated commit. **[AUTHORS MUST CONFIRM THIS IS TRUE]**

---

## REFERENCES

[1] A. Tiwari, S. Vaghela, R. Nagar, and M. Desai, "Applicant tracking and scoring system," *International Research Journal of Engineering and Technology (IRJET)*, vol. 6, no. 4, pp. 320-324, 2019.

[2] S. Pudasaini *et al.*, "Scoring of resume and job description using Word2vec and matching them using Gale-Shapley algorithm," in *Expert Clouds and Applications*, pp. 705-713, 2022, doi: 10.1007/978-981-16-2126-0_55. **[AUTHOR TO CONFIRM: author initial, year, and page range; the supplied sources disagree]**

[3] C. Li, E. Fisher, R. Thomas, S. Pittard, V. Hertzberg, and J. D. Choi, "Competence-level prediction and resume & job description matching using context-aware transformer models," in *Proc. 2020 Conf. Empirical Methods in Natural Language Processing (EMNLP)*, pp. 8456-8466, 2020, doi: 10.18653/v1/2020.emnlp-main.679.

[4] C. Gan, Q. Zhang, and T. Mori, "Application of LLM agents in recruitment: A novel framework for automated resume screening," *Journal of Information Processing*, vol. 32, pp. 881-893, 2024, doi: 10.2197/ipsjjip.32.881.

[5] F. P.-W. Lo *et al.*, "AI hiring with LLMs: A context-aware and explainable multi-agent framework for resume screening," in *Proc. IEEE/CVF Conf. Computer Vision and Pattern Recognition Workshops (CVPRW)*, 2025. doi: 10.1109/cvprw67362.2025.00402. **[AUTHOR TO CONFIRM: page range]**

[6] A. Fabris, N. Baranowska, M. J. Dennis, D. Graus, P. Hacker, J. Saldivar, F. Zuiderveen Borgesius, and A. J. Biega, "Fairness and bias in algorithmic hiring: A multidisciplinary survey," *ACM Transactions on Intelligent Systems and Technology*, vol. 16, no. 1, art. 16, pp. 1-54, 2025, doi: 10.1145/3696457.

[7] P. Baxi, J. Xu, J. Y. Jiang, and S. Jasin, "Prompt injection in automated résumé screening with large language models: Single and multi-injection settings," in *Findings of the Association for Computational Linguistics: ACL 2026*, pp. 2942-2953, 2026, doi: 10.18653/v1/2026.findings-acl.142.

[8] N. Vanetik and G. Kogan, "Job vacancy ranking with sentence embeddings, keywords, and named entities," *Information*, vol. 14, no. 8, p. 468, 2023, doi: 10.3390/info14080468.

[9] "Human and LLM-based resume matching: An observational study," *Findings of NAACL*, 2025. **[AUTHOR TO CONFIRM: authors not present in the supplied source]**

[10] "Evaluating bias in LLMs for job-resume matching: Gender, race, and education," *NAACL Industry Track*, 2025. **[AUTHOR TO CONFIRM: authors not present in the supplied source]**

[11] "FAIRE: Assessing racial and gender bias in AI-driven resume evaluations," 2025. **[AUTHOR TO CONFIRM: authors and venue not present in the supplied source]**

[12] PyMuPDF, "PyMuPDF documentation," 2023. [Online]. Available: https://pymupdf.readthedocs.io/

[13] Streamlit Inc., "Streamlit documentation," 2023. [Online]. Available: https://docs.streamlit.io/

[14] A. Radford *et al.*, "Whisper: Robust speech recognition via large-scale weak supervision," 2022. **[AUTHOR TO CONFIRM: arXiv identifier not present in the supplied source]**