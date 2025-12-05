import streamlit as st
import pandas as pd
from docx import Document
from newats_engine import (
    rank_candidates,
    extract_text_from_pdf,
    extract_text_from_docx,
    clean_and_structure_resume,
    compute_fit_score,
    rewrite_resume,
    client,  # reuse OpenAI client from ats_engine
)

# ============================================================================
# AUTH SESSION STATE
# ============================================================================
if "auth" not in st.session_state:
    st.session_state.auth = {
        "is_authenticated": False,
        "user_name": None,
        "role": None,
    }


def login_mock():
    """
    Temporary login for prototype.
    Replace later with Google SSO.
    """
    st.title("Login to Compliant ATS")
    st.write("Use this simple login while prototyping.")

    user_name = st.text_input("Your name")
    if st.button("Continue"):
        if not user_name.strip():
            st.error("Please enter a name.")
        else:
            st.session_state.auth["is_authenticated"] = True
            st.session_state.auth["user_name"] = user_name.strip()
            from streamlit import rerun
            rerun()



def logout():
    st.session_state.auth = {
        "is_authenticated": False,
        "user_name": None,
        "role": None,
    }

    # Also clear other session-state items
    for key in ["ranked_data", "job_description"]:
        if key in st.session_state:
            del st.session_state[key]

    from streamlit import rerun
    



# ============================================================================
# LEGAL COMPLIANCE MODULE
# ============================================================================

class FeedbackComplianceChecker:
    """
    Ensures all generated feedback complies with employment law.
    Screens for prohibited terms and discriminatory language.
    """
    
    def __init__(self):
        # Prohibited terms that could indicate discrimination
        self.prohibited_terms = {
            # Age-related
            'age', 'young', 'old', 'mature', 'recent graduate', 'retirement',
            'youthful', 'elderly', 'senior', 'junior', 'experienced professional',
            
            # National origin
            'native', 'foreign', 'accent', 'immigrant', 'citizenship',
            'visa', 'work authorization', 'country of origin',
            
            # Gender
            'he', 'she', 'his', 'her', 'him', 'gender', 'man', 'woman',
            'masculine', 'feminine', 'lady', 'gentleman',
            
            # Disability
            'disability', 'handicap', 'disabled', 'able-bodied', 'medical condition',
            'health', 'accommodation',
            
            # Personal characteristics
            'culture fit', 'personality', 'enthusiasm', 'attitude', 'energy level',
            'passion', 'motivated', 'team player', 'ambitious',
            
            # Family/marital status
            'family', 'children', 'married', 'single', 'parent', 'spouse',
            'maternity', 'paternity',
            
            # Religion
            'religious', 'religion', 'faith', 'church', 'mosque', 'temple',
            
            # Race/ethnicity (contextual - may have false positives)
            'diverse', 'diversity', 'minority', 'majority',
            
            # Other protected characteristics
            'pregnant', 'pregnancy', 'veteran', 'military service',
        }
        
        # Context-aware exceptions (these are OK in specific contexts)
        self.allowed_contexts = {
            'experience': ['years of experience', 'work experience'],
            'technical': ['native code', 'native app', 'native development'],
        }
    
    def check_compliance(self, feedback_text: str) -> dict:
        """
        Check if feedback contains prohibited terms.
        
        Returns:
            dict with keys:
                - compliant (bool): Whether feedback passed
                - violations (list): List of found prohibited terms
                - severity (str): 'none', 'low', 'high'
                - recommendation (str): What to do next
        """
        feedback_lower = feedback_text.lower()
        violations = []
        
        for term in self.prohibited_terms:
            if term in feedback_lower:
                # Check if it's in an allowed context
                is_allowed = False
                for context_terms in self.allowed_contexts.values():
                    for context in context_terms:
                        if context in feedback_lower and term in context:
                            is_allowed = True
                            break
                
                if not is_allowed:
                    violations.append(term)
        
        # Determine severity
        severity = 'none'
        if violations:
            # High severity terms
            high_severity = {'age', 'gender', 'disability', 'race', 'religion', 
                           'pregnant', 'family', 'married', 'children'}
            if any(term in high_severity for term in violations):
                severity = 'high'
            else:
                severity = 'low'
        
        # Generate recommendation
        if severity == 'none':
            recommendation = "Feedback passed compliance check."
        elif severity == 'low':
            recommendation = "Review feedback for soft skills language. Consider focusing only on technical qualifications."
        else:
            recommendation = "CRITICAL: Feedback contains prohibited discriminatory language. Do not send. Regenerate with stricter constraints."
        
        return {
            'compliant': len(violations) == 0,
            'violations': violations,
            'severity': severity,
            'recommendation': recommendation
        }
    
    def sanitize_feedback(self, feedback_text: str) -> str:
        """
        Attempt to automatically remove problematic phrases.
        Note: This is a basic implementation. Human review still required.
        """
        lines = feedback_text.split('\n')
        sanitized_lines = []
        
        for line in lines:
            line_lower = line.lower()
            has_violation = any(term in line_lower for term in self.prohibited_terms)
            
            if not has_violation:
                sanitized_lines.append(line)
            else:
                # Skip this line and add a comment
                sanitized_lines.append("<!-- Line removed for compliance -->")
        
        return '\n'.join(sanitized_lines)


# ============================================================================
# FEEDBACK GENERATION FUNCTIONS
# ============================================================================

def generate_compliant_feedback_for_recruiter(job_description: str, cleaned_resume: str, 
                                              max_retries: int = 2) -> dict:
    """
    Generate compliant rejection feedback for recruiters to review.
    Includes automatic compliance checking and retry logic.
    
    Args:
        job_description: Full JD text
        cleaned_resume: Cleaned/structured resume text
        max_retries: Number of retries if compliance violations found
        
    Returns:
        dict with keys:
            - feedback: Generated feedback text (or None if failed)
            - compliant: Whether feedback passed compliance
            - violations: List of prohibited terms found
            - severity: Compliance severity level
            - recommendation: Action recommendation
            - error: Error message if generation failed
    """
    
    checker = FeedbackComplianceChecker()
    
    system_prompt = """You are a Technical Recruitment Specialist generating objective, skills-based feedback for candidates.

YOUR TASK:
Write a professional rejection email that provides specific, actionable feedback based ONLY on technical qualifications and job requirements.

STRICT REQUIREMENTS:

1. **Focus ONLY on Technical Skills & Experience**:
   - Specific technical skills mentioned in JD but missing/weak in resume
   - Years of experience with specific technologies
   - Certifications or credentials required by JD
   - Quantifiable metrics and results
   - Depth of expertise in required domains

2. **Be Specific and Evidence-Based**:
   ✓ "The role requires 5+ years of experience with AWS cloud architecture, but your resume demonstrates 2 years"
   ✓ "The JD specifies expertise in React and TypeScript; your resume shows jQuery and vanilla JavaScript"
   ✗ "You don't seem like a good fit for our team"
   ✗ "We're looking for someone with more enthusiasm"

3. **Provide Constructive Guidance**:
   - Suggest specific skills to develop
   - Recommend certifications that would strengthen candidacy
   - Point to gaps in quantifiable achievements

4. **ABSOLUTE PROHIBITIONS** (Legal Compliance):
   NEVER mention or reference:
   - Age, generation, or career stage (young/old/experienced/recent graduate)
   - Gender, pronouns, or gender-related terms
   - Race, ethnicity, national origin, or accent
   - Disability, health, or medical conditions
   - Family status, marital status, or children
   - Religion or religious practices
   - Personal characteristics: personality, culture fit, enthusiasm, attitude, energy
   - Soft skills: team player, passionate, motivated (focus only on demonstrated technical skills)

5. **Email Structure**:
   - Professional greeting
   - Brief thank you for application
   - 2-3 specific technical gaps (with JD references)
   - Constructive closing with encouragement
   - Professional sign-off

Keep tone respectful, objective, and focused entirely on job-related technical qualifications."""

    user_prompt = f"""JOB DESCRIPTION:
{job_description}

---

CANDIDATE RESUME:
{cleaned_resume}

---

Generate a professional rejection email following all requirements above. Focus exclusively on technical qualifications and objective criteria."""

    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2,  # Low temperature for consistent, professional output
                max_tokens=1000
            )
            
            feedback = response.choices[0].message.content
            
            # Check compliance
            compliance_result = checker.check_compliance(feedback)
            
            if compliance_result['compliant']:
                return {
                    'feedback': feedback,
                    'compliant': True,
                    'violations': [],
                    'severity': 'none',
                    'recommendation': 'Feedback passed compliance check.',
                    'error': None
                }
            else:
                # If not compliant and we have retries left, try again
                if attempt < max_retries:
                    # Add more explicit instructions for next attempt
                    system_prompt += f"\n\nIMPORTANT: Previous attempt included prohibited terms: {compliance_result['violations']}. Completely avoid these concepts."
                    continue
                else:
                    # Out of retries, return non-compliant feedback with warning
                    return {
                        'feedback': feedback,
                        'compliant': False,
                        'violations': compliance_result['violations'],
                        'severity': compliance_result['severity'],
                        'recommendation': compliance_result['recommendation'],
                        'error': None
                    }
                    
        except Exception as e:
            return {
                'feedback': None,
                'compliant': False,
                'violations': [],
                'severity': 'none',
                'recommendation': '',
                'error': f"API Error: {str(e)}"
            }
    
    return {
        'feedback': None,
        'compliant': False,
        'violations': [],
        'severity': 'none',
        'recommendation': '',
        'error': "Failed to generate compliant feedback after maximum retries"
    }


def generate_applicant_feedback(job_description: str, cleaned_resume: str) -> str:
    """
    Generate actionable improvement feedback for applicants.
    This is less restrictive since it's for self-improvement, not rejection.
    
    Args:
        job_description: Full JD text
        cleaned_resume: Cleaned/structured resume text
        
    Returns:
        String containing structured feedback or error message
    """
    
    system_prompt = """You are an Expert Resume Coach helping candidates improve their resumes.

YOUR TASK:
Provide specific, actionable advice to help the candidate strengthen their resume's alignment with the job description.

INSTRUCTIONS:

1. **Identify Specific Gaps**:
   - Compare required skills in JD vs. demonstrated skills in resume
   - Look for missing technical competencies
   - Note where quantifiable results could be added
   - Check for missing relevant certifications or credentials

2. **Be Constructive and Specific**:
   ✓ "The JD emphasizes Docker/Kubernetes experience. Consider adding a project section highlighting container orchestration work, even if it was a side project."
   ✓ "You mention 'improved performance' but don't quantify it. Add metrics like '40% reduction in load time' or 'scaled to 10K concurrent users.'"
   ✗ "You need more experience"
   ✗ "Your resume doesn't show passion"

3. **Focus on Skills and Achievements**:
   - Technical skills and tools
   - Quantifiable accomplishments
   - Relevant certifications
   - Project complexity and scope
   - Leadership and ownership of technical initiatives

4. **Provide Actionable Steps**:
   Each suggestion should include:
   - What's missing or weak
   - Why it matters for this role
   - How to improve it (specific action)

5. **Output Format**:
   Return a bulleted list of 3-6 specific improvements:
   
   • **[Skill/Area]**: [What's missing/weak]. [Specific suggestion with example].
   
   Example:
   • **Cloud Infrastructure Details**: The JD requires AWS expertise with EC2, RDS, and Lambda, but your resume only mentions "cloud experience" generically. Add a technical skills section listing specific AWS services you've used, or create a project highlighting AWS architecture you've designed.

Keep feedback objective, skills-focused, and empowering. Avoid any comments on personality, soft skills, or non-technical attributes."""

    user_prompt = f"""JOB DESCRIPTION:
{job_description}

---

CANDIDATE RESUME:
{cleaned_resume}

---

Provide a bulleted list of specific, actionable improvements to help this candidate strengthen their resume for this role."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,
            max_tokens=1000
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error generating feedback: {str(e)}"


# ============================================================================
# STREAMLIT UI
# ============================================================================

# ============================================================================
# STREAMLIT UI – REDESIGNED
# ============================================================================

st.set_page_config(
    page_title="Compliant ATS Prototype",
    layout="wide",
    page_icon="⚖️",
)

# ============================================================================
# LOGIN GATE
# ============================================================================
if not st.session_state.auth["is_authenticated"]:
    login_mock()
    st.stop()   # prevents rest of UI from rendering



# ---------- Global styling ----------
st.markdown(
    """
    <style>
    /* Make main area a bit wider and cleaner */
    .main .block-container {
        padding-top: 2rem;
        padding-bottom: 3rem;
        padding-left: 3rem;
        padding-right: 3rem;
    }

    .app-header {
        font-size: 2.3rem;
        font-weight: 700;
        margin-bottom: 0.25rem;
    }

    .app-subtitle {
        font-size: 0.95rem;
        color: #9ca3af;
        margin-bottom: 1.5rem;
    }

    .step-pills span {
        display:inline-block;
        padding: 0.22rem 0.75rem;
        border-radius: 999px;
        font-size: 0.75rem;
        margin-right: 0.4rem;
        margin-bottom: 0.25rem;
        border: 1px solid #374151;
        background: #020617;
        color: #e5e7eb;
    }

    .card {
        background: #020617;
        border-radius: 14px;
        padding: 1.2rem 1.4rem;
        border: 1px solid #1f2937;
        margin-bottom: 1rem;
    }

    .card-header {
        font-weight: 600;
        margin-bottom: 0.4rem;
    }

    .subtle-label {
        font-size: 0.8rem;
        color: #9ca3af;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }

    .score-badge {
        display:inline-block;
        padding: 0.15rem 0.65rem;
        border-radius: 999px;
        background:#111827;
        border:1px solid #374151;
        font-size:0.8rem;
        color:#e5e7eb;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------- Sidebar ----------
with st.sidebar:
    st.markdown("### ATS Prototype")
    st.caption(f"Signed in as **{st.session_state.auth['user_name']}**")

    # ---- ROLE SELECTION (POST LOGIN) ----
    role = st.radio(
        "I am a:",
        ["Recruiter", "Applicant"],
        index=0 if st.session_state.auth["role"] != "Applicant" else 1,
    )
    st.session_state.auth["role"] = role

    st.markdown("---")
    st.button("Logout")
    logout()

    st.markdown("---")
    with st.expander("ℹ️ Legal Compliance Info", expanded=False):
        st.markdown("""
            **This system includes:**
            - Automated screening for discriminatory language  
            - Focus on job-related technical qualifications  
            - HR must review all feedback  
        """)



# ---------- Top header ----------
user_name = st.session_state.auth["user_name"]
st.markdown(f"### Welcome, **{user_name}** 👋")

st.markdown('<div class="app-header">⚖️ Compliant ATS Prototype</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="app-subtitle">AI to rank candidates, generate feedback, and support applicants — with legal compliance baked in.</div>',
    unsafe_allow_html=True,
)

# Give a quick visual map of the flow depending on role
role = st.session_state.auth["role"]
if role == "Recruiter":
    st.markdown(
        """
        <div class="step-pills">
            <span>1 · Paste job description</span>
            <span>2 · Upload resumes</span>
            <span>3 · Run ranking engine</span>
            <span>4 · Review & generate feedback</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        """
        <div class="step-pills">
            <span>1 · Paste job description</span>
            <span>2 · Upload or paste resume</span>
            <span>3 · View ATS fit score</span>
            <span>4 · Apply suggestions & download</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("---")


# ============================================================================
# RECRUITER MODE
# ============================================================================
role = st.session_state.auth["role"]
if role == "Recruiter":
    st.markdown("#### Recruiter Workspace · Rank candidates & create legally-safe feedback")
    st.warning(
        "⚠️ **Legal Notice:** This tool generates *draft* feedback only. "
        "All decisions and communications must be reviewed and approved by HR."
    )

    # Initialise session state
    if "ranked_data" not in st.session_state:
        st.session_state["ranked_data"] = None
    if "job_description" not in st.session_state:
        st.session_state["job_description"] = ""

    # Tabs for process stages
    tab_setup, tab_ranking, tab_feedback = st.tabs(
        ["1️⃣ Setup & Upload", "2️⃣ Ranking & Scores", "3️⃣ Feedback Generator"]
    )

    # ---------------- TAB 1: Setup & upload ----------------
    with tab_setup:
        st.markdown('<div class="card"><div class="card-header">Define the role & gather resumes</div>', unsafe_allow_html=True)

        col_jd, col_cv = st.columns([1.1, 1])

        with col_jd:
            st.markdown('<p class="subtle-label">Job description</p>', unsafe_allow_html=True)
            job_description = st.text_area(
                " ",
                height=320,
                key="job_desc_input_recruiter",
                placeholder="Paste the full job description here...",
                value=st.session_state.get(
                    "job_description",
                    (
                        "We need a Chief Financial Officer (CFO). Must have CPA certification. "
                        "Experience managing large corporate budgets. Strategic financial planning."
                    ),
                ),
                label_visibility="collapsed",
            )
            st.session_state["job_description"] = job_description

        with col_cv:
            st.markdown('<p class="subtle-label">Candidate resumes</p>', unsafe_allow_html=True)
            uploaded_files = st.file_uploader(
                "Upload resumes (PDF, DOCX, DOC)",
                type=["pdf", "docx", "doc"],
                accept_multiple_files=True,
            )
            st.caption("You can upload multiple files at once (max ~200MB each).")

        st.markdown("</div>", unsafe_allow_html=True)  # close card

        run_cols = st.columns([3, 1])
        with run_cols[0]:
            st.info(
                "When you're ready, run the ranking engine. We'll clean each resume, embed it, "
                "and compute a semantic fit against the job description."
            )
        with run_cols[1]:
            start_ranking = st.button("🚀 Run ranking engine", type="primary", use_container_width=True)

        if start_ranking:
            if not job_description.strip():
                st.error("Please paste the job description before running the engine.")
                st.stop()

            if not uploaded_files:
                st.error("Please upload at least one resume file.")
                st.stop()

            with st.spinner("Processing resumes and running AI matching..."):
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
                    st.success("✅ Ranking complete. Open the **Ranking & Scores** tab to review.")
                else:
                    st.warning("No valid files were processed.")

    # ---------------- TAB 2: Ranking & scores ----------------
    with tab_ranking:
        st.markdown('<div class="card"><div class="card-header">Semantic match scoreboard</div>', unsafe_allow_html=True)

        ranking_results = st.session_state.get("ranked_data")
        if ranking_results is None:
            st.warning("Run the ranking engine first in **Setup & Upload**.")
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            df = pd.DataFrame(ranking_results)
            df["Match score"] = (df["score"] * 100).round(1)
            df_display = df[["name", "Match score"]].rename(
                columns={"name": "Candidate", "Match score": "Match score (%)"}
            )

            st.dataframe(
                df_display.sort_values("Match score (%)", ascending=False).reset_index(drop=True),
                use_container_width=True,
                hide_index=True,
            )
            st.caption("Scores are based on semantic similarity between the job description and the cleaned resume text.")

            st.markdown("</div>", unsafe_allow_html=True)

            st.markdown('<div class="card"><div class="card-header">Review cleaned resume</div>', unsafe_allow_html=True)
            candidate_names = [r["name"] for r in ranking_results]
            selected_name = st.selectbox("Select candidate", candidate_names)

            selected_candidate = next((r for r in ranking_results if r["name"] == selected_name), None)

            if selected_candidate:
                score_pct = selected_candidate["score"] * 100
                st.markdown(
                    f"**{selected_candidate['name']}**  "
                    f"<span class='score-badge'>Match: {score_pct:.1f}%</span>",
                    unsafe_allow_html=True,
                )
                with st.expander("View cleaned resume text", expanded=False):
                    st.code(selected_candidate["resume"], language="markdown")

            st.markdown("</div>", unsafe_allow_html=True)

    # ---------------- TAB 3: Feedback generator ----------------
    with tab_feedback:
        ranking_results = st.session_state.get("ranked_data")
        job_description = st.session_state.get("job_description", "")

        if ranking_results is None:
            st.warning("Please run the ranking engine first in **Setup & Upload**.")
        else:
            st.markdown('<div class="card"><div class="card-header">Generate compliant rejection feedback</div>', unsafe_allow_html=True)
            st.info(
                "Select a candidate and generate a **draft** rejection email. "
                "The system will automatically screen for potentially non-compliant language."
            )

            candidate_names = [r["name"] for r in ranking_results]
            selected_candidate_name = st.selectbox(
                "Choose candidate",
                candidate_names,
                key="feedback_candidate_selector",
            )

            selected_candidate = next(
                (r for r in ranking_results if r["name"] == selected_candidate_name),
                None,
            )

            if selected_candidate:
                score_pct = selected_candidate["score"] * 100
                st.markdown(
                    f"Drafting feedback for **{selected_candidate['name']}**  "
                    f"<span class='score-badge'>Match: {score_pct:.1f}%</span>",
                    unsafe_allow_html=True,
                )

                generate_btn = st.button(
                    f"✍️ Generate compliant draft",
                    type="primary",
                    use_container_width=False,
                )

                if generate_btn:
                    with st.spinner("Generating legally-aware feedback and running compliance checks..."):
                        result = generate_compliant_feedback_for_recruiter(
                            job_description,
                            selected_candidate["resume"],
                        )

                    st.markdown("---")
                    st.subheader("📋 Compliance check")

                    if result["compliant"]:
                        st.success("✅ Passed – feedback cleared automated compliance screening.")
                    elif result["severity"] == "low":
                        st.warning("⚠️ Potential issues – manual review recommended.")
                        if result["violations"]:
                            st.write(f"**Detected terms:** {', '.join(result['violations'])}")
                        st.write(f"**Recommendation:** {result['recommendation']}")
                    else:
                        st.error("🚫 Critical issues – do **not** send this draft as-is.")
                        if result["violations"]:
                            st.write(f"**Detected terms:** {', '.join(result['violations'])}")
                        st.write(f"**Recommendation:** {result['recommendation']}")

                    if result["feedback"]:
                        st.markdown("---")
                        st.subheader("📧 Draft email (for HR review)")
                        st.info("Please review carefully. Edit as needed before sending to the candidate.")
                        st.code(result["feedback"], language="text")

                        st.markdown("##### HR review checklist")
                        col_a, col_b = st.columns(2)
                        with col_a:
                            check1 = st.checkbox("Focuses only on job-related qualifications")
                            check2 = st.checkbox("No discriminatory or sensitive language")
                            check3 = st.checkbox("Feedback is specific and evidence-based")
                        with col_b:
                            check4 = st.checkbox("Tone is professional and respectful")
                            check5 = st.checkbox("Aligned with criteria used for other candidates")
                            check6 = st.checkbox("Legal / HR review completed (if required)")

                        all_checked = all([check1, check2, check3, check4, check5, check6])

                        if all_checked:
                            st.success("✅ All review items confirmed. This draft can be approved.")
                            col_dl, col_ready = st.columns(2)
                            with col_dl:
                                st.download_button(
                                    label="📩 Download draft",
                                    data=result["feedback"],
                                    file_name=f"Feedback_{selected_candidate['name'].replace('.', '_')}.txt",
                                    mime="text/plain",
                                )
                            with col_ready:
                                if st.button("📧 Mark as ready to send", type="primary"):
                                    st.success(
                                        f"Feedback for **{selected_candidate['name']}** marked as approved and ready to send."
                                    )
                                    st.balloons()
                        else:
                            st.warning("Complete all checklist items before marking this draft as approved.")

                    elif result["error"]:
                        st.error(f"❌ Error while generating feedback: {result['error']}")
                        st.info("You can try again or contact support if the issue persists.")

            st.markdown("</div>", unsafe_allow_html=True)


# ============================================================================
# APPLICANT MODE
# ============================================================================
else:  # role == "Applicant"
    st.markdown("#### Applicant Workspace · Check your fit, get feedback & optimise your resume")

    st.markdown(
        """
        Use this space to understand how well your resume matches a specific job, 
        see objective improvement suggestions, and download an AI-optimised version to edit.
        """
    )

    # ---- Input card ----
    st.markdown('<div class="card"><div class="card-header">Provide job description & resume</div>', unsafe_allow_html=True)

    col_jd, col_cv = st.columns([1.1, 1])

    with col_jd:
        st.markdown('<p class="subtle-label">Job description</p>', unsafe_allow_html=True)
        jd_applicant = st.text_area(
            " ",
            height=260,
            key="jd_applicant_input",
            placeholder="Paste the job description you are applying for...",
            label_visibility="collapsed",
        )

    with col_cv:
        st.markdown('<p class="subtle-label">Your resume</p>', unsafe_allow_html=True)
        resume_file = st.file_uploader(
            "Upload your resume (PDF, DOCX, DOC)",
            type=["pdf", "docx", "doc"],
            key="applicant_uploader",
        )
        st.caption("Or paste the content below if you don't have a file.")

        manual_resume_text = st.text_area(
            " ",
            height=150,
            key="manual_applicant_text",
            placeholder="Paste your resume text here...",
            label_visibility="collapsed",
        )

    st.markdown("</div>", unsafe_allow_html=True)

    analyze_button = st.button("🔍 Analyze & improve my resume", type="primary")

    if analyze_button:
        if not jd_applicant.strip():
            st.error("Please paste the job description first.")
            st.stop()

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
            st.error("Please upload a resume file or paste your resume text.")
            st.stop()

        with st.spinner("Running ATS-style analysis and generating suggestions..."):
            cleaned_resume = clean_and_structure_resume(raw_resume)
            score = compute_fit_score(jd_applicant, cleaned_resume)
            applicant_feedback_list = generate_applicant_feedback(jd_applicant, cleaned_resume)
            optimized_resume_md = rewrite_resume(jd_applicant, cleaned_resume)

        st.success("Analysis complete. Scroll down to review your results.")

        # ---- 1. ATS fit score ----
        st.markdown('<div class="card"><div class="card-header">1. ATS Fit Score</div>', unsafe_allow_html=True)
        score_percent = max(0.0, min(1.0, score)) * 100

        col_a, col_b, col_c = st.columns([1, 3, 1])
        with col_a:
            st.metric("Overall match", f"{score_percent:.1f}%")
        with col_b:
            st.progress(score_percent / 100.0)
        with col_c:
            if score_percent >= 80:
                st.success("Strong match")
            elif score_percent >= 60:
                st.info("Good match")
            elif score_percent >= 40:
                st.warning("Moderate match")
            else:
                st.error("Weak match")

        st.caption(
            "This score estimates how closely your resume content aligns with the job description "
            "using text similarity. It’s a guide, not a guarantee."
        )
        st.markdown("</div>", unsafe_allow_html=True)

        # ---- 2. Actionable feedback ----
        st.markdown('<div class="card"><div class="card-header">2. Actionable improvement suggestions</div>', unsafe_allow_html=True)
        st.markdown(
            "These suggestions focus on **skills, experience, and evidence** you can emphasise or clarify "
            "to improve alignment with the role."
        )
        st.markdown(applicant_feedback_list)
        st.info("💡 Tip: Start by addressing the top 2–3 suggestions for the biggest impact.")
        st.markdown("</div>", unsafe_allow_html=True)

        # ---- 3. Optimised resume ----
        st.markdown('<div class="card"><div class="card-header">3. AI-optimised resume draft</div>', unsafe_allow_html=True)
        st.warning(
            "⚠️ **Important:** This is a draft based on your existing content. "
            "Carefully review and edit before using it in any application. "
            "Never add experience or credentials that are not true."
        )

        with st.expander("View optimised resume (Markdown format)", expanded=True):
            st.code(optimized_resume_md, language="markdown")

        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            st.download_button(
                label="📄 Download optimised resume (Markdown)",
                data=optimized_resume_md,
                file_name="optimized_resume.md",
                mime="text/markdown",
            )
        with col_dl2:
            st.download_button(
                label="📋 Download feedback list",
                data=applicant_feedback_list,
                file_name="resume_feedback.txt",
                mime="text/plain",
            )

        st.markdown("</div>", unsafe_allow_html=True)


# ============================================================================
# FOOTER
# ============================================================================
st.markdown("---")
st.caption(
    "⚖️ **Legal disclaimer:** This tool provides automated assistance only. "
    "All hiring decisions and candidate communications must be made by humans "
    "and comply with applicable employment laws. Consider consulting legal counsel "
    "for your specific policies and jurisdiction."
)
st.caption("Built by Adeola – HR, People Ops & AI-powered recruitment innovation.")
