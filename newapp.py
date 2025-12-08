import streamlit as st
import pandas as pd
from newats_engine import (
    rank_candidates,
    generate_compliant_feedback,
    extract_text_from_pdf,
    extract_text_from_docx,
    clean_and_structure_resume,
    compute_fit_score,
    rewrite_resume,
    client,  # reuse OpenAI client from ats_engine
)

# ==============================
# LOGIN MOCK FOR PROTOTYPE
# ==============================

def login_mock():
    """
    Temporary login for prototype.
    Replace later with real auth (e.g. SSO).
    """
    st.title("Login to Compliant ATS")
    st.write("Use this simple login while prototyping.")

    role = st.radio("I am logging in as:", ["Recruiter", "Applicant"])
    user_name = st.text_input("Your name")

    if st.button("Continue"):
        if not user_name.strip():
            st.error("Please enter a name.")
        else:
            st.session_state.auth = {
                "is_authenticated": True,
                "user_name": user_name.strip(),
                "role": role,
            }
            st.success(f"Welcome, {user_name.strip()}! Logged in as {role}.")
            st.rerun()


# ==============================
# APPLICANT LIST FEEDBACK HELPER
# ==============================

def generate_rejection_email(job_description: str, cleaned_resume: str, candidate_name: str = "the candidate") -> str:
    """
    Generates a fully formatted, legally compliant rejection email
    based on JD + cleaned resume content.
    """
    
    system_prompt = """
    You are an Expert Resume Consultant and Compliance Officer. 
    Your job is to generate a *polite, professional rejection email* that is fully legally compliant.

    ⚠️ Your output MUST ALWAYS be a COMPLETE EMAIL.  
    ⚠️ NOT a list.  
    ⚠️ NOT bullet points alone.  
    ⚠️ NOT freeform commentary.  
    Only a polished email with the structure below.

    ------------------------------------------
    EMAIL FORMAT (YOU MUST FOLLOW THIS EXACTLY)
    ------------------------------------------

    Subject: Feedback on Your Application

    Hi [Candidate Name],

    Thank the candidate sincerely for applying.  
    Explain that after reviewing their application in relation to the job description, 
    the company will not be moving forward.  
    Keep the tone neutral, factual, and professional.

    Then provide TWO SECTIONS:

    ### 🔎 Key Areas of Alignment
    • 2–3 bullet points listing the candidate’s strengths that *directly match the job description*.

    ### 🎯 Key Areas to Strengthen
    • 5–7 bullet points describing hard-skill or experience gaps that prevented progression.
    • Reference only skills, tools, systems, technical depth, demonstrated experience, 
      or missing measurable outcomes.
    • Be specific, factual, and based ONLY on the resume + job description.

    Closing paragraph:
    Thank them again for their interest.  
    Wish them success in their job search.  
    Sign off as “HR Team”.

    ------------------------------------------
    LEGAL COMPLIANCE RULES (MANDATORY)
    ------------------------------------------
    - DO NOT mention personality, attitude, soft skills, or cultural fit.
    - DO NOT reference protected traits (gender, age, race, health, etc).
    - DO NOT speculate about ability or experience.
    - ONLY refer to job-related, demonstrable hard skills or experience.
    """

    user_prompt = f"""
    Candidate Name: {candidate_name}

    Job Description:
    {job_description}

    Cleaned Resume:
    {cleaned_resume}

    Write the rejection email following the exact required format.
    """

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.0  # deterministic, consistent output
    )

    return response.choices[0].message.content



# ==============================
# PAGE CONFIG
# ==============================

st.set_page_config(page_title="Compliant ATS Prototype", layout="wide")


# ==============================
# AUTH INITIALISATION
# ==============================

if "auth" not in st.session_state:
    st.session_state.auth = {
        "is_authenticated": False,
        "user_name": None,
        "role": None,
    }

if not st.session_state.auth["is_authenticated"]:
    # Show login screen only
    login_mock()
    st.stop()

# If we get here, user is logged in
role = st.session_state.auth["role"]
user_name = st.session_state.auth["user_name"]


# ==============================
# SIDEBAR: USER + LEGAL INFO
# ==============================

with st.sidebar:
    st.title("ATS Prototype")
    st.markdown(f"**Logged in as:** {user_name}")
    st.markdown(f"**Role:** {role}")

    if st.button("Log out"):
        st.session_state.clear()
        st.rerun()

    with st.expander("Legal Compliance Info"):
        st.markdown(
            """
            - This tool generates **draft** feedback only.  
            - All emails must be **reviewed and approved by HR** before sending.  
            - Feedback is restricted to **job-related, hard-skill-based** criteria.  
            - The system is designed to avoid references to personality, age, gender,
              culture fit, or any protected characteristic.
            """
        )


# ==============================
# APP HEADER
# ==============================

st.title("⚖️ Compliant ATS Prototype")

if role == "Recruiter":
    st.subheader("Recruiter Dashboard – Rank Candidates & Generate Compliant Feedback")
    st.warning(
        "Legal Notice: All generated feedback must be reviewed by HR before sending to candidates. "
        "This system provides drafts only."
    )
elif role == "Applicant":
    st.subheader("Applicant Dashboard – Check Fit, Get Feedback & Optimise Your Resume")


# ====================================================
# RECRUITER MODE
# ====================================================

if role == "Recruiter":
    tab1, tab2, tab3 = st.tabs(["⚙️ Setup & Upload", "📊 Ranking & Scores", "📧 Feedback Generator"])

    if "ranked_data" not in st.session_state:
        st.session_state["ranked_data"] = None
        st.session_state["job_description"] = ""

    # ---------- TAB 1: Setup & Upload ----------
    with tab1:
        st.header("1. Define Job & Gather Resumes")

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Job Description")
            job_description = st.text_area(
                "Paste the Full Job Description Here:",
                height=300,
                key="job_desc_input_recruiter",
                value=st.session_state.get(
                    "job_description",
                    (
                        "We need a Chief Financial Officer (CFO). Must have CPA certification. "
                        "Experience managing large corporate budgets. Strategic financial planning."
                    ),
                ),
            )
            st.session_state["job_description"] = job_description

        with col2:
            st.subheader("Candidate Resumes")
            uploaded_files = st.file_uploader(
                "Upload Resumes (PDF, DOCX, and DOC supported):",
                type=["pdf", "docx", "doc"],
                accept_multiple_files=True,
            )

        st.markdown("---")

        if uploaded_files and st.button("🚀 Run Ranking Engine", type="primary"):
            if not job_description:
                st.error("Please paste the Job Description before running the engine.")
                st.stop()

            with st.spinner("Processing files, cleaning with AI, and running vector embedding analysis..."):
                candidate_list_for_ranking = []

                for file in uploaded_files:
                    filename = file.name.lower()
                    raw_resume_text = ""

                    if filename.endswith(".pdf"):
                        raw_resume_text = extract_text_from_pdf(file)
                    elif filename.endswith(".docx") or filename.endswith(".doc"):
                        raw_resume_text = extract_text_from_docx(file)
                    else:
                        st.warning(f"Unsupported file type for {file.name}. Skipping.")
                        continue

                    if raw_resume_text:
                        clean_resume_text = clean_and_structure_resume(raw_resume_text)
                        candidate_list_for_ranking.append(
                            {
                                "name": file.name,
                                "resume": clean_resume_text,
                            }
                        )

                if candidate_list_for_ranking:
                    st.info(f"Successfully processed and cleaned {len(candidate_list_for_ranking)} resumes.")

                    ranking_results = rank_candidates(job_description, candidate_list_for_ranking)
                    st.session_state["ranked_data"] = ranking_results
                    st.success("Ranking Complete! See the **Ranking & Scores** tab.")
                else:
                    st.warning("No valid files were processed.")

    # ---------- TAB 2: Ranking & Scores ----------
    with tab2:
        st.header("2. Candidate Ranking Results")

        if st.session_state.get("ranked_data") is not None:
            ranking_results = st.session_state["ranked_data"]

            df = pd.DataFrame(ranking_results)
            df["Score"] = (df["score"] * 100).round(1).astype(str) + "%"
            df_display = df[["name", "Score"]].rename(columns={"name": "Candidate"})

            st.subheader("Semantic Match Scoreboard")
            st.dataframe(df_display, use_container_width=True)

            st.info("The table is sorted by score (highest match first).")

            st.subheader("Review Cleaned Resume Text")
            candidate_names = [r["name"] for r in ranking_results]
            selected_name = st.selectbox("Select Candidate to Review:", candidate_names)

            selected_candidate = next((r for r in ranking_results if r["name"] == selected_name), None)

            if selected_candidate:
                with st.expander(f"Cleaned Resume Text for {selected_name}"):
                    st.code(selected_candidate["resume"], language="markdown")
        else:
            st.warning("Please run the ranking engine in the 'Setup & Upload' tab first.")

    # ---------- TAB 3: Feedback Generator ----------
    with tab3:
        st.header("3. Generate Compliant Rejection Feedback")

        if st.session_state.get("ranked_data") is not None:
            ranking_results = st.session_state["ranked_data"]
            job_description = st.session_state["job_description"]

            # Lowest ranking candidate
            candidate_to_reject = ranking_results[-1]

            # Extract name without file extension
            candidate_name = (
                candidate_to_reject["name"]
                .replace(".pdf", "")
                .replace(".docx", "")
                .replace(".doc", "")
            )

            st.info(
                f"Targeting **{candidate_name}** "
                f"(Lowest Score: {(candidate_to_reject['score'] * 100):.1f}%) for compliant feedback."
            )

            if st.button(f"✍️ Generate Rejection Email for {candidate_name}"):
                with st.spinner("Generating compliant, hard-skill-based email..."):
                    feedback_draft = generate_rejection_email(
                        job_description=job_description,
                        cleaned_resume=candidate_to_reject["resume"],
                        candidate_name=candidate_name,
                    )

                st.subheader("📧 Draft Email (Review Required)")
                st.code(feedback_draft, language="text")

                if st.checkbox("I confirm this feedback is legally safe and accurate."):
                    st.success("✅ Email approved and ready to send.")

                    st.download_button(
                        label="📩 Download Email Draft",
                        data=feedback_draft,
                        file_name=f"Rejection_Email_{candidate_name}.txt",
                        mime="text/plain",
                    )
        else:
            st.warning("Please run the ranking engine in the 'Setup & Upload' tab first.")


# ====================================================
# APPLICANT MODE
# ====================================================

elif role == "Applicant":
    st.markdown(
        "Upload your resume and paste the job description to get your ATS fit score, "
        "a list of actionable improvements, and a suggested AI-optimised resume version."
    )

    col1, col2 = st.columns(2)

    with col1:
        jd_applicant = st.text_area(
            "Paste the Job Description:",
            height=260,
            key="jd_applicant_input",
            placeholder="Paste the job description you are applying for...",
        )

    with col2:
        resume_file = st.file_uploader(
            "Upload Your Resume (PDF, DOCX, or DOC):",
            type=["pdf", "docx", "doc"],
            key="applicant_uploader",
        )
        manual_resume_text = st.text_area(
            "Or paste your resume text here:",
            height=260,
            key="manual_applicant_text",
            placeholder="If you don't have a file, paste your resume content here...",
        )

    analyze_button = st.button("🔍 Analyse & Improve My Resume", type="primary")

    if analyze_button:
        if not jd_applicant:
            st.error("Please paste the Job Description first.")
            st.stop()

        # Extract resume text
        raw_resume = ""
        if resume_file is not None:
            filename = resume_file.name.lower()
            if filename.endswith(".pdf"):
                raw_resume = extract_text_from_pdf(resume_file)
            elif filename.endswith(".docx") or filename.endswith(".doc"):
                raw_resume = extract_text_from_docx(resume_file)
            else:
                st.error("Unsupported file type. Please upload a PDF, DOCX, or DOC file.")
                st.stop()
        elif manual_resume_text.strip():
            raw_resume = manual_resume_text.strip()
        else:
            st.error("Please upload a resume (PDF/DOCX/DOC) or paste your resume text.")
            st.stop()

        with st.spinner("Analysing your resume and generating improvements..."):
            cleaned_resume = clean_and_structure_resume(raw_resume)
            score = compute_fit_score(jd_applicant, cleaned_resume)
           #--- applicant_feedback_list = generate_applicant_list_feedback(jd_applicant, cleaned_resume)----
            optimised_resume_md = rewrite_resume(jd_applicant, cleaned_resume)

        st.success("Analysis complete! Scroll down to see your results.")

        # 1. ATS Fit Score
        st.header("1. ATS Fit Score")
        score_percent = max(0.0, min(1.0, score)) * 100
        col_a, col_b = st.columns([1, 3])
        with col_a:
            st.metric("Overall Match", f"{score_percent:.1f}%")
        with col_b:
            st.progress(score_percent / 100.0)

        st.caption(
            "This score is based on how closely your resume aligns with the job description using AI embeddings."
        )

        # 2. Actionable Feedback List
        st.header("2. Actionable Feedback List")
        st.markdown(
            "Use these objective, skill-based points to quickly edit and improve your resume's alignment."
        )
        st.markdown(applicant_feedback_list)

        # 3. Suggested Optimised Resume
        st.header("3. Suggested Optimised Resume")
        st.markdown(
            "This is an AI-enhanced version of your content focused on the key terms in the JD. "
            "**Review and verify carefully before using.**"
        )
        st.code(optimised_resume_md, language="markdown")

        st.download_button(
            label="📩 Download Optimised Resume (Markdown)",
            data=optimised_resume_md,
            file_name="optimised_resume.md",
            mime="text/markdown",
        )
