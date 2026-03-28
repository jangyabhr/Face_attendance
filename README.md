# QR Bulk Attendance System

A fast, credential-free attendance system for classrooms. Students hold printed QR cards up to the camera — one group photo marks the whole class in seconds.

**No Google Cloud setup. No API keys. No permanent storage.**

---

## How It Works

### One-Time Setup
1. Prepare your roster (Google Sheet or CSV) with columns: `Admission_No, Name, Section, Roll_No`
2. Open the app and load your roster
3. Go to **Print QR Codes** tab → download the QR sheet → print and cut
4. Hand each student their card — they keep it all year

### Daily Attendance (~10 seconds)
1. Students hold up their QR cards facing your camera
2. Take one group photo
3. Open the app, load your roster, upload the photo
4. Click **Scan QR Codes**
5. Review the auto-filled P/A results, correct any mistakes
6. Click **Save Attendance** → download the daily CSV
7. Copy the **Status** column into your master Excel sheet under today's date

---

## Roster Format

### Option A — Public Google Sheet
Share your sheet as *"Anyone with the link can view"* and paste the URL into the app. No credentials needed.

### Option B — CSV Upload
```csv
Admission_No,Name,Section,Roll_No
2024001,Ali Ahmed,A,1
2024002,Sara Khan,A,2
2024003,Omar Malik,B,1
```

**Important:** Row order in the roster determines row order in every daily CSV. Keep your roster rows in the order you want for your master sheet and never reorder them mid-year.

---

## Daily CSV Output

Each day produces a file like `attendance_2024-01-15.csv`:

```csv
Admission_No,Name,Roll_No,Section,Status
2024001,Ali Ahmed,1,A,P
2024002,Sara Khan,2,A,A
2024003,Omar Malik,1,B,P
```

Copy the **Status** column and paste it into your master Excel sheet under the date. Row order is consistent every session so no VLOOKUP is needed.

---

## Deploy to Streamlit Cloud

1. Fork or push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io) → New app
3. Select your repo, set main file to `app.py`
4. Deploy — you'll get a public URL

No secrets or environment variables required.

---

## Dependencies

```
streamlit
pandas
pillow
pyzbar
qrcode
```

System package required: `libzbar0` (listed in `packages.txt`)

---

## Privacy

- No data is stored on the server
- Each session is independent and cleared when the browser tab closes
- The roster and attendance exist only in memory during your session
