from starlette.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_api_platforms():
    res = client.get("/api/platforms")
    assert res.status_code == 200
    data = res.json()
    assert "naukri" in data
    assert "linkedin" in data
    assert "indeed" in data
    assert "hirist" in data

def test_api_applications():
    res = client.get("/api/applications")
    assert res.status_code == 200
    assert isinstance(res.json(), list)

def test_api_profile():
    res = client.get("/api/profile")
    assert res.status_code == 200
    data = res.json()
    assert data["personal"]["full_name"] == "Mohit Kumar"

def test_api_resume_info():
    res = client.get("/api/resume/info")
    assert res.status_code == 200
    data = res.json()
    assert data["exists"] is True
    assert data["file_size_bytes"] > 0
    assert data["extracted_character_count"] > 1000

def test_api_export_csv():
    res = client.get("/api/applications/export?format=csv")
    assert res.status_code == 200
    assert "text/csv" in res.headers.get("content-type", "")

def test_api_export_json():
    res = client.get("/api/applications/export?format=json")
    assert res.status_code == 200
    assert "application/json" in res.headers.get("content-type", "")

def test_api_maintenance_cleanup():
    res = client.post("/api/maintenance/cleanup")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "cleanup_stats" in data
    assert "retention_days" in data["cleanup_stats"]

def test_api_manual_application_workflow(monkeypatch):
    from app.database import record_application, get_application_by_id

    # Mock launch_headed_browser to prevent popping real browser window during testing
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "launch_headed_browser", lambda platform, target_url=None: {
        "status": "ok",
        "mocked": True,
        "platform": platform,
        "url": target_url
    })

    # 1. Create a mock manual action application
    test_app_id = record_application(
        platform="naukri",
        job_id="test_manual_job_999",
        job_title="Data Analyst - Manual Test",
        employer_name="Fintech Corp",
        job_url="https://www.naukri.com/job-listings-manual-test-999",
        job_category="Data Analyst",
        job_location="Gurgaon",
        experience_req="1-2 Yrs",
        match_score=88.5,
        status="Manual Action Required",
        verification_result=None,
        failure_reason="Screening test requires manual completion",
        posted_date="Today",
        posting_age_days=0.1
    )

    assert test_app_id != ""

    # 2. Test opening the manual application
    res_open = client.post(f"/api/applications/{test_app_id}/open")
    assert res_open.status_code == 200
    assert res_open.json()["status"] == "ok"

    # 3. Test marking manual application as verified
    res_verify = client.post(f"/api/applications/{test_app_id}/verify")
    assert res_verify.status_code == 200
    assert res_verify.json()["status"] == "ok"

    updated = get_application_by_id(test_app_id)
    assert updated["status"] == "Applied — Verified"
    assert "manually completed" in updated["verification_result"].lower()

    # 4. Test dismissing/deleting the application
    res_delete = client.delete(f"/api/applications/{test_app_id}")
    assert res_delete.status_code == 200
    assert res_delete.json()["status"] == "ok"

    assert get_application_by_id(test_app_id) is None

def test_api_platform_login_session(monkeypatch):
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "launch_headed_browser", lambda platform, target_url=None: {
        "status": "ok",
        "mocked": True,
        "platform": platform
    })

    res = client.post("/api/platforms/naukri/login")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

def test_api_platform_start_and_stop():
    # 1. Start naukri
    res_start = client.post("/api/platforms/naukri/start")
    assert res_start.status_code == 200
    assert res_start.json()["status"] in ["ok", "already_running"]

    # 2. Duplicate start while active returns already_running
    res_dup = client.post("/api/platforms/naukri/start")
    assert res_dup.status_code == 200
    assert res_dup.json()["status"] in ["ok", "already_running"]

    # 3. Stop naukri
    res_stop = client.post("/api/platforms/naukri/stop")
    assert res_stop.status_code == 200
    assert res_stop.json()["status"] == "ok"



