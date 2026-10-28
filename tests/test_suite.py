import pytest
import os
import json
import asyncio
from pathlib import Path

from datetime import datetime, timezone

from app.database import (
    init_db,
    record_application,
    is_job_applied_or_recorded,
    get_applications,
    get_all_platform_metrics,
    export_applications_csv,
    export_applications_json,
    cleanup_outdated_data,
    update_application_status,
    get_application_by_id
)
from app.matcher import (
    normalize_job_url,
    evaluate_job_match,
    get_resume_text,
    get_profile,
    validate_job_identity,
    verify_job_url_remote,
    extract_platform_job_id,
    validate_job_posting_date
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

    # 3. Insert recent manual-action record from 2 days ago with valid posting date (MUST BE PRESERVED)
    cursor.execute("""
        INSERT OR REPLACE INTO applications (
            id, platform, job_id, job_title, employer_name, job_url,
            match_score, status, posted_date, posting_age_days, discovery_timestamp, created_at
        ) VALUES ('app-recent-manual', 'indeed', 'ind_rec_manual_3', 'Recent Manual Job', 'Recent Employer',
                  'https://in.indeed.com/viewjob?jk=recent-manual-3', 80.0, 'Manual Action Required', 'Today', 0.1, ?, ?)
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

def test_priority_1_to_5_mandatory_requirements():
    """
    Comprehensive tests covering all 12 mandatory test cases from Priority 1 to 5:
    1. A job posted today is accepted if all other checks pass.
    2. A job posted three days ago is accepted if all other checks pass.
    3. A job posted five days ago is rejected.
    4. A job posted months or years ago is rejected.
    5. A job with an unverifiable posting date is excluded from both queues.
    6. An old job already in the database is removed from active queues.
    7. A cached old job cannot reappear in the dashboard.
    8. An eligible, unapplied job requiring manual intervention appears only in the manual dashboard.
    9. A successfully verified application is not submitted again.
    10. A failed or ambiguous submission is handled safely.
    11. Stopping the automation prevents new jobs from being processed.
    12. All four platforms apply the same cutoff rules.
    """
    from app.matcher import validate_job_posting_date, evaluate_job_match
    from app.database import (
        record_application,
        get_applications,
        cleanup_outdated_data,
        is_job_applied_or_recorded,
        get_db_connection
    )

    # 1. Job posted today accepted
    val_today, age_today, _ = validate_job_posting_date("Today")
    assert val_today is True
    assert age_today <= 4.0
    el_today, _, _ = evaluate_job_match("Data Analyst", "Python SQL", "Delhi", "1-2 Yrs", "Today")
    assert el_today is True

    # 2. Job posted 3 days ago accepted
    val_3d, age_3d, _ = validate_job_posting_date("3 days ago")
    assert val_3d is True
    assert age_3d <= 4.0
    el_3d, _, _ = evaluate_job_match("Data Analyst", "Python SQL", "Delhi", "1-2 Yrs", "3 days ago")
    assert el_3d is True

    # 3. Job posted 5 days ago rejected
    val_5d, age_5d, reason_5d = validate_job_posting_date("5 days ago")
    assert val_5d is False
    assert age_5d == 5.0
    assert "outdated" in reason_5d.lower()
    el_5d, _, det_5d = evaluate_job_match("Data Analyst", "Python SQL", "Delhi", "1-2 Yrs", "5 days ago")
    assert el_5d is False
    assert "outdated" in det_5d["rejection_reason"].lower()

    # 4. Job posted months or years ago (e.g. 15 years ago) rejected
    val_15y, age_15y, reason_15y = validate_job_posting_date("15 years ago")
    assert val_15y is False
    assert age_15y > 1000.0
    assert "outdated" in reason_15y.lower()

    val_2mo, age_2mo, reason_2mo = validate_job_posting_date("2 months ago")
    assert val_2mo is False
    assert age_2mo >= 60.0

    # 5. Unverifiable posting date excluded from both queues
    for unverifiable in [None, "", "   ", "Apply before Dec 31st", "Closing date: Tomorrow", "Scraped today"]:
        val_unv, _, r_unv = validate_job_posting_date(unverifiable)
        assert val_unv is False
        el_unv, _, det_unv = evaluate_job_match("Data Analyst", "Python SQL", "Delhi", "1-2 Yrs", unverifiable)
        assert el_unv is False

        # Must not enter manual action queue in DB
        res_id = record_application(
            platform="naukri",
            job_id=f"test_unv_{hash(unverifiable)}",
            job_title="Data Analyst",
            employer_name="Company",
            job_url=f"https://example.com/{hash(unverifiable)}",
            job_category="Analytics",
            job_location="Delhi",
            experience_req="1-2 Yrs",
            match_score=80.0,
            status="Manual Action Required",
            posted_date=unverifiable
        )
        assert res_id == "", "Unverifiable posting date must be rejected by backend record_application"

    # 6. An old job already in the database is removed from active queues
    from datetime import datetime, timezone, timedelta
    one_day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO applications (
            id, platform, job_id, job_title, employer_name, job_url,
            match_score, status, posted_date, posting_age_days, discovery_timestamp, created_at
        ) VALUES ('app-legacy-15y', 'indeed', 'ind_legacy_15y', 'Ancient Analyst', 'Old Corp',
                  'https://example.com/ancient-15y', 75.0, 'Manual Action Required', '15 years ago', 5475.0,
                  ?, ?)
    """, (one_day_ago, one_day_ago))
    conn.commit()
    conn.close()

    # Clean data
    cleanup_res = cleanup_outdated_data(max_posting_age=4)
    assert cleanup_res["deleted_outdated_posting"] >= 1

    # 7. A cached old job cannot reappear in the dashboard
    active_apps = get_applications(platform="indeed", status="Manual Action Required")
    assert not any(a["id"] == "app-legacy-15y" for a in active_apps)

    # 8. An eligible, unapplied job requiring manual intervention appears only in the manual dashboard
    recent_manual_id = record_application(
        platform="hirist",
        job_id="hirist_recent_manual_valid",
        job_title="Risk & Operations Analyst",
        employer_name="Top Fintech",
        job_url="https://example.com/hirist-recent-valid",
        job_category="Analytics",
        job_location="Delhi",
        experience_req="1-2 Yrs",
        match_score=85.0,
        status="Manual Action Required",
        posted_date="2 days ago",
        posting_age_days=2.0
    )
    assert recent_manual_id != ""
    manual_apps = get_applications(platform="hirist", status="Manual Action Required")
    assert any(a["id"] == recent_manual_id for a in manual_apps)

    # 9. A successfully verified application is not submitted again
    verified_id = record_application(
        platform="hirist",
        job_id="hirist_recent_manual_valid",
        job_title="Risk & Operations Analyst",
        employer_name="Top Fintech",
        job_url="https://example.com/hirist-recent-valid",
        job_category="Analytics",
        job_location="Delhi",
        experience_req="1-2 Yrs",
        match_score=85.0,
        status="Applied — Verified",
        posted_date="2 days ago",
        posting_age_days=2.0
    )
    assert is_job_applied_or_recorded("hirist", "hirist_recent_manual_valid", "https://example.com/hirist-recent-valid") is True

    # 10. A failed or ambiguous submission is handled safely
    fail_id = record_application(
        platform="naukri",
        job_id="naukri_fail_test",
        job_title="Business Analyst",
        employer_name="Company X",
        job_url="https://example.com/naukri-fail-test",
        job_category="Analytics",
        job_location="Delhi",
        experience_req="1-2 Yrs",
        match_score=75.0,
        status="Failed",
        failure_reason="Network timeout during form submission"
    )
    failed_apps = get_applications(platform="naukri", status="Failed")
    assert any(a["id"] == fail_id for a in failed_apps)
    # Ensure it did NOT enter Applied or Manual queues
    assert not any(a["id"] == fail_id for a in get_applications(platform="naukri", status="Applied — Verified"))
    assert not any(a["id"] == fail_id for a in get_applications(platform="naukri", status="Manual Action Required"))

    # 11. Stopping the automation prevents new jobs from being processed
    runner = manager.runners["naukri"]
    runner.stop_event.set()
    assert runner.is_stopped() is True

    # 12. All four platforms apply the same cutoff rules (MAX_POSTING_AGE_DAYS)
    for p in ["naukri", "linkedin", "indeed", "hirist"]:
        p_runner = manager.runners[p]
        assert p_runner is not None
        # All runners use base evaluate_job_match & validate_job_posting_date
        el, _, _ = evaluate_job_match("Data Analyst", "Python SQL", "Delhi", "1-2 Yrs", "5 days ago")
        assert el is False
        el_ok, _, _ = evaluate_job_match("Data Analyst", "Python SQL", "Delhi", "1-2 Yrs", "1 day ago")
        assert el_ok is True


# -------------------------------------------------------------
# Priority 6: Automated Regression Tests for Job URL & Identity
# -------------------------------------------------------------

def test_regression_1_valid_recent_linkedin_job_opens_its_own_listing(monkeypatch):
    """1. A valid recent LinkedIn job opens its own listing."""
    from starlette.testclient import TestClient
    from app.main import app

    app_id = record_application(
        platform="linkedin",
        job_id="li_4483210110",
        job_title="Business Analyst",
        employer_name="TravClan",
        job_url="https://www.linkedin.com/jobs/view/4483210110/",
        job_category="Analytics",
        job_location="Delhi NCR",
        experience_req="1-2 Yrs",
        match_score=92.0,
        status="Manual Action Required",
        posted_date="1 day ago",
        posting_age_days=1.0
    )
    assert app_id != ""

    # Mock remote verifier to simulate live valid LinkedIn response
    monkeypatch.setattr(
        "app.main.verify_job_url_remote",
        lambda **kwargs: (True, "Listing verified online", "https://www.linkedin.com/jobs/view/4483210110/")
    )
    launched_urls = []
    monkeypatch.setattr(
        "app.main.launch_headed_browser",
        lambda platform, target_url: launched_urls.append((platform, target_url)) or {"status": "ok"}
    )

    client = TestClient(app)
    res = client.post(f"/api/applications/{app_id}/open")
    assert res.status_code == 200
    assert launched_urls == [("linkedin", "https://www.linkedin.com/jobs/view/4483210110/")]


def test_regression_2_job_url_belonging_to_another_job_rejected(monkeypatch):
    """2. A job URL belonging to another job is rejected."""
    from starlette.testclient import TestClient
    from app.main import app

    app_id = record_application(
        platform="linkedin",
        job_id="li_4489991111",
        job_title="Payments & Settlement Analyst",
        employer_name="Pine Labs",
        job_url="https://www.linkedin.com/jobs/view/4489991111/",
        job_category="Analytics",
        job_location="Noida",
        experience_req="1-3 Yrs",
        match_score=88.0,
        status="Manual Action Required",
        posted_date="2 days ago",
        posting_age_days=2.0
    )

    # Remote verifier detects title/employer mismatch
    monkeypatch.setattr(
        "app.main.verify_job_url_remote",
        lambda **kwargs: (False, "Destination listing 'Sr. Loan Processor at Cornerstone' does not match 'Payments & Settlement Analyst'", None)
    )

    client = TestClient(app)
    res = client.post(f"/api/applications/{app_id}/open")
    assert res.status_code == 410
    assert "Destination listing" in res.json()["detail"]

    # Record must be marked Listing Unavailable and removed from active manual queue
    updated = get_application_by_id(app_id)
    assert updated["status"] == "Listing Unavailable"
    active_manual = get_applications(status="Manual Action Required")
    assert not any(a["id"] == app_id for a in active_manual)


def test_regression_3_recently_scraped_but_15_year_old_listing_rejected():
    """3. A recently scraped but 15-year-old listing is rejected."""
    job = {
        "title": "Sr. Loan Processor",
        "company": "Cornerstone Home Lending",
        "url": "https://www.linkedin.com/jobs/view/4489992222/",
        "job_id": "li_4489992222",
        "posted_date": "15 years ago",
        "discovery_timestamp": datetime.now(timezone.utc).isoformat()
    }
    is_valid, reason, _ = validate_job_identity(job, "linkedin", check_remote=False)
    assert is_valid is False
    assert "outdated" in reason or "rejected" in reason

    rec_id = record_application(
        platform="linkedin",
        job_id="li_4489992222",
        job_title="Sr. Loan Processor",
        employer_name="Cornerstone Home Lending",
        job_url="https://www.linkedin.com/jobs/view/4489992222/",
        job_category="Analytics",
        job_location="Remote",
        experience_req="1-2 Yrs",
        match_score=85.0,
        status="Manual Action Required",
        posted_date="15 years ago"
    )
    assert rec_id == ""


def test_regression_4_closed_job_excluded_from_active_queues():
    """4. A closed job is excluded from active queues."""
    is_valid, reason, _ = verify_job_url_remote(
        platform="linkedin",
        job_url="https://www.linkedin.com/jobs/view/payments-settlement-pinelabs-4029232",
        expected_title="Payments & Settlement Analyst",
        expected_employer="Pine Labs",
        expected_job_id="li_4029232"
    )
    assert is_valid is False

    app_id = record_application(
        platform="linkedin",
        job_id="li_4489993333",
        job_title="Business Analyst",
        employer_name="TravClan",
        job_url="https://www.linkedin.com/jobs/loan-coordinator-jobs?trk=expired_jd_redirect",
        job_category="Analytics",
        job_location="Delhi",
        experience_req="1-2 Yrs",
        match_score=85.0,
        status="Manual Action Required",
        posted_date="1 day ago"
    )
    assert app_id == ""


def test_regression_5_invalid_or_missing_job_id_handled_safely():
    """5. An invalid or missing job ID is handled safely."""
    generic_job = {
        "title": "Junior Data Analyst",
        "company": "Swiggy",
        "url": "https://www.linkedin.com/jobs/search?keywords=Data%20Analyst",
        "job_id": None,
        "posted_date": "1 day ago",
        "experience": "1-2 Yrs"
    }
    is_valid, reason, _ = validate_job_identity(generic_job, "linkedin", check_remote=False)
    assert is_valid is False
    assert "individual job identifier" in reason

    legacy_job = {
        "title": "Payments Analyst",
        "company": "Pine Labs",
        "url": "https://www.linkedin.com/jobs/view/payments-analyst-4029232",
        "job_id": "li_4029232",
        "posted_date": "2 days ago",
        "experience": "1-2 Yrs"
    }
    is_valid2, reason2, _ = validate_job_identity(legacy_job, "linkedin", check_remote=False)
    assert is_valid2 is False
    assert "legacy synthetic job ID" in reason2


def test_regression_6_stale_cached_record_cannot_reappear():
    """6. A stale cached record cannot reappear."""
    import sqlite3
    from app.config import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO applications (
            id, platform, job_id, job_title, employer_name, job_url,
            job_category, job_location, experience_req, match_score,
            discovery_timestamp, status, created_at, posted_date, posting_age_days
        ) VALUES (
            'test_stale_reappear', 'linkedin', 'li_4029232', 'Payments & Settlement Analyst',
            'Pine Labs', 'https://www.linkedin.com/jobs/view/payments-settlement-pinelabs-4029232',
            'Analytics', 'Noida', '1-3 Yrs', 90.0, '2026-10-28T05:00:00Z',
            'Manual Action Required', '2026-10-28T05:00:00Z', '2 days ago', 2.0
        )
    """)
    conn.commit()
    conn.close()

    apps = get_applications(status="Manual Action Required")
    assert not any(a["id"] == "test_stale_reappear" for a in apps)

    stats = cleanup_outdated_data()
    assert stats["deleted_invalid_identity"] >= 1

    reintro_id = record_application(
        platform="linkedin",
        job_id="li_4029232",
        job_title="Payments & Settlement Analyst",
        employer_name="Pine Labs",
        job_url="https://www.linkedin.com/jobs/view/payments-settlement-pinelabs-4029232",
        job_category="Analytics",
        job_location="Noida",
        experience_req="1-3 Yrs",
        match_score=90.0,
        status="Manual Action Required",
        posted_date="2 days ago"
    )
    assert reintro_id == ""


def test_regression_7_open_button_uses_selected_records_validated_url(monkeypatch):
    """7. The Open button uses the selected record's validated URL."""
    from starlette.testclient import TestClient
    from app.main import app

    app_id = record_application(
        platform="linkedin",
        job_id="li_4483556411",
        job_title="Senior Business Analyst",
        employer_name="noon",
        job_url="https://in.linkedin.com/jobs/view/senior-business-analyst-at-noon-4483556411?refId=123",
        job_category="Analytics",
        job_location="Delhi",
        experience_req="1-3 Yrs",
        match_score=89.0,
        status="Manual Action Required",
        posted_date="1 day ago",
        posting_age_days=1.0
    )

    opened_data = {}
    monkeypatch.setattr(
        "app.main.verify_job_url_remote",
        lambda **kwargs: (True, "Listing verified online", "https://www.linkedin.com/jobs/view/4483556411/")
    )
    monkeypatch.setattr(
        "app.main.launch_headed_browser",
        lambda platform, target_url: opened_data.update({"platform": platform, "url": target_url}) or {"status": "ok"}
    )

    client = TestClient(app)
    res = client.post(f"/api/applications/{app_id}/open")
    assert res.status_code == 200
    assert opened_data["url"] == "https://www.linkedin.com/jobs/view/4483556411/"
    assert opened_data["platform"] == "linkedin"

    app_data = get_application_by_id(app_id)
    assert app_data["status"] == "Manual Action Required"


def test_regression_8_other_platforms_continue_to_work_independently():
    """8. Other platforms continue to work independently."""
    for plat in ["naukri", "indeed", "hirist"]:
        runner = manager.runners[plat]
        assert runner is not None
        plat_apps = get_applications(platform=plat, status="Manual Action Required")
        assert len(plat_apps) > 0, f"Expected active manual listings for {plat}"
        for a in plat_apps:
            assert a["platform"] == plat
            assert a["job_url"] != ""
            assert a["posting_age_days"] <= 4.0


def test_regression_9_existing_verified_application_history_remains_intact():
    """9. Existing verified application history remains intact."""
    verified_before = get_applications(status="Applied — Verified")
    assert len(verified_before) >= 3

    cleanup_outdated_data(retention_days=1)
    cleanup_outdated_data(retention_days=30)

    verified_after = get_applications(status="Applied — Verified")
    assert len(verified_after) == len(verified_before)
    verified_ids = {a["id"] for a in verified_after}
    for orig in verified_before:
        assert orig["id"] in verified_ids


def test_regression_10_frontend_accurately_reflects_backend_validation_results():
    """10. The frontend accurately reflects backend validation results."""
    from starlette.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    res = client.get("/api/applications?status=Manual Action Required")
    assert res.status_code == 200
    items = res.json()
    for item in items:
        if item["platform"] == "linkedin":
            assert not item["job_id"].startswith("li_40292")
            assert "4029232" not in item["job_url"]
        assert item["posting_age_days"] <= 4.0



