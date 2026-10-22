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
    # 1. High match analyst role
    eligible, score, details = evaluate_job_match(
        title="Junior Data Analyst",
        description="Looking for an analyst with Python, SQL, Power BI, and Excel skills for transaction monitoring.",
        location="Delhi NCR",
        experience_text="1-2 Yrs"
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
        experience_text="12-15 Yrs"
    )
    assert senior_eligible is False
    assert senior_score < 40.0
    assert "Seniority mismatch" in senior_details["rejection_reason"]

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
