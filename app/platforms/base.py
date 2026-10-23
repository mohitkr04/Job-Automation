import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from datetime import datetime, timezone

from app.database import (
    update_platform_metric,
    record_application,
    is_job_applied_or_recorded,
    cleanup_outdated_data
)
from app.matcher import evaluate_job_match, normalize_job_url, get_profile, get_resume_text
from app.config import (
    BROWSER_PROFILES_DIR,
    REQUEST_TIMEOUT_SECONDS,
    MAX_DISCOVERED_JOBS_PER_RUN,
    BATCH_SIZE,
    RESUME_PATH
)

logger = logging.getLogger(__name__)

class BasePlatformRunner(ABC):
    def __init__(self, platform_id: str, display_name: str):
        self.platform_id = platform_id
        self.display_name = display_name
        self.stop_event = asyncio.Event()
        self.is_running = False
        self._current_task: Optional[asyncio.Task] = None
        self.profile_dir = BROWSER_PROFILES_DIR / platform_id
        self.profile_dir.mkdir(exist_ok=True)

    def prepare_applicant_data(self) -> Dict[str, Any]:
        """Loads, parses, and validates candidate profile and resume file for auto-apply form filling."""
        profile = get_profile()
        resume_file = Path(RESUME_PATH)
        has_resume = resume_file.exists() and resume_file.stat().st_size > 0
        resume_text = get_resume_text() if has_resume else ""

        personal = profile.get("personal", {})
        education = profile.get("education", {})
        preferences = profile.get("preferences", {})

        return {
            "full_name": personal.get("full_name", "Mohit Kumar"),
            "email": personal.get("email", ""),
            "phone": personal.get("phone", ""),
            "current_location": personal.get("current_location", "Delhi"),
            "linkedin_url": personal.get("linkedin_url", ""),
            "github_url": personal.get("github_url", ""),
            "notice_period": preferences.get("notice_period_text", "15 days"),
            "expected_ctc": preferences.get("expected_ctc_lpa", "9-10 LPA"),
            "degree": education.get("degree", "B.Tech"),
            "college": education.get("college_name", ""),
            "college_marks": education.get("college_percentage", 82.0),
            "resume_path": str(resume_file) if has_resume else None,
            "resume_filename": resume_file.name if has_resume else None,
            "resume_size_bytes": resume_file.stat().st_size if has_resume else 0,
            "resume_verified": has_resume and len(resume_text) > 200,
            "experience_years": profile.get("experience", {}).get("total_years", 1.8),
            "skills": profile.get("skills", [])
        }

    def is_stopped(self) -> bool:
        return self.stop_event.is_set()

    async def start(self) -> bool:
        """Starts the platform workflow independently in the background."""
        if self.is_running:
            logger.info(f"{self.display_name} is already running.")
            return False

        self.stop_event.clear()
        self.is_running = True
        update_platform_metric(
            self.platform_id,
            automation_status="Searching",
            current_step_details="Initializing platform discovery...",
            reset_counts=True
        )

        self._current_task = asyncio.create_task(self._safe_run())
        return True

    async def stop(self):
        """Signals graceful stop without corrupting state or in-flight records."""
        if not self.is_running:
            return
        
        logger.info(f"Stop requested for {self.display_name}")
        self.stop_event.set()
        update_platform_metric(
            self.platform_id,
            automation_status="Paused",
            current_step_details="Stop signal received. Finishing in-flight step..."
        )

        if self._current_task:
            try:
                await asyncio.wait_for(asyncio.shield(self._current_task), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        self.is_running = False
        update_platform_metric(
            self.platform_id,
            automation_status="Idle",
            current_step_details="Platform stopped safely."
        )

    async def _safe_run(self):
        try:
            await self.run_workflow()
        except asyncio.CancelledError:
            logger.info(f"{self.display_name} workflow was cancelled.")
        except Exception as e:
            logger.error(f"Error in {self.display_name} runner: {str(e)}", exc_info=True)
            update_platform_metric(
                self.platform_id,
                automation_status="Failed",
                current_step_details=f"Runtime error: {str(e)[:100]}"
            )
        finally:
            self.is_running = False
            if self.stop_event.is_set():
                update_platform_metric(
                    self.platform_id,
                    automation_status="Idle",
                    current_step_details="Platform stopped."
                )
            else:
                update_platform_metric(
                    self.platform_id,
                    automation_status="Completed",
                    current_step_details="Run finished successfully."
                )

    @abstractmethod
    async def check_connection_status(self) -> str:
        """Returns: 'Disconnected' | 'Connecting' | 'Connected' | 'Authentication Required' | 'Error'"""
        pass

    @abstractmethod
    async def discover_jobs(self, batch_index: int = 0) -> List[Dict[str, Any]]:
        """Discovers a batch of jobs for target roles and locations. Returns empty list when exhausted."""
        pass

    async def run_workflow(self):
        """Unified reliable workflow:
        Cleanup -> Continuous Multi-Batch Search -> Extract -> Normalize -> Match -> Filter -> Deduplicate -> Validate -> Fill Form -> Submit -> Verify -> Save Result
        """
        # Step 0: Automatic Data Cleanup
        cleanup_stats = cleanup_outdated_data()
        if cleanup_stats.get("deleted_manual_records", 0) > 0 or cleanup_stats.get("deleted_outdated_posting", 0) > 0:
            logger.info(f"Cleaned up outdated records: {cleanup_stats}")

        # Step 1: Check connection state
        update_platform_metric(self.platform_id, automation_status="Searching", current_step_details="Checking platform status...")
        conn_status = await self.check_connection_status()
        update_platform_metric(self.platform_id, connection_status=conn_status)

        if self.is_stopped():
            return

        # Step 2: Continuous Search & Process Loop across batches
        total_discovered = 0
        batch_index = 0
        max_batches = max(1, MAX_DISCOVERED_JOBS_PER_RUN // max(1, BATCH_SIZE))

        while not self.is_stopped() and total_discovered < MAX_DISCOVERED_JOBS_PER_RUN and batch_index < max_batches:
            update_platform_metric(
                self.platform_id,
                automation_status="Searching",
                current_step_details=f"Scanning listings for target roles (batch {batch_index + 1})..."
            )
            jobs_batch = await self.discover_jobs(batch_index=batch_index)
            if not jobs_batch:
                logger.info(f"{self.display_name}: No further listings discovered for batch {batch_index + 1}. Ending discovery.")
                break

            update_platform_metric(self.platform_id, jobs_discovered_inc=len(jobs_batch))
            total_discovered += len(jobs_batch)

            # Process each job in this batch
            for job in jobs_batch:
                if self.is_stopped():
                    logger.info(f"{self.display_name} received stop signal; aborting loop.")
                    break

                await self._process_single_job(job)
                await asyncio.sleep(0.5) # respectful pace

            batch_index += 1
            if self.is_stopped():
                break
            await asyncio.sleep(1.0) # brief rest between discovery batches

    async def _process_single_job(self, job: Dict[str, Any]):
        title = job.get("title", "Untitled Role")
        company = job.get("company", "Unknown Employer")
        job_url = normalize_job_url(job.get("url", ""))
        job_id = job.get("job_id") or job_url
        location = job.get("location", "")
        exp_req = job.get("experience", "")
        category = job.get("category", "Analytics")
        description = job.get("description", "")
        disc_time = datetime.now(timezone.utc).isoformat()

        update_platform_metric(
            self.platform_id,
            automation_status="Evaluating",
            current_job_title=f"{title} at {company}",
            current_step_details="Checking duplicates and matching against resume..."
        )

        # 1. Deduplication check
        if is_job_applied_or_recorded(self.platform_id, job_id, job_url):
            logger.info(f"Skipping duplicate: {title} ({job_url})")
            update_platform_metric(
                self.platform_id,
                duplicates_skipped_inc=1,
                current_step_details=f"Skipping duplicate: {title[:30]}"
            )
            return

        # 2. Match evaluation against resume & posting date filter (<= 4 days)
        posted_date = job.get("posted_date")
        is_eligible, match_score, match_details = evaluate_job_match(
            title=title,
            description=description,
            location=location,
            experience_text=exp_req,
            posted_date_text=posted_date
        )

        if not is_eligible:
            reason = match_details.get("rejection_reason", "Criteria threshold not met")
            logger.info(f"Job not eligible/outdated ({match_score}%): {title} — {reason}")
            update_platform_metric(
                self.platform_id,
                current_step_details=f"Skipping: {reason[:45]}"
            )
            return

        # Mark as Eligible
        update_platform_metric(
            self.platform_id,
            eligible_jobs_inc=1,
            automation_status="Ready to Apply",
            current_step_details=f"Eligible ({match_score}% match). Preparing application..."
        )

        if self.is_stopped():
            return

        # 3. Application Execution & Verification
        update_platform_metric(
            self.platform_id,
            automation_status="Applying",
            current_step_details="Filling validated profile details..."
        )

        app_outcome, verif_msg, fail_reason = await self.apply_to_job(job, match_details)

        # Update metrics based on outcome
        if app_outcome == "Applied — Verified":
            update_platform_metric(self.platform_id, applications_submitted_inc=1)
        elif app_outcome == "Failed":
            update_platform_metric(self.platform_id, failed_applications_inc=1)

        # Record application ledger entry with posted_date and posting_age_days
        record_application(
            platform=self.platform_id,
            job_id=job_id,
            job_title=title,
            employer_name=company,
            job_url=job_url,
            job_category=category,
            job_location=location,
            experience_req=exp_req,
            match_score=match_score,
            status=app_outcome,
            discovery_timestamp=disc_time,
            application_timestamp=datetime.now(timezone.utc).isoformat() if "Applied" in app_outcome else None,
            verification_result=verif_msg,
            failure_reason=fail_reason,
            job_description=description,
            posted_date=posted_date,
            posting_age_days=match_details.get("posting_age_days")
        )

    @abstractmethod
    async def apply_to_job(self, job: Dict[str, Any], match_details: Dict[str, Any]) -> Tuple[str, str, Optional[str]]:
        """
        Submits or prepares application according to platform rules.
        Returns (status, verification_result, failure_reason).
        Possible statuses:
        - 'Applied — Verified'
        - 'Submission Unconfirmed'
        - 'Manual Action Required'
        - 'Failed'
        """
        pass
