"""
Automated cookie extraction using a persistent Playwright Chromium profile.
No Chrome ABE issues — Playwright manages its own Chromium with its own encryption.
Requires one-time setup via setup_playwright_profile.py (log in to YouTube once).

Usage:
    python extract_cookies_playwright.py [output_path]
"""
import json
import sys
import os
from pathlib import Path

PROFILE_DIR = Path(__file__).parent / "yt_browser_profile"


def extract(output: str) -> int:
    if not PROFILE_DIR.exists():
        print(f"ERROR: Profile not set up. Run setup_playwright_profile.py first.")
        return 1

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=True,
            args=["--no-sandbox"],
        )
        page = ctx.new_page()
        # Navigate to YouTube to refresh session cookies
        page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        cookies = ctx.cookies(["https://www.youtube.com", "https://accounts.google.com"])
        ctx.close()

    if not cookies:
        print("ERROR: No cookies found. Re-run setup_playwright_profile.py to re-login.")
        return 1

    auth_names = {"SID", "SSID", "HSID", "APISID", "SAPISID",
                  "__Secure-3PSID", "__Secure-3PAPISID", "LOGIN_INFO"}
    auth = [c for c in cookies if c["name"] in auth_names]
    if not auth:
        print("ERROR: No auth cookies. Re-run setup_playwright_profile.py to re-login.")
        return 1

    with open(output, "w", encoding="utf-8") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for c in cookies:
            domain = c["domain"]
            if not domain.startswith("."):
                domain = "." + domain
            secure = "TRUE" if c.get("secure") else "FALSE"
            expires = int(c.get("expires") or 0)
            if expires < 0:
                expires = 0
            f.write(f"{domain}\tTRUE\t{c['path']}\t{secure}\t{expires}\t{c['name']}\t{c['value']}\n")

    print(f"Wrote {len(cookies)} cookies ({len(auth)} auth) to {output}")
    return 0


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.environ.get("TEMP", "/tmp"), "youtube_cookies_fresh.txt"
    )
    sys.exit(extract(out))
