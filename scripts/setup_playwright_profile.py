"""
One-time setup: open a persistent Playwright Chromium browser, log in to YouTube,
then close it. The session is saved to scripts/yt_browser_profile/.
After this, extract_cookies_playwright.py can refresh cookies fully automatically.
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

PROFILE_DIR = Path(__file__).parent / "yt_browser_profile"
PROFILE_DIR.mkdir(exist_ok=True)

print(f"Opening browser with persistent profile at: {PROFILE_DIR}")
print("Log in to YouTube, then close the browser window.")
print()

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        args=["--no-sandbox"],
    )
    page = ctx.new_page()
    page.goto("https://www.youtube.com")
    print("Browser opened. Log in to YouTube — auto-saves when logged in (3 min timeout).")
    # Poll until the signed-in avatar button appears (confirms login)
    try:
        page.wait_for_selector("ytd-masthead #avatar-btn", timeout=180000)
        print("Login detected! Saving profile...")
        page.wait_for_timeout(2000)  # let session cookies settle
    except Exception:
        print("Timeout or no login detected — saving whatever session exists.")
    ctx.close()

print("Profile saved. You can now run extract_cookies_playwright.py automatically.")
