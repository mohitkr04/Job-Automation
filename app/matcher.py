import re
import json
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
from datetime import datetime, timezone
import pypdf
from app.config import (
    RESUME_PATH,
    PROFILE_PATH,
    MIN_MATCH_SCORE,
    TARGET_MIN_EXPERIENCE_YEARS,
    TARGET_MAX_EXPERIENCE_YEARS,
    MAX_POSTING_AGE_DAYS
)

_CACHED_RESUME_TEXT: Optional[str] = None
_CACHED_PROFILE: Optional[Dict[str, Any]] = None

def get_resume_text() -> str:
    global _CACHED_RESUME_TEXT
    if _CACHED_RESUME_TEXT is not None:
        return _CACHED_RESUME_TEXT

    resume_file = Path(RESUME_PATH)
    if not resume_file.exists():
        return ""

    try:
        reader = pypdf.PdfReader(str(resume_file))
        text_parts = [page.extract_text() or "" for page in reader.pages]
        _CACHED_RESUME_TEXT = " ".join(text_parts)
    except Exception as e:
        _CACHED_RESUME_TEXT = ""
    
    return _CACHED_RESUME_TEXT

def get_profile() -> Dict[str, Any]:
    global _CACHED_PROFILE
    profile_file = Path(PROFILE_PATH)
    if profile_file.exists():
        try:
            with open(profile_file, "r", encoding="utf-8") as f:
                _CACHED_PROFILE = json.load(f)
                return _CACHED_PROFILE
        except Exception:
            pass
    return {}

def update_profile(new_data: Dict[str, Any]):
    global _CACHED_PROFILE
    profile_file = Path(PROFILE_PATH)
    with open(profile_file, "w", encoding="utf-8") as f:
        json.dump(new_data, f, indent=2)
    _CACHED_PROFILE = new_data

def normalize_job_url(raw_url: str) -> str:
    """Strip tracking query parameters to ensure reliable deduplication."""
    if not raw_url:
        return ""
    try:
        parsed = urllib.parse.urlparse(raw_url)
        clean_path = parsed.path.rstrip("/")
        tracking_params = {
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "ref", "trackingid", "track", "mid", "sid", "f", "rc", "from", "sp",
            "currentjobid", "position", "pagenumber"
        }
        query_dict = urllib.parse.parse_qs(parsed.query)
        clean_query = {
            k: v for k, v in query_dict.items() if k.lower() not in tracking_params
        }
        encoded_query = urllib.parse.urlencode(clean_query, doseq=True)
        return urllib.parse.urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            clean_path,
            "",
            encoded_query,
            ""
        ))
    except Exception:
        return raw_url.strip()

def parse_posting_age_days(text: Optional[str]) -> Optional[float]:
    """
    Parses relative or absolute posting date text into age in days.
    Returns float representing days since posted, or None if unknown/unverifiable.
    Strictly distinguishes posting dates from closing/expiry dates.
    """
    if not text or not str(text).strip():
        return None

    clean = str(text).strip().lower()

    # Disqualify if it's explicitly a closing/expiry date rather than a posting date
    closing_indicators = ["closing date", "apply before", "expires on", "deadline", "expires in", "valid till"]
    if any(ci in clean for ci in closing_indicators) and not any(pi in clean for pi in ["posted", "published", "created", "active since"]):
        return None

    # Immediate / Today patterns
    if any(kw in clean for kw in ["just now", "moments ago", "today", "hour", "minute", "hr", "min", "just posted"]):
        return 0.1

    if "yesterday" in clean:
        return 1.0

    # Years pattern (e.g. '15 years ago', '1 year ago', '2 yrs ago', '1y ago')
    year_match = re.search(r'(\d+)\s*(?:year|years|y|yr|yrs)\s*(?:ago)?\b', clean)
    if year_match:
        return float(year_match.group(1)) * 365.0

    # Months pattern (e.g. '1 month ago', '2 months ago', '3mo ago', '30+ days ago')
    if "30+" in clean:
        return 30.0
    month_match = re.search(r'(\d+)\s*(?:month|months|mo)\s*(?:ago)?\b', clean)
    if month_match:
        return float(month_match.group(1)) * 30.0

    # Weeks pattern (e.g. '1 week ago', '2 weeks ago', '2w ago')
    week_match = re.search(r'(\d+)\s*(?:week|weeks|w)\s*(?:ago)?\b', clean)
    if week_match:
        return float(week_match.group(1)) * 7.0

    # Days pattern (e.g. '1 day ago', '2 days ago', '3d ago', '4d', 'posted 3 days ago')
    day_match = re.search(r'(\d+)\s*(?:day|days|d)\s*(?:ago)?\b', clean)
    if day_match:
        return float(day_match.group(1))

    now = datetime.now(timezone.utc).date()

    # ISO date check (e.g. 2026-10-21 or 2011-04-12)
    iso_match = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', clean)
    if iso_match:
        try:
            d = datetime.strptime(iso_match.group(0), "%Y-%m-%d").date()
            diff = (now - d).days
            return float(max(diff, 0))
        except Exception:
            pass

    # DD/MM/YYYY or DD-MM-YYYY check (e.g. 21/10/2026 or 15-05-2011)
    dmy_match = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', clean)
    if dmy_match:
        day_str, month_str, year_str = dmy_match.group(1), dmy_match.group(2), dmy_match.group(3)
        try:
            d = datetime(int(year_str), int(month_str), int(day_str)).date()
            diff = (now - d).days
            return float(max(diff, 0))
        except Exception:
            pass

    # DD Mon YYYY check (e.g. 21 Oct 2026 or 12 May 2011)
    mon_match = re.search(r'(\d{1,2})\s+([a-z]{3,9})\s+(\d{4})', clean)
    if mon_match:
        try:
            d = datetime.strptime(f"{mon_match.group(1)} {mon_match.group(2)[:3].capitalize()} {mon_match.group(3)}", "%d %b %Y").date()
            diff = (now - d).days
            return float(max(diff, 0))
        except Exception:
            pass

    return None

def validate_job_posting_date(
    posted_date_text: Optional[str],
    max_age_days: int = MAX_POSTING_AGE_DAYS,
    discovery_timestamp: Optional[str] = None
) -> Tuple[bool, Optional[float], str]:
    """
    Centralized job-posting date validation function.
    Mandatory gatekeeper: Runs before any job can enter either the automatic
    application pipeline or the manual-application dashboard.

    Requirements:
    - Default max age: 4 days (configurable via MAX_POSTING_AGE_DAYS).
    - Accepts only verified original posting date within cutoff.
    - Rejects listings older than cutoff (weeks, months, years ago).
    - Distinguishes original posting dates from closing/expiry/modification/scraped dates.
    - Accurately normalizes relative labels ('Today', '1 day ago', '3 days ago') and absolute dates.
    - Takes discovery_timestamp into account so old relative postings cannot be treated as permanently fresh.
    - If date is missing, invalid, or unverifiable, returns is_valid=False.

    Returns:
        (is_valid: bool, posting_age_days: Optional[float], reason: str)
    """
    if not posted_date_text or not str(posted_date_text).strip():
        return False, None, "Posting date is missing or cannot be verified"

    clean = str(posted_date_text).strip().lower()

    # Reject if only modification/scraping date is present without original posting date
    if any(k in clean for k in ["scraped", "discovered", "updated on", "modified on", "refreshed"]):
        if not any(k in clean for k in ["posted", "published", "created"]):
            return False, None, "Modification or scraping date cannot be used as original posting date"

    base_age = parse_posting_age_days(posted_date_text)

    if base_age is None:
        return False, None, "Posting date cannot be reliably verified or has an unrecognized format"

    # If discovery_timestamp is provided, account for elapsed days since initial discovery
    effective_age = base_age
    if discovery_timestamp:
        try:
            # Parse discovery timestamp
            disc_clean = discovery_timestamp.replace("Z", "+00:00")
            disc_dt = datetime.fromisoformat(disc_clean)
            if disc_dt.tzinfo is None:
                disc_dt = disc_dt.replace(tzinfo=timezone.utc)
            now_dt = datetime.now(timezone.utc)
            elapsed_days = max(0.0, (now_dt - disc_dt).total_seconds() / 86400.0)
            # Only add elapsed days if the original date was relative
            # (Absolute dates already calculate diff from current date)
            is_relative = any(k in clean for k in ["ago", "today", "yesterday", "now", "just", "hour", "hr", "minute", "min", "d", "w", "m", "y"])
            if is_relative:
                effective_age = base_age + elapsed_days
        except Exception:
            pass

    if effective_age > float(max_age_days):
        return False, effective_age, f"Job posting is outdated ({effective_age:.1f} days ago, maximum allowed is {max_age_days} days)"

    return True, effective_age, f"Posting date verified ({effective_age:.1f} days ago)"

def parse_experience_years(text: str) -> Tuple[Optional[float], Optional[float]]:
    """Extract minimum and maximum experience required from text (e.g. '1-3 yrs', '2+ years')."""
    if not text:
        return None, None
    
    range_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)\s*(?:yr|year|exp)', text, re.IGNORECASE)
    if range_match:
        return float(range_match.group(1)), float(range_match.group(2))
    
    plus_match = re.search(r'(\d+(?:\.\d+)?)\s*\+\s*(?:yr|year|exp)', text, re.IGNORECASE)
    if plus_match:
        val = float(plus_match.group(1))
        return val, val + 2.0

    single_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:yr|year|exp)', text, re.IGNORECASE)
    if single_match:
        val = float(single_match.group(1))
        return val, val

    return None, None

def evaluate_job_match(
    title: str,
    description: str,
    location: str = "",
    experience_text: str = "",
    posted_date_text: Optional[str] = None
) -> Tuple[bool, float, Dict[str, Any]]:
    """
    Evaluates job eligibility and computes transparent match score (0-100).
    Enforces centralized Job Posting Date Filter (posted within last 3-4 days).
    Returns (is_eligible, match_score, evaluation_details).
    """
    # 1. Centralized Posting Date Filter Check (Priority 1)
    is_date_valid, posting_age, date_reason = validate_job_posting_date(posted_date_text, MAX_POSTING_AGE_DAYS)
    if not is_date_valid:
        return False, 0.0, {
            "eligible": False,
            "rejection_reason": date_reason,
            "posting_age_days": posting_age,
            "breakdown": {"role_match": 0, "skills_match": 0, "exp_match": 0, "location_match": 0}
        }
    profile = get_profile()
    resume_text = get_resume_text()
    
    combined_job_text = f"{title} {description}".lower()
    
    # 1. Seniority Filter: Exclude senior/managerial roles that require 5+ years or VP/Director/Head
    senior_keywords = ["senior", "sr.", "lead", "principal", "manager", "director", "head of", "architect", "vp", "vice president"]
    is_senior = any(re.search(rf"\b{re.escape(kw)}\b", title.lower()) for kw in senior_keywords)
    
    # Check experience requirements
    min_exp, max_exp = parse_experience_years(f"{experience_text} {description}")
    
    # If the posting explicitly asks for 5+ years or is a Director/VP, disqualify
    if (min_exp is not None and min_exp >= 4.5) or ("director" in title.lower() or "head of" in title.lower()):
        return False, 20.0, {
            "eligible": False,
            "rejection_reason": f"Seniority mismatch (requires {min_exp}+ years experience)",
            "breakdown": {"role_match": 10, "skills_match": 10, "exp_match": 0, "location_match": 0}
        }

    # 2. Target Roles Matching
    target_roles = profile.get("target_roles", [
        "Data Analyst", "Business Analyst", "Risk Analyst", "Fraud Analyst",
        "Compliance Analyst", "Operations Analyst", "Product Analyst",
        "Reporting Analyst", "Junior Analytics Engineer"
    ])
    
    role_score = 0.0
    matched_role = None
    for role in target_roles:
        role_tokens = role.lower().split()
        if all(re.search(rf"\b{re.escape(token)}\b", title.lower()) for token in role_tokens):
            role_score = 35.0
            matched_role = role
            break
        elif any(token in title.lower() for token in ["analyst", "analytics", "business intelligence", "risk", "fraud", "operations", "bi"]):
            role_score = max(role_score, 25.0)
            matched_role = "Analyst / Analytics"

    if role_score == 0.0:
        # Check if description has strong analyst requirements
        if "analyst" in combined_job_text or "analytics" in combined_job_text:
            role_score = 15.0

    # 3. Skills Overlap Matching
    skills_list = profile.get("skills", [
        "Python", "SQL", "Pandas", "NumPy", "EDA", "Power BI", "Excel",
        "Dashboard Development", "PostgreSQL", "MySQL", "Fraud Detection",
        "Settlement Validation", "Transaction Monitoring", "Selenium", "Git", "REST APIs"
    ])
    
    matched_skills = []
    for skill in skills_list:
        clean_skill = skill.lower()
        if re.search(rf"\b{re.escape(clean_skill)}\b", combined_job_text):
            matched_skills.append(skill)
            
    # Normalize skill score (up to 35 points)
    # 5 or more matching skills awards full skill points
    skill_ratio = min(len(matched_skills) / 5.0, 1.0)
    skills_score = round(skill_ratio * 35.0, 1)

    # 4. Experience Compatibility (up to 15 points)
    # Candidate Mohit has ~1.8 years experience. Ideal: 0-3 years.
    exp_score = 10.0
    exp_note = "Experience compatible"
    if min_exp is not None:
        if min_exp <= 2.5 and (max_exp is None or max_exp <= 4.0):
            exp_score = 15.0
            exp_note = f"Ideal experience bracket ({min_exp}-{max_exp or min_exp} yrs)"
        elif min_exp <= 3.5:
            exp_score = 10.0
            exp_note = f"Acceptable reach ({min_exp} yrs required)"
        else:
            exp_score = 2.0
            exp_note = f"Experience requirement higher than target ({min_exp} yrs)"
    else:
        # Unspecified experience defaults to entry/mid-tier
        exp_score = 12.0
        exp_note = "Experience requirement open / unspecified"

    # 5. Location and Work Mode Alignment (up to 15 points)
    preferred_locs = [
        "delhi", "delhi ncr", "gurugram", "gurgaon", "noida", "kolkata",
        "pune", "bengaluru", "bangalore", "mumbai", "india", "remote", "hybrid"
    ]
    
    loc_text = f"{location} {combined_job_text}".lower()
    matched_location = None
    loc_score = 5.0  # Open to pan-India
    for loc in preferred_locs:
        if loc in loc_text:
            matched_location = loc.title()
            if loc in ["delhi", "delhi ncr", "gurugram", "gurgaon", "noida"]:
                loc_score = 15.0  # Top tier preference
            elif loc in ["remote", "hybrid"]:
                loc_score = 15.0
            else:
                loc_score = 12.0
            break

    total_score = min(round(role_score + skills_score + exp_score + loc_score, 1), 100.0)
    
    # Eligibility check: must meet threshold score and have minimum role/skill presence
    is_eligible = (total_score >= MIN_MATCH_SCORE) and (role_score >= 15.0 or len(matched_skills) >= 2)

    details = {
        "eligible": is_eligible,
        "total_score": total_score,
        "matched_role": matched_role,
        "matched_skills": matched_skills,
        "matched_location": matched_location or location or "India",
        "experience_note": exp_note,
        "posting_age_days": posting_age,
        "breakdown": {
            "role_score": role_score,
            "skills_score": skills_score,
            "exp_score": exp_score,
            "loc_score": loc_score
        }
    }

    return is_eligible, total_score, details
