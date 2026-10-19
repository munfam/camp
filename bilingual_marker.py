import streamlit as st
import google.generativeai as genai
import psycopg2
import pandas as pd
from datetime import datetime

# -------------------------- PAGE CONFIG (MUST BE FIRST STREAMLIT LINE) --------------------------
st.set_page_config(
    page_title="Mun Compo LAB",
    page_icon="📝",
    layout="wide"
)

# -------------------------- CONFIG --------------------------
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    import os
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-3.8-flash")

DB_HOST = os.getenv("DB_HOST", "postgres")
DB_NAME = os.getenv("DB_NAME", "compodb")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASSWORD")

DELETE_PASSWORD = "romeo2000"

DIFFICULTY_LEVELS = [
    "Primary 1", "Primary 2", "Primary 3", "Primary 4", "Primary 5", "Primary 6",
    "Secondary 1", "Secondary 2", "Secondary 3", "Secondary 4", "Secondary 5",
    "Polytechnic", "University"
]
SKILL_OPTIONS = ["All Skills", "Content", "Organisation", "Vocabulary", "Grammar"]

# -------------------------- DB CONNECTION & AUTO SCHEMA MIGRATION --------------------------
def get_db_connection():
    conn = psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )
    init_db_schema(conn)
    return conn

def init_db_schema(conn):
    cur = conn.cursor()
    # Base table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS composition_attempts (
        id SERIAL PRIMARY KEY,
        student_name TEXT,
        difficulty_level TEXT,
        essay_text TEXT,
        word_count INTEGER,
        feedback TEXT,
        total_score NUMERIC,
        subscore_content NUMERIC,
        subscore_organisation NUMERIC,
        subscore_vocabulary NUMERIC,
        subscore_grammar NUMERIC,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    # Safe add columns if table already exists and missing fields
    cur.execute("ALTER TABLE composition_attempts ADD COLUMN IF NOT EXISTS word_count INTEGER;")
    cur.execute("ALTER TABLE composition_attempts ADD COLUMN IF NOT EXISTS subscore_content NUMERIC;")
    cur.execute("ALTER TABLE composition_attempts ADD COLUMN IF NOT EXISTS subscore_organisation NUMERIC;")
    cur.execute("ALTER TABLE composition_attempts ADD COLUMN IF NOT EXISTS subscore_vocabulary NUMERIC;")
    cur.execute("ALTER TABLE composition_attempts ADD COLUMN IF NOT EXISTS subscore_grammar NUMERIC;")
    conn.commit()
    cur.close()

# -------------------------- DB FUNCTIONS --------------------------
def save_attempt(student_name, difficulty, essay_text, word_count, feedback, total_score, sub_content, sub_org, sub_vocab, sub_gram):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO composition_attempts
    (student_name, difficulty_level, essay_text, word_count, feedback, total_score,
    subscore_content, subscore_organisation, subscore_vocabulary, subscore_grammar)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (student_name, difficulty, essay_text, word_count, feedback, total_score, sub_content, sub_org, sub_vocab, sub_gram))
    conn.commit()
    cur.close()
    conn.close()

def get_all_students():
    conn = get_db_connection()
    df = pd.read_sql("SELECT DISTINCT student_name FROM composition_attempts ORDER BY student_name", conn)
    conn.close()
    return df["student_name"].tolist()

def get_attempts_by_student(student_name):
    conn = get_db_connection()
    df = pd.read_sql("""
        SELECT id, student_name, difficulty_level, word_count, feedback, total_score,
        subscore_content, subscore_organisation, subscore_vocabulary, subscore_grammar, created_at
        FROM composition_attempts
        WHERE student_name = %s
        ORDER BY created_at ASC
    """, conn, params=(student_name,))
    conn.close()
    return df

def delete_student_history(student_name):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM composition_attempts WHERE student_name = %s", (student_name,))
    conn.commit()
    cur.close()
    conn.close()

# -------------------------- Word Count Helper --------------------------
def count_words(text: str) -> int:
    words = text.strip().split()
    return len(words)

# -------------------------- Gemini Marking Function --------------------------
def mark_composition(essay, difficulty, skill_focus):
    prompt = f"""
You are an experienced Singapore MOE composition marker.
Difficulty Level: {difficulty}
Skill Focus: {skill_focus}
Essay:
{essay}

Mark this composition.
Return your result strictly in this JSON format, no extra text outside JSON:
{{
"total_score": number,
"subscore_content": number,
"subscore_organisation": number,
"subscore_vocabulary": number,
"subscore_grammar": number,
"feedback": "detailed feedback with strengths and areas to improve"
}}
Mark according to standard Singapore composition rubric.
If skill_focus = "All Skills", evaluate all four areas.
If a single skill selected, still give all four subscores but prioritise feedback for that skill.
"""
    response = model.generate_content(prompt)
    return response.text

# -------------------------- APP UI --------------------------
st.title("📝 Mun Compo LAB")
tab1, tab2, tab3 = st.tabs(["Mark Composition", "Student Progress Chart", "Manage Records"])

with tab1:
    st.subheader("Mark New Composition")
    student_name = st.text_input("Student Name")
    difficulty = st.selectbox("Difficulty Level", DIFFICULTY_LEVELS)
    skill_focus = st.selectbox("Composition Skill Focus", SKILL_OPTIONS)
    essay_input = st.text_area("Paste student composition here", height=320)

    if essay_input:
        word_cnt = count_words(essay_input)
        st.info(f"Word count: {word_cnt} words")

    if st.button("Mark Composition", type="primary"):
        if not student_name or not essay_input.strip():
            st.warning("Fill student name and essay")
        else:
            with st.spinner("Marking with Gemini..."):
                raw = mark_composition(essay_input, difficulty, skill_focus)
                import json
                result_json = json.loads(raw)
                total = result_json["total_score"]
                sc = result_json["subscore_content"]
                so = result_json["subscore_organisation"]
                sv = result_json["subscore_vocabulary"]
                sg = result_json["subscore_grammar"]
                fb = result_json["feedback"]
                wc = count_words(essay_input)
                save_attempt(student_name, difficulty, essay_input, wc, fb, total, sc, so, sv, sg)
            st.success("Marked and saved to database!")
            st.subheader("Marking Result")
            st.write(f"Total Score: {total}")
            st.write(f"Content: {sc} | Organisation: {so} | Vocabulary: {sv} | Grammar: {sg}")
            st.write(f"Word count: {wc}")
            st.markdown("#### Feedback")
            st.write(fb)

with tab2:
    st.subheader("📊 Student Progress Chart")
    students = get_all_students()
    if not students:
        st.info("No student records yet. Mark your first composition.")
    else:
        selected_student = st.selectbox("Select Student", students)
        records_df = get_attempts_by_student(selected_student)
        st.dataframe(records_df[["created_at", "difficulty_level", "word_count", "total_score",
                                 "subscore_content", "subscore_organisation", "subscore_vocabulary", "subscore_grammar"]])
        if len(records_df) > 1:
            st.line_chart(records_df, x="created_at", y=["total_score","subscore_content","subscore_organisation","subscore_vocabulary","subscore_grammar"])

with tab3:
    st.subheader("Manage Student History")
    students = get_all_students()
    if students:
        del_student = st.selectbox("Select student to delete history", students)
        pw_input = st.text_input("Enter password to delete history", type="password")
        if st.button("DELETE ALL HISTORY FOR THIS STUDENT"):
            if pw_input == DELETE_PASSWORD:
                delete_student_history(del_student)
                st.success(f"Deleted all records for {del_student}")
                st.rerun()
            else:
                st.error("Wrong password!")
    else:
        st.info("No student records available")


