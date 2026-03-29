import streamlit as st
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import io
import re
import math
import qrcode
from pyzbar import pyzbar

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
if 'uploaded_photos' not in st.session_state:
    st.session_state.uploaded_photos = []           # list of {'key', 'bytes', 'name'}
if 'uploaded_photo_keys' not in st.session_state:
    st.session_state.uploaded_photo_keys = frozenset()  # tracks current uploader file set
if 'detected_codes' not in st.session_state:
    st.session_state.detected_codes = []            # union of all codes across photos
if 'daily_csv' not in st.session_state:
    st.session_state.daily_csv = None               # bytes of last saved daily CSV
if 'daily_csv_date' not in st.session_state:
    st.session_state.daily_csv_date = None          # date string for filename

# ---------------------------
# Roster helpers
# ---------------------------
def load_roster_from_public_sheet(url_or_id: str) -> pd.DataFrame:
    """Fetch roster from a public Google Sheet using its CSV export URL.
    Requires the sheet to be shared as 'Anyone with the link can view'.
    No credentials or API keys needed.
    """
    match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', url_or_id)
    sheet_id = match.group(1) if match else url_or_id.strip()
    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    df = pd.read_csv(export_url, dtype=str).fillna("")
    return df.reset_index(drop=True)

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

def build_daily_csv(roster: pd.DataFrame, results: dict, date_str: str) -> bytes:
    """Build a minimal daily attendance CSV in the original roster order."""
    rows = []
    for _, row in roster.iterrows():
        adm = str(row['Admission_No'])
        rows.append({
            'Admission_No': adm,
            'Name': row['Name'],
            'Roll_No': row.get('Roll_No', ''),
            'Section': row.get('Section', ''),
            date_str: results.get(adm, 'A'),
        })
    df = pd.DataFrame(rows)
    return df.to_csv(index=False).encode('utf-8')

# ---------------------------
# QR code functions
# ---------------------------
def generate_qr_image(admission_no: str, name: str) -> Image.Image:
    """Generate a QR code PIL Image with student name and admission number label."""
    qr = qrcode.QRCode(version=1, box_size=15, border=4,
                       error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(admission_no)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert('RGB')

    qr_w, qr_h = qr_img.size
    label_h = 56
    cell = Image.new('RGB', (qr_w, qr_h + label_h), 'white')
    cell.paste(qr_img, (0, 0))

    draw = ImageDraw.Draw(cell)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except Exception:
        font = ImageFont.load_default()
        font_small = font

    display_name = name if len(name) <= 20 else name[:18] + ".."
    draw.text((4, qr_h + 6), display_name, fill='black', font=font)
    draw.text((4, qr_h + 26), admission_no, fill='#555555', font=font_small)
    draw.rectangle([0, 0, qr_w - 1, qr_h + label_h - 1], outline='#cccccc', width=1)
    return cell

def generate_printable_qr_sheet(roster: pd.DataFrame) -> bytes:
    """Create a printable PNG sheet with all student QR codes in a 3-column grid."""
    cols = 3
    padding = 32
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

    with st.expander("⚙️ HOW IT WORKS (First Time Setup)", expanded=st.session_state.roster is None):
        st.markdown("""
        ### One-Time Setup
        1. Load your **roster** — paste your public Google Sheet URL **or** upload a CSV
           - CSV columns required: `Admission_No, Name, Section, Roll_No`
           - For Google Sheet: share it as *"Anyone with the link can view"*
        2. Go to **🖨️ Print QR Codes** tab → download the QR sheet → print & cut
        3. Give each student their QR card — they keep it for the whole year

        ### Daily Attendance (takes ~10 seconds)
        1. Students hold up their QR cards facing your phone camera
        2. Take **one group photo**
        3. Upload it → click **Scan QR Codes**
        4. Review results → **Save Attendance** → download the daily CSV
        5. Copy the **Status** column into your master Excel sheet under today's date
        """)

    st.markdown("---")

    # ---------------------------
    # Sidebar
    # ---------------------------
    with st.sidebar:
        st.header("⚙️ Controls")
        st.metric("📅 Date", datetime.now().strftime('%b %d, %Y'))
        st.markdown("---")

        st.subheader("👥 Roster")

        # Option A: public Google Sheet URL
        sheet_url = st.text_input(
            "Public Google Sheet URL",
            placeholder="https://docs.google.com/spreadsheets/d/...",
            help="Sheet must be shared as 'Anyone with the link can view'. No credentials needed."
        )
        if st.button("Load from Google Sheet", use_container_width=True):
            if not sheet_url.strip():
                st.error("Paste your Google Sheet URL above.")
            else:
                try:
                    with st.spinner("Fetching roster..."):
                        df = load_roster_from_public_sheet(sheet_url.strip())
                    required = {"Admission_No", "Name", "Section", "Roll_No"}
                    if not required.issubset(set(df.columns)):
                        st.error(f"Sheet must have columns: {', '.join(required)}")
                    else:
                        st.session_state.roster = df
                        st.success(f"✅ Loaded {len(df)} students")
                except Exception as e:
                    st.error(f"Failed to load sheet: {e}")

        st.markdown("— or —")

        # Option B: CSV upload
        roster_file = st.file_uploader(
            "Upload Roster CSV", type=['csv'],
            help="Columns: Admission_No, Name, Section, Roll_No"
        )
        if roster_file:
            try:
                df = pd.read_csv(roster_file, dtype=str).fillna("")
                df = df.reset_index(drop=True)
                required = {"Admission_No", "Name", "Section", "Roll_No"}
                if not required.issubset(set(df.columns)):
                    st.error(f"❌ CSV must have: {', '.join(required)}")
                else:
                    st.session_state.roster = df
                    st.success(f"✅ Roster loaded: {len(df)} students")
            except Exception as e:
                st.error(f"Error: {str(e)}")

        if st.session_state.roster is not None:
            st.metric("Students", len(st.session_state.roster))

        st.markdown("---")

        st.subheader("📷 Today's Photos")
        photo_files = st.file_uploader(
            "Upload classroom photo(s)", type=['jpg', 'jpeg', 'png'],
            accept_multiple_files=True,
            help="Upload one photo or several — QR codes are merged across all photos"
        )
        if photo_files is not None:
            current_keys = frozenset(f"{f.name}_{f.size}" for f in photo_files)
            if current_keys != st.session_state.uploaded_photo_keys:
                st.session_state.uploaded_photo_keys = current_keys
                st.session_state.uploaded_photos = [
                    {'key': f"{f.name}_{f.size}", 'bytes': f.read(), 'name': f.name}
                    for f in photo_files
                ]
                st.session_state.scan_done = False
                st.session_state.scan_results = {}
                st.session_state.detected_codes = []
                st.session_state.daily_csv = None
                if photo_files:
                    st.success(f"✅ {len(photo_files)} photo(s) loaded")

        if st.session_state.uploaded_photos:
            st.metric("Photos loaded", len(st.session_state.uploaded_photos))
            if st.button("🔍 Scan QR Codes", use_container_width=True, type="primary"):
                if st.session_state.roster is None:
                    st.error("❌ Load roster first")
                else:
                    with st.spinner(f"Scanning {len(st.session_state.uploaded_photos)} photo(s)..."):
                        all_codes = []
                        for photo in st.session_state.uploaded_photos:
                            all_codes.extend(scan_qr_codes(photo['bytes']))
                        unique_codes = list(set(all_codes))
                        st.session_state.detected_codes = unique_codes
                        results = match_qr_to_roster(unique_codes, st.session_state.roster)
                        st.session_state.scan_results = results
                        st.session_state.scan_done = True
                    present = sum(1 for v in results.values() if v == 'P')
                    st.success(f"✅ {len(st.session_state.uploaded_photos)} photo(s) → {len(unique_codes)} unique codes → {present} present")
                    st.rerun()

    # ---------------------------
    # Scan-ready banner (visible regardless of active tab)
    # ---------------------------
    if st.session_state.scan_done and st.session_state.scan_results:
        st.info("✅ Scan complete — switch to the **📸 Take Attendance** tab to review and save.")

    # ---------------------------
    # Main tabs
    # ---------------------------
    tab1, tab2 = st.tabs(["📸 Take Attendance", "🖨️ Print QR Codes"])

    # ---- TAB 1: TAKE ATTENDANCE ----
    with tab1:
        if st.session_state.uploaded_photos:
            thumb_cols = st.columns(len(st.session_state.uploaded_photos))
            for col, photo in zip(thumb_cols, st.session_state.uploaded_photos):
                col.image(photo['bytes'], caption=photo['name'], use_container_width=True)
        else:
            st.info("👈 Upload today's classroom photo(s) in the sidebar, then click **Scan QR Codes**")

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
                    "✅ SAVE ATTENDANCE", use_container_width=True, type="primary"
                )

            if save_btn:
                today = datetime.now().strftime('%Y-%m-%d')
                csv_bytes = build_daily_csv(roster_df, updated, today)
                st.session_state.daily_csv = csv_bytes
                st.session_state.daily_csv_date = today
                st.session_state.scan_results = updated
                final_present = sum(1 for v in updated.values() if v == 'P')
                st.success(f"✅ Attendance saved for {today} — {final_present} present, {total - final_present} absent.")
                st.balloons()

            # Show download button if CSV is ready (persists across rerenders)
            if st.session_state.daily_csv:
                st.download_button(
                    label="📥 Download Today's Attendance CSV",
                    data=st.session_state.daily_csv,
                    file_name=f"attendance_{st.session_state.daily_csv_date}.csv",
                    mime="text/csv",
                    use_container_width=True,
                    type="primary"
                )
                st.caption(
                    "Paste the date column into your master Excel sheet — the header is already today's date."
                )

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

    st.markdown("---")
    st.markdown(
        "<center>📱 QR Attendance System | No API keys · No cloud setup · Download CSV daily</center>",
        unsafe_allow_html=True
    )

if __name__ == "__main__":
    main()
