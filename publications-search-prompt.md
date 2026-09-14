# Prompt for a web-search agent: find workshop venues I could not

**Why this file exists.** `publications.md` §5 records that web search was unavailable
in the session that produced it (`API Error: 400 ... Tool 'web_search' not found`).
Every venue in that document was found by fetching a URL that could already be named,
then following links. So no venue in it was discovered by *searching for the topic* —
a well-targeted workshop that does not hang off a big conference's workshop index is
invisible to that process. This prompt closes that gap.

Paste everything between the two rulers into an agent that has working web search.

---

## The prompt

You are finding publication venues for a specific paper. Accuracy matters far more
than volume: **one venue I can trust is worth more than ten I have to re-check, and a
venue that does not exist costs me a submission cycle I cannot get back.**

### Today's date

Treat today as **14 September 2026**. Everything below depends on that anchor. If your
knowledge of a venue's dates conflicts with a page you load, the page wins.

### What the paper is

Two papers from one project — an end-to-end AI hiring portal (resume screening → an
adaptive AI video interview → proctoring → recruiter review).

**Paper A — the research paper.** A benchmark and audit answering: *does an adaptive,
resume-grounded AI interview correct the ranking errors that static resume screening
makes?* Synthetic candidates are generated from a ground-truth competence vector, then
four cases are injected by design: an over-seller (inflated resume, low competence), a
hidden gem (terse resume, high competence), and two honest controls. The pipeline is
measured on whether it recovers the planted error. Metrics: NDCG, Kendall's τ,
Precision@k, MRR, rank-shift, false-correction rate, plus robustness to prompt
injection in resumes and interview answers. Baselines: BM25/TF-IDF, Sentence-BERT,
and a zero-shot LLM ranker. Uses LLM-as-judge with a human-agreement check.
4–8 pages.

**Paper B — the system/demo paper.** A working, fully-local (Ollama), transparent
hiring pipeline, with an explicit account of what it refuses to do. 2–6 pages, video.

**Keyword vocabulary to search with:** algorithmic hiring, automated resume screening,
candidate ranking, AI interview, asynchronous video interview, applicant tracking
system, talent acquisition, computational HR, job–candidate matching, hiring
transparency, EU AI Act high-risk, LLM-as-judge, LLM evaluation benchmark, prompt
injection robustness, agentic evaluation, remote proctoring.

### What I want

**Workshops first**, then any other track with a low acceptance bar (student research
tracks, demo tracks, doctoral consortia, findings-style tracks, shared tasks).
Workshops are the priority because they are easier to get into.

**Deadline window: any submission deadline falling between 1 November 2026 and
31 December 2027.** Anything earlier is unreachable; anything later is too far out.

**Rank what you return by two things, in this order:** (1) does the paper get
**published somewhere citable**, and (2) how well the venue's stated scope matches the
topic above. A non-archival workshop is worth listing but say so loudly.

### Do NOT report these back — already verified, in hand

Skip these unless you find a **factual correction**, in which case say so explicitly
and quote the page:

- The ACL-family 2027 joint workshop call (EACL / COLING / NAACL / ACL / EMNLP 2027) —
  organisers notified 2 Oct 2026; suggested author deadlines EACL 15 Dec 2026,
  COLING 5 Jan 2027, NAACL 5 Feb 2027, ACL 25 Apr 2027
- AAAI-27 workshops (papers ~20 Nov 2026, non-archival), AAAI-27 Demonstrations
  (18 Sep 2026), AAAI-27 Student Abstracts (28 Sep 2026)
- ICLR 2027 workshops (organisers notified 29 Nov 2026, suggested papers ~1 Feb 2027)
- RecSys in HR (6th edition 2026; RecSys 2027 is Honolulu 23–27 Aug 2027)
- NeurIPS 2026 workshops, EMNLP 2026 workshops — both closed
- ACL Rolling Review, FAccT 2027, CHI 2027, NAACL 2027 System Demonstrations
- CODS-COMAD (site would not resolve; if you can load it, that IS useful — report it)

### Where to look

**Search the topic vocabulary directly** — e.g. `"workshop" 2027 algorithmic hiring`,
`workshop 2027 "resume screening"`, `CFP 2027 computational HR`, `workshop 2027 LLM
evaluation benchmark`, `"call for papers" 2027 AI recruitment` — and vary the year
between 2026 and 2027.

**Then walk the workshop/CFP index of each of these conferences**, none of which was
checked: **CIKM, SIGIR, ECIR, WSDM, KDD, UMAP, ICWSM, AIES, EAAMO, IJCAI, ECAI, HHAI,
COLM, AACL, LREC, EWAF, ICAIL**, plus any IEEE conference on AI/data that runs
workshops. Also check whether a **shared task or challenge** on candidate matching is
running (RecSys in HR ran a "WorkRB Challenge" in 2026, which suggests the area has
them).

**Aggregators (WikiCFP, ACM DL, DBLP, Google Scholar) may be used to DISCOVER a name,
never as the source of a fact.** Every fact you report must come from the venue's own
domain.

### Hard rules — these are the point of the task

1. **Load every URL you cite.** If you did not successfully load a page, you may not
   state anything from it. No exceptions.
2. **Quote deadlines verbatim** from the page, with the timezone if given.
3. **Never infer a 2027 edition from a 2026 one.** Many 2027 workshop lists do not
   exist yet — the ACL family's is announced 2 Oct 2026, ICLR's 29 Nov 2026. If a
   venue's 2027 edition is not announced, say **"Nth edition expected, not
   announced"** and give the previous edition's dates as the pattern. Do not present
   an expected edition as a real one.
4. **Do not invent venue names or URLs.** A previous automated pass on this project
   "confidently invented several workshop names and URLs." If you are unsure whether
   a workshop exists, either verify it or omit it.
5. **Report your failures.** List every URL that 404'd, timed out, or came back empty,
   so an absence is never mistaken for a finding.
6. **Do not assume archival status.** Report it only if the page states it, and name
   the publisher (ACL Anthology / ACM DL / CEUR-WS / IEEE / none). If the page is
   silent, write **"not stated on the page"** — that is a useful answer.
7. **"No new venues found" is an acceptable and useful result.** Do not pad the list.

### Output format

A markdown section per venue, using these marks: ✅ verified (page loaded, fact
quoted) / ⚠️ unverified or expected-not-announced / ❌ deadline already passed.

For each venue give exactly these fields, writing **"not stated on the page"** where
the page is silent:

| Field | |
| --- | --- |
| Name and edition number | |
| Official URL (that you loaded) | |
| Parent conference, city, conference dates | |
| **Submission deadline** (verbatim, with timezone) | |
| Notification / camera-ready dates | |
| **Page limit**, and whether references and appendices count | |
| **Archival? Published by whom?** | |
| Anonymity: double-blind / single-blind / your choice | |
| Accepts ACL Rolling Review commitments? Separate commitment deadline? | |
| Remote presentation permitted? | |
| **Why it fits** — one sentence, plus a short quote from the venue's own scope text | |

Then close with two things:

- **A table of every URL that failed to load**, with the error.
- **A one-paragraph honest summary** of how thoroughly you searched, and what you
  think you are most likely to have missed.

---

## How to use what comes back

1. Run each returned venue through `publications.md` §9 (the per-venue checklist)
   before you act on it. The agent's verification is not a substitute for yours,
   especially for the archival field.
2. Merge accepted venues into `publications.md` §2 as new numbered subsections, keeping
   the ✅ / ⚠️ / ❌ marking convention.
3. Add every URL to the **Sources** section, and every failure to the failed-to-load
   table there.
4. Add any new deadline to the §8 calendar.
5. If nothing new comes back, that is a real result — record it in §5 so nobody repeats
   the search, and proceed with §2.9 as the plan.

**The failure mode to watch for.** The single most likely bad output is a confident
list of plausible-sounding 2027 workshop names that do not exist yet, because those
lists genuinely have not been published. If the reply names a 2027 ACL, AAAI or ICLR
workshop without saying the list is unannounced, distrust the whole reply and ask it
which URL it loaded for that specific workshop.
