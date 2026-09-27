# Algorithms

Four algorithms, as LaTeX pseudocode ready to paste into the paper and as plain
prose for the body text. Each file states the source function it was read from,
the constants it depends on, and the measurement that exercises it.

| File | Algorithm | Source of truth | Measured in |
|---|---|---|---|
| `alg1-integrity.tex` | Integrity scoring, `c(E) → integrity` | `proctoring/rules.py:compute_integrity_score` | `metrics/m1_integrity.py` |
| `alg2-fusion.tex` | Trust-weighted fusion and ranking | `core/ranking.py:build`, `rank` | `metrics/m5_fusion_audit.py`, `metrics/m10_stability.py` |
| `alg3-guardrail.tex` | Two-tier answer guardrail | `services/guardrail_service.py:check_answer` | `metrics/m2_guardrails.py` |
| `alg4-adaptive.tex` | Adaptive interview turn selection | `services/interview_service.py:_decide` | specification only — see below |

## How to use these in the paper

The `.tex` files use `algorithm` + `algorithmic` (the `algpseudocode` style from
the `algorithmicx` package). Add to the preamble:

```latex
\usepackage{algorithm}
\usepackage{algpseudocode}
```

IEEE's `IEEEtran` class does not object to either. Each file is a complete
floating `algorithm` environment with its own `\caption` and `\label`, so
`\input{algorithms/alg1-integrity}` inside the relevant section is enough.

For a two-column IEEE layout, Algorithms 1 and 3 fit a single column.
Algorithm 2 fits a single column. Algorithm 4 is the longest; if it overflows,
use `algorithm*` (declared in the file as a comment) to span both columns, or
cut the three early-return guards into prose and keep the model-decision branch
as the algorithm.

## Constants, and where they come from

Every numeric constant below was read out of the running system on 2026-09-27,
not from documentation:

```
proctoring/rules.py      SEVERITY_WEIGHTS = {critical: 12.0, high: 6.0, medium: 3.0, low: 1.0}
                         REPEAT_GROWTH = 1.0
                         CO_OCCURRENCE_STEP = 0.25   CO_OCCURRENCE_CAP = 2.0
core/ranking.py          DEFAULT_INTERVIEW_WEIGHT = 0.5      (lambda)
services/guardrail_service.py
                         5 injection pattern families
                         _OFF_TOPIC_OVERLAP = 0.06
services/interview_service.py
                         MAX_CONSECUTIVE_ADAPTIVE = 2
                         MAX_REJECTS_PER_TURN = 3
                         CRITERIA = 5 dimensions, 5 points each, 25 total
core/config.py           integrity_fail_below = 55
                         ats_reject_below = 40
```

Reproduce that list with:

```bash
.venv/Scripts/python -c "from proctoring import rules; print(rules.SEVERITY_WEIGHTS, rules.REPEAT_GROWTH, rules.CO_OCCURRENCE_STEP, rules.CO_OCCURRENCE_CAP)"
```

## An honesty note on Algorithm 4

Algorithms 1–3 are **measured**: the metrics harness runs each of them over a
constructed corpus and reports the result, so the pseudocode can be checked
against a number. Algorithm 4 is **specification only**. Its control flow is
read directly off `_decide`, and the bounds it enforces (at most 2 consecutive
adaptive questions, at most 3 rejected answers per turn, plan exhaustion
forcing a wrap-up) are verified by unit tests — but the *quality* of the
questions it selects has not been measured, because that needs either a real
multi-turn model run or human raters. Present it as a design, not a result, and
do not attach an effectiveness claim to it.
