"""Local web UI: stdlib HTTP server, no extra dependencies. Binds to localhost only."""
import io
import json
import urllib.parse
import urllib.request
import mimetypes
import re
import threading
import traceback
import uuid
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import elevation, geocode, gpx, pipeline
from ..config import Settings, schema

HERE = Path(__file__).parent
JOBS = {}
WORKDIR = Path("track3d_output")
SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")


ROUTERS = {   # profile -> upstream base URLs tried in order (OpenStreetMap-based routing engines)
    "foot": ["https://routing.openstreetmap.de/routed-foot/route/v1/driving/"],
    "bike": ["https://routing.openstreetmap.de/routed-bike/route/v1/driving/"],
    "car": ["https://routing.openstreetmap.de/routed-car/route/v1/driving/",
            "https://router.project-osrm.org/route/v1/driving/"],
}
COORDS = re.compile(r"^-?\d{1,3}(\.\d+)?,-?\d{1,3}(\.\d+)?(;-?\d{1,3}(\.\d+)?,-?\d{1,3}(\.\d+)?){1,24}$")


def fetch_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "track3d/2.0 (route planner)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def route(profile, coords):
    """Snap a route to roads/paths. Only fixed upstream hosts and validated coordinates are used."""
    if profile not in ROUTERS or not COORDS.match(coords):
        raise ValueError("bad route request")
    last = None
    for base in ROUTERS[profile]:
        try:
            d = fetch_json(f"{base}{coords}?overview=full&geometries=geojson&steps=false")
            if d.get("code") == "Ok" and d.get("routes"):
                r = d["routes"][0]
                return dict(coordinates=r["geometry"]["coordinates"], distance=r.get("distance", 0))
            last = d.get("message") or d.get("code") or "no route"
        except Exception as e:
            last = str(e)
    raise RuntimeError(last or "routing failed")


class Job:
    def __init__(self):
        self.id = uuid.uuid4().hex[:10]
        self.status, self.lines, self.result, self.error = "running", [], None, None

    def log(self, msg):
        self.lines.extend(str(msg).split("\n"))

    def view(self, since=0):
        return dict(id=self.id, status=self.status, log=self.lines[since:], cursor=len(self.lines),
                    result=self.result, error=self.error)


def run_job(job, body):
    try:
        cfg = Settings.from_dict(body.get("settings"))
        cfg.output.outdir = str(WORKDIR / job.id)
        cfg.output.export_stl = True          # the preview needs the per-part STL files
        route = gpx.parse_gpx_text(body["gpx"]) if body.get("gpx") else None
        hint = Path(body.get("filename") or "").stem or None
        job.result = pipeline.run(cfg, route=route, log=job.log, name_hint=hint).to_dict()
        job.status = "done"
    except Exception as e:  # shown to the user in the UI
        job.error = str(e) or e.__class__.__name__
        job.log(traceback.format_exc())
        job.status = "error"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, data, ctype="application/json", extra=None):
        if not isinstance(data, (bytes, bytearray)):
            data = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        try:
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # client went away; nothing to send to

    def do_GET(self):
        p = self.path.split("?")[0]
        q = self.path.partition("?")[2]
        if p == "/":
            return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if p == "/planner":
            return self._send(200, (HERE / "planner.html").read_bytes(), "text/html; charset=utf-8")
        if p == "/api/route":
            qs = urllib.parse.parse_qs(q)
            try:
                return self._send(200, route(qs.get("profile", ["foot"])[0], qs.get("c", [""])[0]))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:
                return self._send(502, {"error": str(e)})
        if p == "/api/schema":
            return self._send(200, schema())
        if p.startswith("/api/jobs/"):
            job = JOBS.get(p.split("/")[3])
            since = int(q.split("=")[1]) if q.startswith("since=") else 0
            return self._send(200, job.view(since)) if job else self._send(404, {"error": "unknown job"})
        parts = p.strip("/").split("/")
        if parts[0] == "files" and len(parts) == 3 and SAFE.match(parts[1]) and SAFE.match(parts[2]):
            f = WORKDIR / parts[1] / parts[2]
            if f.is_file():
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                return self._send(200, f.read_bytes(), ctype,
                                  {"Content-Disposition": f'attachment; filename="{f.name}"'}
                                  if "dl" in q else None)
        if parts[0] == "zip" and len(parts) == 2 and SAFE.match(parts[1]) and (WORKDIR / parts[1]).is_dir():
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted((WORKDIR / parts[1]).iterdir()):
                    z.write(f, f.name)
            return self._send(200, buf.getvalue(), "application/zip",
                              {"Content-Disposition": f'attachment; filename="track3d_{parts[1]}.zip"'})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad JSON"})
        if self.path == "/api/geocode":
            try:
                return self._send(200, geocode.search(body.get("q", "")))
            except Exception as e:
                return self._send(502, {"error": f"Place search failed: {e}"})
        if self.path == "/api/elevation":
            try:
                pts = body.get("points") or []
                if not 2 <= len(pts) <= 6000:
                    raise ValueError("send between 2 and 6000 points")
                el = elevation.at_points([p[0] for p in pts], [p[1] for p in pts])
                return self._send(200, {"elev": [round(float(v), 1) for v in el]})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:
                return self._send(502, {"error": f"elevation tiles unavailable ({e})"})
        if self.path == "/api/generate":
            job = Job()
            JOBS[job.id] = job
            threading.Thread(target=run_job, args=(job, body), daemon=True).start()
            return self._send(202, {"id": job.id})
        self._send(404, {"error": "not found"})


def serve(host, port, workdir, open_browser=True):
    global WORKDIR
    WORKDIR = Path(workdir).resolve()
    WORKDIR.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"track3d UI running at {url}  (Ctrl+C to stop)\nFiles are written to {WORKDIR}")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0
