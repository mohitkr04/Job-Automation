import sqlite3
import json
import csv
import io
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Any
from app.config import DB_PATH, LOGS_DIR, CLEANUP_RETENTION_DAYS

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Applications Ledger Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS applications (
        id TEXT PRIMARY KEY,
        platform TEXT NOT NULL,
        job_id TEXT,
        job_title TEXT NOT NULL,
        employer_name TEXT NOT NULL,
        job_url TEXT NOT NULL,
        job_category TEXT,
        job_location TEXT,
        experience_req TEXT,
        match_score REAL NOT NULL,
        discovery_timestamp TEXT NOT NULL,
        application_timestamp TEXT,
        status TEXT NOT NULL,
        verification_result TEXT,
        failure_reason TEXT,
        job_description TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(platform, job_id)
    )
    """)

    # Indexes for fast lookup, deduplication, and cleanup optimization
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_apps_url ON applications(job_url)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_apps_platform ON applications(platform)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_apps_status ON applications(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_apps_status_created ON applications(status, created_at)")

    # Purge any legacy 'Skipped' records to satisfy Requirement 2 (No unnecessary storage)
    cursor.execute("DELETE FROM applications WHERE status LIKE 'Skipped%'")

    # 2. Platform Real-time Metrics Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS platform_metrics (
        platform TEXT PRIMARY KEY,
        connection_status TEXT NOT NULL DEFAULT 'Disconnected',
        last_run_time TEXT,
        automation_status TEXT NOT NULL DEFAULT 'Idle',
        jobs_discovered INTEGER DEFAULT 0,
        eligible_jobs INTEGER DEFAULT 0,
        applications_submitted INTEGER DEFAULT 0,
        failed_applications INTEGER DEFAULT 0,
        duplicates_skipped INTEGER DEFAULT 0,
        current_job_title TEXT,
        current_step_details TEXT,
        updated_at TEXT NOT NULL
    )
    """)

    # Populate default platform metrics rows if missing
    platforms = ["naukri", "linkedin", "indeed", "hirist"]
    now = datetime.now(timezone.utc).isoformat()
    for p in platforms:
        cursor.execute("""
        INSERT OR IGNORE INTO platform_metrics (
            platform, connection_status, automation_status, jobs_discovered,
            eligible_jobs, applications_submitted, failed_applications,
            duplicates_skipped, updated_at
        ) VALUES (?, 'Disconnected', 'Idle', 0, 0, 0, 0, 0, ?)
        """, (p, now))

    conn.commit()
    conn.close()

def record_application(
    platform: str,
    job_id: Optional[str],
    job_title: str,
    employer_name: str,
    job_url: str,
    job_category: str,
    job_location: str,
    experience_req: str,
    match_score: float,
    status: str,
    discovery_timestamp: Optional[str] = None,
    application_timestamp: Optional[str] = None,
    verification_result: Optional[str] = None,
    failure_reason: Optional[str] = None,
    job_description: Optional[str] = None,
) -> str:
    conn = get_db_connection()
    cursor = conn.cursor()
    
    app_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    disc_time = discovery_timestamp or now

    # Store job description only if applied/verified or manual action needed
    desc_to_store = job_description if status in ("Applied — Verified", "Manual Action Required", "Submission Unconfirmed") else None

    try:
        cursor.execute("""
        INSERT INTO applications (
            id, platform, job_id, job_title, employer_name, job_url, job_category,
            job_location, experience_req, match_score, discovery_timestamp,
            application_timestamp, status, verification_result, failure_reason,
            job_description, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(platform, job_id) DO UPDATE SET
            status=excluded.status,
            verification_result=excluded.verification_result,
            failure_reason=excluded.failure_reason,
            application_timestamp=excluded.application_timestamp
        """, (
            app_id, platform, job_id or app_id, job_title, employer_name, job_url,
            job_category, job_location, experience_req, round(match_score, 1),
            disc_time, application_timestamp, status, verification_result,
            failure_reason, desc_to_store, now
        ))
        conn.commit()
    finally:
        conn.close()

    return app_id

def is_job_applied_or_recorded(platform: str, job_id: Optional[str], normalized_url: str) -> bool:
    """Check if the job was already applied to or recorded, to prevent duplicate applications."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if job_id:
            cursor.execute("SELECT id, status FROM applications WHERE platform = ? AND job_id = ?", (platform, job_id))
            row = cursor.fetchone()
            if row:
                return True
        
        cursor.execute("SELECT id, status FROM applications WHERE job_url = ?", (normalized_url,))
        row = cursor.fetchone()
        return row is not None
    finally:
        conn.close()

def get_application_by_id(app_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM applications WHERE id = ?", (app_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def update_application_status(
    app_id: str,
    new_status: str,
    verification_result: Optional[str] = None
) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    try:
        cursor.execute("""
        UPDATE applications SET
            status = ?,
            verification_result = ?,
            application_timestamp = ?
        WHERE id = ?
        """, (
            new_status,
            verification_result or f"Status updated to {new_status} by candidate",
            now if "Applied" in new_status else None,
            app_id
        ))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def delete_application(app_id: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def get_applications(
    platform: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100
) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM applications"
    params = []
    conditions = []
    
    if platform:
        conditions.append("platform = ?")
        params.append(platform)
    if status:
        conditions.append("status = ?")
        params.append(status)
        
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
        
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_platform_metrics() -> Dict[str, Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM platform_metrics")
    rows = cursor.fetchall()
    conn.close()
    return {r["platform"]: dict(r) for r in rows}

def update_platform_metric(
    platform: str,
    connection_status: Optional[str] = None,
    automation_status: Optional[str] = None,
    jobs_discovered_inc: int = 0,
    eligible_jobs_inc: int = 0,
    applications_submitted_inc: int = 0,
    failed_applications_inc: int = 0,
    duplicates_skipped_inc: int = 0,
    current_job_title: Optional[str] = None,
    current_step_details: Optional[str] = None,
    reset_counts: bool = False
):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    
    if reset_counts:
        cursor.execute("""
        UPDATE platform_metrics SET
            jobs_discovered = 0,
            eligible_jobs = 0,
            applications_submitted = 0,
            failed_applications = 0,
            duplicates_skipped = 0,
            last_run_time = ?,
            updated_at = ?
        WHERE platform = ?
        """, (now, now, platform))

    updates = ["updated_at = ?"]
    params = [now]

    if connection_status is not None:
        updates.append("connection_status = ?")
        params.append(connection_status)
    if automation_status is not None:
        updates.append("automation_status = ?")
        params.append(automation_status)
    if jobs_discovered_inc > 0:
        updates.append("jobs_discovered = jobs_discovered + ?")
        params.append(jobs_discovered_inc)
    if eligible_jobs_inc > 0:
        updates.append("eligible_jobs = eligible_jobs + ?")
        params.append(eligible_jobs_inc)
    if applications_submitted_inc > 0:
        updates.append("applications_submitted = applications_submitted + ?")
        params.append(applications_submitted_inc)
    if failed_applications_inc > 0:
        updates.append("failed_applications = failed_applications + ?")
        params.append(failed_applications_inc)
    if duplicates_skipped_inc > 0:
        updates.append("duplicates_skipped = duplicates_skipped + ?")
        params.append(duplicates_skipped_inc)
    if current_job_title is not None:
        updates.append("current_job_title = ?")
        params.append(current_job_title)
    if current_step_details is not None:
        updates.append("current_step_details = ?")
        params.append(current_step_details)

    params.append(platform)
    sql = f"UPDATE platform_metrics SET {', '.join(updates)} WHERE platform = ?"
    cursor.execute(sql, params)
    conn.commit()
    conn.close()

def export_applications_csv() -> str:
    apps = get_applications(limit=5000)
    if not apps:
        return "id,platform,job_id,job_title,employer_name,job_url,status,match_score,created_at\n"
    
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "id", "platform", "job_id", "job_title", "employer_name",
        "job_url", "job_category", "job_location", "experience_req",
        "match_score", "status", "verification_result", "failure_reason",
        "discovery_timestamp", "application_timestamp", "created_at"
    ], extrasaction="ignore")
    writer.writeheader()
    for row in apps:
        writer.writerow(row)
    return output.getvalue()

def export_applications_json() -> str:
    apps = get_applications(limit=5000)
    return json.dumps(apps, indent=2)

def cleanup_outdated_data(retention_days: int = CLEANUP_RETENTION_DAYS) -> Dict[str, Any]:
    """
    Automatically deletes temporary job records, outdated manual-action records,
    and unnecessary application data older than retention_days (default 6 days).

    CRITICAL (Requirement 5): Preserves 'Applied — Verified' and 'Submission Unconfirmed'
    records to maintain essential application history and duplicate prevention.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=retention_days)
    cutoff_iso = cutoff_dt.isoformat()

    # 1. Delete outdated manual action records older than retention cutoff (5-6 days)
    cursor.execute("""
        DELETE FROM applications
        WHERE status = 'Manual Action Required'
          AND created_at < ?
    """, (cutoff_iso,))
    deleted_manual = cursor.rowcount

    # 2. Delete any transient failure records older than retention cutoff
    cursor.execute("""
        DELETE FROM applications
        WHERE status = 'Failed'
          AND created_at < ?
    """, (cutoff_iso,))
    deleted_failed = cursor.rowcount

    # 3. Purge any legacy 'Skipped' records (Requirement 2)
    cursor.execute("""
        DELETE FROM applications
        WHERE status LIKE 'Skipped%'
    """)
    deleted_skips = cursor.rowcount

    conn.commit()

    # 4. Vacuum SQLite database to reclaim disk space
    try:
        cursor.execute("VACUUM")
    except Exception:
        pass
    conn.close()

    # 5. Clean up outdated log files in LOGS_DIR
    cleaned_logs = 0
    logs_path = Path(LOGS_DIR)
    if logs_path.exists():
        for log_file in logs_path.glob("*.log"):
            try:
                mtime = datetime.fromtimestamp(log_file.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff_dt:
                    log_file.unlink()
                    cleaned_logs += 1
            except Exception:
                pass

    return {
        "deleted_manual_records": deleted_manual,
        "deleted_failed_records": deleted_failed,
        "purged_legacy_skips": deleted_skips,
        "cleaned_log_files": cleaned_logs,
        "cutoff_timestamp": cutoff_iso,
        "retention_days": retention_days
    }
