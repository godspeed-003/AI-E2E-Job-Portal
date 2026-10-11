# Reply to the Revision Report

**Re:** `Revision_Report.docx`, companion to *AI-Based Resume Screening and
AI-Assisted Interview Evaluation Framework with Proctoring System*

**From:** the implementation side, answering against the running code
**Date:** 2026-10-11
**Code state:** commit `0b93173`, 404 tests passing

---

## Summary

Thank you — the report is doing the right thing by flagging rather than
resolving. Every item you marked VERIFICATION REQUIRED needed the source tree
rather than the manuscript, so none of them were yours to close.

Of the items I can answer from code:

| | count |
|---|---:|
| Confirmed — your diagnosis was correct | 2 |
| Resolved — the answer was the opposite of the default assumption | 1 |
| Resolved — the error was in *our* documentation, not the paper | 1 |
| Stale — you were reading an older harness run; no paper change needed | 1 |
| **New, and urgent** — counts the paper cites have since moved | 1 |
| Still open — not answerable from the repository | ~20 |

Details below, in your numbering where you used it.

---

## 1. Table II vs Section VII-B — your hypothesis is exactly right

> *"Probable cause: different tie-breaking (candidate id versus keyword score)
> in metrics/m4 versus the fusion audit. Flagged for confirmation."*

**Confirmed.** Two different sort keys over the same LLM scores:

```
metrics/m4_baselines.py:228
    sorted(..., key=lambda i: (-scores[i], ids[i]))
    → ties broken by candidate id.           Arbitrary.

core/ranking.py:273   (resume_only_rank, used by m5_fusion_audit)
    sorted(..., key=lambda c: (-c.llm_score, -c.ats_score, c.application_id))
    → ties broken by ATS keyword score first. Informative.
```

And the reason it moves the metric as much as it does — measured on the shipped
cohort, seed `20260923`:

| | |
|---|---:|
| cohort size | 100 |
| distinct LLM scores | 24 |
| candidates sharing a score | **97 (97%)** |
| largest single tie group | 10 |
| **positions that differ between the two keys** | **83 of 100** |

With 97% of the cohort tied, the tie-break *is* the ranking. The ATS key carries
real signal about the candidate, so m5's 0.6087 is legitimately higher than m4's
0.5572. The difference is neither noise nor a defect.

**Our recommendation for the paper.** Do not leave two numbers for one quantity.
Either:

- **(a)** report both with one sentence — *"the simulated LLM ranker scores
  0.5572 under an arbitrary (id) tie-break and 0.6087 when ties are broken by
  keyword score; 97 of 100 candidates share an LLM score, so the tie-break
  accounts for the whole difference"*; or
- **(b)** report only 0.6087, state the tie-break in the methods, and drop the
  m4 row.

We suggest **(a)**, because it is a finding rather than an inconvenience: a 0–25
integer rubric cannot separate 100 candidates, so whatever breaks its ties is
doing the ranking. That is worth a sentence in the discussion.

**One fragility to name in the methods while you are there.**
`m4_baselines.py:163` selects the baseline role with
`max(roles, key=len(requirements))`. Two roles tie at five requirements, so the
winner is whichever `data/roles.json` happens to list first. Deterministic today,
not pinned — a reordered JSON file would silently change the baseline query.
Name the role explicitly.

---

## 2. The baseline role — the paper's *name* is right and its *list* is wrong

> *"Text says data-engineer role but lists the five requirements that
> data/roles.json gives the backend-developer role... Confirm which role was
> used."*

**Resolved, and the answer is the opposite of the natural assumption.** The
harness really does use the data engineer:

```
m4_baselines.py:163 selects  amazon_data_engineer
  title         Junior Data Engineer
  requirements  Python, SQL, ETL, Pandas, AWS basics
```

For contrast, the other five-requirement role:

```
  amazon_backend_dev
  requirements  Python, Java, APIs, Databases, System design
```

So the fix is to **correct the five requirements** to Python, SQL, ETL, Pandas
and AWS basics, and **keep the role name as it stands**. Renaming the role to
backend developer — the more obvious repair — would make the text wrong in the
other direction.

---

## 3. Hard negatives — ten, and the error was ours

> *"SECURITY-AND-DATA-SAFETY.md says five hard negatives; the corpus code and
> paper say ten. The paper values were kept."*

**You were right to keep the paper's value, and the arithmetic settles it:**

```
metrics.corpora.BENIGN                 18
  of which difficulty == "hard"        10
ATTACKS                                31

false_positive_rate_hard_negatives   0.40   note: "4/10"
```

Five hard negatives cannot produce 0.40. `SECURITY-AND-DATA-SAFETY.md` was
wrong; it has been corrected in the repository as of 2026-10-11. No change
needed in the paper.

---

## 4. The result-file counts — stale, not wrong

> *"provenance.csv 204 rows vs 243 reported quantities; unavailable.md 14 items
> vs 16; m7 tests table 13 rows vs 15 test files; m1 scenarios table 8 rows vs
> nine scenarios. The supplied files appear to be from an earlier run."*

**Your inference was correct — they were from an earlier run.** Re-run of
2026-10-11 against the current tree:

| quantity | you saw | now | paper says | |
|---|---:|---:|---:|---|
| results.json quantities | — | 243 | 243 | ✅ |
| provenance.csv rows | 204 | **243** | 243 | ✅ |
| unavailable items | 14 | **16** | 16 | ✅ |
| m1 integrity scenarios | 8 | **9** | 9 | ✅ |

**No paper change needed for this item.** The `research paper/results/` tree has
been regenerated and committed, so the files a reviewer downloads now match the
numbers the paper prints.

---

## 5. ⚠ New since your report — four counts in the paper have moved

This one is on us, and it is the only item here that needs action before
submission.

We found and fixed a defect in the microphone capture path, and then added a
transcript-review step to the interview room. Both required new tests, which
moved every artefact count the paper cites:

| the paper says | actual, 2026-10-11 |
|---|---|
| 371 collected cases | **404** |
| 15 test files | **18** |
| 358 definitions | **385** |
| 33 source files | **34** |
| — | source lines **10 911**, ratio **0.457** |

`test_cases = 404` still equals pytest's own collection exactly, so the two
numbers remain safe to quote together.

**Please re-read all of these from `results.json` immediately before
submitting.** They are the only numbers in the paper that change without anyone
touching the paper. We have added a flagged warning at §5.9 of
`PAPER-REVISION-GUIDE.txt` and the full working at **PART 12**.

For the record, the defect behind the new tests: the capture sink averaged over
an array axis of length one, so interleaved 48 kHz stereo passed through
unchanged and was then written under a 16 kHz mono WAV header. Measured on a
440 Hz tone, 3.00 s of speech arrived as 18.00 s at 73.3 Hz. Whisper returned
empty strings, which is why the microphone appeared not to work while capture
had been correct throughout. It is not a result in the paper — no reported
measurement depended on it — but it is the reason the counts moved.

---

## 6. Items we agree should stay flagged

Your handling of these is right and we have nothing to add from code:

- **Reference [5]** — cited but absent from the list; page range and DOI
  conflict across project files. Needs the publisher record.
- **Reference [2]** — manuscript 2022 / pp. 705–713 against Crossref 2021 /
  pp. 691–699. Our guide reports the Crossref values; neither of us should
  decide this from a project file.
- **Reference [1]** — we could not independently locate it either, and it
  supports a load-bearing Introduction claim. Either confirm it with the
  publisher or replace it.
- **"Locally deployable"** — keeping the wording with its qualifiers is the
  right call. The default provider is hosted, no local inference run has
  happened, and the paper says both.
- **Latency 118.31 vs 119.35 ms** — different runs; keeping the manuscript's
  values is correct. State the date.
- **Fig. 8 legend overlap** — confirmed, regenerate.
- **Fig. 9** — your caption correction matches the image. Whether to add the
  trade-off panel is an editorial call.

**One we can nudge:** the **C++/"C" boundary case**. The fragment *does* contain
a standalone `C` in "plain C work", so the literal match is defensible. This is
a labelling question rather than a matcher defect. Pick one and say which — the
current text calls it a defect, which overstates it.

---

## 7. Items only the authors can close

Listed so nothing is assumed resolved: the four author-block fields and e-mail
addresses; the CPU model and installed RAM; the repository URL; the
live-provider-run date; the acknowledgment and the venue's AI-disclosure
statement; the eleven missing citations (faster-whisper, YuNet, SFace,
MediaPipe, YOLO11n/ultralytics, scrypt, GDPR processor provisions, the EU AI Act
high-risk employment category, and the original sources for NDCG, Kendall's
τ-b, BM25 and TF-IDF); the §II-D literature gap on video interviewing,
LLM-as-judge validity and remote proctoring; and the code release licence
alongside the AGPL-3.0 `ultralytics` dependency that is already mentioned.

---

## On the manuscript itself

Since you asked whether it is written correctly: structurally it is sound, and
on the point that matters most it is honest. The synthetic cohort is declared
**in the abstract** rather than a footnote. The 0.62 displacement rate is
reported with equal prominence to the gains. The egress finding leads.

Two specific things worth keeping exactly as they are:

- **The 0.62 reframing.** Presenting it as an upper bound against
  `control_pairs_order_preserved = 0.9371` is legitimate — we verified 0.9371
  is a real derived measurement in `m5_fusion_audit`, not a rhetorical
  softening. Keep both numbers adjacent.
- **The two-sentence bound on what the ranking results mean** — that the
  cohort's inflation and its unbiased-but-noisy interview are *assumptions of
  the generator, not findings*, and that the interview is not separable from the
  resume. That paragraph is what makes the rest of the results citable.

**How to reproduce anything above:**

```bash
python -m metrics.run_all
```

Everything lands in `research paper/results/` with a provenance label per
quantity, and `research paper/results/unavailable.md` states what each
unmeasured quantity would require.
