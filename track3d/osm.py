"""OpenStreetMap (Overpass) downloads: roads, buildings, water and land use, plus offline demo data."""
import hashlib
import json
import os
import ssl
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import urllib.parse
import urllib.request

import numpy as np
import truststore

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
_OSM_MEMORY_CACHE = {}
_OSM_CACHE_LOCK = threading.Lock()
_HTTPS_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def overpass(query, log=print):
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = hashlib.md5(query.encode()).hexdigest()
    fn = os.path.join(CACHE_DIR, "osm_" + key + ".json")
    with _OSM_CACHE_LOCK:
        cached = _OSM_MEMORY_CACHE.get(key)
    if cached is not None:
        return cached
    if os.path.exists(fn):
        with open(fn, "r", encoding="utf-8") as f:
            data = json.load(f)
        with _OSM_CACHE_LOCK:
            _OSM_MEMORY_CACHE[key] = data
        return data
    body = urllib.parse.urlencode({"data": query}).encode()

    def request(url, attempt):
        host = url.split("/")[2]
        log(f"  OSM: asking {host} (round {attempt}/3)...")
        req = urllib.request.Request(url, data=body, headers={"User-Agent": "track3d/2.0"})
        with urllib.request.urlopen(req, context=_HTTPS_CONTEXT, timeout=90) as r:
            data = json.loads(r.read())
        if "elements" not in data:
            raise ValueError("unexpected reply (server busy?)")
        return data

    for attempt in range(1, 4):  # up to 3 rounds over all servers
        # A busy Overpass endpoint should not block all the other mirrors. Race
        # the mirrors so a successful one can return without waiting for timeouts.
        pool = ThreadPoolExecutor(max_workers=len(OVERPASS))
        futures = [pool.submit(request, url, attempt) for url in OVERPASS]
        pending = set(futures)
        try:
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    try:
                        data = future.result()
                    except Exception as e:
                        log(f"  OSM server request failed: {e}")
                        continue
                    for other in pending:
                        other.cancel()
                    pool.shutdown(wait=False)
                    with open(fn, "w", encoding="utf-8") as f:
                        json.dump(data, f)
                    with _OSM_CACHE_LOCK:
                        _OSM_MEMORY_CACHE[key] = data
                    return data
        finally:
            pool.shutdown(wait=False)
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


def fetch_water(bbox, log=print):
    """Water bodies as ('area'/'line', arrays of (lat, lon) points)."""
    s, w, n, e = bbox
    q = (f'[out:json][timeout:200];('
         f'way["natural"="water"]({s},{w},{n},{e});'
         f'way["water"~"^(lake|pond|reservoir|basin|lagoon)$"]({s},{w},{n},{e});'
         f'way["waterway"~"^(river|stream|canal|drain)$"]({s},{w},{n},{e});'
         f'way["waterway"="riverbank"]({s},{w},{n},{e});'
         f');out geom;')
    data = overpass(q, log)
    if data is None:
        return None
    out = []
    seen = set()
    for el in data.get("elements", []):
        if el.get("id") in seen:
            continue
        seen.add(el.get("id"))
        g = el.get("geometry")
        if not g:
            continue
        runs = []
        run = []
        for point in g + [None]:
            if point is not None:
                run.append(point)
            elif len(run) >= 2:
                runs.append(run)
                run = []
        for points in runs:
            array = np.array([[p["lat"], p["lon"]] for p in points])
            is_area = (len(points) >= 4 and points[0] == points[-1]) or \
                      el.get("tags", {}).get("waterway") == "riverbank"
            out.append(("area" if is_area else "line", array))
    return out


def fetch_landuse(bbox, log=print):
    """Selected green/open-space polygons as arrays of (lat, lon) points."""
    s, w, n, e = bbox
    q = (f'[out:json][timeout:200];('
         f'way["landuse"~"^(forest|wood|grass|meadow|farmland|cemetery)$"]({s},{w},{n},{e});'
         f'way["leisure"~"^(park|garden|nature_reserve)$"]({s},{w},{n},{e});'
         f'rel["landuse"~"^(forest|wood|grass|meadow|farmland|cemetery)$"]({s},{w},{n},{e});'
         f'rel["leisure"~"^(park|garden|nature_reserve)$"]({s},{w},{n},{e});'
         f');out geom;')
    data = overpass(q, log)
    if data is None:
        return None
    out = []
    seen = set()
    for el in data.get("elements", []):
        if el.get("id") in seen:
            continue
        seen.add(el.get("id"))
        if el.get("type") == "relation":
            for member in el.get("members", []):
                g = member.get("geometry")
                if member.get("role") == "outer" and g and len(g) >= 4 and g[0] == g[-1]:
                    out.append(np.array([[p["lat"], p["lon"]] for p in g]))
        else:
            g = el.get("geometry")
            if g and len(g) >= 4 and g[0] == g[-1] and not any(p is None for p in g):
                out.append(np.array([[p["lat"], p["lon"]] for p in g]))
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


def demo_water(lats, lons):
    """Synthetic lake and river for the offline demo."""
    la, lo = (lats.min(), lats.max()), (lons.min(), lons.max())
    lat0, lon0 = (la[0] + la[1]) / 2, (lo[0] + lo[1]) / 2
    dlat, dlon = (la[1] - la[0]) * 0.18, (lo[1] - lo[0]) * 0.24
    t = np.linspace(0, 2 * np.pi, 40, endpoint=False)
    lake = np.column_stack([lat0 + dlat * np.sin(t), lon0 + dlon * np.cos(t)])
    river_t = np.linspace(0, 1, 80)
    river = np.column_stack([la[0] + (la[1] - la[0]) * river_t,
                             lon0 + (lo[1] - lo[0]) * 0.18 * np.sin(5 * river_t)])
    return [("area", lake), ("line", river)]


def demo_landuse(lats, lons):
    """One synthetic green area for the offline demo."""
    la, lo = (lats.min(), lats.max()), (lons.min(), lons.max())
    return [np.array([[la[0] + (la[1] - la[0]) * 0.08, lo[0] + (lo[1] - lo[0]) * 0.08],
                      [la[0] + (la[1] - la[0]) * 0.08, lo[0] + (lo[1] - lo[0]) * 0.32],
                      [la[0] + (la[1] - la[0]) * 0.30, lo[0] + (lo[1] - lo[0]) * 0.32],
                      [la[0] + (la[1] - la[0]) * 0.30, lo[0] + (lo[1] - lo[0]) * 0.08]])]


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
