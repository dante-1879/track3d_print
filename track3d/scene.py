"""Editable scene: every printable object as parametric data (outline or centre-line plus
height / width / embed), turned into triangles by ONE function (`compile_object`). The generator
and the editor's "compile" both use it, so what you edit is exactly what gets exported.

Object spec (plain dict, JSON friendly, millimetres):
    id, cls, kind ("poly" | "ribbon"), label, pts [[x, y], ...], height, embed, width (ribbon only)
"""
from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass

import numpy as np

from . import export
from .geo import bilerp, resample_polyline, smooth_1d
from .meshing import (carve_groove, clean_polygon, extrude_polygon, min_rect, ribbon_mesh,
                      signed_area, terrain_mesh, triangulate)

VERSION = 2
POLY, RIBBON = "poly", "ribbon"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,48}$")
MAX_POINTS, MAX_OBJECTS = 20000, 200000


@dataclass
class Part:
    name: str
    label: str
    tris: np.ndarray
    color: str
    cls: str = ""
    spec: dict | None = None


class Terrain:
    """Height field (mm) used to sit every object on the ground."""

    def __init__(self, Z, dx, dy):
        self.Z, self.dx, self.dy = np.asarray(Z, float), float(dx), float(dy)

    def z(self, xy):
        xy = np.asarray(xy, float).reshape(-1, 2)
        return bilerp(self.Z, xy[:, 0] / self.dx, xy[:, 1] / self.dy)


def _r(pts):
    return np.round(np.asarray(pts, float), 3).tolist()


def poly_spec(cls, pts, height, embed, label=""):
    return dict(cls=cls, kind=POLY, label=label, pts=_r(pts), height=float(height), embed=float(embed))


def ribbon_spec(cls, pts, width, height, embed, label="", **extra):
    return dict(cls=cls, kind=RIBBON, label=label, pts=_r(pts), width=float(width),
                height=float(height), embed=float(embed), **extra)


def rdp(pts, tol):
    """Douglas-Peucker simplification of an open polyline (keeps both ends)."""
    pts = np.asarray(pts, float)
    n = len(pts)
    if n < 3:
        return pts
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, d = pts[i], pts[j] - pts[i]
        seg = pts[i + 1:j]
        L = float(np.hypot(*d))
        dist = np.hypot(*(seg - a).T) if L < 1e-12 else np.abs(d[0] * (seg[:, 1] - a[1]) - d[1] * (seg[:, 0] - a[0])) / L
        k = int(dist.argmax())
        if dist[k] > tol:
            m = i + 1 + k
            keep[m] = True
            stack += [(i, m), (m, j)]
    return pts[keep]


def ground(spec, T, xy):
    """Elevation the object sits on at each point: the terrain, or a flat plane (`zfix`), plus `zoff`."""
    xy = np.asarray(xy, float).reshape(-1, 2)
    zf = spec.get("zfix")
    z = np.full(len(xy), float(zf)) if zf is not None else T.z(xy)
    return z + float(spec.get("zoff", 0.0))


def centerline(spec, T):
    """(n,3) centre-line of a ribbon sitting on the terrain (or on its flat plane)."""
    seg = resample_polyline(np.asarray(spec["pts"], float), 0.5)
    if spec.get("zfix") is None and spec.get("smooth_z"):
        z = smooth_1d(T.z(seg), 5) + float(spec.get("zoff", 0.0))
    else:
        z = ground(spec, T, seg)
    return np.column_stack([seg, z])


def is_simple(P, max_n=2500):
    """True when no two edges of the closed polygon properly cross (checked only up to max_n points)."""
    n = len(P)
    if n < 4 or n > max_n:
        return True
    a, b = P, np.roll(P, -1, axis=0)

    def orient(p, q, r):
        return (q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0])
    A, B, C, D = a[:, None], b[:, None], a[None], b[None]
    d1, d2, d3, d4 = orient(C, D, A), orient(C, D, B), orient(A, B, C), orient(A, B, D)
    eps = 1e-9
    cross = (((d1 > eps) & (d2 < -eps)) | ((d1 < -eps) & (d2 > eps))) & \
            (((d3 > eps) & (d4 < -eps)) | ((d3 < -eps) & (d4 > eps)))
    cross = np.triu(cross, 2)          # only non-adjacent edge pairs (j >= i + 2)
    cross[0, n - 1] = False            # first and last edge share a vertex
    return not bool(cross.any())


def compile_object(spec, T, fallback="rect", strict=False):
    """spec -> (triangles | None, warning | None).
    fallback: what to do with an outline that crosses itself - "rect" replaces it by its bounding
    rectangle (and updates spec['pts']), anything else drops the object."""
    try:
        if spec["kind"] == POLY:
            P = clean_polygon(np.asarray(spec["pts"], float))
            if P is None:
                return None, "outline collapsed to nothing"
            if signed_area(P) < 0:
                P = P[::-1]
            z = ground(spec, T, P)
            zb, zt = float(z.min()) - spec["embed"], float(z.max()) + spec["height"]
            tri, warn = triangulate(P), None
            if strict and tri is not None and not is_simple(P):
                tri = None                  # edited outline crosses itself: treat like a failed triangulation
            if tri is None:
                rr = min_rect(P) if fallback == "rect" else None
                if rr is None:
                    return None, "outline crosses itself"
                ctr, half, th = rr
                cs, sn = np.cos(th), np.sin(th)
                P = ctr + np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]]) * half @ np.array([[cs, sn], [-sn, cs]])
                tri = triangulate(P)
                if tri is None:
                    return None, "outline crosses itself"
                spec["pts"] = _r(P)
                warn = "outline crossed itself; replaced by its bounding rectangle"
            return extrude_polygon(P, tri, zb, zt), warn
        pts = np.asarray(spec["pts"], float)
        if len(pts) < 2 or float(np.hypot(*np.diff(pts, axis=0).T).sum()) < 0.2:
            return None, "line too short"
        return ribbon_mesh(centerline(spec, T), spec["width"], spec["height"], spec["embed"]), None
    except (ValueError, IndexError, FloatingPointError) as e:
        return None, f"could not be built ({e})"


def make_parts(specs, id_prefix, label_prefix, color, T, fallback="rect"):
    """Compile specs, number the survivors, return [(name, label, color, tris, spec)]."""
    out, warnings = [], 0
    for s in specs:
        tris, warn = compile_object(s, T, fallback)
        if tris is None:
            continue
        warnings += warn is not None
        i = len(out) + 1
        s["id"], s["label"] = f"{id_prefix}_{i:04d}", f"{label_prefix} {i}"
        out.append((s["id"], s["label"], color, tris, s))
    return out, warnings


# ------------------------------------------------------------------ validation
def _num(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return min(max(v, lo), hi) if np.isfinite(v) else default


def clean_objects(objects, classes):
    """Validate / clamp objects arriving from the browser. Raises ValueError on structural problems."""
    if not isinstance(objects, list) or len(objects) > MAX_OBJECTS:
        raise ValueError("objects must be a list (at most %d)" % MAX_OBJECTS)
    known = {c["name"] for c in classes if c.get("editable")}
    seen, out = set(), []
    for o in objects:
        if not isinstance(o, dict):
            raise ValueError("bad object entry")
        oid = str(o.get("id", ""))
        if not ID_RE.match(oid) or oid in seen:
            raise ValueError(f"bad or duplicate object id {oid!r}")
        seen.add(oid)
        if o.get("cls") not in known or o.get("kind") not in (POLY, RIBBON):
            raise ValueError(f"{oid}: unknown class or kind")
        pts = o.get("pts")
        need = 3 if o["kind"] == POLY else 2
        if not isinstance(pts, list) or not need <= len(pts) <= MAX_POINTS:
            raise ValueError(f"{oid}: needs between {need} and {MAX_POINTS} points")
        try:
            arr = np.asarray(pts, float)
        except (TypeError, ValueError):
            raise ValueError(f"{oid}: points must be numbers") from None
        if arr.ndim != 2 or arr.shape[1] != 2 or not np.isfinite(arr).all() or np.abs(arr).max() > 1e5:
            raise ValueError(f"{oid}: invalid points")
        s = dict(id=oid, cls=o["cls"], kind=o["kind"], label=str(o.get("label", oid))[:80], pts=_r(arr),
                 height=_num(o.get("height"), 0.05, 100, 1.0), embed=_num(o.get("embed"), 0, 50, 0.4))
        zoff = _num(o.get("zoff"), -100, 300, 0.0)
        if zoff:
            s["zoff"] = round(zoff, 3)
        if o.get("zfix") is not None:
            s["zfix"] = round(_num(o.get("zfix"), -100, 1000, 0.0), 3)
        if o["kind"] == RIBBON:
            s["width"] = _num(o.get("width"), 0.1, 100, 1.0)
            if o.get("smooth_z"):
                s["smooth_z"] = True
        out.append(s)
    return out


# ----------------------------------------------------------------------- scene
def _b64(a):
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode("ascii")


def _unb64(s, dtype):
    return np.frombuffer(base64.b64decode(s), dtype=dtype)


class Scene:
    """meta (json), objects (list of specs), terrain Z (+ optional tile mask), static parts."""

    def __init__(self, meta, objects, Z, mask=None, statics=None):
        self.meta, self.objects = meta, objects
        self.Z = np.asarray(Z, float)
        self.mask = None if mask is None else np.asarray(mask, bool)
        self.statics = statics or []     # [{name,label,color,tris(float32)}]

    # -- construction from a finished generation
    @classmethod
    def from_generation(cls, ctx, parts, name, info, cfg):
        a = ctx.area
        embed = cfg.path.embed
        by_cls = {}
        for p in parts:
            if p.spec is not None:
                by_cls.setdefault(p.cls, p)
        classes = [dict(name="terrain", label="Terrain", color=cfg.terrain.terrain_color, editable=False)]
        if "path" in by_cls:
            classes.append(dict(name="path", label="Route path", color=cfg.path.path_color, editable=True))
        classes += [
            dict(name="green", label="Green areas", color=cfg.green.green_color, editable=True),
            dict(name="water", label="Water", color=cfg.water.water_color, editable=True),
            dict(name="roads", label="Roads", color=cfg.roads.road_color, editable=True),
            dict(name="buildings", label="Buildings", color=cfg.buildings.building_color, editable=True)]
        statics = [dict(name=p.name, label=p.label, color=p.color, tris=p.tris.astype(np.float32))
                   for p in parts if p.spec is None and p.name != "terrain"]
        classes += [dict(name=s["name"], label=s["label"], color=s["color"], editable=False) for s in statics]
        bh = (cfg.buildings.block_height_min + cfg.buildings.block_height_max) / 2
        add = [dict(key="building", label="Building", cls="buildings", kind=POLY, height=bh, embed=embed),
               dict(key="green", label="Green area", cls="green", kind=POLY, height=cfg.green.green_height, embed=embed),
               dict(key="lake", label="Water body", cls="water", kind=POLY, height=cfg.water.water_height, embed=embed),
               dict(key="river", label="River / stream", cls="water", kind=RIBBON, height=cfg.water.water_height,
                    embed=embed, width=cfg.water.waterway_width),
               dict(key="road", label="Road", cls="roads", kind=RIBBON, height=cfg.roads.road_height, embed=embed,
                    width=0.8)]
        has_path = ctx.C is not None
        groove = dict(depth=cfg.path.groove_depth, clearance=cfg.path.clearance) \
            if has_path and cfg.path.groove_depth > 0 else None
        meta = dict(format="track3d-scene", version=VERSION, name=name, info=info, classes=classes, add_types=add,
                    groove=groove, base_lift=float(ctx.base_lift),
                    area=dict(width=float(a.xs[-1]), depth=float(a.ys[-1]), shape=a.shape,
                              outline=np.round(a.outline(), 3).tolist()),
                    terrain=dict(nx=int(a.nx), ny=int(a.ny), dx=float(a.xs[1] - a.xs[0]),
                                 dy=float(a.ys[1] - a.ys[0])))
        objs = [p.spec for p in parts if p.spec is not None]
        return cls(meta, objs, ctx.Zmm, a.mask, statics)

    # -- helpers
    @property
    def name(self):
        return self.meta["name"]

    def terrain(self):
        t = self.meta["terrain"]
        return Terrain(self.Z, t["dx"], t["dy"])

    def class_order(self):
        return [c["name"] for c in self.meta["classes"]]

    def class_color(self, name, colors=None):
        if colors and re.match(r"^#[0-9a-fA-F]{6}$", str(colors.get(name, ""))):
            return colors[name]
        return next(c["color"] for c in self.meta["classes"] if c["name"] == name)

    # -- compile
    def compile(self, objects=None, colors=None, log=print):
        """-> (parts, warnings[{id,msg}]). `objects` replaces the stored ones (already cleaned)."""
        objs = self.objects if objects is None else objects
        T, t = self.terrain(), self.meta["terrain"]
        xs, ys = np.arange(t["nx"]) * t["dx"], np.arange(t["ny"]) * t["dy"]
        warnings, parts = [], []
        path = next((o for o in objs if o["cls"] == "path"), None)
        Zt, g = T.Z, self.meta.get("groove")
        if path is not None and g:
            C = centerline(path, T)
            log(f"Re-cutting the groove along the edited path ({len(C)} samples)")
            Zt = carve_groove(T.Z, xs, ys, C, path["width"] / 2 + g["clearance"], g["depth"])
        X, Y = np.meshgrid(xs, ys)
        parts.append(Part("terrain", "Terrain", terrain_mesh(np.stack([X, Y, Zt], -1), self.mask, self.meta["base_lift"]),
                          self.class_color("terrain", colors), cls="terrain"))
        outline = np.asarray(self.meta["area"]["outline"], float)
        counts = {}
        for cname in self.class_order():
            for o in (o for o in objs if o["cls"] == cname):
                tris, warn = compile_object(o, T, strict=True)
                if tris is None:
                    warnings.append(dict(id=o["id"], msg=warn))
                    continue
                if warn:
                    warnings.append(dict(id=o["id"], msg=warn))
                if not _inside(np.asarray(o["pts"], float), outline):
                    warnings.append(dict(id=o["id"], msg="reaches outside the base tile"))
                parts.append(Part(o["id"], o.get("label", o["id"]), tris, self.class_color(cname, colors), cname, o))
                counts[cname] = counts.get(cname, 0) + 1
        for s in self.statics:
            parts.append(Part(s["name"], s["label"], s["tris"].astype(float), self.class_color(s["name"], colors), s["name"]))
        log("Built " + ", ".join(f"{n} {k}" for k, n in counts.items()))
        return parts, warnings

    def write_outputs(self, parts, outdir, prefix, log=print):
        """Per-colour STLs (one per class), a combined STL and a coloured 3MF -> [{name,size}]."""
        files = []

        def note(path):
            files.append(dict(name=os.path.basename(path), size=os.path.getsize(path)))
        order = []
        for p in parts:
            if p.cls not in order:
                order.append(p.cls)
        for c in order:
            tris = np.concatenate([p.tris for p in parts if p.cls == c])
            path = os.path.join(outdir, f"{prefix}_{c}.stl")
            export.write_stl(path, tris, log=log)
            note(path)
        path = os.path.join(outdir, f"{prefix}_base_combined.stl")
        export.write_stl(path, np.concatenate([p.tris for p in parts if p.cls != "path"]), log=log)
        note(path)
        path = os.path.join(outdir, f"{prefix}.3mf")
        export.write_3mf(path, parts, title=prefix, log=log)
        note(path)
        return files

    # -- persistence
    def save(self, outdir):
        with open(os.path.join(outdir, "scene.json"), "w") as f:
            json.dump(dict(self.meta, objects=self.objects), f, separators=(",", ":"))
        np.savez_compressed(os.path.join(outdir, "scene_terrain.npz"), Z=self.Z,
                            mask=self.mask if self.mask is not None else np.zeros(0, bool))
        np.savez_compressed(os.path.join(outdir, "scene_statics.npz"),
                            **{s["name"]: s["tris"] for s in self.statics})

    @classmethod
    def load(cls, outdir):
        with open(os.path.join(outdir, "scene.json")) as f:
            d = json.load(f)
        objects = d.pop("objects")
        with np.load(os.path.join(outdir, "scene_terrain.npz")) as z:
            Z, mask = z["Z"], z["mask"]
        statics = []
        with np.load(os.path.join(outdir, "scene_statics.npz")) as z:
            for c in d["classes"]:
                if c["name"] in z.files:
                    statics.append(dict(name=c["name"], label=c["label"], color=c["color"], tris=z[c["name"]]))
        return cls(d, objects, Z, mask if mask.size else None, statics)

    def client_dict(self, objects=None, terrain_stl=None, static_stls=None):
        """What the browser needs: meta + objects + heights (float32, base64) + preview STL names."""
        d = dict(self.meta, objects=self.objects if objects is None else objects)
        d["terrain"] = dict(self.meta["terrain"], z_b64=_b64(self.Z.astype("<f4")),
                            stl=terrain_stl or f"{self.name}_terrain.stl")
        d["statics"] = [dict(name=s["name"], label=s["label"], color=s["color"],
                             stl=(static_stls or {}).get(s["name"], f"{self.name}_{s['name']}.stl"))
                        for s in self.statics]
        return d

    def project_dict(self, objects, colors=None):
        """Self-contained edit file: everything needed to reopen and recompile elsewhere."""
        meta = json.loads(json.dumps(self.meta))
        if colors:
            for c in meta["classes"]:
                c["color"] = self.class_color(c["name"], colors)
        d = dict(format="track3d-project", version=VERSION, meta=meta, objects=objects,
                 terrain_z_b64=_b64(self.Z.astype("<f4")),
                 statics=[dict(name=s["name"], tris_b64=_b64(s["tris"].astype("<f4"))) for s in self.statics])
        if self.mask is not None:
            d["mask_b64"] = _b64(np.packbits(self.mask.ravel()))
            d["mask_shape"] = list(self.mask.shape)
        return d

    @classmethod
    def from_project(cls, d):
        if not isinstance(d, dict) or d.get("format") != "track3d-project" or d.get("version") != VERSION:
            raise ValueError("this is not a track3d edit file (version %d)" % VERSION)
        meta = d["meta"]
        t = meta["terrain"]
        Z = _unb64(d["terrain_z_b64"], "<f4").astype(float)
        if Z.size != t["nx"] * t["ny"]:
            raise ValueError("terrain size does not match the edit file header")
        Z = Z.reshape(t["ny"], t["nx"])
        mask = None
        if d.get("mask_b64"):
            sh = d["mask_shape"]
            mask = np.unpackbits(_unb64(d["mask_b64"], np.uint8))[:sh[0] * sh[1]].astype(bool).reshape(sh)
        tri = {s["name"]: _unb64(s["tris_b64"], "<f4").reshape(-1, 3, 3) for s in d.get("statics", [])}
        statics = [dict(name=c["name"], label=c["label"], color=c["color"], tris=tri[c["name"]])
                   for c in meta["classes"] if c["name"] in tri]
        objects = clean_objects(d.get("objects", []), meta["classes"])
        for o, raw in zip(objects, d.get("objects", [])):
            if raw.get("smooth_z"):
                o["smooth_z"] = True
        return cls(meta, objects, Z, mask, statics)


def _inside(pts, outline, tol=0.05):
    """All points inside the (convex, CCW) tile outline?"""
    a, b = outline, np.roll(outline, -1, axis=0)
    e = b - a
    cr = e[None, :, 0] * (pts[:, None, 1] - a[None, :, 1]) - e[None, :, 1] * (pts[:, None, 0] - a[None, :, 0])
    return bool((cr >= -tol * np.hypot(e[:, 0], e[:, 1])[None, :]).all())
