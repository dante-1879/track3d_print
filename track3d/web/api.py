"""Headless build API: one call in, finished model files out. No browser, no UI session.

    POST /api/build   {"demo": true}                         -> track3d-demo.zip
    POST /api/build   {"gpx": "<gpx ...>", "format": "3mf"}  -> the .3mf
    POST /api/build   {"project": {...edit file...}}         -> rebuild from an edit file, no map download
    GET  /api/build?demo=1&format=3mf                        -> simple links for <a href> / curl

This module holds the logic (parsing, limits, building, packaging); server.py does the HTTP part.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import zipfile
from dataclasses import fields
from pathlib import Path

from .. import export, gpx as gpx_mod, pipeline
from .. import scene as scene_mod
from ..config import SECTIONS, Settings

FORMATS = ("zip", "3mf", "stl", "project", "json")
_INTERNAL = ("scene.json", "scene_terrain.npz", "scene_statics.npz")
_BOOLS, _FLOATS = {"demo", "per_object", "async"}, {"lat", "lon", "radius_km"}
_STRINGS = {"format", "name", "place", "id", "download", "key"}

USAGE = {
    "endpoint": "/api/build",
    "post": "JSON body. Pick ONE source: gpx (GPX XML text) | place + radius_km | lat + lon + radius_km | demo:true | "
            "project (an edit file). Optional: settings (same JSON as the UI's 'Save settings'), options (flat "
            "{field: value}, see /api/schema), objects (edited object list), colors {class: '#rrggbb'}, name, "
            "per_object (one STL per building/road...), format, async.",
    "format": {"zip": "everything (default)", "3mf": "the coloured 3MF only", "stl": "all STL files, zipped",
               "project": "the edit file (.track3d.json)", "json": "manifest with download links (files are kept for a while)"},
    "get": "GET /api/build?demo=1&format=3mf  - flat option names (e.g. &path_width=1.2) are accepted as query parameters.",
    "async": "POST with \"async\": true returns {id, status_url}; poll GET /api/build?id=ID, then "
             "GET /api/build?id=ID&download=zip (or 3mf / stl / project).",
    "auth": "If the server was started with an API key send it as 'X-API-Key: KEY' or 'Authorization: Bearer KEY'.",
}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _truthy(v):
    return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")


def safe_name(s):
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(s or ""))[:60].strip("._")
    return s or None


def request_from_query(qs):
    """Query string (urllib.parse.parse_qs result) -> the same structure as the JSON body."""
    req = {"options": {}}
    for k, vals in qs.items():
        v = vals[-1]
        if k in _STRINGS:
            req[k] = v
        elif k in _BOOLS:
            req[k] = _truthy(v)
        elif k in _FLOATS:
            try:
                req[k] = float(v)
            except ValueError:
                raise ApiError(400, f"{k} must be a number") from None
        else:
            req["options"][k] = v
    return req


# --------------------------------------------------------------------- settings
def _check_limits(cfg):
    """Settings.from_dict does not enforce the sliders' min/max; a public API has to."""
    for key in ("input", *SECTIONS):
        sec = getattr(cfg, key)
        for f in fields(sec):
            lo, hi = f.metadata.get("min"), f.metadata.get("max")
            v = getattr(sec, f.name)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                raise ApiError(400, f"{f.name} must be between {lo} and {hi} (got {v})")


def settings_from_request(req):
    try:
        cfg = Settings.from_dict(req.get("settings") if isinstance(req.get("settings"), dict) else None)
        for k, v in (req.get("options") or {}).items():
            try:
                cfg.set_flat(str(k), v)
            except KeyError:
                raise ApiError(400, f"unknown option {k!r} (see /api/schema for the available names)") from None
    except (ValueError, TypeError) as e:
        raise ApiError(400, f"invalid setting: {e}") from None
    inp = cfg.input
    if _truthy(req.get("demo", False)):
        inp.demo = True
    if req.get("place"):
        inp.place = str(req["place"])[:200]
    if (req.get("lat") is None) != (req.get("lon") is None):
        raise ApiError(400, "send lat and lon together")
    if req.get("lat") is not None:
        lat, lon = float(req["lat"]), float(req["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ApiError(400, "lat must be within -90..90 and lon within -180..180")
        inp.center_lat, inp.center_lon = lat, lon
    if req.get("radius_km") is not None:
        inp.radius_km = float(req["radius_km"])
    _check_limits(cfg)
    return cfg


# ------------------------------------------------------------------------ build
def build(req, folder, log=print):
    """Run one build into `folder`. Returns {name, info, warnings, files, counts}."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    colors = req.get("colors") if isinstance(req.get("colors"), dict) else None
    name = safe_name(req.get("name"))
    if req.get("project") is not None:
        try:
            sc = scene_mod.Scene.from_project(req["project"])
        except (ValueError, KeyError, TypeError) as e:
            raise ApiError(400, f"bad edit file: {e}") from None
        info, name = sc.meta.get("info", {}), name or sc.name
    else:
        cfg = settings_from_request(req)
        route = None
        if req.get("gpx"):
            try:
                route = gpx_mod.parse_gpx_text(str(req["gpx"]))
            except Exception as e:
                raise ApiError(400, f"could not read the GPX: {e}") from None
        inp = cfg.input
        if route is None and not (inp.demo or inp.place or inp.center_lat or inp.center_lon):
            raise ApiError(400, "no source: send gpx, place, lat+lon, demo:true or project")
        cfg.output.outdir = str(folder)
        if name:
            cfg.output.name = name
        try:
            res = pipeline.run(cfg, route=route, log=log, name_hint=None, save_scene=True, write_files=False)
        except (ValueError, RuntimeError, OSError) as e:
            raise ApiError(422, f"generation failed: {e}") from None
        sc, info, name = scene_mod.Scene.load(str(folder)), res.info, res.name
    objs = None
    if req.get("objects") is not None:
        try:
            objs = scene_mod.clean_objects(req["objects"], sc.meta["classes"])
        except ValueError as e:
            raise ApiError(400, f"bad objects: {e}") from None
    parts, warnings = sc.compile(objs, colors, log=log)
    files = sc.write_outputs(parts, str(folder), name, log=log)
    if _truthy(req.get("per_object", False)):
        for p in parts:
            path = folder / f"{name}_{p.name}.stl"
            export.write_stl(str(path), p.tris, log=lambda *_: None)
            files.append(dict(name=path.name, size=path.stat().st_size))
    proj = folder / f"{name}.track3d.json"
    proj.write_text(json.dumps(sc.project_dict(objs if objs is not None else sc.objects, colors), separators=(",", ":")))
    files.append(dict(name=proj.name, size=proj.stat().st_size))
    for fn in _INTERNAL:
        (folder / fn).unlink(missing_ok=True)
    counts = {}
    for p in parts:
        counts[p.cls] = counts.get(p.cls, 0) + 1
    return dict(name=name, info=info, warnings=warnings, files=files, counts=counts)


# --------------------------------------------------------------------- packaging
def package(folder, result, fmt):
    """-> (bytes, content_type, filename) for zip / 3mf / stl / project."""
    folder, name = Path(folder), result["name"]
    names = [f["name"] for f in result["files"]]
    if fmt in ("3mf", "project"):
        ext = ".3mf" if fmt == "3mf" else ".track3d.json"
        pick = [n for n in names if n.endswith(ext)]
        if not pick:
            raise ApiError(500, f"no {fmt} file was produced")
        ctype = "model/3mf" if fmt == "3mf" else "application/json"
        return (folder / pick[0]).read_bytes(), ctype, pick[0]
    if fmt == "stl":
        names = [n for n in names if n.endswith(".stl")]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.write(folder / n, n)
    return buf.getvalue(), "application/zip", f"{name}{'_stl' if fmt == 'stl' else ''}.zip"


def manifest(result, job_id, base=""):
    """JSON description of a kept build with download links."""
    return dict(id=job_id, name=result["name"], info=result["info"], counts=result["counts"],
                warnings=result["warnings"],
                files=[dict(name=f["name"], size=f["size"], url=f"{base}/files/{job_id}/{f['name']}?dl=1")
                       for f in result["files"]],
                zip=f"{base}/api/build?id={job_id}&download=zip", status_url=f"{base}/api/build?id={job_id}")


def remove(folder):
    shutil.rmtree(folder, ignore_errors=True)


def clean_old(workdir, ttl_seconds, now):
    """Delete finished API builds (folders named api-*) older than the retention time."""
    gone = []
    for d in Path(workdir).glob("api-*"):
        try:
            if d.is_dir() and now - d.stat().st_mtime > ttl_seconds:
                shutil.rmtree(d, ignore_errors=True)
                gone.append(d.name)
        except OSError:
            pass
    return gone
