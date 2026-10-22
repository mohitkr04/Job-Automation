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
