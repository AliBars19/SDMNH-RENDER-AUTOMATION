"""Check if YouTube is logged in via CDP and print all auth-relevant cookies."""
import json, sys, time, urllib.request
import websocket

def get_ws_url(port=9222):
    with urllib.request.urlopen(f"http://localhost:{port}/json", timeout=5) as r:
        targets = json.loads(r.read())
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise RuntimeError("No page target")
    return pages[0]["webSocketDebuggerUrl"]

ws_url = get_ws_url()
ws = websocket.create_connection(ws_url, timeout=15)

# Get ALL cookies in the cookie store
ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies", "params": {}}))
while True:
    resp = json.loads(ws.recv())
    if resp.get("id") == 1:
        cookies = resp.get("result", {}).get("cookies", [])
        break

ws.close()

print(f"Total cookies in Chrome: {len(cookies)}")
yt = [c for c in cookies if "youtube" in c.get("domain","") or "google" in c.get("domain","")]
print(f"YouTube/Google cookies: {len(yt)}")

auth_keys = {"SID", "SSID", "HSID", "APISID", "SAPISID", "__Secure-3PSID", "__Secure-3PAPISID", "LOGIN_INFO"}
auth = [c for c in yt if c["name"] in auth_keys]
print(f"Auth cookies found: {len(auth)}")
for c in auth:
    print(f"  {c['name']} = {c['value'][:30]}...")
if not auth:
    print("NOT LOGGED IN — open YouTube in Chrome and log in to your Google account")
