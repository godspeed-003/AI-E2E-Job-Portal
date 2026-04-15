def rank_candidates(candidates: list) -> list:
    # Sort first by llm_score DESC, then by ats_score DESC
    return sorted(
        candidates,
        key=lambda x: (x.get("llm_score", 0), x.get("ats_score", 0)),
        reverse=True
    )
