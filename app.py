import streamlit as st
import pandas as pd
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import io
import base64

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
    .stats-box {
        background-color: #1e1e1e;
        padding: 20px;
        border-radius: 10px;
        border-left: 5px solid #4CAF50;
        margin: 10px 0;
    }
    .face-box {
        border: 3px solid #FF9800;
        cursor: pointer;
        transition: all 0.3s;
    }
    .face-box:hover {
        border-color: #4CAF50;
        box-shadow: 0 0 10px #4CAF50;
    }
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
    st.session_state.assignments = {}  # face_id -> roster_index
if 'photo' not in st.session_state:
    st.session_state.photo = None
if 'photo_name' not in st.session_state:
    st.session_state.photo_name = None
if 'detection_params' not in st.session_state:
    st.session_state.detection_params = {
        'scaleFactor': 1.1,
        'minNeighbors': 5,
        'minSize': 40
    }

# Helper Functions
def load_roster(uploaded_file):
    """Load and validate roster CSV"""
    try:
        df = pd.read_csv(uploaded_file, dtype=str).fillna("")
        required = {"Admission_No", "Name", "Section", "Roll_No"}
        if not required.issubset(set(df.columns)):
            st.error(f"❌ Roster must contain columns: {', '.join(required)}")
            return None
        st.session_state.roster = df
        return df
    except Exception as e:
        st.error(f"❌ Error loading roster: {str(e)}")
        return None

def detect_faces(image_array, params):
    """Detect faces in image using OpenCV"""
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
    """Draw rectangles and labels on image"""
    img_draw = image.copy()
    draw = ImageDraw.Draw(img_draw)
    
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    except:
        font = ImageFont.load_default()
        font_small = ImageFont.load_default()
    
    for face in faces:
        face_id = face['id']
        x, y, w, h = face['x'], face['y'], face['w'], face['h']
        
        # Check if assigned
        if face_id in assignments:
            color = "#4CAF50"  # Green for assigned
            idx = assignments[face_id]
            if roster is not None and idx < len(roster):
                name = roster.iloc[idx]['Name']
                label = f"#{face_id}: {name}"
            else:
                label = f"#{face_id} ✓"
            width = 4
        else:
            color = "#FF9800"  # Orange for unassigned
            label = f"#{face_id}"
            width = 3
        
        # Draw rectangle
        draw.rectangle([x, y, x+w, y+h], outline=color, width=width)
        
        # Draw label with background
        bbox = draw.textbbox((x, y-25), label, font=font_small)
        draw.rectangle([bbox[0]-5, bbox[1]-2, bbox[2]+5, bbox[3]+2], fill=color)
        draw.text((x, y-25), label, fill="white", font=font_small)
    
    return img_draw

def search_roster(roster, query):
    """Search roster by name, admission number, section, or roll number"""
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

def generate_attendance_report(roster, assignments, photo_name):
    """Generate attendance DataFrame"""
    present_indices = set(assignments.values())
    
    attendance = []
    for idx, row in roster.iterrows():
        attendance.append({
            'Admission_No': row['Admission_No'],
            'Roll_No': row['Roll_No'],
            'Name': row['Name'],
            'Section': row['Section'],
            'Status': 'Present' if idx in present_indices else 'Absent',
            'Date': datetime.now().strftime('%Y-%m-%d'),
            'Time': datetime.now().strftime('%H:%M:%S'),
            'Photo': photo_name or ''
        })
    
    return pd.DataFrame(attendance)

def get_download_link(df, filename, file_format='xlsx'):
    """Generate download link for DataFrame"""
    if file_format == 'xlsx':
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Attendance')
        output.seek(0)
        b64 = base64.b64encode(output.read()).decode()
        mime_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    else:  # CSV
        output = io.StringIO()
        df.to_csv(output, index=False)
        b64 = base64.b64encode(output.getvalue().encode()).decode()
        mime_type = 'text/csv'
    
    return f'data:{mime_type};base64,{b64}'

# Main App
def main():
    st.title("📸 Face Tap Attendance System")
    st.markdown("---")
    
    # Sidebar
    with st.sidebar:
        st.header("⚙️ Settings")
        
        # Roster Upload
        st.subheader("1️⃣ Load Roster")
        roster_file = st.file_uploader(
            "Upload CSV (Admission_No, Name, Section, Roll_No)",
            type=['csv'],
            help="CSV must contain: Admission_No, Name, Section, Roll_No"
        )
        
        if roster_file:
            roster = load_roster(roster_file)
            if roster is not None:
                st.success(f"✅ Loaded {len(roster)} students")
        
        st.markdown("---")
        
        # Photo Upload
        st.subheader("2️⃣ Upload Photo")
        photo_file = st.file_uploader(
            "Upload classroom photo",
            type=['jpg', 'jpeg', 'png'],
            help="Take a clear group photo with good lighting"
        )
        
        if photo_file:
            image = Image.open(photo_file).convert('RGB')
            st.session_state.photo = np.array(image)
            st.session_state.photo_name = photo_file.name
            st.success(f"✅ Photo loaded: {photo_file.name}")
        
        st.markdown("---")
        
        # Detection Settings
        st.subheader("3️⃣ Detection Settings")
        with st.expander("🔧 Adjust Parameters", expanded=False):
            st.session_state.detection_params['scaleFactor'] = st.slider(
                "Scale Factor",
                min_value=1.05,
                max_value=1.5,
                value=1.1,
                step=0.05,
                help="Lower = more faces (slower)"
            )
            
            st.session_state.detection_params['minNeighbors'] = st.slider(
                "Min Neighbors",
                min_value=3,
                max_value=10,
                value=5,
                help="Lower = more faces (less accurate)"
            )
            
            st.session_state.detection_params['minSize'] = st.slider(
                "Min Face Size",
                min_value=20,
                max_value=100,
                value=40,
                help="Smaller = detect distant faces"
            )
        
        # Detect Faces Button
        if st.button("🔍 Detect Faces", use_container_width=True):
            if st.session_state.photo is None:
                st.error("❌ Please upload a photo first")
            else:
                with st.spinner("Detecting faces..."):
                    faces = detect_faces(st.session_state.photo, st.session_state.detection_params)
                    st.session_state.faces = faces
                    st.session_state.assignments = {}  # Reset assignments
                
                if faces:
                    st.success(f"✅ Detected {len(faces)} faces")
                else:
                    st.warning("⚠️ No faces detected. Try adjusting parameters.")
        
        st.markdown("---")
        
        # Actions
        st.subheader("4️⃣ Actions")
        
        if st.button("🗑️ Clear All Assignments", use_container_width=True):
            st.session_state.assignments = {}
            st.rerun()
        
        if st.button("📊 View Statistics", use_container_width=True):
            if st.session_state.roster is not None:
                st.session_state.show_stats = True
    
    # Main Content Area
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader("📷 Classroom Photo")
        
        if st.session_state.photo is not None and st.session_state.faces:
            # Draw faces on image
            img_pil = Image.fromarray(st.session_state.photo)
            img_with_faces = draw_faces_on_image(
                img_pil,
                st.session_state.faces,
                st.session_state.assignments,
                st.session_state.roster
            )
            
            st.image(img_with_faces, use_container_width=True, caption="Click on face IDs in the list to assign students →")
            
            # Face assignment info
            assigned_count = len(st.session_state.assignments)
            total_faces = len(st.session_state.faces)
            st.info(f"🟢 Assigned: {assigned_count} | 🟠 Unassigned: {total_faces - assigned_count}")
            
        elif st.session_state.photo is not None:
            st.image(st.session_state.photo, use_container_width=True, caption="Photo loaded - Click 'Detect Faces' in sidebar")
        else:
            st.info("👆 Upload a photo using the sidebar")
    
    with col2:
        st.subheader("👥 Face Assignment")
        
        if st.session_state.faces and st.session_state.roster is not None:
            # Show unassigned faces
            unassigned_faces = [f for f in st.session_state.faces if f['id'] not in st.session_state.assignments]
            
            if unassigned_faces:
                st.markdown("### 🟠 Unassigned Faces")
                
                for face in unassigned_faces:
                    with st.expander(f"Face #{face['id']}", expanded=False):
                        # Search box
                        search_query = st.text_input(
                            "Search student",
                            key=f"search_{face['id']}",
                            placeholder="Name, Admission No, Section, or Roll No"
                        )
                        
                        # Filter roster
                        filtered_roster = search_roster(st.session_state.roster, search_query)
                        
                        if len(filtered_roster) > 0:
                            # Create display format
                            display_options = []
                            for idx, row in filtered_roster.iterrows():
                                display = f"{row['Name']} ({row['Section']}) - Roll: {row['Roll_No']} - Adm: {row['Admission_No']}"
                                display_options.append((display, idx))
                            
                            # Select student
                            selected = st.selectbox(
                                "Select student",
                                options=display_options,
                                format_func=lambda x: x[0],
                                key=f"select_{face['id']}"
                            )
                            
                            if st.button(f"✅ Assign to Face #{face['id']}", key=f"assign_{face['id']}", use_container_width=True):
                                # Check for duplicates
                                idx = selected[1]
                                if idx in st.session_state.assignments.values():
                                    st.warning("⚠️ Student already assigned to another face!")
                                else:
                                    st.session_state.assignments[face['id']] = idx
                                    st.success(f"✅ Assigned {st.session_state.roster.iloc[idx]['Name']}")
                                    st.rerun()
                        else:
                            st.warning("No students found")
            else:
                st.success("✅ All faces assigned!")
            
            st.markdown("---")
            
            # Show assigned faces
            if st.session_state.assignments:
                st.markdown("### 🟢 Assigned Students")
                
                for face_id, roster_idx in st.session_state.assignments.items():
                    student = st.session_state.roster.iloc[roster_idx]
                    col_a, col_b = st.columns([4, 1])
                    
                    with col_a:
                        st.text(f"#{face_id}: {student['Name']} ({student['Section']})")
                    
                    with col_b:
                        if st.button("❌", key=f"remove_{face_id}"):
                            del st.session_state.assignments[face_id]
                            st.rerun()
        
        elif st.session_state.faces:
            st.info("👈 Load roster CSV first")
        else:
            st.info("👈 Detect faces first")
    
    # Statistics Section
    if st.session_state.roster is not None and st.session_state.faces:
        st.markdown("---")
        st.subheader("📊 Attendance Statistics")
        
        total_students = len(st.session_state.roster)
        present_count = len(set(st.session_state.assignments.values()))
        absent_count = total_students - present_count
        present_pct = (present_count / total_students * 100) if total_students > 0 else 0
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("Total Students", total_students)
        with col2:
            st.metric("Present", present_count, f"{present_pct:.1f}%")
        with col3:
            st.metric("Absent", absent_count, f"{100-present_pct:.1f}%")
        with col4:
            st.metric("Faces Detected", len(st.session_state.faces))
    
    # Save Attendance Section
    if st.session_state.roster is not None and st.session_state.assignments:
        st.markdown("---")
        st.subheader("💾 Save Attendance")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            if st.button("📥 Download Excel", use_container_width=True):
                df = generate_attendance_report(
                    st.session_state.roster,
                    st.session_state.assignments,
                    st.session_state.photo_name
                )
                
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False, sheet_name='Attendance')
                output.seek(0)
                
                st.download_button(
                    label="📥 Download XLSX",
                    data=output,
                    file_name=f"attendance_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
        
        with col2:
            if st.button("📥 Download CSV", use_container_width=True):
                df = generate_attendance_report(
                    st.session_state.roster,
                    st.session_state.assignments,
                    st.session_state.photo_name
                )
                
                csv = df.to_csv(index=False)
                st.download_button(
                    label="📥 Download CSV",
                    data=csv,
                    file_name=f"attendance_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
        
        with col3:
            if st.button("👁️ Preview Report", use_container_width=True):
                df = generate_attendance_report(
                    st.session_state.roster,
                    st.session_state.assignments,
                    st.session_state.photo_name
                )
                st.dataframe(df, use_container_width=True)
    
    # Footer
    st.markdown("---")
    st.markdown(
        "<center>Made with ❤️ | Face Detection Attendance System v2.0</center>",
        unsafe_allow_html=True
    )

if __name__ == "__main__":
    main()
