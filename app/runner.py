import asyncio
import logging
from typing import Dict, Any, Optional
from app.platforms.naukri import NaukriRunner
from app.platforms.linkedin import LinkedInRunner
from app.platforms.indeed import IndeedRunner
from app.platforms.hirist import HiristRunner
from app.database import get_all_platform_metrics, update_platform_metric

logger = logging.getLogger(__name__)

class PlatformManager:
    _instance: Optional["PlatformManager"] = None

    def __init__(self):
        self.runners = {
            "naukri": NaukriRunner(),
            "linkedin": LinkedInRunner(),
            "indeed": IndeedRunner(),
            "hirist": HiristRunner()
        }

    @classmethod
    def get_instance(cls) -> "PlatformManager":
        if cls._instance is None:
            cls._instance = PlatformManager()
        return cls._instance

    async def start_platform(self, platform_id: str):
        if platform_id not in self.runners:
            raise ValueError(f"Unknown platform: {platform_id}")
        runner = self.runners[platform_id]
        await runner.start()

    async def stop_platform(self, platform_id: str):
        if platform_id not in self.runners:
            raise ValueError(f"Unknown platform: {platform_id}")
        runner = self.runners[platform_id]
        await runner.stop()

    async def check_platform_connection(self, platform_id: str) -> str:
        if platform_id not in self.runners:
            raise ValueError(f"Unknown platform: {platform_id}")
        runner = self.runners[platform_id]
        status = await runner.check_connection_status()
        update_platform_metric(platform_id, connection_status=status)
        return status

    def get_all_statuses(self) -> Dict[str, Any]:
        metrics = get_all_platform_metrics()
        # Merge running state
        for pid, runner in self.runners.items():
            if pid in metrics:
                metrics[pid]["is_active"] = runner.is_running
        return metrics

manager = PlatformManager.get_instance()
