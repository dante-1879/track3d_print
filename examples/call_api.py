"""Call the track3d build API from Python (standard library only).
    python call_api.py http://localhost:8765 [API_KEY]"""
import json, sys, urllib.request

base = sys.argv[1].rstrip("/")
headers = {"Content-Type": "application/json"}
if len(sys.argv) > 2:
    headers["X-API-Key"] = sys.argv[2]

body = {"demo": True,                      # or {"place": "Zermatt", "radius_km": 5} or {"gpx": open("route.gpx").read()}
        "options": {"path_width": 1.6},    # any setting from /api/schema, by its field name
        "format": "zip"}
req = urllib.request.Request(base + "/api/build", json.dumps(body).encode(), headers)
with urllib.request.urlopen(req, timeout=600) as r:
    meta = json.loads(r.headers["X-Track3D-Meta"])
    data = r.read()
open(meta["name"] + ".zip", "wb").write(data)
print(f"saved {meta['name']}.zip  ({len(data) // 1024} KB)  parts: {meta['counts']}")
