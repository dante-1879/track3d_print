"""OpenStreetMap (Overpass) downloads: roads and buildings, plus offline demo data."""
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request

import numpy as np

from .elevation import CACHE_DIR

OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.private.coffee/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass.openstreetmap.fr/api/interpreter"]

# road class -> ribbon width in mm (each detail level adds to the previous)
_L1 = {"motorway": 1.8, "trunk": 1.8, "primary": 1.6, "motorway_link": 1.0,
       "trunk_link": 1.0, "primary_link": 1.0}
_L2 = dict(_L1, secondary=1.2, tertiary=1.0, secondary_link=0.8, tertiary_link=0.8)
_L3 = dict(_L2, unclassified=0.7, residential=0.7, living_street=0.6)
ROAD_CLASSES = {1: _L1, 2: _L2, 3: _L3}


def overpass(query, log=print):
    os.makedirs(CACHE_DIR, exist_ok=True)
    fn = os.path.join(CACHE_DIR, "osm_" + hashlib.md5(query.encode()).hexdigest() + ".json")
    if os.path.exists(fn):
        with open(fn, "r", encoding="utf-8") as f:
            return json.load(f)
    body = urllib.parse.urlencode({"data": query}).encode()
    for attempt in range(1, 4):  # up to 3 rounds over all servers
        for url in OVERPASS:
            host = url.split("/")[2]
            try:
                log(f"  OSM: asking {host} (round {attempt}/3)...")
                req = urllib.request.Request(url, data=body, headers={"User-Agent": "track3d/2.0"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    data = json.loads(r.read())
                if "elements" not in data:
                    raise ValueError("unexpected reply (server busy?)")
                with open(fn, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                return data
            except Exception as e:  # try the next server
                log(f"  OSM server {host} failed: {e}")
        if attempt < 3:
            log("  All OSM servers busy, waiting 15 s and retrying...")
            time.sleep(15)
    return None


def fetch_roads(bbox, detail, log=print):
    s, w, n, e = bbox
    classes = ROAD_CLASSES[detail]
    rx = "|".join(classes)
    data = overpass(f'[out:json][timeout:200];way["highway"~"^({rx})$"]({s},{w},{n},{e});out geom({s},{w},{n},{e});', log)
    if data is None:
        return None
    roads = []
    for el in data.get("elements", []):
        g, hw = el.get("geometry"), el.get("tags", {}).get("highway")
        if not g or hw not in classes:
            continue
        run = []  # vertices outside the requested area come back as null: split the way there
        for p in g + [None]:
            if p is not None:
                run.append([p["lat"], p["lon"]])
            else:
                if len(run) >= 2:
                    roads.append((classes[hw], np.array(run)))
                run = []
    return roads


def fetch_buildings(bbox, log=print):
    """Building centre points (for the abstract 'blocks' style)."""
    s, w, n, e = bbox
    data = overpass(f'[out:json][timeout:200];way["building"]({s},{w},{n},{e});out center qt;', log)
    if data is None:
        return None
    pts = [(el["center"]["lat"], el["center"]["lon"]) for el in data.get("elements", []) if "center" in el]
    return np.array(pts, dtype=float).reshape(-1, 2)


def _levels(tags):
    try:
        if "building:levels" in tags:
            return float(str(tags["building:levels"]).split(";")[0])
        if "height" in tags:
            return float(str(tags["height"]).split()[0].replace("m", "")) / 3.0
    except ValueError:
        pass
    return None


def fetch_footprints(bbox, log=print):
    """Building outlines as (levels or None, array[[lat, lon], ...]). Includes multipolygon
    buildings (outer rings). Geometry is returned whole; the caller clips it to the tile."""
    s, w, n, e = bbox
    q = (f'[out:json][timeout:200];(way["building"]({s},{w},{n},{e});'
         f'rel["building"]({s},{w},{n},{e}););out geom;')
    data = overpass(q, log)
    if data is None:
        return None
    out = []

    def ring(g):
        if not g or len(g) < 4 or any(p is None for p in g):
            return None
        return np.array([[p["lat"], p["lon"]] for p in g])
    for el in data.get("elements", []):
        lv = _levels(el.get("tags", {}))
        if el.get("type") == "relation":
            for m in el.get("members", []):
                g = m.get("geometry")
                if m.get("role") == "outer" and g and g[0] == g[-1]:
                    r = ring(g)
                    if r is not None:
                        out.append((lv, r))
        else:
            r = ring(el.get("geometry"))
            if r is not None:
                out.append((lv, r))
    return out


# ------------------------------------------------------------------ offline demo data
def demo_osm(lats, lons):
    rng = np.random.default_rng(1)
    la, lo = (lats.min(), lats.max()), (lons.min(), lons.max())

    def f(u, v):
        u, v = np.broadcast_arrays(np.asarray(u, float), np.asarray(v, float))
        return np.stack([la[0] + (la[1] - la[0]) * v, lo[0] + (lo[1] - lo[0]) * u], axis=-1)
    t = np.linspace(0, 1, 60)
    roads = [(1.8, f(t, 0.5 + 0.1 * np.sin(6 * t))), (1.6, f(0.5 + 0.08 * np.sin(5 * t), t)),
             (1.2, f(t, 0.25 + 0.05 * np.cos(9 * t))), (1.0, f(t, 0.75)), (1.0, f(0.25 + 0 * t, t)),
             (0.7, f(0.35 + 0 * t, t)), (0.7, f(t, 0.4))]
    c = f(np.array([0.5]), np.array([0.5]))[0]
    pts = c + rng.normal(0, 1, (30000, 2)) * [(la[1] - la[0]) * 0.12, (lo[1] - lo[0]) * 0.12]
    return roads, pts


def demo_footprints(bpts, kx, ky, k=1.0):
    """Synthetic buildings (rectangles and L-shapes), sized as if the map were ~3 km across."""
    rng = np.random.default_rng(2)
    out = []
    for lat, lon in bpts[:4000]:
        a, b, th = rng.uniform(8, 20) * k, rng.uniform(8, 16) * k, rng.uniform(0, np.pi)
        if rng.random() < .4:   # L-shape
            pts = np.array([[0, 0], [a, 0], [a, b * .45], [a * .45, b * .45], [a * .45, b], [0, b]])
        else:
            pts = np.array([[0, 0], [a, 0], [a, b], [0, b]])
        pts = pts - pts.mean(0)
        c, s_ = np.cos(th), np.sin(th)
        xy = pts @ np.array([[c, s_], [-s_, c]])
        xy = np.vstack([xy, xy[:1]])
        out.append((float(rng.integers(1, 6)) if rng.random() < .5 else None,
                    np.stack([lat + xy[:, 1] / ky, lon + xy[:, 0] / kx], axis=1)))
    return out
