
import streamlit as st
import pandas as pd
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import io
import json
import face_recognition  # New library for face recognition
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ---------------------------
# Page configuration & styles
# ---------------------------
st.set_page_config(
    page_title="Face Tap Attendance System (Upgraded)",
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
if 'faces' not in st.session_state:
    st.session_state.faces = []
if 'assignments' not in st.session_state:
    st.session_state.assignments = {}
if 'photo' not in st.session_state:
    st.session_state.photo = None
if 'photo_name' not in st.session_state:
    st.session_state.photo_name = None
if 'face_memory' not in st.session_state:
    st.session_state.face_memory = {}  # admission_no -> face_encoding
if 'sheets_connected' not in st.session_state:
    st.session_state.sheets_connected = False
if 'spreadsheet_id' not in st.session_state:
    st.session_state.spreadsheet_id = None
if 'show_master' not in st.session_state:
    st.session_state.show_master = False
if 'match_tolerance' not in st.session_state:
    st.session_state.match_tolerance = 0.6  # Lower is stricter

# ---------------------------
# Google Sheets helpers (No changes needed here)
# ---------------------------
def connect_to_sheets(credentials_dict, spreadsheet_id):
    """Connect to Google Sheets using service account JSON (dict) and Spreadsheet ID."""
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
    """Read entire A:Z range from a sheet tab into a DataFrame (or None)."""
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
    """Create a new sheet tab if it doesn't exist."""
    try:
        service = st.session_state.sheets_service
        spreadsheet_id = st.session_state.spreadsheet_id
        spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        existing = [s['properties']['title'] for s in spreadsheet.get('sheets', [])]
        if sheet_name not in existing:
            body = {'requests': [{'addSheet': {'properties': {'title': sheet_name}}}]}
            service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
            return True
        return False
    except Exception as e:
        st.error(f"Error creating sheet: {str(e)}")
        return False

def write_sheet(sheet_name, df):
    """Write DataFrame to Google Sheet tab from A1 using USER_ENTERED so numbers are numbers."""
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

def update_master_attendance(roster, assignments, date_str):
    master = read_sheet("Attendance")
    if master is None or master.empty:
        master = init_master_attendance(roster)

    if 'Admission_No' not in master.columns and 'Admission_No' in roster.columns:
        master = init_master_attendance(roster)

    if date_str not in master.columns:
        master[date_str] = ''

    present_indices = set(assignments.values())

    for idx, row in roster.iterrows():
        admission_no = row['Admission_No']
        mask = master['Admission_No'] == admission_no

        if mask.any():
            m_idx = master[mask].index[0]
            if master.loc[m_idx, date_str] not in ['P', 'A', 'p', 'a']:
                is_present = idx in present_indices
                master.loc[m_idx, date_str] = 'P' if is_present else 'A'

                tp_raw = str(master.loc[m_idx, 'Total_Present'])
                ta_raw = str(master.loc[m_idx, 'Total_Absent'])
                tp = int(tp_raw) if tp_raw.isdigit() else 0
                ta = int(ta_raw) if ta_raw.isdigit() else 0

                tp += 1 if is_present else 0
                ta += 0 if is_present else 1

                master.loc[m_idx, 'Total_Present'] = str(tp)
                master.loc[m_idx, 'Total_Absent'] = str(ta)
                total_days = tp + ta
                master.loc[m_idx, 'Attendance_%'] = str(round((tp / total_days) * 100, 2) if total_days else 0.0)
        else:
            is_present = idx in present_indices
            new_row = row.copy()
            new_row['Total_Present'] = '1' if is_present else '0'
            new_row['Total_Absent'] = '0' if is_present else '1'
            new_row['Attendance_%'] = '100' if is_present else '0'
            new_row[date_str] = 'P' if is_present else 'A'
            master = pd.concat([master, pd.DataFrame([new_row])], ignore_index=True)

    write_sheet("Attendance", master)
    return master

def save_face_memory():
    if not st.session_state.face_memory:
        return
    data = []
    for admission_no, encoding in st.session_state.face_memory.items():
        if encoding is None:
            continue
        encoding_str = ','.join(map(str, encoding.tolist()))
        data.append({'Admission_No': admission_no, 'Encoding': encoding_str})
    if data:
        df = pd.DataFrame(data)
        write_sheet("FaceMemory", df)

def load_face_memory():
    df = read_sheet("FaceMemory")
    if df is None or df.empty:
        return {}
    memory = {}
    try:
        for _, row in df.iterrows():
            admission_no = row.get('Admission_No', '')
            encoding_str = row.get('Encoding', '')
            if not admission_no or not encoding_str:
                continue
            encoding = np.array([float(x) for x in encoding_str.split(',')])
            memory[admission_no] = encoding
    except Exception as e:
        st.warning(f"Error loading face memory: {str(e)}")
        return {}
    return memory

# -------------------------------------------------
# Face detection & matching (UPGRADED LOGIC)
# -------------------------------------------------
def get_face_encoding(image_array, face_location):
    """Generate a 128-d face encoding for a given face location."""
    try:
        # The face_recognition library needs a list of locations.
        encodings = face_recognition.face_encodings(image_array, [face_location])
        if encodings:
            return encodings[0]
        return None
    except Exception as e:
        st.warning(f"Error creating face encoding: {str(e)}")
        return None

def match_face_to_saved(current_encoding, saved_encodings, tolerance=0.6):
    """Find the best match for a face encoding from saved encodings."""
    best_match = None
    if current_encoding is None or not saved_encodings:
        return None

    try:
        known_encodings = list(saved_encodings.values())
        known_admissions = list(saved_encodings.keys())

        # Get the distance of the current face to all known faces
        distances = face_recognition.face_distance(known_encodings, current_encoding)

        if len(distances) > 0:
            best_match_index = np.argmin(distances)
            # If the best match is within the tolerance, we have a match
            if distances[best_match_index] <= tolerance:
                best_match = known_admissions[best_match_index]
    except Exception as e:
        st.warning(f"Face matching error: {str(e)}")
        return None
    return best_match

def auto_assign_faces(faces, image_array, roster, saved_encodings, tolerance=0.6):
    """Auto-assign faces by matching encodings with saved face memory."""
    assignments = {}
    for face in faces:
        # Convert face dict to the (top, right, bottom, left) tuple format
        top, left, h, w = face['y'], face['x'], face['h'], face['w']
        face_location = (top, left + w, top + h, left)

        encoding = get_face_encoding(image_array, face_location)
        if encoding is not None:
            matched_admission = match_face_to_saved(encoding, saved_encodings, tolerance=tolerance)
            if matched_admission:
                mask = roster['Admission_No'] == matched_admission
                if mask.any():
                    roster_idx = roster[mask].index[0]
                    assignments[face['id']] = roster_idx
    return assignments

def detect_faces(image_array):
    """Detect faces using face_recognition library (more accurate)."""
    # This returns a list of tuples (top, right, bottom, left)
    face_locations = face_recognition.face_locations(image_array)

    face_list = []
    for i, (top, right, bottom, left) in enumerate(face_locations, start=1):
        # Convert back to x, y, w, h for consistency with the rest of the app
        face_list.append({'id': i, 'x': int(left), 'y': int(top), 'w': int(right - left), 'h': int(bottom - top)})
    return face_list

def draw_faces_on_image(image, faces, assignments, roster):
    """Draw rectangles and labels on the class photo."""
    img_draw = image.copy()
    draw = ImageDraw.Draw(img_draw)
    try:
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except Exception:
        font_small = ImageFont.load_default()

    for face in faces:
        face_id = face['id']
        x, y, w, h = face['x'], face['y'], face['w'], face['h']
        if face_id in assignments:
            color = "#4CAF50"
            idx = assignments[face_id]
            if roster is not None and idx < len(roster):
                name = roster.iloc[idx]['Name']
                label = f"#{face_id}: {name}"
            else:
                label = f"#{face_id} ✓"
            width = 4
        else:
            color = "#FF9800"
            label = f"#{face_id}"
            width = 3

        draw.rectangle([x, y, x+w, y+h], outline=color, width=width)
        try:
            bbox = draw.textbbox((x, y-25), label, font=font_small)
            draw.rectangle([bbox[0]-5, bbox[1]-2, bbox[2]+5, bbox[3]+2], fill=color)
        except Exception:
            pass
        draw.text((x, max(0, y-25)), label, fill="white", font=font_small)
    return img_draw

def search_roster(roster, query):
    """Filter roster by Name / Admission_No / Section / Roll_No (contains)."""
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
# Main App
# ---------------------------
def main():
    st.title("✨ Face Attendance (Upgraded Engine)")

    with st.expander("⚙️ SETUP INSTRUCTIONS (First Time Only)", expanded=not st.session_state.sheets_connected):
        st.markdown("""
        ### 📋 One-Time Setup
        **Step 1:** Create a Google Sheet.
        **Step 2:** Enable *Google Sheets API* & create a **Service Account** key.
        **Step 3:** Share the Sheet with the **service account email** as *Editor*.
        **Step 4:** Connect below (auto via `st.secrets` or manual upload/paste).
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
                        st.session_state.face_memory = load_face_memory()
                        if st.session_state.face_memory:
                            st.info(f"🧠 Loaded {len(st.session_state.face_memory)} saved faces")
        except Exception as e:
            st.error(f"Auto connect failed: {e}")

        if not auto_connected:
            st.info("Provide credentials manually if secrets are not configured.")
            creds_method = st.radio("How will you provide credentials?", ["Upload JSON", "Paste JSON"], horizontal=True)

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
                        st.success("✅ Connected to Google Sheets!")
                        roster = read_sheet("Roster")
                        if roster is not None and not roster.empty:
                            st.session_state.roster = roster
                            st.success(f"✅ Loaded roster: {len(roster)} students")
                        st.session_state.face_memory = load_face_memory()
                        if st.session_state.face_memory:
                            st.info(f"🧠 Loaded {len(st.session_state.face_memory)} saved faces")
                        st.rerun()
            st.stop()

    # Main UI (when connected)
    st.success("🔗 Connected to Google Sheets")

    with st.sidebar:
        st.header("⚙️ Daily Controls")
        st.metric("📅 Date", datetime.now().strftime('%b %d, %Y'))
        st.markdown("---")

        st.subheader("👥 Roster")
        if st.button("📥 Load from Sheets", use_container_width=True):
            roster = read_sheet("Roster")
            if roster is not None and not roster.empty:
                st.session_state.roster = roster
                st.success(f"✅ Loaded {len(roster)} students")
            else:
                st.info("No roster found. Upload CSV below.")

        roster_file = st.file_uploader("Or Upload New Roster CSV", type=['csv'], help="Columns: Admission_No, Name, Section, Roll_No")
        if roster_file:
            try:
                df = pd.read_csv(roster_file, dtype=str).fillna("")
                required = {"Admission_No", "Name", "Section", "Roll_No"}
                if not required.issubset(set(df.columns)):
                    st.error(f"❌ Must have: {', '.join(required)}")
                else:
                    st.session_state.roster = df
                    if write_sheet("Roster", df):
                        st.success(f"✅ Roster saved: {len(df)} students")
            except Exception as e:
                st.error(f"Error: {str(e)}")

        if st.session_state.roster is not None:
            st.metric("Students", len(st.session_state.roster))

        st.markdown("---")

        st.subheader("📸 Today's Photo")
        photo_file = st.file_uploader("Upload classroom photo", type=['jpg', 'jpeg', 'png'])
        if photo_file:
            image = Image.open(photo_file).convert('RGB')
            st.session_state.photo = np.array(image)
            st.session_state.photo_name = photo_file.name
            st.success("✅ Photo loaded")

        st.markdown("---")

        st.subheader("🔍 Detection & Matching")
        st.session_state.match_tolerance = st.slider("Match Tolerance", 0.4, 0.7, st.session_state.match_tolerance, 0.01, help="Lower is stricter. Default is 0.6.")

        if st.button("🔍 Detect & Auto-Assign", use_container_width=True):
            if st.session_state.photo is None:
                st.error("❌ Upload photo first")
            elif st.session_state.roster is None:
                st.error("❌ Load roster first")
            else:
                with st.spinner("Detecting faces..."):
                    faces = detect_faces(st.session_state.photo)
                    st.session_state.faces = faces
                    st.session_state.assignments = st.session_state.assignments or {}

                    if faces:
                        st.success(f"✅ Found {len(faces)} faces")
                        if st.session_state.face_memory:
                            with st.spinner("🧠 Auto-assigning..."):
                                auto = auto_assign_faces(
                                    faces,
                                    st.session_state.photo,
                                    st.session_state.roster,
                                    st.session_state.face_memory,
                                    tolerance=st.session_state.match_tolerance
                                )
                                st.session_state.assignments.update(auto)
                                if auto:
                                    st.success(f"🎯 Auto-assigned {len(auto)} faces!")
                        else:
                            st.info("💡 First time: Teach system by assigning manually")
                    else:
                        st.warning("⚠️ No faces found")

        st.markdown("---")

        if st.button("🗑️ Clear Assignments", use_container_width=True):
            st.session_state.assignments = {}
            st.rerun()

        if st.button("📊 View Master Sheet", use_container_width=True):
            st.session_state.show_master = True

    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("📷 Classroom View")
        if st.session_state.photo is not None and st.session_state.faces:
            img_pil = Image.fromarray(st.session_state.photo)
            img_with_faces = draw_faces_on_image(
                img_pil, st.session_state.faces, st.session_state.assignments, st.session_state.roster
            )
            st.image(img_with_faces, use_container_width=True)

            assigned = len(st.session_state.assignments)
            total = len(st.session_state.faces)
            st.info(f"🟢 Assigned: {assigned} | 🟠 Pending: {total - assigned}")

        elif st.session_state.photo is not None:
            st.image(st.session_state.photo, use_container_width=True)
        else:
            st.info("👈 Upload photo to begin")

    with col2:
        st.subheader("👤 Assign Faces")

        if st.session_state.faces and st.session_state.roster is not None:
            unassigned = [f for f in st.session_state.faces if f['id'] not in st.session_state.assignments]

            if unassigned:
                st.markdown(f"### 🟠 Unassigned ({len(unassigned)})")

                for face in unassigned[:3]:
                    with st.expander(f"Face #{face['id']}", expanded=True):
                        try:
                            x, y, w, h = face['x'], face['y'], face['w'], face['h']
                            padding = 20
                            y1, y2 = max(0, y - padding), min(st.session_state.photo.shape[0], y + h + padding)
                            x1, x2 = max(0, x - padding), min(st.session_state.photo.shape[1], x + w + padding)

                            face_crop = st.session_state.photo[y1:y2, x1:x2]
                            face_img = Image.fromarray(face_crop)
                            face_img.thumbnail((150, 150), Image.LANCZOS)
                            st.image(face_img, caption=f"Face #{face['id']}", use_container_width=False)
                        except Exception:
                            st.warning("Could not load face preview")

                        if st.button("🗑️ Delete This Face", key=f"del_{face['id']}", use_container_width=True):
                            st.session_state.faces = [f for f in st.session_state.faces if f['id'] != face['id']]
                            st.session_state.faces = [{**f, 'id': i + 1} for i, f in enumerate(st.session_state.faces)]
                            st.session_state.assignments = {
                                new_id: idx for old_id, idx in st.session_state.assignments.items()
                                if (new_id := next((nf['id'] for nf in st.session_state.faces if st.session_state.faces[old_id-1]['x'] == nf['x']), None))
                            }
                            st.success(f"Deleted Face (and re-numbered)")
                            st.rerun()

                        st.markdown("---")

                        search = st.text_input("Search Student", key=f"s_{face['id']}", placeholder="Name, Roll, Admission...")
                        filtered = search_roster(st.session_state.roster, search) if search is not None else st.session_state.roster

                        if len(filtered) > 0:
                            options = [(f"{row['Name']} ({row['Section']}) - R:{row['Roll_No']}", idx) for idx, row in filtered.iterrows()]
                            selected = st.selectbox("Select Student", options=options, format_func=lambda x: x[0], key=f"sel_{face['id']}")

                            if st.button("✅ Assign", key=f"a_{face['id']}", use_container_width=True):
                                idx = selected[1]
                                admission_no = st.session_state.roster.iloc[idx]['Admission_No']
                                st.session_state.assignments[face['id']] = idx

                                top, left, h, w = face['y'], face['x'], face['h'], face['w']
                                face_location = (top, left + w, top + h, left)
                                encoding = get_face_encoding(st.session_state.photo, face_location)
                                if encoding is not None:
                                    st.session_state.face_memory[admission_no] = encoding

                                st.success("✅ Assigned & Learned!")
                                st.rerun()
                        else:
                            st.info("No students found.")
                if len(unassigned) > 3:
                    st.info(f"📋 {len(unassigned) - 3} more faces to assign.")
            else:
                st.success("✅ All faces assigned!")

            st.markdown("---")

            if st.session_state.assignments:
                st.markdown(f"### 🟢 Assigned ({len(st.session_state.assignments)})")
                for face_id, idx in list(st.session_state.assignments.items())[:10]:
                    student = st.session_state.roster.iloc[idx]
                    face = next((f for f in st.session_state.faces if f['id'] == face_id), None)
                    with st.container():
                        col_img, col_info = st.columns([1, 3])
                        with col_img:
                            if face:
                                try:
                                    x, y, w, h = face['x'], face['y'], face['w'], face['h']
                                    face_crop = st.session_state.photo[y:y+h, x:x+w]
                                    face_img = Image.fromarray(face_crop)
                                    face_img.thumbnail((60, 60), Image.LANCZOS)
                                    st.image(face_img, use_container_width=True)
                                except Exception:
                                    st.write(f"#{face_id}")
                            else:
                                st.write(f"#{face_id}")
                        with col_info:
                            st.text(f"{student['Name']}")
                            st.caption(f"Section: {student['Section']} | Roll: {student['Roll_No']}")
                            if st.button("❌ Remove", key=f"rm_{face_id}"):
                                del st.session_state.assignments[face_id]
                                st.rerun()
                        st.markdown("---")
                if len(st.session_state.assignments) > 10:
                    st.caption(f"+ {len(st.session_state.assignments) - 10} more assigned")

    # Save section
    if st.session_state.roster is not None and st.session_state.assignments:
        st.markdown("---")
        st.subheader("💾 Save Attendance")
        col1, col2, col3 = st.columns(3)
        with col1:
            present = len(set(st.session_state.assignments.values()))
            total = len(st.session_state.roster)
            st.metric("Present", f"{present}/{total}")
        with col2:
            pct = (present / total * 100) if total > 0 else 0
            st.metric("Rate", f"{pct:.1f}%")
        with col3:
            learned = len(st.session_state.face_memory)
            st.metric("Learned", learned)

        if st.button("✅ SAVE TO GOOGLE SHEETS", use_container_width=True, type="primary"):
            with st.spinner("Saving..."):
                today = datetime.now().strftime('%Y-%m-%d')
                master = update_master_attendance(
                    st.session_state.roster,
                    st.session_state.assignments,
                    today
                )
                save_face_memory()
                st.success(f"✅ Saved for {today}!")
                st.balloons()
                st.info(f"📊 {present} present, {total - present} absent")

    # View master
    if st.session_state.get('show_master'):
        st.markdown("---")
        st.subheader("📊 Master Attendance Sheet")
        master = read_sheet("Attendance")
        if master is not None and not master.empty:
            st.dataframe(master, use_container_width=True, height=400)
            c1, c2 = st.columns(2)
            with c1:
                csv = master.to_csv(index=False)
                st.download_button("📥 CSV", csv, f"attendance_{datetime.now().strftime('%Y%m%d')}.csv", use_container_width=True)
            with c2:
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    master.to_excel(writer, index=False)
                output.seek(0)
                st.download_button("📥 Excel", output, f"attendance_{datetime.now().strftime('%Y%m%d')}.xlsx", use_container_width=True)
        else:
            st.info("No data yet")
        if st.button("Close"):
            st.session_state.show_master = False
            st.rerun()

    st.markdown("---")
    st.markdown("<center>📚 Face Attendance System | Data in Google Sheets ☁️</center>", unsafe_allow_html=True)

if __name__ == "__main__":
    main()
