# FINAL SELF-CHECK

Each question answered yes/no with the evidence that establishes it. Checks 1, 2, 4, 5, 6
and 7 were executed programmatically against `02-REVISED-PAPER.md`, not read by eye.

---

**1. Does every `[n]` in the text have a reference?**
**YES.** Citations found in the body: `[1]`–`[14]`. References in the list: `[1]`–`[14]`.
The two sets are identical; no citation lacks a reference and no reference is uncited.
Numbering is contiguous from 1 with no gaps. Verified by set comparison of the in-text
`[n]` markers against the list entries.

**2. Does any number appear that is not in a supplied file or verified by a run?**
**YES — none found.** Every figure was either read from `research paper/results/` or
re-derived by executing the harness modules against the current code. The full set of
decimal figures in the body was diffed against that verified set; every hit resolved,
including the rounded forms (0.61 ← 0.6087, 0.94 ← 0.9440, 0.17 ← 0.1701, 0.55 ← 0.5471,
0.38 ← 0.3826, 0.29 ← 0.2855, 93.71% ← 0.9371) and the derived control-displacement case
means (15.25 and 12.27 places, computed from `m5_fusion_audit__displaced_controls.csv`).
Three-digit-plus integers all resolve: 1000 (determinism check), 131 and 243 (provenance
counts), 132 (λ argmax draws), 158 (CPU model), 199 (successive seeds), 6492 (resumes in
Li *et al.*). Three items are worth naming explicitly because they are **not** from the
supplied results and were found by running the code:

- `ats_score("Python expert", [])` → **0**, which contradicts the original manuscript and is now reflected in equation (1).
- `ats_score("Expert in PostgreSQL", ["SQL"])` → **0**, which establishes the V-E case as a mislabel.
- `ats_score("C++ only, no plain C work", ["C"])` → **100**, which establishes the other V-E case as a real matcher defect.

One deliberate exception: the latency table reports the supplied 2026-09-27 values, because a
re-run on this machine produced materially different numbers (scrypt p50 78.93 ms vs
119.35 ms). Re-measuring on different hardware and reporting those instead would have been a
silent substitution, so the supplied values are used and the machine is named in IV-I and V-F.
No number was carried over from a source outside the project.

**3. Does "false correction rate" appear anywhere?**
**NO.** Zero occurrences, including in the metric's own name in `m5_fusion_audit.py`. The
metric appears only as the **control displacement rate** — the share of honest controls
displaced by more than five positions — in the Abstract, I, V-B (Table VI), VI-A and VIII.
The phrases "false correction rate", "false_correction_rate", "62% of the rank changes it
made were not corrections", "high-recall but imprecise" and "high-recall, low-precision"
all return zero matches.

**4. Is "independent" applied to the interview?**
**NO.** The construction "independent measurement" returns zero occurrences. The interview
is called "an additional evidence source" in the Abstract, I, III-D, IV-A, VI-B and VIII.
Nine uses of "independent" remain and each is legitimate: the integrity function is
*order-independent* (III-E); the service layer is *independent of the user interface*
(III); four *independent evaluation sources* (V preamble); document length is drawn
*independently* of score and competence (IV-A); a *fixed overhead* is *independent of
resume length* (V-G); raters would score *independently* and there is *no independent rater*
(VI-B, VII).

**5. Does the word "simulated" appear in every ranking caption?**
**YES.** Seven ranking tables and figures carry captions and all seven contain "simulated":
Table II ("ON THE SIMULATED COHORT (SIMULATED)"), Table V, Table VI, Table VII, Table VIII,
Fig. 5, Fig. 6, Fig. 7. Fig. 7 additionally says the sweep "carries no recommended value".
Table I (modules), Table IX (latency), Table X (egress) and Figs. 1–4, 8–11 are not ranking
quantities and correctly omit it; Fig. 8 is integrity scoring and says "Measured, not
modelled".

**6. Do section cross-references resolve?**
**YES.** Sections I–VIII all present; subsections A–I present. Every reference in the text
of the form "Section N", "Section N-M" resolves to an existing heading:
III-D, IV-A, IV-I, V-A, V-B, V-C, V-G, V-H, VI-B, and II, III, IV, V, VI, VII, VIII.
Zero broken references. The old self-references to the previous numbering ("Section V-B"
pointing at a different subsection, the III-H pointer) were removed during the reorder, and
the Introduction roadmap was rewritten to match the final order exactly.

**7. Is any raw LaTeX left?**
**NO stray markup.** Five display-math blocks, each a properly delimited equation carrying
`\tag{1}` through `\tag{5}`, with zero `$$` occurrences outside them and zero `\operatorname`
outside math mode. All five equations are referred to in the text as "(1)", "(2) and (3)"
and "(4) and (5)". The encoding damage in the original manuscript is gone: zero occurrences
of the corrupted italic-theta glyph, of the stray Devanagari-range subscript, of the
non-breaking-space artefact, or of the backtick that appeared in the abstract.
`\operatorname{round}`, `\operatorname{clamp}` and `\min` appear only inside math mode.

**8. Legacy template text removed?**
**YES.** Zero occurrences of "Commented [A1]", "copyright form", "RESULT AND CONCLUSION",
"[REQUIRED", "[RESULT NOT YET AVAILABLE", or any trace of the previous AI pass's
advice-to-the-author block that occupied three pages of the earlier draft.

**9. Is the reference list a single list, numbered by first appearance?**
**YES.** One list of 14 entries. The manuscript originally had **none** — both the `.docx`
and the `.pdf` end at the word "REFERENCES" while the body cites [1]–[11] — and an earlier
draft had two lists, the second of 24 uncited entries containing unresolvable venue strings.
Numbering follows first appearance: Tiwari, Pudasaini, Li, Gan, Lo, Fabris and Baxi appear
in the Introduction as [1]–[7]; Vanetik, the human-matching study and the two bias studies
first appear in II-B to II-D as [8]–[11]; the three tool references first appear in III-B
and III-H as [12]–[14].

**10. Are claims strengthened anywhere?**
**NO.** Every "simulated" scope statement from the original is retained and several are
tightened. The unmeasured quantities are stated as unmeasured rather than estimated, the
guardrail figure is a "detection rate on this self-authored corpus" rather than a
resistance or security claim, no fairness claim is made anywhere, and no recommended value
of λ appears in the text, in a caption, or in the abstract.

---

## Not verifiable by me — requires the authors

| Item | Why |
|---|---|
| Author block | three authors, affiliations, emails — currently a marked placeholder |
| `[REPOSITORY URL]` | the public URL of the released harness; not invented |
| `[AUTHORS MUST CONFIRM THIS IS TRUE]` | the Acknowledgment asserts the authors verified every number against the released harness |
| Six `[AUTHOR TO CONFIRM]` reference fields | Pudasaini's initial/year/pages, Lo's pages, the missing authors of [9], [10], [11], and Whisper's identifier — the supplied sources disagree or omit them |
| Thirteen `[CITE-NEEDED]` topics | no supplied source exists; `related_sources_ai_recruitment.md` was named in the instructions but is not present anywhere in the project |
| V-E's "22 of 23" figure | derived arithmetically from the reported confusion matrix under a corrected label, not re-measured; re-running requires editing the hand label in `metrics/m3_ats.py` |

All of the above are itemised in `04-OPEN-ITEMS.md`.

---

## Deliverables

| # | File | Contents |
|---|---|---|
| 1 | `01-CHECKPOINT-AND-PHASE0-VERIFICATION.md` | Phase 0 verification table, all verified numbers, the nine places the manuscript was wrong, reproduction commands |
| 2 | `02-REVISED-PAPER.md` | the complete revised paper, I–VIII + Ethics + Acknowledgment + References |
| 3 | `03-CHANGE-LOG.md` | `[Review item # | What changed | Where]` for all 28 items plus Phase 0 |
| 4 | `04-OPEN-ITEMS.md` | every remaining marker with the exact run or source that closes it |
| 5 | `05-SELF-CHECK.md` | this file |

Revised paper: ~12300 words of body, 8 tables, 11 captioned figures, 5 numbered equations,
14 references, 249-word abstract, 5 index terms.