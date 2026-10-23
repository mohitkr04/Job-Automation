import pytest
import os
import json
import asyncio
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

@pytest.mark.asyncio
async def test_multi_batch_discovery_removes_four_job_limit():
    """Verify that platforms support multi-batch discovery exceeding the 4-job limit."""
    naukri_runner = manager.runners["naukri"]
    batch_0 = await naukri_runner.discover_jobs(batch_index=0)
    batch_1 = await naukri_runner.discover_jobs(batch_index=1)
    
    assert len(batch_0) >= 5, f"Batch 0 returned only {len(batch_0)} jobs"
    assert len(batch_1) >= 4, f"Batch 1 returned only {len(batch_1)} jobs"
    total_discovered = len(batch_0) + len(batch_1)
    assert total_discovered > 4, "Failed to remove 4-job limit"
    
    # Verify IDs are distinct across batches
    ids_0 = {j["job_id"] for j in batch_0}
    ids_1 = {j["job_id"] for j in batch_1}
    assert ids_0.isdisjoint(ids_1), "Batches must contain distinct job IDs"

@pytest.mark.asyncio
async def test_stop_button_graceful_interruption():
    """Verify Start and Stop controls operate cleanly without hanging or state corruption."""
    runner = manager.runners["indeed"]
    started = await runner.start()
    assert started is True
    assert runner.is_running is True

    # Stop runner
    await runner.stop()
    assert runner.is_running is False
    assert runner.is_stopped() is True

@pytest.mark.asyncio
async def test_prevent_duplicate_concurrent_runs():
    """Verify that multiple Start requests do not spawn duplicate background runs."""
    runner = manager.runners["hirist"]
    started_first = await runner.start()
    assert started_first is True

    # Second start while running must return False
    started_second = await runner.start()
    assert started_second is False

    await runner.stop()

def test_record_application_stores_posted_date_and_revalidation():
    """Verify record_application persists posted_date and get_applications revalidates."""
    app_id = record_application(
        platform="naukri",
        job_id="test_date_validation_job",
        job_title="Risk Data Analyst",
        employer_name="Fintech Corp",
        job_url="https://www.naukri.com/job-listings-date-val-123",
        job_category="Analytics",
        job_location="Delhi NCR",
        experience_req="1-2 Yrs",
        match_score=85.0,
        status="Manual Action Required",
        posted_date="2 days ago",
        posting_age_days=2.0
    )

    # Must appear in active applications
    active_apps = get_applications(platform="naukri", status="Manual Action Required")
    matching = [a for a in active_apps if a["id"] == app_id]
    assert len(matching) == 1
    assert matching[0]["posted_date"] == "2 days ago"
    assert matching[0]["posting_age_days"] == 2.0

    # Insert an outdated manual record (15 days ago)
    old_app_id = record_application(
        platform="naukri",
        job_id="test_old_job_15d",
        job_title="Legacy Analyst",
        employer_name="Old Corp",
        job_url="https://www.naukri.com/job-listings-old-15d",
        job_category="Analytics",
        job_location="Delhi NCR",
        experience_req="1-2 Yrs",
        match_score=80.0,
        status="Manual Action Required",
        posted_date="15 days ago",
        posting_age_days=15.0
    )

    # get_applications MUST filter out the 15-day-old job
    revalidated = get_applications(platform="naukri", status="Manual Action Required")
    assert not any(a["id"] == old_app_id for a in revalidated), "Outdated job (>4 days) must be excluded from active queue"

@pytest.mark.asyncio
async def test_auto_apply_form_filling_and_resume_attachment(monkeypatch):
    """Verify applicant details and resume attachment during auto-apply execution."""
    runner = manager.runners["naukri"]
    
    # 1. Verify candidate profile details extracted for application form
    applicant = runner.prepare_applicant_data()
    assert applicant["full_name"] == "Mohit Kumar"
    assert applicant["email"] == "mohitkrgupta.13.05@gmail.com"
    assert applicant["phone"] == "6299329495"
    assert applicant["current_location"] == "Delhi"
    assert "15 days" in applicant["notice_period"]
    assert applicant["expected_ctc"] == "9-10 LPA"
    assert applicant["experience_years"] == 1.8
    assert applicant["resume_verified"] is True
    assert applicant["resume_size_bytes"] > 1000
    assert applicant["resume_filename"] == "resume.pdf"

    # 2. Test auto-apply submission in authenticated session
    monkeypatch.setattr(runner, "check_connection_status", lambda: asyncio.sleep(0, result="Connected"))

    mock_job = {
        "job_id": "test_direct_apply_job",
        "title": "Junior Data Analyst",
        "company": "Test Analytics Ltd",
        "url": "https://www.naukri.com/job-listings-test-direct",
        "apply_type": "direct",
        "posted_date": "Today"
    }
    mock_match = {"eligible": True, "total_score": 88.0, "posting_age_days": 0.1}

    outcome, verif_msg, fail_reason = await runner.apply_to_job(mock_job, mock_match)
    assert outcome == "Applied — Verified"
    assert fail_reason is None
    assert "Mohit Kumar" in verif_msg
    assert "mohitkrgupta.13.05@gmail.com" in verif_msg
    assert "6299329495" in verif_msg
    assert "resume.pdf" in verif_msg

    # 3. Test failure safety when resume is missing
    monkeypatch.setattr("app.platforms.base.RESUME_PATH", "non_existent_resume.pdf")
    fail_outcome, fail_msg, fail_r = await runner.apply_to_job(mock_job, mock_match)
    assert fail_outcome == "Failed"
    assert fail_r == "Missing Resume"



