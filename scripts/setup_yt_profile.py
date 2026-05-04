"""
One-time setup: opens visible Chromium, navigates to YouTube.
Auto-detects login. If already logged in, extracts immediately.
If not, waits up to 5 minutes for you to log in, then auto-extracts.
Profile saved to %LOCALAPPDATA%\PlaywrightYT for future headless runs.
"""
import asyncio
import os
import sys
from pathlib import Path
from playwright.async_api import async_playwright

PROFILE = str(Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "SDMNH Profile")
TARGET = {".youtube.com", "youtube.com", ".google.com", "google.com",
          ".googleapis.com", ".ytimg.com", ".ggpht.com"}
LOGIN_TIMEOUT = 300  # seconds


async def is_logged_in(page) -> bool:
    try:
        # YouTube shows #avatar-btn when signed in
        el = await page.query_selector("#avatar-btn, ytd-topbar-menu-button-renderer[is-account-menu]")
        return el is not None
    except Exception:
        return False


async def wait_for_login(page) -> bool:
    print("Not logged in. Waiting up to 5 minutes for you to log in...")
    for _ in range(LOGIN_TIMEOUT):
        await asyncio.sleep(1)
        if await is_logged_in(page):
            return True
    return False


async def main(output: str):
    print(f"Profile: {PROFILE}")
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            PROFILE,
            channel="chrome",
            headless=False,
            args=["--no-first-run", "--no-default-browser-check"],
        )
        page = await ctx.new_page()
        print("Opening YouTube...")
        await page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)  # let page settle

        if await is_logged_in(page):
            print("Already logged in. Extracting cookies...")
        else:
            logged_in = await wait_for_login(page)
            if not logged_in:
                print("Timed out waiting for login.")
                await ctx.close()
                return 1
            print("Logged in! Extracting cookies...")

        cookies = await ctx.cookies(["https://www.youtube.com",
                                     "https://google.com",
                                     "https://accounts.google.com"])
        await ctx.close()

    written = 0
    with open(output, "w", newline="\n") as f:
        f.write("# Netscape HTTP Cookie File\n\n")
        for c in cookies:
            domain = c["domain"]
            if not any(domain == t or domain.endswith(t) for t in TARGET):
                continue
            if not c.get("value"):
                continue
            sub     = "TRUE" if domain.startswith(".") else "FALSE"
            secure  = "TRUE" if c.get("secure") else "FALSE"
            expires = int(c.get("expires", 0))
            f.write(f"{domain}\t{sub}\t{c['path']}\t{secure}\t{expires}\t"
                    f"{c['name']}\t{c['value']}\n")
            written += 1

    print(f"Wrote {written} cookies -> {output}")
    return 0 if written > 0 else 1


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else str(
        Path(os.environ["TEMP"]) / "youtube_cookies_fresh.txt")
    sys.exit(asyncio.run(main(out)))
