import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# Environment overrides
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
DEBUG = os.getenv("DEBUG", "False").lower() in ("true", "1", "yes")

# Database & File Paths
DB_PATH = os.getenv("DB_PATH", str(DATA_DIR / "applications.db"))
PROFILE_PATH = os.getenv("PROFILE_PATH", str(DATA_DIR / "profile.json"))
RESUME_PATH = os.getenv("RESUME_PATH", str(DATA_DIR / "resume.pdf"))
BROWSER_PROFILES_DIR = DATA_DIR / "browser_profiles"
BROWSER_PROFILES_DIR.mkdir(exist_ok=True)

# Automation constraints
HEADLESS = os.getenv("HEADLESS", "True").lower() in ("true", "1", "yes")
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
MIN_DELAY_SECONDS = float(os.getenv("MIN_DELAY_SECONDS", "2.0"))
MAX_DELAY_SECONDS = float(os.getenv("MAX_DELAY_SECONDS", "4.5"))

# Matching thresholds
MIN_MATCH_SCORE = float(os.getenv("MIN_MATCH_SCORE", "60.0"))
TARGET_MIN_EXPERIENCE_YEARS = float(os.getenv("TARGET_MIN_EXPERIENCE_YEARS", "0.0"))
TARGET_MAX_EXPERIENCE_YEARS = float(os.getenv("TARGET_MAX_EXPERIENCE_YEARS", "2.5"))

# Posting Date Filter & Retention Policies
MAX_POSTING_AGE_DAYS = int(os.getenv("MAX_POSTING_AGE_DAYS", "4"))
CLEANUP_RETENTION_DAYS = int(os.getenv("CLEANUP_RETENTION_DAYS", "6"))
MAX_DISCOVERED_JOBS_PER_RUN = int(os.getenv("MAX_DISCOVERED_JOBS_PER_RUN", "50"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))

# Optional Local Portal Credentials (loaded only from local .env, NEVER committed)
PORTAL_EMAIL = os.getenv("PORTAL_EMAIL", "")
PORTAL_PASSWORD = os.getenv("PORTAL_PASSWORD", "")

