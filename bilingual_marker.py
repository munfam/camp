import streamlit as st
from docx import Document
import google.generativeai as genai
import os
import csv
from io import StringIO
from datetime import datetime
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
import re

# --------------------------
# Postgres Connection
# --------------------------
DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "comprehensiondb")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

def get_db_conn():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )
    return conn

def init_db():
    conn = get_db_conn()
    cur = conn.cursor()
    # Students table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id SERIAL PRIMARY KEY,
        student_name TEXT UNIQUE NOT NULL,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)
    # Assessment attempts table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS attempts (
        id SERIAL PRIMARY KEY,
        student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
        date TIMESTAMP NOT NULL,
        sg_level TEXT NOT NULL,
        skill_tag TEXT NOT NULL,
        question TEXT,
        original_text TEXT,
        rewrite_text TEXT,
        original_score NUMERIC,
        rewrite_score NUMERIC,
        mark_result TEXT,
        rewrite_result TEXT,
        -- per skill subscores
        subscore_content NUMERIC,
        subscore_organisation NUMERIC,
        subscore_vocabulary NUMERIC,
        subscore_grammar NUMERIC,
        subscore_argumentation NUMERIC,
        subscore_criticalthinking NUMERIC,
        subscore_toneregister NUMERIC,
        subscore_evidencecitation NUMERIC
    );
    """)
    conn.commit()
    cur.close()
    conn.close()

# --------------------------
# Student CRUD
# --------------------------
def create_student(student_name):
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO students (student_name) VALUES (%s) RETURNING id;", (student_name,))
        student_id = cur.fetchone()[0]
        conn.commit()
        return student_id
    except psycopg2.IntegrityError:
        conn.rollback()
        return None
    finally:
        cur.close()
        conn.close()

def get_students():
    conn = get_db_conn()
    df = pd.read_sql("SELECT id, student_name, created_at FROM students ORDER BY student_name", conn)
    conn.close()
    return df

def get_student_id_by_name(name):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM students WHERE student_name = %s", (name,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None

def delete_student(name):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM students WHERE student_name = %s", (name,))
    conn.commit()
    cur.close()
    conn.close()

# --------------------------
# Attempt CRUD
# --------------------------
def save_attempt(student_name, record):
    sid = get_student_id_by_name(student_name)
    if sid is None:
        return
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO attempts (
            student_id, date, sg_level, skill_tag, question,
            original_text, rewrite_text, original_score, rewrite_score,
            mark_result, rewrite_result,
            subscore_content,
            subscore_organisation,
            subscore_vocabulary,
            subscore_grammar,
            subscore_argumentation,
            subscore_criticalthinking,
            subscore_toneregister,
            subscore_evidencecitation
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        sid,
        record["date"],
        record["sg_level"],
        record["skill_tag"],
        "",
        record["original_text"],
        record["rewrite_text"],
        record["original_score"],
        record["rewrite_score"],
        record["mark_result"],
        record["rewrite_result"],
        record["subscore_content"],
        record["subscore_organisation"],
        record["subscore_vocabulary"],
        record["subscore_grammar"],
        record["subscore_argumentation"],
        record["subscore_criticalthinking"],
        record["subscore_toneregister"],
        record["subscore_evidencecitation"]
    ))
    conn.commit()
    cur.close()
    conn.close()

def get_attempts_by_student(student_name):
    sid = get_student_id_by_name(student_name)
    if sid is None:
        return pd.DataFrame()
    conn = get_db_conn()
    df = pd.read_sql("""
        SELECT date, sg_level, skill_tag, question, original_text, rewrite_text,
               original_score, rewrite_score, mark_result, rewrite_result,
               subscore_content,
               subscore_organisation,
               subscore_vocabulary,
               subscore_grammar,
               subscore_argumentation,
               subscore_criticalthinking,
               subscore_toneregister,
               subscore_evidencecitation
        FROM attempts WHERE student_id = %s ORDER BY date ASC
    """, conn, params=(sid,))
    conn.close()
    return df

def get_all_attempts():
    conn = get_db_conn()
    df = pd.read_sql("""
        SELECT s.student_name, a.*
        FROM attempts a
        JOIN students s ON a.student_id = s.id
    """, conn)
    conn.close()
    return df

# --------------------------
# Gemini & Helpers (Composition Marking)
# --------------------------
API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel("gemini-3.8-flash")

def read_docx(uploaded_file):
    doc = Document(uploaded_file)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)

def extract_total_score(text, label):
    try:
        for line in text.splitlines():
            if line.strip().startswith(label):
                val = re.search(r"(\d+(?:\.\d+)?)", line)
                if val:
                    return float(val.group(1))
    except Exception:
        return None

def extract_subscores(mark_text):
    sub = {
        "subscore_content": None,
        "subscore_organisation": None,
        "subscore_vocabulary": None,
        "subscore_grammar": None,
        "subscore_argumentation": None,
        "subscore_criticalthinking": None,
        "subscore_toneregister": None,
        "subscore_evidencecitation": None
    }
    patterns = [
        ("subscore_content", r"Content\s*:\s*(\d+(?:\.\d+)?)"),
        ("subscore_organisation", r"Organisation\s*:\s*(\d+(?:\.\d+)?)"),
        ("subscore_vocabulary", r"Vocabulary\s*:\s*(\d+(?:\.\d+)?)"),
        ("subscore_grammar", r"Grammar\s*:\s*(\d+(?:\.\d+)?)"),
        ("subscore_argumentation", r"Argumentation\s*:\s*(\d+(?:\.\d+)?)"),
        ("subscore_criticalthinking", r"Critical Thinking\s*:\s*(\d+(?:\.\d+)?)"),
        ("subscore_toneregister", r"Tone & Register\s*:\s*(\d+(?:\.\d+)?)"),
        ("subscore_evidencecitation", r"Evidence & Citation\s*:\s*(\d+(?:\.\d+)?)"),
    ]
    for key, pat in patterns:
        m = re.search(pat, mark_text)
        if m:
            sub[key] = float(m.group(1))
    return sub

def build_composition_marking_prompt(essay_text, feedback_lang, level, selected_skill):
    focus_note = ""
    if selected_skill != "All Skills":
        focus_note = f"""
IMPORTANT FOCUS: Your primary focus skill for feedback and recommendations is {selected_skill}.
You must still assess and give subscores for ALL 8 skills, but your feedback and recommendations should prioritise {selected_skill}.
"""

    prompt = f"""
You are an English composition examiner. Difficulty level: {level}.
Mark this student composition out of 10 total. Adjust marking standard to match level {level}.

Level expectations:
Primary Low: simple sentences acceptable, focus on basic ideas and simple grammar.
Primary High: expect descriptive details, better paragraph organisation.
Secondary Low: coherent plot, varied sentences, fewer grammar errors.
Secondary High: characterisation, theme awareness, mature vocabulary.
JC1 / JC2: Expect discursive/argumentative writing, critical analysis, nuanced reasoning, tight thesis, academic vocabulary.
Polytechnic: Expect practical argumentation, clear position, formal academic tone, logical evaluation of ideas.
University: Rigorous academic essay, deep critical analysis, balanced evaluation of multiple perspectives, precise thesis, coherent scholarly argument, awareness of assumptions, formal academic register.

{focus_note}

You MUST evaluate ALL 8 composition skills, give a subscore /10 for every skill:
1. Content: relevance, completeness, depth of ideas
2. Organisation: structure, paragraphing, logical flow, intro/conclusion
3. Vocabulary: word choice, precision, range, appropriateness
4. Grammar: syntax, tenses, subject-verb agreement, punctuation accuracy
5. Argumentation: thesis clarity, logical reasoning, counterarguments, consistent stance
6. Critical Thinking: analysis, questioning assumptions, balanced evaluation of viewpoints
7. Tone & Register: style matching audience/essay type, formality, consistency
8. Evidence & Citation: use of supporting examples, source integration, referencing awareness

Detect whether essay is English or Simplified Chinese. Write feedback in {feedback_lang}.
If feedback_lang = Auto, use same language as essay.

【Marking Rubric (Total 10 marks)】
1. Content & Story / Argument relevance (4 marks): relevance, ideas, depth of reasoning
2. Organisation & Paragraphing (2 marks): intro, body, conclusion, logical flow
3. Language & Vocabulary (2 marks): word choice, sentence variety, register appropriate for level
4. Grammar, spelling / characters & punctuation (2 marks): accuracy

Rules:
- Use age/level appropriate expectation. Higher levels require deeper analysis and more sophisticated language.
- Deduct marks for off-topic, grammar mistakes, weak structure.
- Reward sensory description, show-don’t-tell for lower levels; reward critical analysis and evaluation for JC/Poly/University.
- Explicitly comment on strengths and weaknesses for EVERY one of the 8 skills in your feedback.

Output strictly follow this EXACT format:
Total Score: [number]/10
Content: [number]/10
Organisation: [number]/10
Vocabulary: [number]/10
Grammar: [number]/10
Argumentation: [number]/10
Critical Thinking: [number]/10
Tone & Register: [number]/10
Evidence & Citation: [number]/10

Feedback: [detailed feedback covering all 8 skills]
Recommendations for improvement:
- tip1
- tip2
- tip3

Student Composition:
{essay_text}
"""
    return prompt

def build_composition_rewrite_check_prompt(essay_text, original_feedback, feedback_lang, level, selected_skill):
    focus_note = ""
    if selected_skill != "All Skills":
        focus_note = f"""
IMPORTANT FOCUS: Your primary focus skill for feedback is {selected_skill}.
You still assess all 8 skills and give subscores for all of them, but prioritise {selected_skill}.
"""
    prompt = f"""
You are composition examiner. Level: {level}.
Original feedback given to student:
{original_feedback}

Student has rewritten composition below.
Compare rewritten essay against previous feedback.
Identify which recommendations student followed, which were ignored.
Give new total score out of 10, plus subscore /10 for each of the 8 skills.
Write feedback in {feedback_lang}. If feedback_lang = Auto, match essay language.

{focus_note}

You MUST evaluate ALL 8 composition skills:
1. Content: relevance, completeness, depth of ideas
2. Organisation: structure, paragraphing, logical flow, intro/conclusion
3. Vocabulary: word choice, precision, range, appropriateness
4. Grammar: syntax, tenses, subject-verb agreement, punctuation accuracy
5. Argumentation: thesis clarity, logical reasoning, counterarguments, consistent stance
6. Critical Thinking: analysis, questioning assumptions, balanced evaluation of viewpoints
7. Tone & Register: style matching audience/essay type, formality, consistency
8. Evidence & Citation: use of supporting examples, source integration, referencing awareness

For JC1/JC2/Polytechnic: evaluate depth of argument, critical reasoning and formal academic register.
For University: evaluate scholarly depth, critical analysis, thesis precision and academic argument quality.
Assess improvements across all 8 skills.

Output format strictly:
New Total Score (rewritten): [number]/10
Content: [number]/10
Organisation: [number]/10
Vocabulary: [number]/10
Grammar: [number]/10
Argumentation: [number]/10
Critical Thinking: [number]/10
Tone & Register: [number]/10
Evidence & Citation: [number]/10

Recommendations successfully applied:
- point1
- point2
Recommendations NOT applied:
- point1
- point2
Improvement summary: [summary across all 8 skills]
Remaining areas to improve: [notes across all 8 skills]

Rewritten composition:
{essay_text}
"""
    return prompt

def generate_full_report(student_name, sg_level, skill_tag, original_text, mark_result, rewrite_text, rewrite_result):
    report = []
    report.append("==== Composition Assessment Report ====")
    report.append(f"Student Name: {student_name}")
    report.append(f"Difficulty Level: {sg_level}")
    report.append(f"Selected Skill Focus: {skill_tag}")
    report.append(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report.append("\n--- Original Student Composition ---")
    report.append(original_text if original_text else "No composition uploaded")
    report.append("\n--- Marking Result ---")
    report.append(mark_result if mark_result else "Not marked")
    report.append("\n--- Rewritten Composition ---")
    report.append(rewrite_text if rewrite_text else "No rewritten essay uploaded")
    report.append("\n--- Rewrite Evaluation ---")
    report.append(rewrite_result if rewrite_result else "Rewrite not checked")
    return "\n".join(report)

def generate_csv(student_name, sg_level, skill_tag, original_text, mark_result, rewrite_text, rewrite_result):
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Student Name", "Difficulty Level","Skill Focus", "Original Essay", "Original Marking", "Rewritten Essay", "Rewrite Evaluation"])
    writer.writerow([student_name, sg_level, skill_tag, original_text, mark_result, rewrite_text, rewrite_result])
    return output.getvalue()

# --------------------------
# Dashboard
# --------------------------
def build_class_dashboard():
    all_df = get_all_attempts()
    if all_df.empty:
        return pd.DataFrame(), pd.DataFrame()
    class_data = []
    skill_subscore_cols = [
        "subscore_content",
        "subscore_organisation",
        "subscore_vocabulary",
        "subscore_grammar",
        "subscore_argumentation",
        "subscore_criticalthinking",
        "subscore_toneregister",
        "subscore_evidencecitation"
    ]
    for student_name in all_df["student_name"].unique():
        student_sub = all_df[all_df["student_name"] == student_name]
        orig_scores = student_sub["original_score"].dropna().tolist()
        rewrite_scores = student_sub["rewrite_score"].dropna().tolist()
        avg_original = sum(orig_scores)/len(orig_scores) if orig_scores else None
        avg_rewrite = sum(rewrite_scores)/len(rewrite_scores) if rewrite_scores else None
        total_attempts = len(student_sub)
        class_data.append({
            "Student Name": student_name,
            "Total Attempts": total_attempts,
            "Avg Original Score": round(avg_original,2) if avg_original else None,
            "Avg Rewrite Score": round(avg_rewrite,2) if avg_rewrite else None
        })
    # skill averages across all attempts
    skill_avg_df = all_df[skill_subscore_cols].mean().reset_index()
    skill_avg_df.columns = ["SkillColumn","Average"]
    skill_mapping = {
        "subscore_content":"Content",
        "subscore_organisation":"Organisation",
        "subscore_vocabulary":"Vocabulary",
        "subscore_grammar":"Grammar",
        "subscore_argumentation":"Argumentation",
        "subscore_criticalthinking":"Critical Thinking",
        "subscore_toneregister":"Tone & Register",
        "subscore_evidencecitation":"Evidence & Citation"
    }
    skill_avg_df["SkillName"] = skill_avg_df["SkillColumn"].map(skill_mapping)
    return pd.DataFrame(class_data), skill_avg_df

# --------------------------
# Streamlit UI
# --------------------------
st.set_page_config(page_title="Composition Checker", layout="wide")
st.title("✍️ Mun Compo LABs")

# Initialise database
try:
    init_db()
except Exception as e:
    st.error(f"Database connection failed: {e}")

# Session state
if "mark_result" not in st.session_state:
    st.session_state.mark_result = ""
if "rewrite_result" not in st.session_state:
    st.session_state.rewrite_result = ""
if "subscores" not in st.session_state:
    st.session_state.subscores = {}

students_df = get_students()
student_names_list = students_df["student_name"].tolist()

# Sidebar
st.sidebar.header("👨‍🎓 Student Enrolment & History")
app_page = st.sidebar.radio("Select Page", ["Composition Marking", "Class Dashboard"])

if app_page == "Class Dashboard":
    st.header("🏫 Class Dashboard (Composition)")
    class_df, skill_avg_df = build_class_dashboard()
    if not class_df.empty:
        st.subheader("Class Summary Table")
        st.dataframe(class_df)
        avg_all_original = class_df["Avg Original Score"].mean()
        avg_all_rewrite = class_df["Avg Rewrite Score"].mean()
        st.subheader("Class Statistics")
        st.write(f"Number of students: {len(class_df)}")
        st.write(f"Class average original score: {round(avg_all_original,2)} /10")
        st.write(f"Class average rewrite score: {round(avg_all_rewrite,2)} /10")
        st.line_chart(class_df, x="Student Name", y=["Avg Original Score","Avg Rewrite Score"])
        st.subheader("📊 Average Skill Subscores across all students")
        st.dataframe(skill_avg_df[["SkillName","Average"]])
        st.bar_chart(skill_avg_df, x="SkillName", y="Average")

        class_csv = class_df.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Download Class Summary CSV", data=class_csv, file_name="class_summary.csv", mime="text/csv")
    else:
        st.info("No composition records yet. Go to Composition Marking page to enrol students and start marking.")

else:
    mode = st.sidebar.radio("Select Mode", ["New Student Enrolment", "Load Existing Student"])
    selected_student = ""
    if mode == "New Student Enrolment":
        new_student_name = st.sidebar.text_input("Enter new student name")
        if st.sidebar.button("Enrol Student"):
            if new_student_name.strip():
                sid = create_student(new_student_name.strip())
                if sid:
                    st.sidebar.success(f"✅ Student {new_student_name} enrolled")
                else:
                    st.sidebar.warning("Student already exists!")
    elif mode == "Load Existing Student":
        if student_names_list:
            selected_student = st.sidebar.selectbox("Choose student", student_names_list)
            student_records_df = get_attempts_by_student(selected_student)
            if not student_records_df.empty:
                st.sidebar.subheader(f"{selected_student}'s Past Compositions")
                for idx, row in student_records_df.iterrows():
                    st.sidebar.markdown(f"""
**Attempt {idx+1}**
Date: {row["date"]}
Level: {row["sg_level"]}
Skill Focus: {row["skill_tag"]}
Original Score: {row["original_score"]}
Rewrite Score: {row["rewrite_score"]}
""")
            else:
                st.sidebar.info("No past composition attempts yet")

            delete_pw = st.sidebar.text_input("Enter password to delete history", type="password")
            if st.sidebar.button("Clear this student's history"):
                if delete_pw == "romeo2000":
                    delete_student(selected_student)
                    st.sidebar.warning("Student and history deleted! Refresh page.")
                else:
                    st.sidebar.error("❌ Incorrect password. Deletion cancelled.")
        else:
            st.sidebar.info("No enrolled students yet, go enrol a new student.")

    if selected_student or (mode == "New Student Enrolment" and new_student_name.strip()):
        active_student = selected_student if selected_student else new_student_name.strip()

        # Level list
        difficulty_levels = [
            "Primary Low",
            "Primary High",
            "Secondary Low",
            "Secondary High",
            "JC1",
            "JC2",
            "Polytechnic",
            "University"
        ]
        sg_level = st.selectbox("🎓 Difficulty Level", difficulty_levels)

        # Skill dropdown with All Skills option
        skill_options = [
            "All Skills",
            "Content",
            "Organisation",
            "Vocabulary",
            "Grammar",
            "Argumentation",
            "Critical Thinking",
            "Tone & Register",
            "Evidence & Citation"
        ]
        selected_skill = st.selectbox("🧠 Skill Focus (still evaluates all skills)", skill_options)
        feedback_language = st.selectbox("Feedback language", ["Auto (match essay)", "English", "Chinese"])

        st.subheader("Step 1: Upload original student composition (.docx)")
        upload_original = st.file_uploader("Original Essay", type="docx", key="original")
        original_text = ""
        if upload_original is not None:
            original_text = read_docx(upload_original)
            st.subheader("Original Essay Preview")
            st.write(original_text)

        if st.button("✅ Mark Original Composition"):
            if not original_text.strip():
                st.warning("Upload composition docx first.")
            else:
                with st.spinner("Marking composition ..."):
                    prompt = build_composition_marking_prompt(original_text, feedback_language, sg_level, selected_skill)
                    response = model.generate_content(prompt)
                    st.session_state.mark_result = response.text
                    st.session_state.subscores = extract_subscores(response.text)
                st.subheader("📊 Marking Result")
                st.write(st.session_state.mark_result)
                # Display extracted subscores table
                sub_df = pd.DataFrame([st.session_state.subscores]).T.reset_index()
                sub_df.columns = ["SkillField","Score/10"]
                skill_mapping = {
                    "subscore_content":"Content",
                    "subscore_organisation":"Organisation",
                    "subscore_vocabulary":"Vocabulary",
                    "subscore_grammar":"Grammar",
                    "subscore_argumentation":"Argumentation",
                    "subscore_criticalthinking":"Critical Thinking",
                    "subscore_toneregister":"Tone & Register",
                    "subscore_evidencecitation":"Evidence & Citation"
                }
                sub_df["Skill"] = sub_df["SkillField"].map(skill_mapping)
                st.subheader("📈 Skill Subscores")
                st.dataframe(sub_df[["Skill","Score/10"]])
                st.bar_chart(sub_df, x="Skill", y="Score/10")

        st.divider()
        st.subheader("Step 2: Upload rewritten / revised composition (.docx)")
        upload_rewrite = st.file_uploader("Revised Essay", type="docx", key="rewrite")
        rewrite_text = ""
        if upload_rewrite is not None:
            rewrite_text = read_docx(upload_rewrite)
            st.subheader("Rewritten Essay Preview")
            st.write(rewrite_text)

        if st.button("🔍 Evaluate Rewritten Essay"):
            if not st.session_state.mark_result.strip():
                st.warning("Mark original essay first before checking rewrite.")
            elif not rewrite_text.strip():
                st.warning("Upload revised composition docx.")
            else:
                with st.spinner("Comparing rewrite with previous feedback ..."):
                    prompt = build_composition_rewrite_check_prompt(rewrite_text, st.session_state.mark_result, feedback_language, sg_level, selected_skill)
                    response = model.generate_content(prompt)
                    st.session_state.rewrite_result = response.text
                st.subheader("📝 Rewrite Evaluation Result")
                st.write(st.session_state.rewrite_result)

                orig_score = extract_total_score(st.session_state.mark_result, "Total Score")
                rev_score = extract_total_score(st.session_state.rewrite_result, "New Total Score (rewritten)")
                sub = extract_subscores(st.session_state.mark_result)
                record = {
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "sg_level": sg_level,
                    "skill_tag": selected_skill,
                    "original_text": original_text,
                    "rewrite_text": rewrite_text,
                    "original_score": orig_score,
                    "rewrite_score": rev_score,
                    "mark_result": st.session_state.mark_result,
                    "rewrite_result": st.session_state.rewrite_result,
                    **sub
                }
                save_attempt(active_student, record)
                st.success("✅ Composition attempt saved to database!")

        st.divider()
        st.subheader("📈 Student Progress Chart")
        records_df = get_attempts_by_student(active_student)
        if not records_df.empty:
            chart_data = []
            for idx, row in records_df.iterrows():
                chart_data.append({
                    "Attempt": idx+1,
                    "Original Score": row["original_score"],
                    "Rewrite Score": row["rewrite_score"]
                })
            df_chart = pd.DataFrame(chart_data)
            st.line_chart(df_chart, x="Attempt", y=["Original Score", "Rewrite Score"])
        else:
            st.info("No saved attempts yet. Complete marking and rewrite evaluation to save record.")

        st.divider()
        st.subheader("💾 Export Composition Report")
        full_report_text = generate_full_report(active_student, sg_level, selected_skill, original_text, st.session_state.mark_result, rewrite_text, st.session_state.rewrite_result)
        csv_data = generate_csv(active_student, sg_level, selected_skill, original_text, st.session_state.mark_result, rewrite_text, st.session_state.rewrite_result)
        col1, col2 = st.columns(2)
        with col1:
            st.download_button("📄 Download Text Report (.txt)", data=full_report_text, file_name=f"{active_student}_composition_report.txt", mime="text/plain")
        with col2:
            st.download_button("📊 Download CSV (.csv)", data=csv_data, file_name=f"{active_student}_composition_report.csv", mime="text/csv")
    else:
        st.info("👈 Go to sidebar to enrol or select student before starting marking")

