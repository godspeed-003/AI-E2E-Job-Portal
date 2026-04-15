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
