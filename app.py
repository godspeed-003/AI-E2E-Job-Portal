import streamlit as st
import json
import os
import uuid

from modules.parser import extract_text_from_pdf
from modules.cleaner import clean_text
from modules.ats import compute_ats_score
from modules.evaluator import evaluate_resume
from modules.storage import save_candidate_result, load_results_for_role
from modules.ranker import rank_candidates

st.set_page_config(page_title="AI Resume Screener", layout="wide")

st.markdown("""
<style>
    /* Increase base font size globally */
    html, body, [class*="css"], p, div, span, label, li, ul, ol, a, h1, h2, h3, h4, h5, h6 {
        font-size: 22px !important;
    }
</style>
""", unsafe_allow_html=True)

@st.cache_data
def load_data():
    with open(os.path.join("data", "companies.json"), "r") as f:
        companies = json.load(f)
    with open(os.path.join("data", "roles.json"), "r") as f:
        roles = json.load(f)
    return companies, roles

companies, roles = load_data()

st.title("AI Resume Screening System (MVP)")

mode = st.radio("Select View Mode", ["Candidate", "Company"], horizontal=True)

if mode == "Candidate":
    st.header("Upload Resume")
    
    role_options = {r["title"]: r for r in roles}
    selected_role_title = st.selectbox("Select Role", list(role_options.keys()))
    selected_role = role_options[selected_role_title]
    
    company_info = next((c for c in companies if c["company_id"] == selected_role["company_id"]), {})
    
    st.write(f"**Company**: {company_info.get('name', 'Unknown')}")
    st.write(f"**Job Description**: {selected_role.get('job_description', '')}")
    
    uploaded_file = st.file_uploader("Choose a PDF resume", type="pdf")
    
    if uploaded_file is not None and st.button("Submit Application"):
        with st.spinner("Processing..."):
            temp_path = f"temp_{uuid.uuid4().hex}.pdf"
            try:
                with open(temp_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                
                raw_text = extract_text_from_pdf(temp_path)
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            
            if not raw_text.strip():
                st.error("Could not extract text from the PDF. Please try another file.")
            else:
                clean_resume_text = clean_text(raw_text)
                
                ats_score = compute_ats_score(clean_resume_text, selected_role["requirements"])
                st.write(f"**ATS Score**: {ats_score}")
                
                if ats_score < 40:
                    st.error("Status: Rejected (ATS Score < 40)")
                else:
                    st.info("ATS Passed. Running Llama Evaluation...")
                    eval_results = evaluate_resume(clean_resume_text, selected_role, company_info)
                    
                    if not eval_results:
                        st.error("Failed to get evaluation from Llama.")
                    else:
                        import hashlib
                        candidate_id = f"resume_{hashlib.md5(raw_text.encode(errors='ignore')).hexdigest()[:8]}"
                        result_doc = {
                            "candidate_id": candidate_id,
                            "name": eval_results.get("candidate_name", "Unknown Applicant"), 
                            "role_id": selected_role["role_id"],
                            "ats_score": ats_score,
                            "llm_score": eval_results.get("total_score", 0),
                            "max_score": 25,
                            "alignment_score": eval_results.get("alignment_score", 0),
                            "strengths": eval_results.get("strengths", []),
                            "weaknesses": eval_results.get("weaknesses", []),
                            "reason": eval_results.get("reason", ""),
                            "status": "Shortlisted" if eval_results.get("total_score", 0) >= 15 else "Under Review"
                        }
                        
                        save_candidate_result(selected_role["role_id"], result_doc)
                        
                        st.success(f"Status: {result_doc['status']}")
                        st.write(f"**LLM Score**: {result_doc['llm_score']}/25")
                        st.write(f"**Alignment**: {result_doc['alignment_score']}")
                        
elif mode == "Company":
    st.header("Company Dashboard")
    
    role_options = {r["title"]: r for r in roles}
    selected_role_title = st.selectbox("Select Role", list(role_options.keys()))
    selected_role = role_options[selected_role_title]
    company_info = next((c for c in companies if c["company_id"] == selected_role["company_id"]), {})
    
    st.subheader(company_info.get("name", "Unknown"))
    st.caption("Culture: " + ", ".join(company_info.get("culture", [])))
    
    results = load_results_for_role(selected_role["role_id"])
    
    if not results:
        st.info("No candidates found for this role.")
    else:
        ranked_results = rank_candidates(results)
        
        for idx, res in enumerate(ranked_results):
            with st.expander(f"Rank {idx+1}: {res.get('name', 'Applicant')} - ATS: {res.get('ats_score')}, LLM: {res.get('llm_score')} ({res.get('status')})"):
                st.write(f"**Alignment Score**: {res.get('alignment_score')}")
                st.write("**Strengths**:")
                for s in res.get("strengths", []):
                    st.write(f"- {s}")
                st.write("**Weaknesses**:")
                for w in res.get("weaknesses", []):
                    st.write(f"- {w}")
                st.write(f"**Reasoning**: {res.get('reason', '')}")
