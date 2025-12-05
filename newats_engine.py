import os
import io
from docx import Document
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from pypdf import PdfReader

# Optional dotenv import
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Try to import Streamlit if available
try:
    import streamlit as st
except ImportError:
    st = None


# -------------------------
# OPENAI CLIENT SETUP
# -------------------------
def get_openai_api_key() -> str:
    """Load API key from Streamlit secrets or environment variables."""
    if st is not None:
        try:
            return st.secrets["OPENAI_API_KEY"]
        except Exception:
            pass

    key = os.getenv("OPENAI_API_KEY")
    if key:
        return key

    msg = "OPENAI_API_KEY missing. Set it in Streamlit secrets or .env."
    if st is not None:
        st.error(msg)
        st.stop()
    raise RuntimeError(msg)


OPENAI_API_KEY = get_openai_api_key()

from openai import OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)


# ======================================================
# 1. PDF TEXT EXTRACTION
# ======================================================
def extract_text_from_pdf(uploaded_file):
    """Reads a Streamlit UploadedFile object and extracts raw text."""
    uploaded_file.seek(0)
    reader = PdfReader(io.BytesIO(uploaded_file.read()))
    text = ""
    for page in reader.pages:
        text += page.extract_text() or ""
    return text.strip()

# ======================================================
# 2. DOCX TEXT EXTRACTION
# ======================================================
def extract_text_from_docx(uploaded_file):
    """Reads a Streamlit UploadedFile object and extracts text from DOCX."""
    # Ensure we read from the beginning
    uploaded_file.seek(0)
    doc = Document(uploaded_file)
    text = []
    for para in doc.paragraphs:
        if para.text:
            text.append(para.text)
    return "\n".join(text).strip()



# ======================================================
# 2. CLEANING & STRUCTURING
# ======================================================
def clean_and_structure_resume(raw_resume_text):
    """Uses LLM to clean noise and apply section tags."""
    
    system_prompt = """
    You are an expert Document Processor. Clean noisy resume text and return structured sections:
    [SUMMARY], [SKILLS], [EXPERIENCE], [EDUCATION]
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": raw_resume_text},
            ],
            temperature=0.0,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error during cleaning: {e}"


# ======================================================
# 3. EMBEDDINGS + FIT SCORE + RANKING
# ======================================================
def get_embedding(text):
    text = text.replace("\n", " ")
    emb = client.embeddings.create(
        input=[text], model="text-embedding-3-small"
    ).data[0].embedding
    return emb


def compute_fit_score(job_description: str, resume_text: str) -> float:
    jd_vec = get_embedding(job_description)
    res_vec = get_embedding(resume_text)
    score = cosine_similarity([jd_vec], [res_vec])[0][0]
    return float(score)


def rank_candidates(job_description, candidates_data):
    jd_vec = get_embedding(job_description)
    results = []

    for c in candidates_data:
        res_vec = get_embedding(c["resume"])
        score = cosine_similarity([jd_vec], [res_vec])[0][0]
        results.append({"name": c["name"], "score": score, "resume": c["resume"]})

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ======================================================
# 4. FEEDBACK ENGINE
# ======================================================
def generate_compliant_feedback(job_description, candidate_resume):
    """Generate legally compliant rejection feedback."""
    system_prompt = """
    You are a Compliance Resume Consultant. Provide lawful, hard-skill-focused feedback only.
    """

    user_prompt = f"""
    JOB DESCRIPTION:
    {job_description}

    RESUME:
    {candidate_resume}
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error: {e}"


# ======================================================
# 5. RESUME REWRITE ENGINE
# ======================================================
def rewrite_resume(job_description: str, resume_text: str) -> str:
    """Rewrite resume for better alignment while staying truthful."""
    system_prompt = """
    You are an expert ATS Resume Writer. Maintain truth, improve clarity, 
    rephrase bullets, strengthen relevance, but do not invent experience.
    Output in Markdown.
    """

    prompt = f"""
    JOB DESCRIPTION:
    {job_description}

    ORIGINAL RESUME:
    {resume_text}

    Rewrite the resume and then list what changed and why.
    """

    try:
        res = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        return res.choices[0].message.content
    except Exception as e:
        return f"Error during rewrite: {e}"
