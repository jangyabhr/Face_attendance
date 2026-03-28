
import streamlit as st
import pandas as pd
from PIL import Image
from datetime import datetime
import io
import json
import base64
import anthropic
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ---------------------------
# Page configuration & styles
# ---------------------------
st.set_page_config(
    page_title="Face Attendance (Claude Vision)",
    page_icon="✨",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .main {background-color: #0e1117;}
    .stButton>button {
        width: 100%;
        background-color: #4CAF50;
        color: white;
        font-weight: bold;
    }
    .stButton>button:hover {background-color: #45a049;}
    h1 {color: #4CAF50;}
    h2, h3 {color: #FF9800;}
</style>
""", unsafe_allow_html=True)

# ---------------------------
# Session state initialization
# ---------------------------
if 'roster' not in st.session_state:
    st.session_state.roster = None
if 'student_photos' not in st.session_state:
    st.session_state.student_photos = {}   # admission_no -> {"name": str, "photo_b64": str}
if 'claude_results' not in st.session_state:
    st.session_state.claude_results = {}   # admission_no -> "P" | "A" | "?"
if 'analysis_done' not in st.session_state:
    st.session_state.analysis_done = False
if 'photo' not in st.session_state:
    st.session_state.photo = None          # PIL Image
if 'photo_bytes' not in st.session_state:
    st.session_state.photo_bytes = None    # raw bytes for API
if 'sheets_connected' not in st.session_state:
    st.session_state.sheets_connected = False
if 'spreadsheet_id' not in st.session_state:
    st.session_state.spreadsheet_id = None
if 'show_master' not in st.session_state:
    st.session_state.show_master = False

# ---------------------------
# Google Sheets helpers
# ---------------------------
def connect_to_sheets(credentials_dict, spreadsheet_id):
    try:
        credentials = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        service = build('sheets', 'v4', credentials=credentials)
        st.session_state.sheets_service = service
        st.session_state.spreadsheet_id = spreadsheet_id
        st.session_state.sheets_connected = True
        return True
    except Exception as e:
        st.error(f"Connection failed: {str(e)}")
        return False

def read_sheet(sheet_name):
    try:
        service = st.session_state.sheets_service
        spreadsheet_id = st.session_state.spreadsheet_id
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A:Z"
        ).execute()
        values = result.get('values', [])
        if not values:
            return None
        df = pd.DataFrame(values[1:], columns=values[0])
        return df
    except HttpError as e:
        if hasattr(e, "resp") and getattr(e.resp, "status", None) == 404:
            return None
        st.error(f"Error reading sheet: {str(e)}")
        return None
    except Exception as e:
        st.error(f"Error reading sheet: {str(e)}")
        return None

def create_sheet_if_not_exists(sheet_name):
    try:
        service = st.session_state.sheets_service
        spreadsheet_id = st.session_state.spreadsheet_id
        spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        existing = [s['properties']['title'] for s in spreadsheet.get('sheets', [])]
        if sheet_name not in existing:
            body = {'requests': [{'addSheet': {'properties': {'title': sheet_name}}}]}
            service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
        return True
    except Exception as e:
        st.error(f"Error creating sheet: {str(e)}")
        return False

def write_sheet(sheet_name, df):
    try:
        service = st.session_state.sheets_service
        spreadsheet_id = st.session_state.spreadsheet_id
        create_sheet_if_not_exists(sheet_name)
        values = [df.columns.tolist()] + df.astype(str).values.tolist()
        body = {'values': values}
        try:
            service.spreadsheets().values().clear(
                spreadsheetId=spreadsheet_id,
                range=f"{sheet_name}!A:Z"
            ).execute()
        except Exception:
            pass
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()
        return True
    except Exception as e:
        st.error(f"Error writing sheet: {str(e)}")
        return False

def init_master_attendance(roster):
    df = roster.copy()
    df['Total_Present'] = '0'
    df['Total_Absent'] = '0'
    df['Attendance_%'] = '0'
    return df

def update_master_attendance(roster, results, date_str):
    """Update attendance sheet. results = {admission_no: "P"/"A"/"?"}"""
    master = read_sheet("Attendance")
    if master is None or master.empty:
        master = init_master_attendance(roster)
    if 'Admission_No' not in master.columns:
        master = init_master_attendance(roster)
    if date_str not in master.columns:
        master[date_str] = ''

    for idx, row in roster.iterrows():
        admission_no = row['Admission_No']
        status = results.get(admission_no, 'A')
        if status == '?':
            status = 'A'
        is_present = (status == 'P')
        mask = master['Admission_No'] == admission_no
        if mask.any():
            m_idx = master[mask].index[0]
            if master.loc[m_idx, date_str] not in ['P', 'A', 'p', 'a']:
                master.loc[m_idx, date_str] = status
                tp_raw = str(master.loc[m_idx, 'Total_Present']).split('.')[0]
                ta_raw = str(master.loc[m_idx, 'Total_Absent']).split('.')[0]
                tp = int(tp_raw) if tp_raw.isdigit() else 0
                ta = int(ta_raw) if ta_raw.isdigit() else 0
                tp += 1 if is_present else 0
                ta += 0 if is_present else 1
                master.loc[m_idx, 'Total_Present'] = str(tp)
                master.loc[m_idx, 'Total_Absent'] = str(ta)
                total_days = tp + ta
                master.loc[m_idx, 'Attendance_%'] = str(round((tp / total_days) * 100, 2) if total_days else 0.0)
        else:
            new_row = row.copy()
            new_row['Total_Present'] = '1' if is_present else '0'
            new_row['Total_Absent'] = '0' if is_present else '1'
            new_row['Attendance_%'] = '100' if is_present else '0'
            new_row[date_str] = status
            master = pd.concat([master, pd.DataFrame([new_row])], ignore_index=True)

    write_sheet("Attendance", master)
    return master

def search_roster(roster, query):
    if not query:
        return roster
    q = query.lower()
    cols = {c.lower(): c for c in roster.columns}
    name_c = cols.get('name', 'Name')
    adm_c = cols.get('admission_no', 'Admission_No')
    sec_c = cols.get('section', 'Section')
    roll_c = cols.get('roll_no', 'Roll_No')
    mask = (
        roster[name_c].str.lower().str.contains(q, regex=False) |
        roster[adm_c].str.lower().str.contains(q, regex=False) |
        roster[sec_c].str.lower().str.contains(q, regex=False) |
        roster[roll_c].str.lower().str.contains(q, regex=False)
    )
    return roster[mask]

# ---------------------------
# Photo helpers
# ---------------------------
def compress_photo_for_storage(image_bytes: bytes, size=(150, 150)) -> str:
    """Thumbnail to 150x150 JPEG, return base64 string for Google Sheets storage."""
    img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    img.thumbnail(size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=85)
    return base64.b64encode(buf.getvalue()).decode()

def compress_photo_for_api(image_bytes: bytes, max_size=(512, 512)) -> str:
    """Resize to max 512x512 JPEG, return base64 string for Claude API."""
    img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    img.thumbnail(max_size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=85)
    return base64.b64encode(buf.getvalue()).decode()

# ---------------------------
# Student photo enrollment
# ---------------------------
def enroll_student_photo(admission_no: str, name: str, photo_bytes: bytes) -> bool:
    """Save a student reference photo to the StudentPhotos sheet (upsert)."""
    try:
        photo_b64 = compress_photo_for_storage(photo_bytes)
        df = read_sheet("StudentPhotos")
        new_row = pd.DataFrame([{'Admission_No': admission_no, 'Name': name, 'PhotoData': photo_b64}])
        if df is None or df.empty:
            df = new_row
        else:
            mask = df['Admission_No'] == admission_no
            if mask.any():
                df.loc[mask, 'Name'] = name
                df.loc[mask, 'PhotoData'] = photo_b64
            else:
                df = pd.concat([df, new_row], ignore_index=True)
        success = write_sheet("StudentPhotos", df)
        if success:
            st.session_state.student_photos[admission_no] = {"name": name, "photo_b64": photo_b64}
        return success
    except Exception as e:
        st.error(f"Enrollment failed: {str(e)}")
        return False

def load_student_photos() -> dict:
    """Load all student reference photos from the StudentPhotos sheet."""
    df = read_sheet("StudentPhotos")
    if df is None or df.empty:
        return {}
    photos = {}
    for _, row in df.iterrows():
        adm = str(row.get('Admission_No', '')).strip()
        photo_b64 = str(row.get('PhotoData', '')).strip()
        name = str(row.get('Name', '')).strip()
        if adm and photo_b64:
            photos[adm] = {"name": name, "photo_b64": photo_b64}
    return photos

# ---------------------------
# Claude Vision identification
# ---------------------------
def parse_claude_response(response_text: str, enrolled_admission_nos: list) -> dict:
    """Parse Claude's JSON response into {admission_no: 'P'/'A'/'?'}."""
    results = {adm: 'A' for adm in enrolled_admission_nos}
    try:
        # Extract JSON from response (Claude may include surrounding text)
        text = response_text.strip()
        start = text.find('{')
        end = text.rfind('}') + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
            for adm in data.get('present', []):
                if adm in results:
                    results[adm] = 'P'
            for adm in data.get('uncertain', []):
                if adm in results:
                    results[adm] = '?'
    except Exception:
        pass
    return results

def identify_attendance_with_claude(
    class_photo_bytes: bytes,
    enrolled_students: list,
    api_key: str,
    model: str = "claude-haiku-4-5-20251001",
    batch_size: int = 10
) -> dict:
    """
    Identify present students using Claude Vision API.
    enrolled_students: [{"admission_no": str, "name": str, "photo_b64": str}]
    Returns {admission_no: "P"/"A"/"?"}
    """
    client = anthropic.Anthropic(api_key=api_key)
    class_photo_b64 = compress_photo_for_api(class_photo_bytes)
    all_results = {}

    batches = [enrolled_students[i:i+batch_size] for i in range(0, len(enrolled_students), batch_size)]
    progress = st.progress(0, text="Asking Claude to identify students...")

    for batch_num, batch in enumerate(batches):
        progress.progress(
            int((batch_num / len(batches)) * 100),
            text=f"Processing batch {batch_num + 1}/{len(batches)}..."
        )
        content = [
            {
                "type": "text",
                "text": (
                    "You are an attendance tracking assistant. "
                    "Below are reference photos of students, followed by a classroom photo. "
                    "Compare each student's reference photo to the faces visible in the classroom photo. "
                    "Determine which students are physically present in the classroom photo.\n\n"
                    "REFERENCE PHOTOS:"
                )
            }
        ]

        for student in batch:
            content.append({
                "type": "text",
                "text": f"Student ID: {student['admission_no']} — {student['name']}"
            })
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": student['photo_b64']
                }
            })

        content.append({
            "type": "text",
            "text": "CLASSROOM PHOTO (identify which students above are present in this photo):"
        })
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": class_photo_b64
            }
        })
        content.append({
            "type": "text",
            "text": (
                'Respond with ONLY a valid JSON object in this exact format (no extra text):\n'
                '{"present": ["ADM001", "ADM003"], "absent": ["ADM002"], "uncertain": ["ADM004"]}\n'
                'Every student ID from the reference photos must appear in exactly one list.'
            )
        })

        try:
            response = client.messages.create(
                model=model,
                max_tokens=512,
                messages=[{"role": "user", "content": content}]
            )
            response_text = response.content[0].text
            batch_admission_nos = [s['admission_no'] for s in batch]
            batch_results = parse_claude_response(response_text, batch_admission_nos)
            all_results.update(batch_results)
        except Exception as e:
            st.warning(f"Batch {batch_num + 1} error: {str(e)}")
            for student in batch:
                all_results[student['admission_no']] = '?'

    progress.progress(100, text="Done!")
    return all_results

# ---------------------------
# Main App
# ---------------------------
def main():
    st.title("✨ Face Attendance (Claude Vision)")

    with st.expander("⚙️ SETUP INSTRUCTIONS (First Time Only)", expanded=not st.session_state.sheets_connected):
        st.markdown("""
        ### One-Time Setup
        1. Create a Google Sheet and enable the **Google Sheets API**
        2. Create a **Service Account** key (JSON)
        3. Share the Sheet with the service account email as *Editor*
        4. Get an **Anthropic API key** from console.anthropic.com
        5. Add to Streamlit secrets: `ANTHROPIC_API_KEY`, `spreadsheet_id`, `gcp_service_account`
        """)

    st.markdown("---")

    # Connection UI
    if not st.session_state.sheets_connected:
        st.subheader("🔗 Connect to Google Sheets")
        auto_connected = False
        try:
            if "gcp_service_account" in st.secrets and "spreadsheet_id" in st.secrets:
                with st.spinner("Connecting to Google Sheets..."):
                    credentials_dict = dict(st.secrets["gcp_service_account"])
                    spreadsheet_id = st.secrets["spreadsheet_id"]
                    if connect_to_sheets(credentials_dict, spreadsheet_id):
                        st.success("✅ Connected to Google Sheets!")
                        auto_connected = True
                        roster = read_sheet("Roster")
                        if roster is not None and not roster.empty:
                            st.session_state.roster = roster
                            st.success(f"✅ Loaded roster: {len(roster)} students")
                        photos = load_student_photos()
                        if photos:
                            st.session_state.student_photos = photos
                            st.info(f"📸 Loaded {len(photos)} enrolled student photos")
        except Exception as e:
            st.error(f"Auto connect failed: {e}")

        if not auto_connected:
            st.info("Provide credentials manually if secrets are not configured.")
            creds_method = st.radio("Credentials method", ["Upload JSON", "Paste JSON"], horizontal=True)
            credentials_dict = None
            if creds_method == "Upload JSON":
                cred_file = st.file_uploader("Upload service account JSON", type=["json"])
                if cred_file:
                    try:
                        credentials_dict = json.load(cred_file)
                    except Exception as e:
                        st.error(f"Invalid JSON: {e}")
            else:
                json_text = st.text_area("Paste service account JSON here")
                if json_text.strip():
                    try:
                        credentials_dict = json.loads(json_text)
                    except Exception as e:
                        st.error(f"Invalid JSON: {e}")

            spreadsheet_id = st.text_input("Spreadsheet ID", placeholder="1AbC... (from the Sheet URL)")
            if st.button("Connect"):
                if not credentials_dict or not spreadsheet_id:
                    st.error("Provide both credentials and Spreadsheet ID")
                else:
                    if connect_to_sheets(credentials_dict, spreadsheet_id):
                        st.success("✅ Connected!")
                        roster = read_sheet("Roster")
                        if roster is not None and not roster.empty:
                            st.session_state.roster = roster
                        photos = load_student_photos()
                        if photos:
                            st.session_state.student_photos = photos
                        st.rerun()
            st.stop()

    st.success("🔗 Connected to Google Sheets")

    # ---------------------------
    # Sidebar
    # ---------------------------
    with st.sidebar:
        st.header("⚙️ Controls")
        st.metric("📅 Date", datetime.now().strftime('%b %d, %Y'))
        st.markdown("---")

        # Anthropic API key
        st.subheader("🤖 Claude API Key")
        default_key = ""
        try:
            default_key = st.secrets.get("ANTHROPIC_API_KEY", "")
        except Exception:
            pass
        api_key_input = st.text_input(
            "Anthropic API Key",
            value=default_key,
            type="password",
            placeholder="sk-ant-...",
            help="From console.anthropic.com"
        )
        if api_key_input:
            st.session_state.anthropic_key = api_key_input

        st.markdown("---")

        # Roster
        st.subheader("👥 Roster")
        if st.button("📥 Load Roster from Sheets", use_container_width=True):
            roster = read_sheet("Roster")
            if roster is not None and not roster.empty:
                st.session_state.roster = roster
                st.success(f"✅ Loaded {len(roster)} students")
            else:
                st.info("No roster found. Upload CSV below.")

        roster_file = st.file_uploader(
            "Upload Roster CSV",
            type=['csv'],
            help="Columns: Admission_No, Name, Section, Roll_No"
        )
        if roster_file:
            try:
                df = pd.read_csv(roster_file, dtype=str).fillna("")
                required = {"Admission_No", "Name", "Section", "Roll_No"}
                if not required.issubset(set(df.columns)):
                    st.error(f"❌ CSV must have: {', '.join(required)}")
                else:
                    st.session_state.roster = df
                    if write_sheet("Roster", df):
                        st.success(f"✅ Roster saved: {len(df)} students")
            except Exception as e:
                st.error(f"Error: {str(e)}")

        if st.session_state.roster is not None:
            st.metric("Students", len(st.session_state.roster))

        st.markdown("---")

        # Student enrollment
        st.subheader("📸 Enroll Students")
        enrolled_count = len(st.session_state.student_photos)
        st.caption(f"{enrolled_count} students enrolled with reference photos")

        if st.button("🔄 Reload Enrolled Photos", use_container_width=True):
            photos = load_student_photos()
            st.session_state.student_photos = photos
            st.success(f"Loaded {len(photos)} photos")

        if st.session_state.roster is not None:
            roster_df = st.session_state.roster
            student_options = [
                f"{row['Name']} ({row['Admission_No']})"
                for _, row in roster_df.iterrows()
            ]
            selected_student_label = st.selectbox("Select student to enroll", student_options)
            enroll_photo = st.file_uploader(
                "Upload reference photo",
                type=['jpg', 'jpeg', 'png'],
                key="enroll_photo"
            )
            if st.button("✅ Enroll Student", use_container_width=True):
                if enroll_photo and selected_student_label:
                    idx = student_options.index(selected_student_label)
                    row = roster_df.iloc[idx]
                    admission_no = row['Admission_No']
                    name = row['Name']
                    photo_bytes = enroll_photo.read()
                    with st.spinner("Enrolling..."):
                        if enroll_student_photo(admission_no, name, photo_bytes):
                            st.success(f"✅ Enrolled {name}")
                        else:
                            st.error("Enrollment failed")
                else:
                    st.warning("Select a student and upload a photo")

        st.markdown("---")

        # Today's photo
        st.subheader("📷 Today's Photo")
        photo_file = st.file_uploader("Upload classroom photo", type=['jpg', 'jpeg', 'png'])
        if photo_file:
            photo_bytes = photo_file.read()
            st.session_state.photo = Image.open(io.BytesIO(photo_bytes)).convert('RGB')
            st.session_state.photo_bytes = photo_bytes
            st.session_state.analysis_done = False
            st.session_state.claude_results = {}
            st.success("✅ Photo loaded")

        st.markdown("---")

        if st.button("📊 View Master Sheet", use_container_width=True):
            st.session_state.show_master = not st.session_state.show_master
            st.rerun()

    # ---------------------------
    # Main content
    # ---------------------------
    col1, col2 = st.columns([3, 2])

    with col1:
        st.subheader("📷 Classroom Photo")
        if st.session_state.photo is not None:
            st.image(st.session_state.photo, use_container_width=True)
        else:
            st.info("👈 Upload today's classroom photo in the sidebar")

    with col2:
        st.subheader("🤖 Claude Analysis")

        if st.session_state.photo is None:
            st.info("Upload a classroom photo first")
        elif not st.session_state.student_photos:
            st.warning("No enrolled students found. Enroll students with reference photos first.")
        elif not st.session_state.get('anthropic_key'):
            st.warning("Enter your Anthropic API key in the sidebar")
        else:
            enrolled_in_roster = []
            if st.session_state.roster is not None:
                for _, row in st.session_state.roster.iterrows():
                    adm = row['Admission_No']
                    if adm in st.session_state.student_photos:
                        enrolled_in_roster.append({
                            "admission_no": adm,
                            "name": row['Name'],
                            "photo_b64": st.session_state.student_photos[adm]['photo_b64']
                        })

            st.info(
                f"📸 {len(enrolled_in_roster)} of {len(st.session_state.roster) if st.session_state.roster is not None else 0} "
                f"students have reference photos"
            )

            if st.button("🔍 Identify Attendance with Claude", use_container_width=True, type="primary"):
                if not enrolled_in_roster:
                    st.error("No students with reference photos found in the current roster")
                else:
                    with st.spinner("Sending to Claude Vision API..."):
                        results = identify_attendance_with_claude(
                            class_photo_bytes=st.session_state.photo_bytes,
                            enrolled_students=enrolled_in_roster,
                            api_key=st.session_state.anthropic_key
                        )
                    st.session_state.claude_results = results
                    st.session_state.analysis_done = True
                    st.rerun()

    # ---------------------------
    # Review & Save section
    # ---------------------------
    if st.session_state.analysis_done and st.session_state.claude_results and st.session_state.roster is not None:
        st.markdown("---")
        st.subheader("✏️ Review & Correct Attendance")
        st.caption("Claude's results are pre-filled. Correct any mistakes before saving.")

        results = dict(st.session_state.claude_results)
        roster_df = st.session_state.roster

        # Build editable table
        review_data = []
        for _, row in roster_df.iterrows():
            adm = row['Admission_No']
            current_status = results.get(adm, 'A')
            has_photo = adm in st.session_state.student_photos
            review_data.append({
                "Admission_No": adm,
                "Name": row['Name'],
                "Section": row['Section'],
                "Roll_No": row['Roll_No'],
                "Status": current_status,
                "Has Photo": "✅" if has_photo else "❌"
            })

        review_df = pd.DataFrame(review_data)

        # Summary metrics
        present_count = sum(1 for s in results.values() if s == 'P')
        absent_count = sum(1 for s in results.values() if s == 'A')
        uncertain_count = sum(1 for s in results.values() if s == '?')
        total = len(roster_df)

        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("Present", present_count)
        mc2.metric("Absent", absent_count)
        mc3.metric("Uncertain (?)", uncertain_count)
        mc4.metric("Rate", f"{present_count/total*100:.1f}%" if total else "0%")

        # Editable status per student
        st.markdown("**Adjust statuses if needed (P = Present, A = Absent):**")

        with st.form("review_form"):
            updated_results = {}
            cols_per_row = 3
            student_rows = [review_data[i:i+cols_per_row] for i in range(0, len(review_data), cols_per_row)]

            for row_group in student_rows:
                form_cols = st.columns(cols_per_row)
                for fc, student in zip(form_cols, row_group):
                    with fc:
                        adm = student['Admission_No']
                        key = f"status_{adm}"
                        badge = "🟢" if student['Status'] == 'P' else ("🟡" if student['Status'] == '?' else "🔴")
                        new_status = st.selectbox(
                            f"{badge} {student['Name']}",
                            options=['P', 'A', '?'],
                            index=['P', 'A', '?'].index(student['Status']),
                            key=key
                        )
                        updated_results[adm] = new_status

            submitted = st.form_submit_button("✅ SAVE TO GOOGLE SHEETS", use_container_width=True, type="primary")

        if submitted:
            with st.spinner("Saving attendance..."):
                today = datetime.now().strftime('%Y-%m-%d')
                master = update_master_attendance(roster_df, updated_results, today)
                present_final = sum(1 for s in updated_results.values() if s == 'P')
                st.success(f"✅ Attendance saved for {today}! {present_final} present, {total - present_final} absent.")
                st.balloons()
                st.session_state.claude_results = updated_results

    elif st.session_state.roster is not None and not st.session_state.analysis_done:
        st.markdown("---")
        st.info("Upload a classroom photo and click 'Identify Attendance with Claude' to begin.")

    # ---------------------------
    # Master attendance view
    # ---------------------------
    if st.session_state.get('show_master'):
        st.markdown("---")
        st.subheader("📊 Master Attendance Sheet")
        master = read_sheet("Attendance")
        if master is not None and not master.empty:
            st.dataframe(master, use_container_width=True, height=400)
            c1, c2 = st.columns(2)
            with c1:
                csv = master.to_csv(index=False)
                st.download_button(
                    "📥 Download CSV",
                    csv,
                    f"attendance_{datetime.now().strftime('%Y%m%d')}.csv",
                    use_container_width=True
                )
            with c2:
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    master.to_excel(writer, index=False)
                output.seek(0)
                st.download_button(
                    "📥 Download Excel",
                    output,
                    f"attendance_{datetime.now().strftime('%Y%m%d')}.xlsx",
                    use_container_width=True
                )
        else:
            st.info("No attendance data yet.")

    st.markdown("---")
    st.markdown(
        "<center>📚 Face Attendance System (Claude Vision) | Data in Google Sheets ☁️</center>",
        unsafe_allow_html=True
    )

if __name__ == "__main__":
    main()
