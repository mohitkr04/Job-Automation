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
    export_applications_json
)
from app.matcher import get_profile, update_profile, get_resume_text
from app.runner import manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("app.main")

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Local database initialized.")
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
        await manager.start_platform(platform_id)
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
