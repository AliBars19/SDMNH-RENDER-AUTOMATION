"""
Extract YouTube cookies from Chrome via Chrome DevTools Protocol (CDP).
Requires Chrome running with --remote-debugging-port=9222 --remote-allow-origins=*
Writes Netscape HTTP Cookie file compatible with yt-dlp.
"""
import json
import sys
import os
import time
import urllib.request
import websocket


def get_ws_url(port=9222):
    url = f"http://localhost:{port}/json"
    with urllib.request.urlopen(url, timeout=5) as r:
        targets = json.loads(r.read())
    # Prefer an existing page target, else use first available
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise RuntimeError("No page target found in Chrome debugger")
    return pages[0]["webSocketDebuggerUrl"]


def cdp_call(ws, method, params=None, msg_id=1):
    cmd = {"id": msg_id, "method": method, "params": params or {}}
    ws.send(json.dumps(cmd))
    while True:
        resp = json.loads(ws.recv())
        if resp.get("id") == msg_id:
            return resp.get("result", {})


def get_all_cookies(ws_url, navigate: bool = False):
    ws = websocket.create_connection(ws_url, timeout=15)
    try:
        if navigate:
            cdp_call(ws, "Page.navigate", {"url": "https://www.youtube.com"}, msg_id=1)
            time.sleep(3)
            ws.settimeout(1)
            try:
                while True:
                    ws.recv()
            except Exception:
                pass
            ws.settimeout(15)
        # Read cookie store directly — populated from Cookies file at Chrome startup
        result = cdp_call(ws, "Network.getAllCookies", {}, msg_id=2)
        cookies = result.get("cookies", [])
        if not cookies:
            result = cdp_call(ws, "Storage.getCookies", {}, msg_id=3)
            cookies = result.get("cookies", [])
        return cookies
    finally:
        ws.close()


def write_netscape(cookies, output, domains=("youtube", "google")):
    filtered = [c for c in cookies if any(d in c.get("domain", "") for d in domains)]
    with open(output, "w", encoding="utf-8") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for c in filtered:
            domain = c["domain"]
            if not domain.startswith("."):
                domain = "." + domain
            path = c.get("path", "/")
            secure = "TRUE" if c.get("secure") else "FALSE"
            expires = int(c.get("expires", 0))
            if expires < 0:
                expires = 0
            name = c["name"]
            value = c.get("value", "")
            f.write(f"{domain}\tTRUE\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")
    return filtered


if __name__ == "__main__":
    output = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.environ.get("TEMP", "/tmp"), "yt_fresh.txt"
    )

    ws_url = get_ws_url()
    print("Connected to Chrome debugger — navigating to YouTube...")
    cookies = get_all_cookies(ws_url)
    print(f"Total cookies in browser: {len(cookies)}")
    written = write_netscape(cookies, output)
    print(f"YouTube/Google cookies: {len(written)}")
    if not written:
        print("ERROR: No YouTube/Google cookies found — are you logged in to YouTube?")
        sys.exit(1)
    print(f"Written to {output} ({os.path.getsize(output)} bytes)")
