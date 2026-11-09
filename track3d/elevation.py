"""Elevation sources: AWS Terrarium tiles (no key, cached) and a synthetic demo surface."""
import io
import math
import os
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .geo import bilerp

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "track3d")
_TILE_MEMORY_CACHE = {}
_TILE_CACHE_LOCK = threading.Lock()


def lonlat_to_pixel(lon, lat, z):
    n = 256.0 * 2 ** z
    x = (np.asarray(lon) + 180.0) / 360.0 * n
    y = (1.0 - np.arcsinh(np.tan(np.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def fetch_tile(z, x, y):
    n = 2 ** z
    if not 0 <= y < n:
        raise ValueError(f"latitude out of range for tile z={z} y={y} (valid 0..{n - 1})")
    x %= n  # wrap longitudes past the antimeridian (Leaflet can report lng like 190 or -215)
    key = (z, x, y)
    with _TILE_CACHE_LOCK:
        cached = _TILE_MEMORY_CACHE.get(key)
    if cached is not None:
        return cached
    os.makedirs(CACHE_DIR, exist_ok=True)
    fn = os.path.join(CACHE_DIR, f"{z}_{x}_{y}.png")
    if os.path.exists(fn):
        with open(fn, "rb") as f:
            data = f.read()
        with _TILE_CACHE_LOCK:
            _TILE_MEMORY_CACHE[key] = data
        return data
    url = TILE_URL.format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={"User-Agent": "track3d/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} for {url}") from e
    with open(fn, "wb") as f:
        f.write(data)
    with _TILE_CACHE_LOCK:
        _TILE_MEMORY_CACHE[key] = data
    return data


def _mosaic(z, tx0, tx1, ty0, ty1):
    """Elevation (m) of a block of Terrarium tiles stitched together."""
    from PIL import Image
    mosaic = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256))
    tiles = [(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]

    def decode(tile):
        tx, ty = tile
        img = np.asarray(Image.open(io.BytesIO(fetch_tile(z, tx, ty))).convert("RGB"), float)
        e = img[..., 0] * 256.0 + img[..., 1] + img[..., 2] / 256.0 - 32768.0
        return tx, ty, e

    with ThreadPoolExecutor(max_workers=min(8, len(tiles))) as pool:
        for tx, ty, e in pool.map(decode, tiles):
            mosaic[(ty - ty0) * 256:(ty - ty0 + 1) * 256,
                   (tx - tx0) * 256:(tx - tx0 + 1) * 256] = e
    return mosaic


def at_points(lats, lons, max_tiles=64):
    """Elevation in metres at arbitrary points (e.g. along a route), from zoom-12 tiles or coarser."""
    lats, lons = np.asarray(lats, float), np.asarray(lons, float)
    if not (np.isfinite(lats).all() and np.isfinite(lons).all()):
        raise ValueError("points contain non-numeric coordinates")
    lats = np.clip(lats, -85.0511, 85.0511)       # Web-Mercator limit
    lons = (lons + 180.0) % 360.0 - 180.0          # normalise to [-180, 180)
    z = 12
    while True:
        px, py = lonlat_to_pixel(lons, lats, z)
        tx0, tx1, ty0, ty1 = int(px.min() // 256), int(px.max() // 256), int(py.min() // 256), int(py.max() // 256)
        if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) <= max_tiles or z <= 5:
            break
        z -= 1
    mosaic = _mosaic(z, tx0, tx1, ty0, ty1)
    return bilerp(mosaic, px - tx0 * 256 - 0.5, py - ty0 * 256 - 0.5)


def from_tiles(lats, lons, grid_spacing_m, log=print):
    """Elevation (len(lats), len(lons)) in metres, sampled bilinearly."""
    from PIL import Image

    res0 = 156543.03392 * math.cos(math.radians(float(np.mean(lats))))  # m/pixel at zoom 0
    z = 14
    for cand in range(8, 15):  # smallest zoom whose resolution is fine enough
        if res0 / 2 ** cand <= grid_spacing_m:
            z = cand
            break
    while True:  # keep tile count sane
        pxa, pya = lonlat_to_pixel([lons.min(), lons.max()], [lats.max(), lats.min()], z)
        tx0, tx1 = int(pxa[0] // 256), int(pxa[1] // 256)
        ty0, ty1 = int(pya[0] // 256), int(pya[1] // 256)
        if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) <= 36 or z <= 6:
            break
        z -= 1
    log(f"Elevation: zoom {z}, {(tx1 - tx0 + 1) * (ty1 - ty0 + 1)} tile(s)")

    mosaic = _mosaic(z, tx0, tx1, ty0, ty1)

    LON, LAT = np.meshgrid(lons, lats)
    px, py = lonlat_to_pixel(LON, LAT, z)
    return bilerp(mosaic, px - tx0 * 256 - 0.5, py - ty0 * 256 - 0.5)


def demo(lats, lons):
    LON, LAT = np.meshgrid(lons, lats)
    u = (LON - lons.min()) / (lons.max() - lons.min())
    v = (LAT - lats.min()) / (lats.max() - lats.min())
    return (1300 + 220 * np.sin(3.1 * u) * np.cos(2.3 * v) + 120 * np.sin(7 * u + 2 * v)
            + 80 * np.cos(9 * v - 3 * u) + 400 * np.exp(-(((u - .7) ** 2 + (v - .6) ** 2) / .05)))


def demo_route():
    t = np.linspace(0, 2 * np.pi, 400)
    pts = np.stack([27.70 + 0.10 * np.sin(t) * (1 + .25 * np.cos(3 * t)) + 0.02 * np.sin(5 * t),
                    85.30 + 0.14 * np.cos(t) * (1 + .20 * np.sin(2 * t))], axis=1)
    return np.vstack([pts, pts[:1]])
