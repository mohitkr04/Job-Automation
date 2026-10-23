import os
import subprocess
import webbrowser
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from app.config import BROWSER_PROFILES_DIR

logger = logging.getLogger(__name__)

PLATFORM_LOGIN_URLS = {
    "naukri": "https://www.naukri.com/nlogin/login",
    "linkedin": "https://www.linkedin.com/login",
    "indeed": "https://secure.indeed.com/account/login",
    "hirist": "https://www.hirist.tech/login"
}

def get_browser_executable() -> Optional[str]:
    """Locates Google Chrome or Microsoft Edge executable on the system."""
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None

def launch_headed_browser(platform: str, target_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Launches a real headed browser window using the platform's persistent profile directory.
    Sessions, cookies, and logins remain stored in data/browser_profiles/<platform>.
    """
    profile_dir = BROWSER_PROFILES_DIR / platform.lower()
    profile_dir.mkdir(parents=True, exist_ok=True)

    url_to_open = target_url or PLATFORM_LOGIN_URLS.get(platform.lower(), "https://www.google.com")
    browser_exe = get_browser_executable()

    if browser_exe:
        try:
            cmd = [
                browser_exe,
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                url_to_open
            ]
            subprocess.Popen(cmd)
            logger.info(f"Launched headed browser for {platform} at {url_to_open}")
            return {
                "status": "ok",
                "browser": "standalone",
                "executable": browser_exe,
                "profile_dir": str(profile_dir),
                "url": url_to_open
            }
        except Exception as e:
            logger.warning(f"Failed to launch standalone browser {browser_exe}: {e}")

    # Fallback to default browser
    webbrowser.open(url_to_open)
    return {
        "status": "ok",
        "browser": "system_default",
        "url": url_to_open
    }
