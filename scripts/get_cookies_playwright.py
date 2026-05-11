"""
Daily cookie refresh: extracts YouTube cookies from Chrome via yt-dlp.
No profile setup required — reads from existing logged-in Chrome session.
"""
import os
import subprocess
import sys
from pathlib import Path


def extract(output: str) -> int:
    result = subprocess.run(
        [
            "yt-dlp",
            "--cookies-from-browser", "chrome",
            "--cookies", output,
            "--skip-download",
            "https://www.youtube.com/",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stderr.strip() or result.stdout.strip())
        return 1

    if not Path(output).exists():
        print(f"ERROR: output file not created: {output}")
        return 1

    count = sum(
        1 for line in Path(output).read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    print(f"Wrote {count} cookies -> {output}")
    return 0 if count > 0 else 1


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else str(
        Path(os.environ["TEMP"]) / "youtube_cookies_fresh.txt"
    )
    sys.exit(extract(out))
