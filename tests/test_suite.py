import pytest
import os
import json
from pathlib import Path

from app.database import (
    init_db,
    record_application,
    is_job_applied_or_recorded,
    get_applications,
    get_all_platform_metrics,
    export_applications_csv,
    export_applications_json
)
from app.matcher import (
    normalize_job_url,
    evaluate_job_match,
    get_resume_text,
    get_profile
)
from app.runner import manager

@pytest.fixture(autouse=True)
def setup_test_db():
    init_db()

def test_phase_a_environment_and_resume():
    """Verify local setup, database initialization, and local resume extraction."""
    metrics = get_all_platform_metrics()
    assert "naukri" in metrics
    assert "linkedin" in metrics
    assert "indeed" in metrics
    assert "hirist" in metrics

    # Verify resume text extraction
    resume_text = get_resume_text()
    assert len(resume_text) > 1000
    assert "Mohit Kumar" in resume_text
    assert "Risk Analyst" in resume_text

    # Verify profile data
    profile = get_profile()
    assert profile["personal"]["full_name"] == "Mohit Kumar"
    assert profile["education"]["college_percentage"] == 82.0
    assert "Data Analyst" in profile["target_roles"]

def test_phase_c_url_normalization():
    """Verify tracking parameters are stripped to prevent duplicate submissions."""
    raw_url = "https://www.naukri.com/job-listings-data-analyst-delhi-12345?utm_source=google&ref=searchDesk&trackingId=xyz"
    normalized = normalize_job_url(raw_url)
    assert "utm_source" not in normalized
    assert "trackingId" not in normalized
    assert normalized == "https://www.naukri.com/job-listings-data-analyst-delhi-12345"

def test_phase_d_matching_and_eligibility():
    """Verify scoring logic, role alignment, skills weighting, and seniority rejection."""
    # 1. High match analyst role with valid recent posting date
    eligible, score, details = evaluate_job_match(
        title="Junior Data Analyst",
        description="Looking for an analyst with Python, SQL, Power BI, and Excel skills for transaction monitoring.",
        location="Delhi NCR",
        experience_text="1-2 Yrs",
        posted_date_text="1 day ago"
    )
    assert eligible is True
    assert score >= 75.0
    assert "Python" in details["matched_skills"]
    assert "SQL" in details["matched_skills"]

    # 2. Senior role rejection
    senior_eligible, senior_score, senior_details = evaluate_job_match(
        title="VP of Global Operations",
        description="Must have 12+ years experience managing multi-national teams.",
        location="Bengaluru",
        experience_text="12-15 Yrs",
        posted_date_text="Today"
    )
    assert senior_eligible is False
    assert senior_score < 40.0
    assert "Seniority mismatch" in senior_details["rejection_reason"]

def test_job_posting_date_filter():
    """Verify Job Posting Date Filter: Only consider jobs posted within last 3-4 days."""
    # Valid recent postings (0 to 4 days)
    for valid_date in ["Just now", "Today", "1 day ago", "2 days ago", "3d", "4 days ago"]:
        eligible, _, det = evaluate_job_match(
            title="Junior Data Analyst",
            description="Python SQL analyst",
            location="Delhi",
            experience_text="1-2 Yrs",
            posted_date_text=valid_date
        )
        assert eligible is True, f"Failed for valid date: {valid_date}"

    # Outdated postings (> 4 days)
    for old_date in ["5 days ago", "6d ago", "12 days ago", "1 month ago", "30+ days ago"]:
        eligible, _, det = evaluate_job_match(
            title="Junior Data Analyst",
            description="Python SQL analyst",
            location="Delhi",
            experience_text="1-2 Yrs",
            posted_date_text=old_date
        )
        assert eligible is False, f"Expected reject for old date: {old_date}"
        assert "Job posting is outdated" in det["rejection_reason"]

    # Missing or unverified date
    for missing_date in [None, "", "   "]:
        eligible, _, det = evaluate_job_match(
            title="Junior Data Analyst",
            description="Python SQL analyst",
            location="Delhi",
            experience_text="1-2 Yrs",
            posted_date_text=missing_date
        )
        assert eligible is False
        assert "missing or cannot be verified" in det["rejection_reason"]

def test_automatic_cleanup_and_history_retention():
    """
    Verify Automatic Data Cleanup (5-6 days) and History Preservation:
    - Outdated temporary manual-action records (> 6 days) are deleted.
    - Verified application records ('Applied — Verified') are PRESERVED regardless of age.
    - Duplicate prevention continues to recognize verified applications.
    """
    from datetime import datetime, timezone, timedelta
    from app.database import cleanup_outdated_data, get_db_connection

    conn = get_db_connection()
    cursor = conn.cursor()

    ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

    # 1. Insert verified application from 10 days ago (MUST BE PRESERVED)
    cursor.execute("""
        INSERT OR REPLACE INTO applications (
            id, platform, job_id, job_title, employer_name, job_url,
            match_score, status, discovery_timestamp, created_at
        ) VALUES ('app-old-verified', 'linkedin', 'li_old_verif_1', 'Verified Analyst', 'Top Employer',
                  'https://www.linkedin.com/jobs/view/old-verified-1', 90.0, 'Applied — Verified', ?, ?)
    """, (ten_days_ago, ten_days_ago))

    # 2. Insert outdated manual-action record from 10 days ago (MUST BE DELETED)
    cursor.execute("""
        INSERT OR REPLACE INTO applications (
            id, platform, job_id, job_title, employer_name, job_url,
            match_score, status, discovery_timestamp, created_at
        ) VALUES ('app-old-manual', 'naukri', 'nk_old_manual_2', 'Old Manual Job', 'Old Employer',
                  'https://www.naukri.com/job-listings-old-manual-2', 75.0, 'Manual Action Required', ?, ?)
    """, (ten_days_ago, ten_days_ago))

    # 3. Insert recent manual-action record from 2 days ago (MUST BE PRESERVED)
    cursor.execute("""
        INSERT OR REPLACE INTO applications (
            id, platform, job_id, job_title, employer_name, job_url,
            match_score, status, discovery_timestamp, created_at
        ) VALUES ('app-recent-manual', 'indeed', 'ind_rec_manual_3', 'Recent Manual Job', 'Recent Employer',
                  'https://in.indeed.com/viewjob?jk=recent-manual-3', 80.0, 'Manual Action Required', ?, ?)
    """, (two_days_ago, two_days_ago))

    conn.commit()
    conn.close()

    # Run cleanup with 6 days retention
    stats = cleanup_outdated_data(retention_days=6)
    assert stats["deleted_manual_records"] >= 1

    # Verify:
    # 1. Verified old application is PRESERVED
    assert is_job_applied_or_recorded('linkedin', 'li_old_verif_1', 'https://www.linkedin.com/jobs/view/old-verified-1') is True

    # 2. Old manual action record is DELETED
    assert is_job_applied_or_recorded('naukri', 'nk_old_manual_2', 'https://www.naukri.com/job-listings-old-manual-2') is False

    # 3. Recent manual action record is PRESERVED
    assert is_job_applied_or_recorded('indeed', 'ind_rec_manual_3', 'https://in.indeed.com/viewjob?jk=recent-manual-3') is True

def test_phase_d_deduplication():
    """Verify deduplication detects existing records and skips re-submitting."""
    test_url = "https://www.linkedin.com/jobs/view/test-dedup-analyst-999"
    normalized_url = normalize_job_url(test_url)
    
    # Record mock application
    record_application(
        platform="linkedin",
        job_id="li_test_999",
        job_title="Test Analyst",
        employer_name="Test Corp",
        job_url=normalized_url,
        job_category="Analytics",
        job_location="Delhi",
        experience_req="1-2 Yrs",
        match_score=85.0,
        status="Applied — Verified"
    )

    # Check deduplication
    assert is_job_applied_or_recorded("linkedin", "li_test_999", normalized_url) is True
    assert is_job_applied_or_recorded("linkedin", "different_id", "https://unseen-job.com") is False

def test_phase_e_platform_independence():
    """Verify that four platforms operate independently."""
    statuses = manager.get_all_statuses()
    for p in ["naukri", "linkedin", "indeed", "hirist"]:
        assert p in statuses
        assert statuses[p]["connection_status"] in [
            "Disconnected", "Connecting", "Connected", "Authentication Required", "Error"
        ]

def test_phase_h_storage_and_export():
    """Verify CSV and JSON export functionality."""
    csv_data = export_applications_csv()
    assert "id,platform,job_id,job_title,employer_name" in csv_data

    json_data = export_applications_json()
    parsed = json.loads(json_data)
    assert isinstance(parsed, list)

