"""Projection, map area / tile shape, and small numeric helpers."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

POLY = {"hexagon": (6, 30.0), "octagon": (8, 0.0)}   # sides, angle (deg) of the first edge normal
RECT = ("rectangle", "square")


# ----------------------------------------------------------------------- helpers
def bilerp(Z, gx, gy):
    """Bilinear sample of 2D array Z at fractional (col gx, row gy) positions."""
    h, w = Z.shape
    gx = np.clip(gx, 0, w - 1.000001)
    gy = np.clip(gy, 0, h - 1.000001)
    x0 = np.floor(gx).astype(int)
    y0 = np.floor(gy).astype(int)
    fx, fy = gx - x0, gy - y0
    return (Z[y0, x0] * (1 - fx) * (1 - fy) + Z[y0, x0 + 1] * fx * (1 - fy)
            + Z[y0 + 1, x0] * (1 - fx) * fy + Z[y0 + 1, x0 + 1] * fx * fy)


def box_blur(Z, k):
    if k <= 0:
        return Z
    pad = np.pad(Z, k, mode="edge")
    out = np.zeros_like(Z)
    n = 2 * k + 1
    for dy in range(n):
        for dx in range(n):
            out += pad[dy:dy + Z.shape[0], dx:dx + Z.shape[1]]
    return out / (n * n)


def smooth_1d(a, window, closed=False):
    if window < 2 or len(a) < window:
        return a
    k = window // 2
    mode = "wrap" if closed else "edge"
    pad = np.pad(a, [(k, k)] + [(0, 0)] * (a.ndim - 1), mode=mode)
    out = np.zeros_like(a, dtype=float)
    for i in range(2 * k + 1):
        out += pad[i:i + len(a)]
    return out / (2 * k + 1)


def resample_polyline(xy, step):
    d = np.hypot(*np.diff(xy, axis=0).T)
    keep = np.concatenate([[True], d > 1e-9])
    xy = xy[keep]
    d = np.hypot(*np.diff(xy, axis=0).T)
    s = np.concatenate([[0], np.cumsum(d)])
    n = max(int(s[-1] / step), 2)
    si = np.linspace(0, s[-1], n)
    return np.stack([np.interp(si, s, xy[:, 0]), np.interp(si, s, xy[:, 1])], axis=1)


def corridor_mask(shape, items, cell):
    """Boolean grid (rows=y, cols=x, `cell` mm per cell) of cells within `radius` mm of any
    (points_mm, radius) polyline."""
    nyc, nxc = shape
    hit = np.zeros(shape, bool)
    for pts, radius in items:
        if pts is None or not len(pts):
            continue
        h = np.zeros(shape, bool)
        cx, cy = np.floor(pts[:, 0] / cell).astype(int), np.floor(pts[:, 1] / cell).astype(int)
        v = (cx >= 0) & (cx < nxc) & (cy >= 0) & (cy < nyc)
        h[cy[v], cx[v]] = True
        r = int(math.ceil(radius / cell))
        acc = h.copy()
        for ox in range(-r, r + 1):
            for oy in range(-r, r + 1):
                if (ox or oy) and ox * ox + oy * oy <= r * r:
                    yd, ysr = slice(max(oy, 0), nyc + min(oy, 0)), slice(max(-oy, 0), nyc + min(-oy, 0))
                    xd, xsr = slice(max(ox, 0), nxc + min(ox, 0)), slice(max(-ox, 0), nxc + min(-ox, 0))
                    acc[yd, xd] |= h[ysr, xsr]
        hit |= acc
    return hit


def lookup(mask, pts, cell):
    """mask value at mm points (clamped to the grid)."""
    iy = np.clip(np.floor(pts[:, 1] / cell).astype(int), 0, mask.shape[0] - 1)
    ix = np.clip(np.floor(pts[:, 0] / cell).astype(int), 0, mask.shape[1] - 1)
    return mask[iy, ix]


# -------------------------------------------------------------------------- area
@dataclass(eq=False)
class Area:
    """Geographic window of the model plus its mapping to millimetres."""
    shape: str
    latmin: float
    latmax: float
    lonmin: float
    lonmax: float
    kx: float            # metres per degree lon
    ky: float            # metres per degree lat
    wm: float
    hm: float
    long_side: float     # metres
    nx: int
    ny: int
    lons: np.ndarray
    lats: np.ndarray
    s: float             # mm per metre
    xs: np.ndarray       # grid columns in mm
    ys: np.ndarray       # grid rows in mm
    mask: object = None  # (ny-1, nx-1) bool of kept cells, or None for full rectangle

    @property
    def bbox(self):
        return (self.latmin, self.lonmin, self.latmax, self.lonmax)

    def to_mm(self, lat, lon):
        return np.stack([(np.asarray(lon) - self.lonmin) * self.kx * self.s,
                         (np.asarray(lat) - self.latmin) * self.ky * self.s], axis=-1)

    def _shape_test(self, x, y):
        if self.shape in RECT:
            return np.ones(np.shape(x), bool)
        R = self.xs[-1] / 2
        dx, dy = x - self.xs[-1] / 2, y - self.ys[-1] / 2
        if self.shape == "circle":
            return np.hypot(dx, dy) <= R
        n, off = POLY[self.shape]
        a = R * math.cos(math.pi / n)
        ok = np.ones(np.shape(x), bool)
        for k in range(n):
            th = math.radians(off + 360.0 * k / n)
            ok &= dx * math.cos(th) + dy * math.sin(th) <= a
        return ok

    def outline(self, inset=0.0):
        """Tile outline as a CCW polygon in mm (used to clip buildings at the edge)."""
        W, H = self.xs[-1], self.ys[-1]
        if self.shape in RECT:
            return np.array([[inset, inset], [W - inset, inset], [W - inset, H - inset], [inset, H - inset]])
        R = W / 2 - inset
        if self.shape == "circle":
            n, off = 120, 0.0
            ang = np.radians(off + 360.0 * np.arange(n) / n)
        else:
            n, off = POLY[self.shape]
            ang = np.radians(off + 180.0 / n + 360.0 * np.arange(n) / n)   # vertices sit between edge normals
            R = R / 1.0
        return np.stack([W / 2 + R * np.cos(ang), H / 2 + R * np.sin(ang)], axis=1)

    def contains(self, xy):
        """Which mm points lie inside the tile outline."""
        xy = np.atleast_2d(xy)
        x, y = xy[:, 0], xy[:, 1]
        inside = (x >= 0) & (x <= self.xs[-1]) & (y >= 0) & (y <= self.ys[-1])
        return inside & self._shape_test(x, y)


def make_area(cfg, pts=None, center=None, radius_m=None):
    """cfg: AreaSettings. Either a route (pts: lat/lon rows) or a centre + radius."""
    shape = cfg.shape
    n = POLY[shape][0] if shape in POLY else None
    lat_ref = pts[:, 0].mean() if pts is not None else center[0]
    kx, ky = 111320.0 * math.cos(math.radians(lat_ref)), 110540.0

    if pts is not None:
        la0, la1, lo0, lo1 = pts[:, 0].min(), pts[:, 0].max(), pts[:, 1].min(), pts[:, 1].max()
        clat, clon = (la0 + la1) / 2, (lo0 + lo1) / 2
        w0, h0 = (lo1 - lo0) * kx, (la1 - la0) * ky
        if shape in RECT:
            pad = cfg.padding * max(w0, h0, 500)
            if shape == "square":
                half_w = half_h = (max(w0, h0) + 2 * pad) / 2
            else:
                half_w, half_h = w0 / 2 + pad, h0 / 2 + pad
        else:
            rmax = float(np.max(np.hypot((pts[:, 0] - clat) * ky, (pts[:, 1] - clon) * kx)))
            reach = max(rmax, 250.0) * (1 + cfg.padding)
            half_w = half_h = reach if shape == "circle" else reach / math.cos(math.pi / n)
    else:
        clat, clon = center
        half_w = half_h = radius_m if shape in RECT or shape == "circle" else radius_m / math.cos(math.pi / n)

    lonmin, lonmax = clon - half_w / kx, clon + half_w / kx
    latmin, latmax = clat - half_h / ky, clat + half_h / ky
    wm, hm = (lonmax - lonmin) * kx, (latmax - latmin) * ky
    long_side = max(wm, hm)
    nx = max(int(round(cfg.resolution * wm / long_side)), 8)
    ny = max(int(round(cfg.resolution * hm / long_side)), 8)
    lons, lats = np.linspace(lonmin, lonmax, nx), np.linspace(latmin, latmax, ny)
    s = cfg.size / long_side
    xs, ys = (lons - lonmin) * kx * s, (lats - latmin) * ky * s
    area = Area(shape, latmin, latmax, lonmin, lonmax, kx, ky, wm, hm, long_side, nx, ny,
                lons, lats, s, xs, ys)
    if shape not in RECT:
        cx, cy = np.meshgrid((xs[:-1] + xs[1:]) / 2, (ys[:-1] + ys[1:]) / 2)
        area.mask = area._shape_test(cx, cy)
    return area
