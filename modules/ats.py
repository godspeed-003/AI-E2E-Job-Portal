def compute_ats_score(resume_text: str, requirements: list) -> int:
    if not resume_text or not requirements:
        return 0
    text_lower = resume_text.lower()
    matched_count = 0
    for req in requirements:
        if req.lower() in text_lower:
            matched_count += 1
    
    score = (matched_count / len(requirements)) * 100
    return int(score)
