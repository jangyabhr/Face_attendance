import streamlit as st
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import io
import json
import math
import qrcode
from pyzbar import pyzbar
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ---------------------------
# Page configuration & styles
# ---------------------------
st.set_page_config(
    page_title="QR Attendance System",
    page_icon="📱",
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
if 'scan_results' not in st.session_state:
    st.session_state.scan_results = {}      # admission_no -> "P"/"A"
if 'scan_done' not in st.session_state:
    st.session_state.scan_done = False
if 'photo_bytes' not in st.session_state:
    st.session_state.photo_bytes = None
if 'detected_codes' not in st.session_state:
    st.session_state.detected_codes = []    # raw decoded strings from photo
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
    """Update attendance sheet. results = {admission_no: 'P'/'A'}"""
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
# QR code functions
# ---------------------------
def generate_qr_image(admission_no: str, name: str) -> Image.Image:
    """Generate a QR code PIL Image with student name and admission number label."""
    qr = qrcode.QRCode(version=1, box_size=8, border=3,
                       error_correction=qrcode.constants.ERROR_CORRECT_H)
    qr.add_data(admission_no)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert('RGB')

    qr_w, qr_h = qr_img.size
    label_h = 44
    cell = Image.new('RGB', (qr_w, qr_h + label_h), 'white')
    cell.paste(qr_img, (0, 0))

    draw = ImageDraw.Draw(cell)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    except Exception:
        font = ImageFont.load_default()
        font_small = font

    # Truncate long names
    display_name = name if len(name) <= 20 else name[:18] + ".."
    draw.text((4, qr_h + 4), display_name, fill='black', font=font)
    draw.text((4, qr_h + 20), admission_no, fill='#555555', font=font_small)
    draw.rectangle([0, 0, qr_w - 1, qr_h + label_h - 1], outline='#cccccc', width=1)
    return cell

def generate_printable_qr_sheet(roster: pd.DataFrame) -> bytes:
    """Create a printable PNG sheet with all student QR codes in a 4-column grid."""
    cols = 4
    padding = 16
    sample_qr = generate_qr_image("SAMPLE", "Sample")
    cell_w, cell_h = sample_qr.size

    n = len(roster)
    rows = math.ceil(n / cols)
    sheet_w = cols * cell_w + (cols + 1) * padding
    sheet_h = rows * cell_h + (rows + 1) * padding + 50  # +50 for header

    sheet = Image.new('RGB', (sheet_w, sheet_h), 'white')
    draw = ImageDraw.Draw(sheet)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
    except Exception:
        font_title = ImageFont.load_default()

    draw.text((padding, 12), "Student QR Attendance Cards  —  Print, cut & distribute (one-time)", fill='#333333', font=font_title)

    for i, (_, row) in enumerate(roster.iterrows()):
        col_idx = i % cols
        row_idx = i // cols
        x = padding + col_idx * (cell_w + padding)
        y = 50 + padding + row_idx * (cell_h + padding)
        qr_cell = generate_qr_image(str(row['Admission_No']), str(row['Name']))
        sheet.paste(qr_cell, (x, y))

    buf = io.BytesIO()
    sheet.save(buf, format='PNG')
    return buf.getvalue()

def scan_qr_codes(image_bytes: bytes) -> list:
    """Detect all QR codes in a single image. Returns list of decoded strings."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        decoded = pyzbar.decode(img)
        codes = [d.data.decode('utf-8').strip() for d in decoded]
        return codes
    except Exception as e:
        st.error(f"QR scan error: {str(e)}")
        return []

def match_qr_to_roster(detected_codes: list, roster: pd.DataFrame) -> dict:
    """Cross-reference detected QR codes against roster admission numbers."""
    roster_admissions = set(roster['Admission_No'].astype(str).tolist())
    results = {adm: 'A' for adm in roster_admissions}
    for code in detected_codes:
        if code in roster_admissions:
            results[code] = 'P'
    return results

# ---------------------------
# Main App
# ---------------------------
def main():
    st.title("📱 QR Bulk Attendance System")

    # Setup instructions
    with st.expander("⚙️ HOW IT WORKS (First Time Setup)", expanded=not st.session_state.sheets_connected):
        st.markdown("""
        ### One-Time Setup
        1. Connect your **Google Sheet** below (service account credentials + spreadsheet ID)
        2. Upload your **roster CSV** — columns: `Admission_No, Name, Section, Roll_No`
        3. Go to **🖨️ Print QR Codes** tab → download the QR sheet → print & cut
        4. Give each student their QR card — they keep it for the whole year

        ### Daily Attendance (takes ~10 seconds)
        1. Upload today's roster (or load from Sheets)
        2. Students hold up their QR cards facing your phone camera
        3. Take **one group photo**
        4. Upload it → click **Scan QR Codes**
        5. Review results → Save to Google Sheets
        """)

    st.markdown("---")

    # ---------------------------
    # Google Sheets connection
    # ---------------------------
    if not st.session_state.sheets_connected:
        st.subheader("🔗 Connect to Google Sheets")
        auto_connected = False
        try:
            if "gcp_service_account" in st.secrets and "spreadsheet_id" in st.secrets:
                with st.spinner("Connecting..."):
                    if connect_to_sheets(dict(st.secrets["gcp_service_account"]), st.secrets["spreadsheet_id"]):
                        st.success("✅ Connected to Google Sheets!")
                        auto_connected = True
                        roster = read_sheet("Roster")
                        if roster is not None and not roster.empty:
                            st.session_state.roster = roster
                            st.success(f"✅ Loaded roster: {len(roster)} students")
        except Exception as e:
            st.error(f"Auto connect failed: {e}")

        if not auto_connected:
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
                        roster = read_sheet("Roster")
                        if roster is not None and not roster.empty:
                            st.session_state.roster = roster
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

        st.subheader("👥 Roster")
        if st.button("📥 Load Roster from Sheets", use_container_width=True):
            roster = read_sheet("Roster")
            if roster is not None and not roster.empty:
                st.session_state.roster = roster
                st.success(f"✅ Loaded {len(roster)} students")
            else:
                st.info("No roster found. Upload CSV below.")

        roster_file = st.file_uploader(
            "Upload Roster CSV", type=['csv'],
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

        st.subheader("📷 Today's Photo")
        photo_file = st.file_uploader(
            "Upload classroom photo", type=['jpg', 'jpeg', 'png'],
            help="Students should be holding their QR cards facing the camera"
        )
        if photo_file:
            st.session_state.photo_bytes = photo_file.read()
            st.session_state.scan_done = False
            st.session_state.scan_results = {}
            st.session_state.detected_codes = []
            st.success("✅ Photo loaded")

        if st.session_state.photo_bytes:
            if st.button("🔍 Scan QR Codes", use_container_width=True, type="primary"):
                if st.session_state.roster is None:
                    st.error("❌ Load roster first")
                else:
                    with st.spinner("Scanning for QR codes..."):
                        codes = scan_qr_codes(st.session_state.photo_bytes)
                        st.session_state.detected_codes = codes
                        results = match_qr_to_roster(codes, st.session_state.roster)
                        st.session_state.scan_results = results
                        st.session_state.scan_done = True
                    present = sum(1 for v in results.values() if v == 'P')
                    st.success(f"✅ Found {len(codes)} QR codes → {present} students present")
                    st.rerun()

        st.markdown("---")
        if st.button("📊 View Master Sheet", use_container_width=True):
            st.session_state.show_master = not st.session_state.show_master
            st.rerun()

    # ---------------------------
    # Main tabs
    # ---------------------------
    tab1, tab2 = st.tabs(["📸 Take Attendance", "🖨️ Print QR Codes"])

    # ---- TAB 1: TAKE ATTENDANCE ----
    with tab1:
        if st.session_state.photo_bytes:
            img = Image.open(io.BytesIO(st.session_state.photo_bytes))
            st.image(img, caption="Uploaded classroom photo", use_container_width=True)
        else:
            st.info("👈 Upload today's classroom photo in the sidebar, then click **Scan QR Codes**")

        if st.session_state.scan_done and st.session_state.scan_results:
            results = st.session_state.scan_results
            roster_df = st.session_state.roster
            present_count = sum(1 for v in results.values() if v == 'P')
            absent_count = sum(1 for v in results.values() if v == 'A')
            total = len(roster_df)
            unknown = [c for c in st.session_state.detected_codes
                       if c not in set(roster_df['Admission_No'].astype(str).tolist())]

            # Metrics
            st.markdown("---")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Present", present_count)
            m2.metric("Absent", absent_count)
            m3.metric("Rate", f"{present_count / total * 100:.1f}%" if total else "0%")
            m4.metric("Unknown codes", len(unknown),
                      help="QR codes detected but not in roster")

            if unknown:
                st.warning(f"⚠️ {len(unknown)} unrecognised QR code(s): {', '.join(unknown)}")

            # Review & correct
            st.markdown("---")
            st.subheader("✏️ Review & Correct")
            st.caption("Results are pre-filled from the scan. Adjust any mistakes before saving.")

            with st.form("review_form"):
                updated = {}
                cols_per_row = 3
                rows_data = [
                    {"Admission_No": r['Admission_No'], "Name": r['Name'],
                     "Section": r['Section'], "Status": results.get(str(r['Admission_No']), 'A')}
                    for _, r in roster_df.iterrows()
                ]
                grouped = [rows_data[i:i+cols_per_row] for i in range(0, len(rows_data), cols_per_row)]

                for group in grouped:
                    fcols = st.columns(cols_per_row)
                    for fc, student in zip(fcols, group):
                        with fc:
                            adm = student['Admission_No']
                            badge = "🟢" if student['Status'] == 'P' else "🔴"
                            updated[adm] = st.selectbox(
                                f"{badge} {student['Name']}",
                                options=['P', 'A'],
                                index=0 if student['Status'] == 'P' else 1,
                                key=f"s_{adm}"
                            )

                save_btn = st.form_submit_button(
                    "✅ SAVE TO GOOGLE SHEETS", use_container_width=True, type="primary"
                )

            if save_btn:
                with st.spinner("Saving..."):
                    today = datetime.now().strftime('%Y-%m-%d')
                    update_master_attendance(roster_df, updated, today)
                    final_present = sum(1 for v in updated.values() if v == 'P')
                st.success(f"✅ Saved for {today}! {final_present} present, {total - final_present} absent.")
                st.balloons()
                st.session_state.scan_results = updated

    # ---- TAB 2: PRINT QR CODES ----
    with tab2:
        st.subheader("🖨️ Student QR Code Cards")

        if st.session_state.roster is None:
            st.info("Load a roster first (sidebar) to generate QR codes.")
        else:
            roster_df = st.session_state.roster
            st.markdown(f"""
            **{len(roster_df)} students** in the current roster.

            - Click **Download QR Sheet** to get a printable PNG
            - Print at full size (A4 / Letter), cut along the card borders
            - Give each student their card — **one-time only**
            - Students keep the card and hold it up for every class photo
            """)

            col_dl, col_prev = st.columns([1, 2])

            with col_dl:
                if st.button("📥 Generate & Download QR Sheet", use_container_width=True, type="primary"):
                    with st.spinner("Generating QR codes..."):
                        sheet_bytes = generate_printable_qr_sheet(roster_df)
                    st.download_button(
                        label="⬇️ Download QR Sheet (PNG)",
                        data=sheet_bytes,
                        file_name=f"qr_cards_{datetime.now().strftime('%Y%m%d')}.png",
                        mime="image/png",
                        use_container_width=True
                    )

                st.markdown("---")
                st.markdown("**Individual QR code lookup:**")
                search = st.text_input("Search student", placeholder="Name or Admission No")
                filtered = search_roster(roster_df, search) if search else roster_df.head(5)

                for _, row in filtered.head(5).iterrows():
                    with st.expander(f"{row['Name']} — {row['Admission_No']}"):
                        qr_img = generate_qr_image(str(row['Admission_No']), str(row['Name']))
                        st.image(qr_img, width=160)
                        buf = io.BytesIO()
                        qr_img.save(buf, format='PNG')
                        st.download_button(
                            "⬇️ Download",
                            data=buf.getvalue(),
                            file_name=f"qr_{row['Admission_No']}.png",
                            mime="image/png",
                            key=f"dl_{row['Admission_No']}"
                        )

            with col_prev:
                st.markdown("**Preview (first 8 students):**")
                preview_roster = roster_df.head(8)
                cols = st.columns(4)
                for i, (_, row) in enumerate(preview_roster.iterrows()):
                    with cols[i % 4]:
                        qr_img = generate_qr_image(str(row['Admission_No']), str(row['Name']))
                        st.image(qr_img, use_container_width=True)

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
                st.download_button(
                    "📥 Download CSV",
                    master.to_csv(index=False),
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
        "<center>📱 QR Attendance System | No AI · No API costs · Works offline · Data in Google Sheets ☁️</center>",
        unsafe_allow_html=True
    )

if __name__ == "__main__":
    main()
