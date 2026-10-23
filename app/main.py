import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.config import BASE_DIR, APP_HOST, APP_PORT, DEBUG, RESUME_PATH
from app.database import (
    init_db,
    get_applications,
    export_applications_csv,
    export_applications_json,
    cleanup_outdated_data,
    update_application_status,
    delete_application,
    get_application_by_id
)
from app.matcher import get_profile, update_profile, get_resume_text
from app.runner import manager
from app.browser_assistant import launch_headed_browser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("app.main")

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    cleaned = cleanup_outdated_data()
    logger.info(f"Local database initialized and cleaned: {cleaned}")
    yield

app = FastAPI(title="Local-First Job Application Automation", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Endpoints
@app.get("/api/platforms")
async def get_platforms():
    return manager.get_all_statuses()

@app.post("/api/platforms/{platform_id}/start")
async def start_platform(platform_id: str):
    try:
        started = await manager.start_platform(platform_id)
        if not started:
            return {"status": "already_running", "message": f"{platform_id} automation is already active"}
        return {"status": "ok", "message": f"{platform_id} automation started"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/platforms/{platform_id}/stop")
async def stop_platform(platform_id: str):
    try:
        await manager.stop_platform(platform_id)
        return {"status": "ok", "message": f"{platform_id} automation stopped"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/platforms/{platform_id}/test-connection")
async def test_platform_connection(platform_id: str):
    try:
        status = await manager.check_platform_connection(platform_id)
        return {"platform": platform_id, "connection_status": status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/platforms/{platform_id}/login")
async def login_platform(platform_id: str):
    try:
        res = launch_headed_browser(platform_id)
        return {"status": "ok", "message": f"Launched login session for {platform_id}", "details": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/applications/{app_id}/open")
async def open_application_in_browser(app_id: str):
    app_data = get_application_by_id(app_id)
    if not app_data:
        raise HTTPException(status_code=404, detail="Application record not found")
    
    url = app_data.get("job_url")
    platform = app_data.get("platform", "naukri")
    if not url:
        raise HTTPException(status_code=400, detail="Application has no job URL")

    res = launch_headed_browser(platform, target_url=url)
    return {"status": "ok", "message": f"Opened {app_data.get('job_title')} in {platform} browser session", "details": res}

@app.post("/api/applications/{app_id}/verify")
async def mark_application_verified(app_id: str):
    app_data = get_application_by_id(app_id)
    if not app_data:
        raise HTTPException(status_code=404, detail="Application record not found")
    
    success = update_application_status(
        app_id,
        "Applied — Verified",
        "Manually completed and verified by candidate in browser session"
    )
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update application status")
    
    return {"status": "ok", "message": "Marked application as Applied & Verified"}

@app.delete("/api/applications/{app_id}")
async def remove_application(app_id: str):
    success = delete_application(app_id)
    if not success:
        raise HTTPException(status_code=404, detail="Application not found or already deleted")
    return {"status": "ok", "message": "Application dismissed from manual queue"}

@app.get("/api/applications")
async def list_applications(platform: Optional[str] = None, status: Optional[str] = None, limit: int = 200):
    apps = get_applications(platform=platform, status=status, limit=limit)
    return apps

@app.get("/api/applications/export")
async def export_applications(format: str = "csv"):
    if format == "json":
        data = export_applications_json()
        return Response(content=data, media_type="application/json", headers={
            "Content-Disposition": "attachment; filename=job_applications.json"
        })
    else:
        csv_data = export_applications_csv()
        return Response(content=csv_data, media_type="text/csv", headers={
            "Content-Disposition": "attachment; filename=job_applications.csv"
        })

@app.get("/api/profile")
async def get_user_profile():
    return get_profile()

@app.post("/api/profile")
async def save_user_profile(payload: Dict[str, Any]):
    update_profile(payload)
    return {"status": "ok", "message": "Profile updated successfully"}

@app.post("/api/maintenance/cleanup")
async def trigger_cleanup(retention_days: Optional[int] = None):
    if retention_days is not None and retention_days < 1:
        raise HTTPException(status_code=400, detail="retention_days must be at least 1")
    stats = cleanup_outdated_data(retention_days) if retention_days else cleanup_outdated_data()
    return {"status": "ok", "cleanup_stats": stats}

@app.get("/api/resume/info")
async def get_resume_info():
    resume_file = Path(RESUME_PATH)
    exists = resume_file.exists()
    size = resume_file.stat().st_size if exists else 0
    text_len = len(get_resume_text()) if exists else 0
    return {
        "exists": exists,
        "path": str(resume_file),
        "file_size_bytes": size,
        "extracted_character_count": text_len
    }

@app.get("/api/events")
async def stream_events(request: Request):
    """Server-Sent Events (SSE) providing live metrics without client polling."""
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            metrics = manager.get_all_statuses()
            yield f"data: {json.dumps(metrics)}\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# Frontend Static Files
FRONTEND_DIR = BASE_DIR / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

@app.get("/")
async def root():
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return HTMLResponse("<h1>Job Application Automation Backend Running</h1>")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=APP_HOST, port=APP_PORT, reload=DEBUG)
