import requests
import json

def generate_question(candidate_record: dict, role_info: dict, transcript: list) -> str:
    """
    Generates an interview question for the candidate based on the turn number and transcript.
    """
    url = "http://localhost:11434/api/generate"
    turn = len(transcript) + 1
    
    if turn == 1:
        weaknesses = ", ".join(candidate_record.get("weaknesses", []))
        prompt = f"""
You are an expert technical interviewer. You are interviewing {candidate_record.get('name', 'a candidate')} for the role of {role_info.get('title', 'Software Engineer')}.
The candidate's identified weaknesses compared to the job description are: {weaknesses}.
Ask ONE concise, open-ended question to probe their experience in these weak areas. Do not include greetings.
"""
    else:
        transcript_text = ""
        for t in transcript:
            transcript_text += f"Q: {t['question']}\nA: {t['answer']}\n\n"
            
        prompt = f"""
You are an expert technical interviewer following up on a candidate's previous responses using the STAR Framework (Situation, Task, Action, Result).
Candidate Name: {candidate_record.get('name', 'Candidate')}
Role: {role_info.get('title', 'Software Engineer')}

Previous Transcript:
{transcript_text}

Generate ONE concise follow-up question based on the latest answer. Probe for specific execution details, metrics, or challenges faced. Do not include greetings.
"""

    payload = {
        "model": "llama3.1",
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.7
        }
    }
    
    try:
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        return result.get("response", "").strip()
    except Exception as e:
        print(f"Error generating question: {e}")
        return "Could you provide more details about your previous experience?"
