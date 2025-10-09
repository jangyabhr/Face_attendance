import streamlit as st
import pandas as pd
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import io
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Page configuration
st.set_page_config(
    page_title="Face Tap Attendance System",
    page_icon="📸",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
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

# Initialize session state
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
if 'detection_params' not in st.session_state:
    st.session_state.detection_params = {
        'scaleFactor': 1.1,
        'minNeighbors': 5,
        'minSize': 40
    }
if 'sheets_connected' not in st.session_state:
    st.session_state.sheets_connected = False
if 'spreadsheet_id' not in st.session_state:
    st.session_state.spreadsheet_id = None

# Google Sheets Functions
def connect_to_sheets(credentials_dict, spreadsheet_id):
    """Connect to Google Sheets"""
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
    """Read data from Google Sheet"""
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
        if e.resp.status == 404:
            return None
        st.error(f"Error reading sheet: {str(e)}")
        return None

def write_sheet(sheet_name, df):
    """Write data to Google Sheet"""
    try:
        service = st.session_state.sheets_service
        spreadsheet_id = st.session_state.spreadsheet_id
        
        # Convert DataFrame to list of lists
        values = [df.columns.tolist()] + df.values.tolist()
        
        body = {'values': values}
        
        # Clear existing data
        service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A:Z"
        ).execute()
        
        # Write new data
        result = service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption='RAW',
            body=body
        ).execute()
        
        return True
    except Exception as e:
        st.error(f"Error writing sheet: {str(e)}")
        return False

def init_master_attendance(roster):
    """Initialize master attendance sheet"""
    df = roster.copy()
    df['Total_Present'] = '0'
    df['Total_Absent'] = '0'
    df['Attendance_%'] = '0'
    return df

def update_master_attendance(roster, assignments, date_str):
    """Update master attendance with today's data"""
    # Read existing master sheet
    master = read_sheet("Attendance")
    
    if master is None or master.empty:
        master = init_master_attendance(roster)
    
    # Add date column if doesn't exist
    if date_str not in master.columns:
        master[date_str] = 'A'
    
    # Update attendance
    present_indices = set(assignments.values())
    
    for idx, row in roster.iterrows():
        admission_no = row['Admission_No']
        
        # Find in master
        mask = master['Admission_No'] == admission_no
        
        if mask.any():
            master_idx = master[mask].index[0]
            
            # Check if already marked today
            current_status = master.loc[master_idx, date_str]
            
            if current_status not in ['P', 'A']:  # Not marked yet
                if idx in present_indices:
                    master.loc[master_idx, date_str] = 'P'
                    master.loc[master_idx, 'Total_Present'] = str(int(master.loc[master_idx, 'Total_Present']) + 1)
                else:
                    master.loc[master_idx, date_str] = 'A'
                    master.loc[master_idx, 'Total_Absent'] = str(int(master.loc[master_idx, 'Total_Absent']) + 1)
                
                # Calculate percentage
                total_p = int(master.loc[master_idx, 'Total_Present'])
                total_a = int(master.loc[master_idx, 'Total_Absent'])
                total_days = total_p + total_a
                
                if total_days > 0:
                    pct = round((total_p / total_days) * 100, 2)
                    master.loc[master_idx, 'Attendance_%'] = str(pct)
        else:
            # Add new student
            new_row = row.copy()
            new_row[date_str] = 'P' if idx in present_indices else 'A'
            new_row['Total_Present'] = '1' if idx in present_indices else '0'
            new_row['Total_Absent'] = '0' if idx in present_indices else '1'
            new_row['Attendance_%'] = '100' if idx in present_indices else '0'
            master = pd.concat([master, pd.DataFrame([new_row])], ignore_index=True)
    
    # Save to Google Sheets
    write_sheet("Attendance", master)
    return master

def save_face_memory():
    """Save face encodings to Google Sheets"""
    if not st.session_state.face_memory:
        return
    
    # Convert to DataFrame
    data = []
    for admission_no, encoding in st.session_state.face_memory.items():
        # Convert numpy array to string
        encoding_str = ','.join(map(str, encoding.tolist()))
        data.append({
            'Admission_No': admission_no,
            'Encoding': encoding_str
        })
    
    df = pd.DataFrame(data)
    write_sheet("FaceMemory", df)

def load_face_memory():
    """Load face encodings from Google Sheets"""
    df = read_sheet("FaceMemory")
    
    if df is None or df.empty:
        return {}
    
    memory = {}
    for _, row in df.iterrows():
        admission_no = row['Admission_No']
        encoding_str = row['Encoding']
        
        # Convert string back to numpy array
        encoding = np.array([float(x) for x in encoding_str.split(',')])
        memory[admission_no] = encoding
    
    return memory

# Face detection and recognition
def get_simple_face_encoding(image_array, face):
    """Create simple face encoding"""
    x, y, w, h = face['x'], face['y'], face['w'], face['h']
    face_crop = image_array[y:y+h, x:x+w]
    
    face_resized = cv2.resize(face_crop, (100, 100))
    gray = cv2.cvtColor(face_resized, cv2.COLOR_RGB2GRAY)
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
    hist = cv2.normalize(hist, hist).flatten()
    
    return hist

def match_face_to_saved(current_encoding, saved_encodings, threshold=0.65):
    """Match face to saved encodings"""
    best_match = None
    best_score = 0
    
    for admission_no, saved_encoding in saved_encodings.items():
        score = cv2.compareHist(current_encoding, saved_encoding, cv2.HISTCMP_CORREL)
        
        if score > best_score and score > threshold:
            best_score = score
            best_match = admission_no
    
    return best_match, best_score

def auto_assign_faces(faces, image_array, roster, saved_encodings):
    """Auto-assign faces based on saved encodings"""
    assignments = {}
    
    for face in faces:
        encoding = get_simple_face_encoding(image_array, face)
        matched_admission, confidence = match_face_to_saved(encoding, saved_encodings)
        
        if matched_admission:
            mask = roster['Admission_No'] == matched_admission
            if mask.any():
                roster_idx = roster[mask].index[0]
                assignments[face['id']] = roster_idx
    
    return assignments

def detect_faces(image_array, params):
    """Detect faces using OpenCV"""
    gray = cv2.cvtColor(image_array, cv2.COLOR_RGB2GRAY)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    
    faces = cascade.detectMultiScale(
        gray,
        scaleFactor=params['scaleFactor'],
        minNeighbors=params['minNeighbors'],
        minSize=(params['minSize'], params['minSize'])
    )
    
    face_list = []
    for i, (x, y, w, h) in enumerate(faces, start=1):
        face_list.append({
            'id': i,
            'x': int(x),
            'y': int(y),
            'w': int(w),
            'h': int(h)
        })
    
    return face_list

def draw_faces_on_image(image, faces, assignments, roster):
    """Draw faces on image"""
    img_draw = image.copy()
    draw = ImageDraw.Draw(img_draw)
    
    try:
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except:
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
        
        bbox = draw.textbbox((x, y-25), label, font=font_small)
        draw.rectangle([bbox[0]-5, bbox[1]-2, bbox[2]+5, bbox[3]+2], fill=color)
        draw.text((x, y-25), label, fill="white", font=font_small)
    
    return img_draw

def search_roster(roster, query):
    """Search roster"""
    if not query:
        return roster
    
    query = query.lower()
    mask = (
        roster['Name'].str.lower().str.contains(query, regex=False) |
        roster['Admission_No'].str.lower().str.contains(query, regex=False) |
        roster['Section'].str.lower().str.contains(query, regex=False) |
        roster['Roll_No'].str.lower().str.contains(query, regex=False)
    )
    return roster[mask]

# Main App
def main():
    st.title("📸 Face Attendance with Google Sheets")
    
    # Setup instructions
    with st.expander("⚙️ SETUP INSTRUCTIONS (First Time Only)", expanded=not st.session_state.sheets_connected):
        st.markdown("""
        ### 📋 One-Time Setup (5 minutes):
        
        **Step 1: Create Google Sheet**
        1. Go to [Google Sheets](https://sheets.google.com)
        2. Create new sheet named "School Attendance" (or any name)
        3. Note the Spreadsheet ID from URL: `https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit`
        
        **Step 2: Enable Google Sheets API**
        1. Go to [Google Cloud Console](https://console.cloud.google.com)
        2. Create new project (or select existing)
        3. Enable "Google Sheets API"
        4. Create Service Account:
           - Go to "Credentials" → "Create Credentials" → "Service Account"
           - Name it "attendance-app"
           - Create Key (JSON format)
           - Download the JSON file
        
        **Step 3: Share Sheet with Service Account**
        1. Open your Google Sheet
        2. Click "Share" button
        3. Add the service account email (from JSON: `client_email`)
        4. Give "Editor" permission
        
        **Step 4: Upload Credentials Below** ⬇️
        
        ### 💡 After Setup:
        - ✅ Data saved permanently in Google Sheets
        - ✅ Access from anywhere
        - ✅ Multiple teachers can use (just share the sheet)
        - ✅ Automatic backups (Google's infrastructure)
        """)
    
    st.markdown("---")
    
    # Connection setup - Auto-connect using Streamlit secrets
    if not st.session_state.sheets_connected:
        st.subheader("🔗 Connect to Google Sheets")
        
        # Try to auto-connect using secrets
        try:
            if "gcp_service_account" in st.secrets and "spreadsheet_id" in st.secrets:
                with st.spinner("Connecting to Google Sheets..."):
                    credentials_dict = dict(st.secrets["gcp_service_account"])
                    spreadsheet_id = st.secrets["spreadsheet_id"]
                    
                    if connect_to_sheets(credentials_dict, spreadsheet_id):
                        st.success("✅ Connected to Google Sheets!")
                        
                        # Load roster if exists
                        roster = read_sheet("Roster")
                        if roster is not None and not roster.empty:
                            st.session_state.roster = roster
                            st.success(f"✅ Loaded roster: {len(roster)} students")
                        
                        # Load face memory
                        st.session_state.face_memory = load_face_memory()
                        if st.session_state.face_memory:
                            st.info(f"🧠 Loaded {len(st.session_state.face_memory)} saved faces")
                        
                        st.rerun()
            else:
                st.error("❌ Secrets not configured!")
                st.info("👉 Add credentials in Streamlit Cloud app settings → Secrets")
                with st.expander("📋 How to Configure Secrets"):
                    st.code("""
# In Streamlit Cloud: Settings → Secrets
# Add this configuration:

spreadsheet_id = "YOUR_SPREADSHEET_ID"

[gcp_service_account]
type = "service_account"
project_id = "your-project-id"
private_key_id = "your-private-key-id"
private_key = "-----BEGIN PRIVATE KEY-----\\nYOUR_PRIVATE_KEY\\n-----END PRIVATE KEY-----\\n"
client_email = "your-service-account@your-project.iam.gserviceaccount.com"
client_id = "your-client-id"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "https://www.googleapis.com/robot/v1/metadata/x509/..."
                    """, language="toml")
                st.stop()
        except Exception as e:
            st.error(f"Connection failed: {str(e)}")
            st.info("Check your secrets configuration")
            st.stop()
    
    # Main interface (after connected)
    st.success(f"🔗 Connected to Google Sheets")
    
    # Sidebar
    with st.sidebar:
        st.header("⚙️ Daily Controls")
        
        st.metric("📅 Date", datetime.now().strftime('%b %d, %Y'))
        
        st.markdown("---")
        
        # Roster management
        st.subheader("👥 Roster")
        
        if st.button("📥 Load from Sheets", use_container_width=True):
            roster = read_sheet("Roster")
            if roster is not None and not roster.empty:
                st.session_state.roster = roster
                st.success(f"✅ Loaded {len(roster)} students")
            else:
                st.info("No roster found. Upload CSV below.")
        
        roster_file = st.file_uploader(
            "Or Upload New Roster CSV",
            type=['csv'],
            help="Columns: Admission_No, Name, Section, Roll_No"
        )
        
        if roster_file:
            try:
                df = pd.read_csv(roster_file, dtype=str).fillna("")
                required = {"Admission_No", "Name", "Section", "Roll_No"}
                
                if not required.issubset(set(df.columns)):
                    st.error(f"❌ Must have: {', '.join(required)}")
                else:
                    st.session_state.roster = df
                    # Save to Google Sheets
                    if write_sheet("Roster", df):
                        st.success(f"✅ Roster saved: {len(df)} students")
            except Exception as e:
                st.error(f"Error: {str(e)}")
        
        if st.session_state.roster is not None:
            st.metric("Students", len(st.session_state.roster))
        
        st.markdown("---")
        
        # Photo upload
        st.subheader("📸 Today's Photo")
        
        photo_file = st.file_uploader(
            "Upload classroom photo",
            type=['jpg', 'jpeg', 'png']
        )
        
        if photo_file:
            image = Image.open(photo_file).convert('RGB')
            st.session_state.photo = np.array(image)
            st.session_state.photo_name = photo_file.name
            st.success("✅ Photo loaded")
        
        st.markdown("---")
        
        # Detection
        st.subheader("🔍 Detection")
        
        with st.expander("⚙️ Settings"):
            st.session_state.detection_params['scaleFactor'] = st.slider(
                "Scale Factor", 1.05, 1.5, 1.1, 0.05
            )
            st.session_state.detection_params['minNeighbors'] = st.slider(
                "Min Neighbors", 3, 10, 5
            )
            st.session_state.detection_params['minSize'] = st.slider(
                "Min Size", 20, 100, 40
            )
        
        if st.button("🔍 Detect & Auto-Assign", use_container_width=True):
            if st.session_state.photo is None:
                st.error("❌ Upload photo first")
            elif st.session_state.roster is None:
                st.error("❌ Load roster first")
            else:
                with st.spinner("Detecting..."):
                    faces = detect_faces(st.session_state.photo, st.session_state.detection_params)
                    st.session_state.faces = faces
                    
                    if faces:
                        st.success(f"✅ Found {len(faces)} faces")
                        
                        if st.session_state.face_memory:
                            with st.spinner("🧠 Auto-assigning..."):
                                auto = auto_assign_faces(
                                    faces,
                                    st.session_state.photo,
                                    st.session_state.roster,
                                    st.session_state.face_memory
                                )
                                st.session_state.assignments = auto
                                if auto:
                                    st.success(f"🎯 Auto-assigned {len(auto)} faces!")
                        else:
                            st.session_state.assignments = {}
                            st.info("💡 First time: Teach system by assigning manually")
                    else:
                        st.warning("⚠️ No faces found")
        
        st.markdown("---")
        
        if st.button("🗑️ Clear Assignments", use_container_width=True):
            st.session_state.assignments = {}
            st.rerun()
        
        if st.button("📊 View Master Sheet", use_container_width=True):
            st.session_state.show_master = True
    
    # Main content
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader("📷 Classroom View")
        
        if st.session_state.photo is not None and st.session_state.faces:
            img_pil = Image.fromarray(st.session_state.photo)
            img_with_faces = draw_faces_on_image(
                img_pil,
                st.session_state.faces,
                st.session_state.assignments,
                st.session_state.roster
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
                
                for face in unassigned[:3]:  # Show max 3 at once
                    with st.expander(f"Face #{face['id']}", expanded=True):
                        search = st.text_input(
                            "Search",
                            key=f"s_{face['id']}",
                            placeholder="Name, Roll..."
                        )
                        
                        filtered = search_roster(st.session_state.roster, search)
                        
                        if len(filtered) > 0:
                            options = []
                            for idx, row in filtered.iterrows():
                                disp = f"{row['Name']} ({row['Section']}) - R:{row['Roll_No']}"
                                options.append((disp, idx))
                            
                            selected = st.selectbox(
                                "Student",
                                options=options,
                                format_func=lambda x: x[0],
                                key=f"sel_{face['id']}"
                            )
                            
                            if st.button("✅ Assign", key=f"a_{face['id']}", use_container_width=True):
                                idx = selected[1]
                                admission_no = st.session_state.roster.iloc[idx]['Admission_No']
                                
                                st.session_state.assignments[face['id']] = idx
                                
                                # Learn face
                                encoding = get_simple_face_encoding(st.session_state.photo, face)
                                st.session_state.face_memory[admission_no] = encoding
                                
                                st.success("✅ Assigned & Learned!")
                                st.rerun()
                
                if len(unassigned) > 3:
                    st.info(f"📋 {len(unassigned) - 3} more faces to assign...")
            else:
                st.success("✅ All assigned!")
            
            st.markdown("---")
            
            if st.session_state.assignments:
                st.markdown(f"### 🟢 Assigned ({len(st.session_state.assignments)})")
                
                for face_id, idx in list(st.session_state.assignments.items())[:5]:
                    student = st.session_state.roster.iloc[idx]
                    col_a, col_b = st.columns([4, 1])
                    
                    with col_a:
                        st.text(f"#{face_id}: {student['Name']}")
                    with col_b:
                        if st.button("❌", key=f"rm_{face_id}"):
                            del st.session_state.assignments[face_id]
                            st.rerun()
    
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
            pct = (present/total*100) if total > 0 else 0
            st.metric("Rate", f"{pct:.1f}%")
        
        with col3:
            learned = len(st.session_state.face_memory)
            st.metric("Learned", learned)
        
        if st.button("✅ SAVE TO GOOGLE SHEETS", use_container_width=True, type="primary"):
            with st.spinner("Saving..."):
                today = datetime.now().strftime('%Y-%m-%d')
                
                # Update master attendance
                master = update_master_attendance(
                    st.session_state.roster,
                    st.session_state.assignments,
                    today
                )
                
                # Save face memory
                save_face_memory()
                
                st.success(f"✅ Saved for {today}!")
                st.balloons()
                st.info(f"📊 {present} present, {total-present} absent")
    
    # View master
    if st.session_state.get('show_master'):
        st.markdown("---")
        st.subheader("📊 Master Attendance Sheet")
        
        master = read_sheet("Attendance")
        if master is not None and not master.empty:
            st.dataframe(master, use_container_width=True, height=400)
            
            col1, col2 = st.columns(2)
            
            with col1:
                csv = master.to_csv(index=False)
                st.download_button(
                    "📥 CSV",
                    csv,
                    f"attendance_{datetime.now().strftime('%Y%m%d')}.csv",
                    use_container_width=True
                )
            
            with col2:
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    master.to_excel(writer, index=False)
                output.seek(0)
                
                st.download_button(
                    "📥 Excel",
                    output,
                    f"attendance_{datetime.now().strftime('%Y%m%d')}.xlsx",
                    use_container_width=True
                )
        else:
            st.info("No data yet")
        
        if st.button("Close"):
            st.session_state.show_master = False
            st.rerun()
    
    st.markdown("---")
    st.markdown("<center>📚 Face Attendance System | Data in Google Sheets ☁️</center>", unsafe_allow_html=True)

if __name__ == "__main__":
    main()
