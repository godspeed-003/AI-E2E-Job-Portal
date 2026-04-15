Alright—this is now a **clean, implementation-ready PRD** with everything integrated: PDF parsing, ATS filtering, Gemini evaluation, real-world companies, culture-fit scoring, Streamlit UI, and deterministic behavior.

No ambiguity left. An LLM (or you) can directly build from this.

---

# 📘 FINAL PRODUCT REQUIREMENTS DOCUMENT (PRD)

---

## 1. Product Overview

**Product Name:** AI Resume Screening System (MVP)

**Objective:**
Build a system that:

* Accepts resume PDFs
* Extracts and cleans text
* Performs ATS-style filtering
* Evaluates candidates using Gemini (deterministic)
* Incorporates **job fit + culture fit (Amazon/Deloitte-inspired)**
* Ranks candidates per role
* Displays results via Streamlit UI

---

## 2. Scope (MVP)

### ✅ Included:

* PDF resume upload
* PyMuPDF-based extraction
* Text cleaning (handling broken formatting)
* ATS keyword scoring
* Gemini-based evaluation (1 call per resume)
* Culture + values alignment scoring
* JSON storage
* Streamlit UI (Company + Candidate views)

### ❌ Excluded:

* Interview module
* Video proctoring
* Authentication system

---

## 3. System Architecture

```id="arch01"
Streamlit UI
   ↓
Resume Upload (PDF)
   ↓
PDF Parser (PyMuPDF)
   ↓
Text Cleaner
   ↓
ATS Scorer
   ↓
Gemini Evaluator (Deterministic)
   ↓
JSON Storage
   ↓
Ranking Engine
   ↓
UI Display
```

---

## 4. Tech Stack

```id="stack01"
Python
Streamlit
PyMuPDF (fitz)
Google Gemini API (Flash model)
JSON (storage)
```

---

## 5. File Structure

```id="fs01"
project/
│
├── app.py
├── requirements.txt
│
├── data/
│   ├── companies.json
│   ├── roles.json
│   ├── results/
│
├── modules/
│   ├── parser.py
│   ├── cleaner.py
│   ├── ats.py
│   ├── evaluator.py
│   ├── ranker.py
│   ├── storage.py
│
├── prompts/
│   ├── evaluation_prompt.txt
│
└── dummy_data/
    ├── resumes/
```

---

## 6. Data Models

---

### 🏢 Companies

```json id="comp01"
[
  {
    "company_id": "amazon",
    "name": "Amazon",
    "type": "Tech",
    "culture": [
      "Customer Obsession",
      "Ownership",
      "Bias for Action",
      "Dive Deep",
      "Deliver Results"
    ],
    "values": [
      "Leaders are owners",
      "Think big",
      "Data-driven decisions",
      "High standards",
      "Frugality"
    ]
  },
  {
    "company_id": "deloitte",
    "name": "Deloitte",
    "type": "Non-Tech",
    "culture": [
      "Integrity",
      "Collaboration",
      "Professional excellence",
      "Inclusion",
      "Client commitment"
    ],
    "values": [
      "Act with integrity",
      "Build trust",
      "Respect others",
      "Deliver quality",
      "Make impact"
    ]
  }
]
```

---

### 👔 Roles (5 Total)

```json id="roles01"
[
  {
    "role_id": "amazon_data_engineer",
    "company_id": "amazon",
    "title": "Junior Data Engineer",
    "job_description": "Build scalable data pipelines and support analytics systems.",
    "requirements": ["Python", "SQL", "ETL", "Pandas", "AWS basics"]
  },
  {
    "role_id": "amazon_backend_dev",
    "company_id": "amazon",
    "title": "Backend Developer",
    "job_description": "Develop scalable backend services and APIs.",
    "requirements": ["Python/Java", "APIs", "Databases", "System design"]
  },
  {
    "role_id": "deloitte_hr",
    "company_id": "deloitte",
    "title": "HR Associate",
    "job_description": "Support hiring and employee engagement.",
    "requirements": ["Communication", "Recruitment", "Conflict resolution"]
  },
  {
    "role_id": "deloitte_marketing",
    "company_id": "deloitte",
    "title": "Marketing Executive",
    "job_description": "Execute campaigns and manage brand presence.",
    "requirements": ["Content", "SEO", "Campaigns", "Social media"]
  },
  {
    "role_id": "deloitte_operations",
    "company_id": "deloitte",
    "title": "Operations Coordinator",
    "job_description": "Manage internal processes and coordination.",
    "requirements": ["Coordination", "Reporting", "Attention to detail"]
  }
]
```

---

### 📊 Candidate Result Schema

```json id="res01"
{
  "candidate_id": "resume_001",
  "name": "Unknown",
  "role_id": "amazon_data_engineer",
  "ats_score": 75,
  "llm_score": 18,
  "max_score": 25,
  "alignment_score": 0.72,
  "strengths": [],
  "weaknesses": [],
  "reason": "",
  "status": "shortlisted"
}
```

---

## 7. Resume Parsing (`parser.py`)

```python id="parser01"
def extract_text_from_pdf(file_path: str) -> str:
```

### Implementation:

* Use PyMuPDF
* Loop through pages and concatenate text

---

## 8. Text Cleaning (`cleaner.py`)

### Goals:

* Fix broken formatting
* Normalize whitespace
* Improve LLM readability

### Rules:

* Merge spaced characters (`P h o n e → Phone`)
* Remove excessive newlines
* Normalize spacing

---

## 9. ATS Scoring (`ats.py`)

```python id="ats01"
def compute_ats_score(resume_text, role) -> int:
```

### Logic:

* Match resume text with role requirements
* Score = % of matched keywords

### Threshold:

```id="ats02"
Reject if ATS < 40
```

---

## 10. Gemini Evaluation (`evaluator.py`)

---

### Model Config:

```id="gem01"
temperature = 0
```

---

### Prompt Template

```id="prompt01"
You are an ATS resume evaluator.

Given:
1. Job Description
2. Role Requirements
3. Company Culture
4. Company Values
5. Resume Text

Tasks:
1. Define 5 evaluation criteria:
   - skill_match
   - experience
   - projects/work
   - communication
   - culture_fit

2. Score each (0–5)

3. Culture Fit Rules:
- Amazon → ownership, depth, impact
- Deloitte → communication, teamwork, clarity
- If unclear → neutral (2–3)

4. Output:
- total_score (out of 25)
- alignment_score (0–1)
- 2 strengths
- 2 weaknesses
- 1–2 line reasoning

STRICT:
- JSON only
- deterministic
- no hallucination

FORMAT:
{
  "criteria": {
    "skill_match": 0-5,
    "experience": 0-5,
    "projects": 0-5,
    "communication": 0-5,
    "culture_fit": 0-5
  },
  "total_score": int,
  "alignment_score": float,
  "strengths": [],
  "weaknesses": [],
  "reason": ""
}
```

---

## 11. Ranking (`ranker.py`)

```python id="rank01"
def rank_candidates(candidates):
```

### Sort by:

1. LLM score
2. ATS score

---

## 12. Storage (`storage.py`)

* Store per role:

```id="store01"
data/results/{role_id}.json
```

---

## 13. Streamlit UI (`app.py`)

---

### 🔘 User Mode Selection

* Company
* Candidate

---

### 🏢 Company View

* Select role
* Display:

  * Company name
  * Culture tags

#### Table:

| Rank | Name | LLM Score | ATS | Alignment | Status |

#### Expandable:

* Strengths
* Weaknesses
* Reason

---

### 👨‍💻 Candidate View

* Upload PDF
* Select role

#### Output:

* ATS result
* Final status:

  * Rejected
  * Under Review
  * Shortlisted

---

### 🔄 Switch Mode

* Button at bottom

---

## 14. Execution Flow

```id="flow01"
Upload → Extract → Clean → ATS
        ↓
   If <40 → Reject
        ↓
   Gemini Evaluation
        ↓
   Store JSON
        ↓
   Rank
        ↓
   Display
```

---

## 15. Edge Cases

* Empty PDF → reject
* Bad extraction → fallback to raw text
* Gemini invalid JSON → retry once
* Duplicate resume → overwrite

---

## 16. Determinism Strategy

```id="det01"
temperature = 0
fixed prompt
structured JSON output
```

---

## 17. Requirements

```id="req01"
streamlit
pymupdf
google-generativeai
python-dotenv
```

---

## 18. Dummy Data Strategy

* 5 resumes per role:

  * Strong match
  * Medium match
  * Weak match
  * Irrelevant
  * Edge case

---

# 🔚 Final Note (Important)

This is no longer a “college project-level” system.

You’ve built:

* Multi-stage pipeline (ATS + LLM)
* Deterministic evaluation
* Culture-aware scoring
* Role-specific ranking

That’s already close to how **real hiring pipelines are prototyped**.

But I still don't care about the project, I just need it built fast and don't want to be involved in the process, so just build it. 
---