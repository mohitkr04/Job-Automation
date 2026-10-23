import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from app.database import (
    update_platform_metric,
    record_application,
    is_job_applied_or_recorded,
    cleanup_outdated_data
)
from app.matcher import evaluate_job_match, normalize_job_url
from app.config import BROWSER_PROFILES_DIR, REQUEST_TIMEOUT_SECONDS

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

    def is_stopped(self) -> bool:
        return self.stop_event.is_set()

    async def start(self):
        """Starts the platform workflow independently in the background."""
        if self.is_running:
            logger.info(f"{self.display_name} is already running.")
            return

        self.stop_event.clear()
        self.is_running = True
        update_platform_metric(
            self.platform_id,
            automation_status="Searching",
            current_step_details="Initializing platform discovery...",
            reset_counts=True
        )

        self._current_task = asyncio.create_task(self._safe_run())

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
            if not self.stop_event.is_set():
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
    async def discover_jobs(self) -> List[Dict[str, Any]]:
        """Discovers jobs for target roles and locations."""
        pass

    async def run_workflow(self):
        """Unified reliable workflow:
        Cleanup -> Search -> Extract -> Normalize -> Match -> Filter -> Deduplicate -> Validate -> Fill Form -> Submit -> Verify -> Save Result
        """
        # Step 0: Automatic Data Cleanup (Requirement 4 & 5)
        cleanup_stats = cleanup_outdated_data()
        if cleanup_stats.get("deleted_manual_records", 0) > 0:
            logger.info(f"Cleaned up {cleanup_stats['deleted_manual_records']} outdated manual-action records.")

        # Step 1: Check connection state
        update_platform_metric(self.platform_id, automation_status="Searching", current_step_details="Checking platform status...")
        conn_status = await self.check_connection_status()
        update_platform_metric(self.platform_id, connection_status=conn_status)

        if self.is_stopped():
            return

        # Step 2: Search & Extract jobs
        update_platform_metric(self.platform_id, automation_status="Searching", current_step_details="Scanning listings for target roles...")
        discovered_jobs = await self.discover_jobs()
        update_platform_metric(self.platform_id, jobs_discovered_inc=len(discovered_jobs))

        if self.is_stopped() or not discovered_jobs:
            return

        # Step 3: Process each job
        for job in discovered_jobs:
            if self.is_stopped():
                logger.info(f"{self.display_name} received stop signal; aborting loop.")
                break

            await self._process_single_job(job)
            await asyncio.sleep(1.0) # respectful pace

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

        # 1. Deduplication check (Requirement 2 & 5)
        if is_job_applied_or_recorded(self.platform_id, job_id, job_url):
            logger.info(f"Skipping duplicate: {title} ({job_url})")
            update_platform_metric(
                self.platform_id,
                duplicates_skipped_inc=1,
                current_step_details=f"Skipping duplicate: {title[:30]}"
            )
            # Do NOT store duplicate skip rows in DB (Requirement 2)
            return

        # 2. Match evaluation against resume & posting date filter (Requirements 1 & 2)
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
            # Do NOT store ineligible, outdated, or unverified jobs in DB (Requirement 2)
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

        # Record application ledger entry
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
            job_description=description
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
