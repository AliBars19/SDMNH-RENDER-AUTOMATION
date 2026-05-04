"""
Daily cookie refresh: uses saved PlaywrightYT profile (set up via setup_yt_profile.py).
Launches Chromium headless, extracts YouTube/Google cookies, writes Netscape format.
No Chrome ABE issues — Playwright Chromium uses standard v10 encryption.
"""
import asyncio
import os
import sys
from pathlib import Path
from playwright.async_api import async_playwright

PROFILE = str(Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "SDMNH Profile")
TARGET = {".youtube.com", "youtube.com", ".google.com", "google.com",
          ".googleapis.com", ".ytimg.com", ".ggpht.com"}


async def main(output: str):
    if not Path(PROFILE).exists():
        print(f"ERROR: Profile not set up. Run setup_yt_profile.py first.")
        return 1

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            PROFILE,
            channel="chrome",
            headless=True,
            args=["--no-first-run", "--no-default-browser-check"],
        )
        page = await ctx.new_page()
        await page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=30000)
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
