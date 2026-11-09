"""Local web UI: stdlib HTTP server, no extra dependencies. Binds to localhost only."""
import hmac
import io
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import mimetypes
import re
import threading
import traceback
import uuid
import webbrowser
import zipfile
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

from .. import elevation, geocode, gpx, pipeline
from ..config import Settings, schema
from .. import export
from .. import scene as scene_mod
from . import api

HERE = Path(__file__).parent
JOBS = {}
JOB_KEYS = {}
JOBS_LOCK = threading.Lock()
WORKDIR = Path("track3d_output")
SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")


CONFIG = dict(cors=[], api_key=[], queue_wait=30.0, ttl_hours=24.0, frame_ancestors="", max_body=40 * 1048576)
BUILD_SLOTS = threading.BoundedSemaphore(2)
VENDOR = {"three.min.js", "OrbitControls.js", "STLLoader.js"}
CORS_PATHS = ("/api/build", "/api/schema", "/api/health", "/track3d-client.js", "/files/", "/zip/")


def configure(cors=None, api_key=None, max_builds=None, queue_wait=None, ttl_hours=None, frame_ancestors=None,
              max_body_mb=None):
    """Deployment options. Each falls back to a TRACK3D_* environment variable, then to a safe default
    (no cross-origin access, no API key, 2 parallel builds, 24 h retention, 40 MB request limit)."""
    global BUILD_SLOTS
    env = os.environ.get

    def items(v, name):
        v = env(name, "") if v is None else v
        return [x.strip() for x in (v.split(",") if isinstance(v, str) else v) if x and x.strip()]
    CONFIG["cors"] = items(cors, "TRACK3D_CORS_ORIGINS")
    CONFIG["api_key"] = items(api_key, "TRACK3D_API_KEY")
    CONFIG["queue_wait"] = float(queue_wait if queue_wait is not None else env("TRACK3D_QUEUE_WAIT", 30))
    CONFIG["ttl_hours"] = float(ttl_hours if ttl_hours is not None else env("TRACK3D_TTL_HOURS", 24))
    CONFIG["frame_ancestors"] = (frame_ancestors if frame_ancestors is not None else env("TRACK3D_FRAME_ANCESTORS", "")).strip()
    CONFIG["max_body"] = int(float(max_body_mb if max_body_mb is not None else env("TRACK3D_MAX_BODY_MB", 40)) * 1048576)
    BUILD_SLOTS = threading.BoundedSemaphore(max(1, int(max_builds if max_builds is not None else env("TRACK3D_MAX_BUILDS", 2))))


def cors_headers(origin):
    allowed = CONFIG["cors"]
    if not allowed or not origin:
        return {}
    expose = {"Access-Control-Expose-Headers": "Content-Disposition, X-Track3D-Meta"}
    if "*" in allowed:
        return {"Access-Control-Allow-Origin": "*", **expose}
    return {"Access-Control-Allow-Origin": origin, "Vary": "Origin", **expose} if origin in allowed else {}


def new_api_job():
    job = Job()
    job.id = "api-" + uuid.uuid4().hex[:12]
    job.kind = "build"
    job.created = time.time()
    with JOBS_LOCK:
        JOBS[job.id] = job
    return job


def run_api_build(job, req):
    """Asynchronous variant of /api/build: waits for a free slot, then builds."""
    job.status = "queued"
    BUILD_SLOTS.acquire()
    job.status = "running"
    try:
        job.result = api.build(req, WORKDIR / job.id, job.log)
        job.status = "done"
    except api.ApiError as e:
        job.error, job.status = str(e), "error"
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        job.error, job.status = f"build failed: {e}", "error"
    finally:
        BUILD_SLOTS.release()
        if job.status == "error":
            api.remove(WORKDIR / job.id)


def cleanup_loop():
    while True:
        time.sleep(600)
        try:
            ttl, now = CONFIG["ttl_hours"] * 3600, time.time()
            for jid in api.clean_old(WORKDIR, ttl, now):
                with JOBS_LOCK:
                    JOBS.pop(jid, None)
            with JOBS_LOCK:                     # also forget old finished / failed API jobs whose folder is already gone
                for jid in [k for k, j in JOBS.items() if getattr(j, "kind", "") == "build"
                            and j.status in ("done", "error") and now - getattr(j, "created", now) > ttl]:
                    JOBS.pop(jid, None)
        except Exception:
            pass


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
        self.key = None
        self.kind = "generate"
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
        job.result = pipeline.run(cfg, route=route, log=job.log, name_hint=hint, save_scene=True).to_dict()
        get_scene(job.id)
        job.status = "done"
    except Exception as e:  # shown to the user in the UI
        job.error = str(e) or e.__class__.__name__
        job.log(traceback.format_exc())
        job.status = "error"
        with JOBS_LOCK:
            JOB_KEYS.pop(job.key, None)


SCENES = {}          # job id -> Scene (loaded lazily from the job folder)
STATIC_JS = {"geom.js", "editor.js"}


def get_scene(job_id, fresh=False):
    """The job's scene. fresh=True re-reads the original saved state from disk (used by "discard edits")."""
    if not SAFE.match(job_id or ""):
        raise ValueError("unknown job")
    with JOBS_LOCK:
        sc = None if fresh else SCENES.get(job_id)
    if sc is None:
        folder = WORKDIR / job_id
        if not (folder / "scene.json").is_file():
            raise ValueError("this job has no editable scene")
        sc = scene_mod.Scene.load(str(folder))
        with JOBS_LOCK:
            SCENES[job_id] = sc
    return sc


def preview_names(job_id, sc):
    """Names of the STL files the viewer should load (edited versions win when present)."""
    folder = WORKDIR / job_id
    pre = f"edited_{sc.name}"
    pick = lambda part: f"{pre}_{part}.stl" if (folder / f"{pre}_{part}.stl").is_file() else f"{sc.name}_{part}.stl"
    return pick("terrain"), {s["name"]: pick(s["name"]) for s in sc.statics}


def run_compile(job, scene_id, body):
    """Rebuild every object from its (edited) parameters and write the new model + the edit file."""
    try:
        sc = get_scene(scene_id)
        objs = scene_mod.clean_objects(body.get("objects"), sc.meta["classes"])
        colors = body.get("colors") if isinstance(body.get("colors"), dict) else None
        job.log(f"Compiling {len(objs):,} objects...")
        parts, warnings = sc.compile(objs, colors, log=job.log)
        folder = WORKDIR / scene_id
        prefix = f"edited_{sc.name}"
        files = sc.write_outputs(parts, str(folder), prefix, log=job.log)
        proj = folder / f"{prefix}.track3d.json"
        proj.write_text(json.dumps(sc.project_dict(objs, colors), separators=(",", ":")))
        files.append(dict(name=proj.name, size=proj.stat().st_size))
        for w in warnings[:200]:
            job.log(f"  ! {w['id']}: {w['msg']}")
        sc.objects = objs                       # the compiled state is the new "current" scene
        job.result = dict(files=files, warnings=warnings, objects=len(objs), terrain_stl=f"{prefix}_terrain.stl",
                          statics={s["name"]: f"{prefix}_{s['name']}.stl" for s in sc.statics})
        job.status = "done"
    except Exception as e:
        job.error = str(e) or e.__class__.__name__
        job.log(traceback.format_exc())
        job.status = "error"


def run_open(job, project):
    """Open an edit file as a fresh job: write the scene, build the preview meshes."""
    try:
        sc = scene_mod.Scene.from_project(project)
        folder = WORKDIR / job.id
        folder.mkdir(parents=True, exist_ok=True)
        sc.save(str(folder))
        job.log(f"Opened edit file: {len(sc.objects):,} objects")
        parts, warnings = sc.compile(None, None, log=job.log)
        prefix = sc.name
        files = sc.write_outputs(parts, str(folder), prefix, log=job.log)
        with JOBS_LOCK:
            SCENES[job.id] = sc
        job.result = dict(name=sc.name, info=sc.meta.get("info", {}), files=files, scene=True, parts=[],
                          warnings=warnings)
        job.status = "done"
    except Exception as e:
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
        self.send_header("X-Content-Type-Options", "nosniff")
        hdrs = dict(extra or {})
        if getattr(self, "path", "").split("?")[0].startswith(CORS_PATHS):
            hdrs.update(cors_headers(self.headers.get("Origin")))
        if ctype.startswith("text/html") and CONFIG["frame_ancestors"]:
            hdrs["Content-Security-Policy"] = f"frame-ancestors {CONFIG['frame_ancestors']}"
        for k, v in hdrs.items():
            self.send_header(k, v)
        try:
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # client went away; nothing to send to

    def do_OPTIONS(self):                       # CORS pre-flight (only for the public API paths)
        p = self.path.split("?")[0]
        if not p.startswith(CORS_PATHS):
            return self._send(404, {"error": "not found"})
        extra = cors_headers(self.headers.get("Origin"))
        if extra:
            extra.update({"Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Max-Age": "600",
                          "Access-Control-Allow-Headers": "Content-Type, X-API-Key, Authorization"})
        self._send(204, b"", "text/plain", extra)

    def _authorized(self):
        keys = CONFIG["api_key"]
        if not keys:
            return True
        auth = self.headers.get("Authorization", "")
        tok = self.headers.get("X-API-Key") or (auth.split(None, 1)[1] if auth.lower().startswith("bearer ") and " " in auth else "")
        tok = tok or urllib.parse.parse_qs(self.path.partition("?")[2]).get("key", [""])[-1]
        return any(hmac.compare_digest(str(tok), k) for k in keys)

    def _build(self, req):
        """ONE route for headless use: POST /api/build (or GET with query parameters) -> the finished files."""
        if not self._authorized():
            return self._send(401, {"error": "missing or wrong API key"}, extra={"WWW-Authenticate": "Bearer"})
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or ""
        base = f"{self.headers.get('X-Forwarded-Proto') or 'http'}://{host}" if host else ""
        try:
            if req.get("id"):
                return self._build_status(req, base)
            fmt = str(req.get("format", "zip")).lower()
            if fmt not in api.FORMATS:
                raise api.ApiError(400, f"format must be one of {', '.join(api.FORMATS)}")
            if req == {"options": {}}:
                return self._send(200, api.USAGE)
            if api._truthy(req.get("async", False)):
                job = new_api_job()
                threading.Thread(target=run_api_build, args=(job, req), daemon=True).start()
                return self._send(202, {"id": job.id, "status": "queued", "status_url": f"{base}/api/build?id={job.id}"})
            if not BUILD_SLOTS.acquire(timeout=CONFIG["queue_wait"]):
                return self._send(503, {"error": "the server is busy with other builds; retry shortly"}, extra={"Retry-After": "15"})
            job = new_api_job()
            keep = fmt == "json"
            try:
                result = api.build(req, WORKDIR / job.id, job.log)
                job.result, job.status = result, "done"
                if keep:
                    return self._send(200, api.manifest(result, job.id, base))
                data, ctype, fname = api.package(WORKDIR / job.id, result, fmt)
                meta = dict(name=result["name"], info=result["info"], counts=result["counts"],
                            warnings=len(result["warnings"]), files=[f["name"] for f in result["files"]])
                return self._send(200, data, ctype, {"Content-Disposition": f'attachment; filename="{fname}"',
                                                     "X-Track3D-Meta": json.dumps(meta)})
            finally:
                BUILD_SLOTS.release()
                if not keep:
                    api.remove(WORKDIR / job.id)
                    with JOBS_LOCK:
                        JOBS.pop(job.id, None)
        except api.ApiError as e:
            return self._send(e.status, {"error": str(e)})
        except Exception as e:
            print(traceback.format_exc(), file=sys.stderr)
            return self._send(500, {"error": f"build failed: {e}"})

    def _build_status(self, req, base):
        jid = str(req["id"])
        job = JOBS.get(jid)
        if not (jid.startswith("api-") and SAFE.match(jid) and job):
            raise api.ApiError(404, "unknown or expired build id")
        if req.get("download"):
            fmt = str(req["download"]).lower()
            if fmt not in api.FORMATS or fmt == "json":
                raise api.ApiError(400, "download must be zip, 3mf, stl or project")
            if job.status != "done":
                raise api.ApiError(409, f"build is {job.status}")
            data, ctype, fname = api.package(WORKDIR / jid, job.result, fmt)
            return self._send(200, data, ctype, {"Content-Disposition": f'attachment; filename="{fname}"'})
        out = dict(id=jid, status=job.status, error=job.error)
        if job.status == "done":
            out["result"] = api.manifest(job.result, jid, base)
        return self._send(200, out)

    def do_GET(self):
        p = self.path.split("?")[0]
        q = self.path.partition("?")[2]
        if p == "/api/build":
            try:
                return self._build(api.request_from_query(urllib.parse.parse_qs(q)))
            except api.ApiError as e:
                return self._send(e.status, {"error": str(e)})
        if p == "/api/health":
            return self._send(200, {"ok": True})
        if p == "/track3d-client.js":
            return self._send(200, (HERE / "track3d-client.js").read_bytes(), "text/javascript; charset=utf-8")
        if p.startswith("/vendor/") and p[8:] in VENDOR:
            return self._send(200, (HERE / "vendor" / p[8:]).read_bytes(), "text/javascript; charset=utf-8",
                              {"Cache-Control": "public, max-age=86400"})
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
        if p.lstrip("/") in STATIC_JS:
            return self._send(200, (HERE / p.lstrip("/")).read_bytes(), "text/javascript; charset=utf-8")
        if p.startswith("/api/scene/"):
            try:
                jid = p.split("/")[3]
                fresh = "original=1" in q
                sc = get_scene(jid, fresh)
                tstl, sstl = (f"{sc.name}_terrain.stl", None) if fresh else preview_names(jid, sc)
                return self._send(200, sc.client_dict(terrain_stl=tstl, static_stls=sstl))
            except ValueError as e:
                return self._send(404, {"error": str(e)})
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
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self._send(400, {"error": "bad Content-Length"})
        if n > CONFIG["max_body"]:
            left = n
            if n <= 4 * CONFIG["max_body"]:             # read and discard so the client sees the 413, not a reset
                while left > 0:
                    chunk = self.rfile.read(min(65536, left))
                    if not chunk:
                        break
                    left -= len(chunk)
            self.close_connection = True
            return self._send(413, {"error": f"request too large (limit {CONFIG['max_body'] // 1048576} MB)"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad JSON"})
        if not isinstance(body, dict):
            return self._send(400, {"error": "the request body must be a JSON object"})
        if self.path.split("?")[0] == "/api/build":
            return self._build(body)
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
            key_body = dict(body)
            key_body.pop("request_id", None)
            key = hashlib.sha256(json.dumps(key_body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            with JOBS_LOCK:
                existing_id = JOB_KEYS.get(key)
                if existing_id in JOBS:
                    return self._send(202, {"id": existing_id, "cached": JOBS[existing_id].status == "done"})
                job = Job()
                job.key = key
                JOBS[job.id] = job
                JOB_KEYS[key] = job.id
            threading.Thread(target=run_job, args=(job, body), daemon=True).start()
            return self._send(202, {"id": job.id})
        if self.path == "/api/compile":
            try:
                get_scene(str(body.get("id")))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            job = Job()
            job.kind = "compile"
            with JOBS_LOCK:
                JOBS[job.id] = job
            threading.Thread(target=run_compile, args=(job, body["id"], body), daemon=True).start()
            return self._send(202, {"id": job.id})
        if self.path == "/api/project/save":
            try:
                sc = get_scene(str(body.get("id")))
                objs = scene_mod.clean_objects(body.get("objects"), sc.meta["classes"])
                colors = body.get("colors") if isinstance(body.get("colors"), dict) else None
                return self._send(200, sc.project_dict(objs, colors))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        if self.path == "/api/project/open":
            job = Job()
            job.kind = "open"
            with JOBS_LOCK:
                JOBS[job.id] = job
            threading.Thread(target=run_open, args=(job, body.get("project")), daemon=True).start()
            return self._send(202, {"id": job.id})
        self._send(404, {"error": "not found"})


def serve(host, port, workdir, open_browser=True, **options):
    global WORKDIR
    WORKDIR = Path(workdir).resolve()
    WORKDIR.mkdir(parents=True, exist_ok=True)
    configure(**options)
    threading.Thread(target=cleanup_loop, daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    url = f"http://{host}:{port}/"
    print(f"track3d UI running at {url}  (Ctrl+C to stop)\nFiles are written to {WORKDIR}")
    print(f"Headless API:  POST {url}api/build   (GET {url}api/build for usage)")
    if CONFIG["api_key"]:
        print(f"  API key required for /api/build ({len(CONFIG['api_key'])} configured)")
    if CONFIG["cors"]:
        print("  Cross-origin access allowed for: " + ", ".join(CONFIG["cors"]))
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0
