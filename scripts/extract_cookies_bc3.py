"""
Extract YouTube cookies from Chrome using browser-cookie3.
Writes Netscape cookie file format compatible with yt-dlp.
"""
import sys
import os
import browser_cookie3

output = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TEMP", "/tmp"), "yt_fresh.txt")

jar = browser_cookie3.chrome(domain_name='.youtube.com')
cookies = list(jar)
print(f"Got {len(cookies)} YouTube cookies from Chrome")

if not cookies:
    print("ERROR: No YouTube cookies found — are you logged in to YouTube in Chrome?")
    sys.exit(1)

with open(output, 'w', encoding='utf-8') as f:
    f.write("# Netscape HTTP Cookie File\n")
    for c in cookies:
        domain = c.domain if c.domain.startswith('.') else '.' + c.domain
        secure = "TRUE" if c.secure else "FALSE"
        expires = int(c.expires) if c.expires else 0
        f.write(f"{domain}\tTRUE\t{c.path}\t{secure}\t{expires}\t{c.name}\t{c.value}\n")

size = os.path.getsize(output)
print(f"Written {len(cookies)} cookies to {output} ({size} bytes)")
