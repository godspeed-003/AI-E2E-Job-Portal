import json
import os
import random

ROLES = {
    "amazon_data_engineer": {
        "names": ["Liam Smith", "Olivia Johnson", "Noah Williams", "Emma Brown", "Oliver Jones"],
        "strengths": ["Strong Python and SQL skills", "Experience with AWS redshift", "Built scalable ETL pipelines", "Good grasp of Pandas", "Excellent problem solving"],
        "weaknesses": ["Limited system design experience", "Needs more experience in streaming data", "No Docker experience", "Lacks Airflow knowledge", "Communication could be improved"]
    },
    "amazon_backend_dev": {
        "names": ["James Garcia", "Isabella Martinez", "William Rodriguez", "Sophia Hernandez", "Benjamin Lopez"],
        "strengths": ["Deep knowledge of Java and Spring", "Experience building REST APIs", "Understands distributed systems", "Solid database optimization skills", "Good at system design"],
        "weaknesses": ["React knowledge is basic", "Lacks Go experience", "Not familiar with Kubernetes", "Needs more caching expertise", "Sometimes overengineers solutions"]
    },
    "deloitte_hr": {
        "names": ["Aarav Patel", "Diya Sharma", "Vihaan Singh", "Aditi Rao", "Arjun Gupta"],
        "strengths": ["Excellent communication skills", "Great at conflict resolution", "Strong recruitment experience", "Good understanding of employee engagement", "Empathetic and professional"],
        "weaknesses": ["Less experience with HR software", "Needs better technical recruiting knowledge", "Struggles with large datasets", "Public speaking can improve", "New to the consulting sector"]
    },
    "deloitte_marketing": {
        "names": ["Riya Desai", "Krishna Verma", "Ananya Reddy", "Ishaan Joshi", "Kavya Iyer"],
        "strengths": ["SEO expert", "Creative content creator", "Strong social media presence", "Experience with successful ad campaigns", "Good at metric analysis"],
        "weaknesses": ["Lacks B2B marketing experience", "New to video marketing", "Needs more copywriting practice", "Event management is weak", "Not familiar with HubSpot"]
    },
    "deloitte_operations": {
        "names": ["Rohan Mehta", "Neha Kapoor", "Yash Ahuja", "Priya Das", "Kabir Nair"],
        "strengths": ["Detail-oriented", "Strong organizational skills", "Great at process optimization", "Reliable reporting", "Good team coordinator"],
        "weaknesses": ["Sometimes struggles with ambiguity", "Needs more leadership experience", "Advanced Excel skills lacking", "Slower in fast-paced tech shifts", "Limited budget management"]
    }
}

STATUSES = ["Shortlisted", "Under Review", "Under Review", "Shortlisted", "Rejected"]

def generate_dummies():
    os.makedirs(os.path.join("data", "results"), exist_ok=True)
    
    for role_id, role_data in ROLES.items():
        results = []
        for i in range(5):
            candidate_name = role_data["names"][i]
            
            # Make the first 2-3 good, last 2 weaker
            is_good = i < 3
            ats_score = random.randint(75, 95) if is_good else random.randint(30, 50)
            llm_score = random.randint(16, 25) if is_good else random.randint(5, 14)
            alignment_score = random.uniform(0.7, 0.95) if is_good else random.uniform(0.3, 0.6)
            
            s1 = random.choice(role_data["strengths"])
            s2 = random.choice([s for s in role_data["strengths"] if s != s1])
            w1 = random.choice(role_data["weaknesses"])
            w2 = random.choice([w for w in role_data["weaknesses"] if w != w1])
            reason = f"{candidate_name} aligns well with the role requirements due to {s1.lower()}. However, {w1.lower()} could be a potential risk."

            if ats_score < 40:
                status = "Rejected"
                llm_score = 0
                alignment_score = 0
                strengths = []
                weaknesses = []
                reason = "Candidate failed preliminary ATS screening. LLM evaluation bypassed."
            else:
                status = "Shortlisted" if llm_score >= 15 else "Under Review"
                strengths = [s1, s2]
                weaknesses = [w1, w2]
                alignment_score = round(alignment_score, 2)
            
            candidate = {
                "candidate_id": f"dummy_{role_id}_{i+1}",
                "name": candidate_name,
                "role_id": role_id,
                "ats_score": ats_score,
                "llm_score": llm_score,
                "max_score": 25,
                "alignment_score": alignment_score,
                "strengths": strengths,
                "weaknesses": weaknesses,
                "reason": reason,
                "status": status
            }
            results.append(candidate)
            
        file_path = os.path.join("data", "results", f"{role_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

if __name__ == "__main__":
    generate_dummies()
    print("Dummy data generated successfully.")
