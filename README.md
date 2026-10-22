# AutoApply: Local-First Multi-Platform Job Application Automation

A lightweight, reliable, locally operated application for job discovery, resume-based matching, eligibility verification, and application workflow automation across:
- **Naukri**
- **LinkedIn**
- **Indeed**
- **Hirist (Hirest)**

Built with **FastAPI**, **SQLite**, **Playwright**, and a responsive modern dashboard.

---

## 🚀 Key Features

1. **Local-First & Storage Efficient**:
   - Zero cloud database dependency.
   - Applications, match scores, and platform metrics stored locally in `data/applications.db` (SQLite).
   - Only minimal audit records are kept; no bulky full-page HTML dumps or unnecessary screenshots.

2. **Resume-Based Matching & Seniority Guard**:
   - Primary source of truth is the candidate's actual resume (`data/resume.pdf` for Mohit Kumar) and centralized profile (`data/profile.json`).
   - Calculates a transparent match score (0–100%) incorporating skills overlap, role alignment, experience bracket tolerance (~1.8 yrs target), and location preferences.
   - Automatically rejects senior/managerial roles (Director, VP, Head, 5+ yrs) to avoid wasted applications.

3. **Four Independent Platform Controls**:
   - **Naukri**: `Start Naukri` / `Stop Naukri`
   - **LinkedIn**: `Start LinkedIn` / `Stop LinkedIn`
   - **Indeed**: `Start Indeed` / `Stop Indeed`
   - **Hirist**: `Start Hirist` / `Stop Hirist`
   - Triggering or stopping one platform does **not** affect any other platform.

4. **Security & Anti-Bot Respect**:
   - **Never bypasses CAPTCHA, MFA, or OTP challenges**.
   - **Never stores raw passwords in code or `.env`**.
   - Session data is kept in isolated local browser profile folders (`data/browser_profiles/<platform>`).
   - If an unrecognized challenge or external ATS application portal is detected, the system safely marks the attempt as `Manual Action Required` rather than failing blindly or attempting unauthorized bot actions.

5. **Application Ledger & Export**:
   - Deduplication prevents submitting the same job twice (keyed by normalized URL and platform job ID).
   - Real-time Server-Sent Events (SSE) update the dashboard with live counters.
   - 1-click export of application records to **CSV** and **JSON**.

---

## 🛠️ Windows Setup & Running Instructions

### 1. Requirements
- Python 3.10+ (tested on Python 3.14.6)
- Google Chrome or Microsoft Edge installed on Windows

### 2. Configure Environment
Copy the example configuration file:
```cmd
copy .env.example .env
```

### 3. Install Dependencies (if not already installed)
```cmd
python -m pip install fastapi uvicorn pydantic pypdf httpx pytest
```

### 4. Run the Application
Start the local FastAPI server:
```cmd
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Open your browser and navigate to:
```
http://127.0.0.1:8000
```

---

## 🧪 Running Automated Tests

Run the full test suite with `pytest`:
```cmd
python -m pytest tests/ -v
```

The test suite validates:
- **Phase A**: Local setup, SQLite database schema, and PDF text extraction.
- **Phase B**: Platform connection state management.
- **Phase C**: URL normalization and tracking parameter removal.
- **Phase D**: Transparent resume match scoring, seniority filtering, and deduplication.
- **Phase E**: Platform independence (isolated start/stop mechanisms).
- **Phase H**: Application ledger persistence and CSV/JSON export.

---

## 📂 Project Architecture

```
Job Automation/
├── .gitignore               # Strict ignore rules (DBs, sessions, logs, secrets)
├── .env.example             # Safe placeholder configuration
├── README.md                # Windows setup and operational guide
├── app/
│   ├── config.py            # Local environment and paths
│   ├── database.py          # SQLite ledger, deduplication & exports
│   ├── matcher.py           # Transparent scoring & URL normalizer
│   ├── main.py              # FastAPI server & SSE endpoint
│   ├── runner.py            # Multi-platform independent coordinator
│   └── platforms/
│       ├── base.py          # Base runner with graceful stop & workflow
│       ├── naukri.py        # Naukri runner
│       ├── linkedin.py      # LinkedIn runner
│       ├── indeed.py        # Indeed runner
│       └── hirist.py        # Hirist (Hirest) runner
├── frontend/
│   ├── index.html           # Responsive dashboard & modal
│   ├── style.css            # Dark mode design tokens & animations
│   └── app.js               # SSE client, controls, ledger table & modal
├── data/
│   ├── applications.db      # Local SQLite database (git-ignored)
│   ├── profile.json         # Centralized editable candidate profile
│   └── resume.pdf           # Primary resume source of truth
└── tests/
    ├── test_suite.py        # Core suite (Phases A-H)
    └── test_api.py          # REST & SSE endpoint tests
```

---

## 🔒 Security & Privacy Notice
- No sensitive user data or credentials are committed to version control.
- `data/applications.db`, `data/browser_profiles/`, and `.env` are strictly excluded in `.gitignore`.
