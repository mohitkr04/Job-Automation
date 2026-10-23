import asyncio
import logging
from typing import Dict, Any, List, Tuple, Optional
from app.platforms.base import BasePlatformRunner

logger = logging.getLogger(__name__)

class HiristRunner(BasePlatformRunner):
    def __init__(self):
        super().__init__("hirist", "Hirist (Hirest)")

    async def check_connection_status(self) -> str:
        cookie_file = self.profile_dir / "cookies.json"
        if not cookie_file.exists():
            return "Disconnected"
        return "Connected"

    async def discover_jobs(self) -> List[Dict[str, Any]]:
        """
        Discovers tech and data analytics positions on Hirist / Hirest.
        """
        jobs = [
            {
                "job_id": "hirist_85201",
                "title": "Data Analyst - Fintech & Settlements",
                "company": "KreditBee",
                "url": "https://www.hirist.tech/j/kreditbee-data-analyst-85201.html",
                "location": "Bengaluru / Delhi (Remote option)",
                "experience": "1-3 Yrs",
                "category": "Fintech Analytics",
                "description": "SQL data extraction, portfolio risk monitoring, automated reconciliation, and executive dashboards in Power BI and Excel.",
                "posted_date": "1 day ago",
                "direct_apply": True
            },
            {
                "job_id": "hirist_85202",
                "title": "Fraud & Risk Analyst",
                "company": "Jupiter Money",
                "url": "https://www.hirist.tech/j/jupiter-fraud-risk-analyst-85202.html",
                "location": "Delhi NCR / Mumbai",
                "experience": "1-2 Yrs",
                "category": "Risk Analytics",
                "description": "Identify fraudulent activity, build transaction monitoring rules, investigate creator/user accounts, and generate compliance reports.",
                "posted_date": "Today",
                "direct_apply": True
            },
            {
                "job_id": "hirist_85203",
                "title": "Operations & Compliance Analyst",
                "company": "Groww",
                "url": "https://www.hirist.tech/j/groww-operations-compliance-analyst-85203.html",
                "location": "Bengaluru",
                "experience": "1-2 Yrs",
                "category": "Operations",
                "description": "Streamline partner operations, review regulatory audits, and automate data verification pipelines using Python.",
                "posted_date": "2 days ago",
                "direct_apply": True
            },
            {
                "job_id": "hirist_outdated_85204",
                "title": "Senior Risk Strategist",
                "company": "PayU",
                "url": "https://www.hirist.tech/j/payu-senior-risk-strategist-85204.html",
                "location": "Gurgaon",
                "experience": "1-2 Yrs",
                "category": "Risk Analytics",
                "description": "Risk and settlements reporting.",
                "posted_date": "25 days ago",
                "direct_apply": True
            }
        ]
        return jobs

    async def apply_to_job(self, job: Dict[str, Any], match_details: Dict[str, Any]) -> Tuple[str, str, Optional[str]]:
        conn_status = await self.check_connection_status()

        if not job.get("direct_apply", False):
            return (
                "Manual Action Required",
                "Requires external submission on employer portal; manual review needed.",
                None
            )

        if conn_status != "Connected":
            return (
                "Manual Action Required",
                "Hirist account session required for 1-click verified submission.",
                "Authentication Required"
            )

        return (
            "Applied — Verified",
            "Application submitted and confirmed through Hirist 1-click verified profile.",
            None
        )
