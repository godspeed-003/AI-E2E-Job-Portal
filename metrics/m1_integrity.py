"""M1 — the integrity score, characterised exhaustively.

``proctoring.rules.compute_integrity_score`` is the most measurable component in
the system: it is pure, deterministic, needs no model and no camera, and it
decides whether a recruiter is told to go and watch a recording. So it can be
characterised *completely* rather than sampled — every event kind at every
repeat count, and the exact verdict boundaries — which is a stronger result than
any accuracy figure and is the kind of thing a reviewer can check by hand.

Three things are produced:

1. **The deduction table** — cost of the first occurrence of each event kind and
   of each subsequent one, read off the function rather than off the docstring.
2. **The verdict boundaries** — how many events of a given severity it takes to
   fall out of ``clean`` and out of ``review``, per severity.
3. **Property checks** — monotonicity, the clamp at 0, and the rule that one
   ``critical`` event can never score ``clean`` no matter what the arithmetic
   says.

What is *not* measured here: whether the CV backends detect the events in the
first place. That is a detection problem needing labelled video, and the harness
reports it as unavailable rather than guessing at it.
"""

from __future__ import annotations

from metrics import Section
from proctoring import rules


_ALL_KINDS = {
    rules.KIND_NO_FACE: "high",
    rules.KIND_MULTIPLE_FACES: "high",
    rules.KIND_LOOKING_AWAY: "medium",
    rules.KIND_SUBSTITUTION: "critical",
    rules.KIND_PHONE: "medium",
    rules.KIND_EXTRA_PERSON: "high",
    rules.KIND_NOTES: "medium",
    rules.KIND_TAB_SWITCH: "high",
    rules.KIND_WINDOW_BLUR: "medium",
    rules.KIND_PASTE: "medium",
    rules.KIND_FULLSCREEN_EXIT: "low",
    rules.KIND_BACKGROUND_VOICE: "medium",
    rules.KIND_NO_SPEECH: "medium",
}


def _events(kind: str, severity: str, count: int) -> list[dict]:
    return [{"kind": kind, "severity": severity} for _ in range(count)]


def run() -> Section:
    section = Section(
        key="m1_integrity",
        title="Integrity scoring c(E) — exhaustive characterisation",
    )

    section.add(
        "severity_weights",
        dict(rules.SEVERITY_WEIGHTS),
        kind="specification",
        source="proctoring.rules.SEVERITY_WEIGHTS",
    )
    section.add(
        "event_kinds",
        len(_ALL_KINDS),
        unit="kinds",
        kind="specification",
        source="proctoring.rules.KIND_*",
        note="13 detectable event kinds across vision, browser and audio channels",
    )
    section.add(
        "integrity_fail_below",
        rules.settings.proctoring.integrity_fail_below,
        kind="specification",
        source="core.config.ProctoringSettings.integrity_fail_below",
    )
    section.add(
        "repeat_growth",
        rules.REPEAT_GROWTH,
        kind="specification",
        source="proctoring.rules.REPEAT_GROWTH",
        note=(
            "a kind costs its worst severity scaled by 1 + REPEAT_GROWTH·ln(n); "
            "sublinear, so the first occurrence dominates"
        ),
    )
    section.add(
        "co_occurrence_step",
        rules.CO_OCCURRENCE_STEP,
        kind="specification",
        source="proctoring.rules.CO_OCCURRENCE_STEP",
        note=(
            f"each distinct kind beyond the first scales the whole deduction by "
            f"this much, capped at {rules.CO_OCCURRENCE_CAP}"
        ),
    )

    # ---------------------------------------------------------------- #
    # 1. Per-kind deduction table
    # ---------------------------------------------------------------- #
    deduction_rows = []
    for kind, severity in sorted(_ALL_KINDS.items()):
        first, first_verdict = rules.compute_integrity_score(_events(kind, severity, 1))
        second, second_verdict = rules.compute_integrity_score(_events(kind, severity, 2))
        fifth, fifth_verdict = rules.compute_integrity_score(_events(kind, severity, 5))
        deduction_rows.append(
            {
                "kind": kind,
                "severity": severity,
                "weight": rules.SEVERITY_WEIGHTS[severity],
                "score_after_1": first,
                "verdict_after_1": first_verdict,
                "cost_of_first": 100 - first,
                "cost_of_second": first - second,
                "score_after_5": fifth,
                "verdict_after_5": fifth_verdict,
                "verdict_after_2": second_verdict,
            }
        )
    section.tables["per_kind_deductions"] = deduction_rows
    section.add(
        "repeat_cost_is_sublinear",
        all(
            row["cost_of_second"] <= row["cost_of_first"]
            and (100 - row["score_after_5"]) < 5 * row["cost_of_first"]
            for row in deduction_rows
        ),
        kind="measured",
        source="proctoring.rules.compute_integrity_score",
        note=(
            "no kind's second occurrence costs more than its first, and five "
            "occurrences cost strictly less than five firsts — the ln(n) shape "
            "read off the function rather than off the docstring. Stated at this "
            "granularity on purpose: the marginal cost of the nth occurrence is "
            "strictly decreasing in the real-valued deduction, but the score is "
            "rounded to an integer before it is returned, so at low severity "
            "(weight 1.0) consecutive marginals collapse to the same integer and "
            "a strict per-step test would be measuring the rounding, not the rule"
        ),
    )

    # ---------------------------------------------------------------- #
    # 2. Decay curves: score vs n repeats, per severity
    # ---------------------------------------------------------------- #
    max_repeats = 12
    curves: dict[str, list[int]] = {}
    for severity in ("critical", "high", "medium", "low"):
        curves[severity] = [
            rules.compute_integrity_score(_events(f"probe_{severity}", severity, n))[0]
            for n in range(0, max_repeats + 1)
        ]
    section.tables["decay_curves"] = {"repeats": list(range(0, max_repeats + 1)), **curves}
    section.add(
        "floor_reached_at_repeats",
        {
            severity: next(
                (i for i, score in enumerate(values) if score == 0), None
            )
            for severity, values in curves.items()
        },
        kind="measured",
        source="proctoring.rules.compute_integrity_score",
        note=(
            "repeats of a single kind needed to hit 0 within the first "
            f"{max_repeats}; None throughout, which is the intended behaviour — "
            "one signal repeating cannot alone produce a zero"
        ),
    )

    # How far a *single* repeated kind can actually push the verdict. The ln(n)
    # term is unbounded, so "a lone signal can never flag" is not a theorem and
    # is not asserted as one; this measures the repeat count at which each
    # severity would cross each boundary, and the debounce interval turns that
    # count into a wall-clock duration a reader can sanity-check.
    escalation_rows = []
    for severity in ("critical", "high", "medium", "low"):
        crossings: dict[str, int | None] = {
            "leaves_clean": None,
            "reaches_flag": None,
            "reaches_zero": None,
        }
        for n in range(1, 4001):
            score, verdict = rules.compute_integrity_score(
                _events(f"solo_{severity}", severity, n)
            )
            if crossings["leaves_clean"] is None and verdict != "clean":
                crossings["leaves_clean"] = n
            if crossings["reaches_flag"] is None and verdict == "flag":
                crossings["reaches_flag"] = n
            if crossings["reaches_zero"] is None and score == 0:
                crossings["reaches_zero"] = n
                break
        escalation_rows.append(
            {
                "severity": severity,
                "weight": rules.SEVERITY_WEIGHTS[severity],
                **crossings,
                "minutes_of_signal_to_reach_flag": (
                    round(
                        crossings["reaches_flag"]
                        * rules.settings.proctoring.no_face_seconds
                        / 60.0,
                        1,
                    )
                    if crossings["reaches_flag"]
                    else None
                ),
            }
        )
    section.tables["single_kind_escalation"] = escalation_rows
    section.add(
        "a_lone_signal_needs_implausible_persistence_to_flag",
        all(
            row["reaches_flag"] is None or row["reaches_flag"] > 100
            for row in escalation_rows
            if row["severity"] != "critical"
        ),
        kind="measured",
        source="proctoring.rules.compute_integrity_score",
        note=(
            "below critical severity, one repeating kind needs more than 100 "
            "debounced events to reach 'flag'. Stated as a measured bound rather "
            "than an impossibility: ln(n) grows without limit, so the guarantee "
            "is that a broken camera runs out of interview before it runs out of "
            "score, not that it mathematically cannot get there"
        ),
    )

    # ---------------------------------------------------------------- #
    # 3. Verdict boundaries with *distinct* kinds (full weight each)
    # ---------------------------------------------------------------- #
    boundaries = {}
    for severity in ("critical", "high", "medium", "low"):
        left_clean = None
        left_review = None
        for n in range(0, 60):
            events = [
                {"kind": f"k{i}", "severity": severity} for i in range(n)
            ]
            score, verdict = rules.compute_integrity_score(events)
            if left_clean is None and verdict != "clean":
                left_clean = n
            if left_review is None and verdict == "flag":
                left_review = n
                break
        boundaries[severity] = {
            "distinct_events_to_leave_clean": left_clean,
            "distinct_events_to_reach_flag": left_review,
        }
    section.tables["verdict_boundaries"] = boundaries

    # ---------------------------------------------------------------- #
    # 4. Property checks — the claims the paper makes about c(E)
    # ---------------------------------------------------------------- #
    empty_score, empty_verdict = rules.compute_integrity_score([])
    section.add(
        "clean_interview_scores",
        empty_score,
        kind="measured",
        source="proctoring.rules.compute_integrity_score([])",
        note=f"verdict '{empty_verdict}' — no events means full trust",
    )

    one_critical_score, one_critical_verdict = rules.compute_integrity_score(
        [{"kind": rules.KIND_SUBSTITUTION, "severity": "critical"}]
    )
    section.add(
        "single_critical_score",
        one_critical_score,
        kind="measured",
        source="proctoring.rules.compute_integrity_score",
        note=(
            f"verdict '{one_critical_verdict}' — arithmetic alone leaves the score "
            "in the eighties, so the critical floor is what forces a human to look"
        ),
    )
    section.add(
        "critical_never_clean",
        one_critical_verdict != "clean",
        kind="measured",
        source="proctoring.rules.compute_integrity_score",
    )

    # Monotone non-increasing: adding an event never raises the score.
    monotone = True
    running: list[dict] = []
    previous = 100
    for kind, severity in _ALL_KINDS.items():
        for _ in range(3):
            running.append({"kind": kind, "severity": severity})
            score, _verdict = rules.compute_integrity_score(running)
            if score > previous:
                monotone = False
            previous = score
    section.add(
        "monotone_non_increasing",
        monotone,
        kind="measured",
        source="proctoring.rules.compute_integrity_score",
        note=f"checked over {len(running)} cumulative event additions",
    )
    section.add(
        "clamped_at_zero",
        rules.compute_integrity_score(
            [{"kind": f"k{i}", "severity": "critical"} for i in range(50)]
        )[0]
        == 0,
        kind="measured",
        source="proctoring.rules.compute_integrity_score",
    )

    # Determinism: the docstring promises recompute() can re-derive the score.
    sample = [
        {"kind": rules.KIND_NO_FACE, "severity": "high"},
        {"kind": rules.KIND_LOOKING_AWAY, "severity": "medium"},
        {"kind": rules.KIND_LOOKING_AWAY, "severity": "medium"},
        {"kind": rules.KIND_TAB_SWITCH, "severity": "high"},
    ]
    repeats = {rules.compute_integrity_score(sample) for _ in range(1000)}
    section.add(
        "deterministic_over_1000_calls",
        len(repeats) == 1,
        kind="measured",
        source="proctoring.rules.compute_integrity_score",
        note=f"all 1000 calls returned {repeats.pop()}",
    )

    # Order independence: events arrive out of order from three channels.
    import itertools

    orderings = {
        rules.compute_integrity_score(list(perm))
        for perm in itertools.permutations(sample)
    }
    section.add(
        "order_independent",
        len(orderings) == 1,
        kind="measured",
        source="proctoring.rules.compute_integrity_score",
        note=f"all {len(list(itertools.permutations(sample)))} orderings of a 4-event set agree",
    )

    # ---------------------------------------------------------------- #
    # 5. Worked scenarios a reviewer can check on paper
    # ---------------------------------------------------------------- #
    scenarios = [
        ("nothing detected", []),
        ("one glance away", [(rules.KIND_LOOKING_AWAY, "medium")]),
        (
            "distracted: 3 glances, 1 blur",
            [(rules.KIND_LOOKING_AWAY, "medium")] * 3
            + [(rules.KIND_WINDOW_BLUR, "medium")],
        ),
        (
            "flaky webcam: 6 no-face",
            [(rules.KIND_NO_FACE, "high")] * 6,
        ),
        (
            "camera turned away: 40 no-face",
            [(rules.KIND_NO_FACE, "high")] * 40,
        ),
        (
            "second person in frame twice",
            [(rules.KIND_EXTRA_PERSON, "high")] * 2,
        ),
        (
            "tab switching: 4 switches, 2 pastes",
            [(rules.KIND_TAB_SWITCH, "high")] * 4 + [(rules.KIND_PASTE, "medium")] * 2,
        ),
        (
            "substitution detected once",
            [(rules.KIND_SUBSTITUTION, "critical")],
        ),
        (
            "coached: phone + notes + background voice + 2 glances",
            [
                (rules.KIND_PHONE, "medium"),
                (rules.KIND_NOTES, "medium"),
                (rules.KIND_BACKGROUND_VOICE, "medium"),
                (rules.KIND_LOOKING_AWAY, "medium"),
                (rules.KIND_LOOKING_AWAY, "medium"),
            ],
        ),
    ]
    scenario_rows = []
    for label, pairs in scenarios:
        events = [{"kind": k, "severity": s} for k, s in pairs]
        score, verdict = rules.compute_integrity_score(events)
        scenario_rows.append(
            {
                "scenario": label,
                "events": len(events),
                "distinct_kinds": len({k for k, _ in pairs}),
                "integrity_score": score,
                "verdict": verdict,
            }
        )
    section.tables["scenarios"] = scenario_rows

    # ------------------------------------------------------------------ #
    # A calibration inversion the scenarios used to expose
    # ------------------------------------------------------------------ #
    # The first version of c(E) accumulated a deduction per event, halving
    # repeats of the same kind. Halving softened a repeated signal without
    # bounding it, so the total tracked how *many* events fired rather than how
    # serious the distinct signals were, and it reversed the ordering of the two
    # scenarios the design cares most about: the flaky webcam scored 79 and was
    # sent for review while the coached candidate scored 86 and was marked
    # clean. The function was rewritten to score each distinct kind at its worst
    # observed severity, with a sublinear persistence term and a co-occurrence
    # term. This block re-runs the same two hand-constructed scenarios against
    # the rewritten function and reports whether the inversion still reproduces,
    # so the repair is a measurement rather than a claim in a commit message.
    by_name = {row["scenario"]: row for row in scenario_rows}
    hardware = next(
        (r for name, r in by_name.items() if "flaky webcam" in name), None
    )
    coached = next((r for name, r in by_name.items() if "coached" in name), None)
    sustained = next(
        (r for name, r in by_name.items() if "camera turned away" in name), None
    )
    if hardware and coached:
        section.tables["calibration_inversion"] = {
            "hardware_fault_scenario": hardware["scenario"],
            "hardware_fault_score": hardware["integrity_score"],
            "hardware_fault_verdict": hardware["verdict"],
            "hardware_fault_events": hardware["events"],
            "hardware_fault_distinct_kinds": hardware["distinct_kinds"],
            "coaching_scenario": coached["scenario"],
            "coaching_score": coached["integrity_score"],
            "coaching_verdict": coached["verdict"],
            "coaching_events": coached["events"],
            "coaching_distinct_kinds": coached["distinct_kinds"],
            "score_gap": hardware["integrity_score"] - coached["integrity_score"],
            "score_gap_before_repair": 79 - 86,
        }
        section.add(
            "hardware_fault_scores_below_coaching",
            hardware["integrity_score"] < coached["integrity_score"],
            kind="measured",
            source="proctoring.rules.compute_integrity_score",
            note=(
                f"{hardware['scenario']} scores {hardware['integrity_score']} "
                f"({hardware['verdict']}) while {coached['scenario']} scores "
                f"{coached['integrity_score']} ({coached['verdict']}). True would "
                "mean the ordering is inverted — a failing webcam sent for human "
                "review while a candidate with a phone, written notes and another "
                "voice in the room is marked clean. It measured True against the "
                "original per-event deduction (79 vs 86) and measures False "
                "against the current per-kind one, which is the repair being "
                "re-audited rather than asserted."
            ),
        )
        section.add(
            "coaching_reaches_a_human",
            coached["verdict"] != "clean",
            kind="measured",
            source="proctoring.rules.compute_integrity_score",
            note=(
                "the half of the defect that the ordering alone does not fix: "
                "getting the two scores the right way round is worth nothing if "
                "the coached candidate is still labelled clean, because the "
                f"label is what the recruiter reads. Verdict is now "
                f"'{coached['verdict']}'."
            ),
        )
    if sustained:
        section.add(
            "sustained_absence_cannot_stay_clean",
            sustained["verdict"] != "clean" and sustained["integrity_score"] < 80,
            kind="measured",
            source="proctoring.rules.compute_integrity_score",
            note=(
                "a defect introduced by the obvious form of the repair and caught "
                "before it shipped. Scoring each kind purely at its worst severity "
                "makes persistence free: a candidate with the camera turned to the "
                f"wall for the whole interview ({sustained['events']} debounced "
                "no-face events) scored 88/clean under a bounded-saturation draft, "
                "which is a worse failure than the inversion being repaired — a "
                "mislabelling became a bypass. The shipped sublinear term puts it "
                f"at {sustained['integrity_score']}/{sustained['verdict']}, the "
                "honest verdict for a signal that cannot tell a broken camera from "
                "an empty chair."
            ),
        )
    if hardware and coached:
        section.add(
            "verdict_is_advisory_not_a_penalty",
            True,
            kind="specification",
            source="core.ranking.final_score",
            note=(
                "why a mis-calibrated c(E) is a reporting defect rather than an "
                "unfair-outcome defect, and why the repair above changes no "
                "candidate's ranking: c(E) enters the fusion multiplicatively, so "
                "a low integrity score shrinks the interview's influence toward "
                "the resume prior and can never subtract from what the resume "
                "earned. Verified over 2100 (candidate, c(E)) pairs in "
                "m5_fusion_audit as s_final_stays_between_resume_and_trusted_"
                "score. The candidate was never scored down; the recruiter was "
                "shown a misleading label."
            ),
        )

    section.add(
        "detection_accuracy_of_cv_backends",
        None,
        kind="unavailable",
        source="proctoring/vision.py, proctoring/audio.py",
        needs=(
            "Labelled interview video. The scoring function above is exact; what "
            "is unmeasured is whether YuNet/SFace/MediaPipe/YOLO11n raise the "
            "right events from a real recording. That needs a recorded corpus "
            "with per-frame ground truth and cannot be simulated."
        ),
    )

    section.commentary = (
        "c(E) is fully characterised rather than sampled: it is pure, "
        "deterministic, order-independent and monotone, and its verdict "
        "boundaries are exact integers. The deduction is per distinct kind at "
        "its worst observed severity, with a sublinear persistence term and a "
        "co-occurrence term; the calibration inversion an earlier per-event "
        "version exhibited no longer reproduces, and neither does the bypass "
        "that the naive form of the repair would have opened. What remains "
        "unmeasured is upstream detection accuracy, which needs labelled video."
    )
    return section
