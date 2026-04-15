import json
import os

RESULTS_DIR = os.path.join("data", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

def save_candidate_result(role_id: str, result: dict):
    file_path = os.path.join(RESULTS_DIR, f"{role_id}.json")
    results = []
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                results = json.load(f)
        except Exception:
            results = []
    
    # Overwrite if duplicate
    found = False
    for i, res in enumerate(results):
        if res.get("candidate_id") == result.get("candidate_id"):
            results[i] = result
            found = True
            break
    if not found:
        results.append(result)
        
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

def load_results_for_role(role_id: str) -> list:
    file_path = os.path.join(RESULTS_DIR, f"{role_id}.json")
    if not os.path.exists(file_path):
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []
