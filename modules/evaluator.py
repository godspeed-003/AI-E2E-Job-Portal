import os
import json
import re
import requests

def evaluate_resume(resume_text: str, role_info: dict, company_info: dict) -> dict:
    prompt_path = os.path.join("prompts", "evaluation_prompt.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt_template = f.read()

    # We must use replace() here because the prompt template contains JSON braces mapping format specifiers
    prompt = prompt_template.replace("{job_description}", role_info.get("job_description", "")) \
                            .replace("{requirements}", ", ".join(role_info.get("requirements", []))) \
                            .replace("{culture}", ", ".join(company_info.get("culture", []))) \
                            .replace("{values}", ", ".join(company_info.get("values", []))) \
                            .replace("{resume_text}", resume_text)

    url = "http://localhost:11434/api/generate"
    payload = {
        "model": "llama3.1",
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.0
        }
    }
    
    try:
        response = requests.post(url, json=payload, timeout=120)
        response.raise_for_status()
        result = response.json()
        
        text = result.get("response", "")
        
        json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_match = re.search(r'{[\s\S]*}', text)
            json_str = json_match.group(0) if json_match else "{}"
            
        return json.loads(json_str)
    except Exception as e:
        print(f"Error evaluating resume with Ollama: {e}")
        return {}

def evaluate_interview(transcript: list) -> dict:
    """
    Evaluates the full interview transcript against 5 parameters on a 0-5 scale.
    Parameters: technical_depth, problem_solving, communication, culture_fit, practical_impact.
    """
    url = "http://localhost:11434/api/generate"
    
    transcript_text = ""
    for t in transcript:
        transcript_text += f"Q: {t['question']}\nA: {t['answer']}\n\n"
        
    prompt = f"""
You are an expert technical recruiter evaluating an interview transcript.
Transcript:
{transcript_text}

Evaluate the candidate on the following 5 parameters on a scale of 0 to 5:
1. technical_depth: Specificity of technical tools and implementation.
2. problem_solving: Clarity of decision-making under constraints.
3. communication: Conciseness, directness, and coherence.
4. culture_fit: Alignment with typical company values like ownership and teamwork.
5. practical_impact: Metrics and tangible results provided in answers.

Return strictly JSON matching this structure:
{{
    "criteria": {{
        "technical_depth": int,
        "problem_solving": int,
        "communication": int,
        "culture_fit": int,
        "practical_impact": int
    }},
    "total_score": int,
    "strengths": ["string"],
    "weaknesses": ["string"],
    "summary": "string"
}}
"""

    payload = {
        "model": "llama3.1",
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.0
        }
    }
    
    try:
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        
        text = result.get("response", "")
        
        json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_match = re.search(r'{[\s\S]*}', text)
            json_str = json_match.group(0) if json_match else "{}"
            
        return json.loads(json_str)
    except Exception as e:
        print(f"Error evaluating interview with Ollama: {e}")
        return {{}}

