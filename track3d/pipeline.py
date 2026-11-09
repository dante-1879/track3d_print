"""Orchestration: settings (+ route) -> parts -> files. Knows nothing about CLI or UI."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import numpy as np

from . import elevation, export, geocode
from .config import Settings
from .features import REGISTRY
from .features.path import carve_terrain, prepare_centerline
from .geo import box_blur, make_area
from .meshing import terrain_mesh
from .scene import Part, Scene, Terrain


@dataclass
class Context:
    cfg: Settings
    log: object
    demo: bool
    area: object = None
    route: object = None
    Zmm: object = None
    C: object = None
    pxy: object = None
    closed: bool = False
    path_embed: float = 0.4
    base_lift: float = 0.0      # terrain solid starts this high (a bottom plate fills the gap)
    path_keepout: float = 0.0   # mm from the route centre-line that roads/buildings must stay out of
    road_clear: list = field(default_factory=list)
    T: object = None            # Terrain height field (uncarved) the objects sit on


@dataclass
class Result:
    name: str
    parts: list
    files: list
    info: dict

    def to_dict(self):
        return dict(name=self.name, info=self.info, files=self.files, scene=True,
                    parts=[dict(name=p.name, label=p.label, color=p.color, triangles=len(p.tris),
                                stl=f"{self.name}_{p.name}.stl") for p in self.parts])


def _slug(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_")[:40] or "model"


def run(cfg: Settings, route=None, log=print, name_hint=None, save_scene=False, write_files=True) -> Result:
    inp = cfg.input
    center, label = None, None
    if inp.demo and route is None:
        route = elevation.demo_route()
    if route is None:
        if inp.place:
            hits = geocode.search(inp.place, 1)
            if not hits:
                raise ValueError(f"Place not found: {inp.place!r}")
            center, label = (hits[0]["lat"], hits[0]["lon"]), hits[0]["name"]
            log(f"Place: {label}")
        elif inp.center_lat or inp.center_lon:
            center = (inp.center_lat, inp.center_lon)
        else:
            raise ValueError("Give a GPX route, a place name, coordinates, or turn on demo mode.")

    name = cfg.output.name or name_hint or ("demo" if inp.demo else _slug(inp.place) if inp.place else "model")
    name = _slug(name)
    outdir = cfg.output.outdir
    os.makedirs(outdir, exist_ok=True)
    ctx = Context(cfg=cfg, log=log, demo=inp.demo, route=route)

    # --- area and elevation
    a = ctx.area = make_area(cfg.area, pts=route, center=center, radius_m=inp.radius_km * 1000)
    log(f"Map area: {a.wm / 1000:.1f} km x {a.hm / 1000:.1f} km, grid {a.nx} x {a.ny}, shape {a.shape}")
    elev = elevation.demo(a.lats, a.lons) if inp.demo else \
        elevation.from_tiles(a.lats, a.lons, a.long_side / cfg.area.resolution, log)
    if cfg.terrain.flatten_sea:
        elev = np.maximum(elev, 0.0)
    elev = box_blur(elev, cfg.terrain.smooth)
    log(f"Elevation range: {elev.min():.0f} m to {elev.max():.0f} m")
    ctx.Zmm = cfg.area.base + (elev - elev.min()) * a.s * cfg.terrain.exaggeration
    ctx.T = Terrain(ctx.Zmm, a.xs[1] - a.xs[0], a.ys[1] - a.ys[0])

    # --- route, then terrain (carved for the route if there is one)
    if route is not None:
        prepare_centerline(ctx)
    Zt = carve_terrain(ctx)
    for feat in REGISTRY:
        if feat.enabled(cfg):
            feat.prepare(ctx)
    X, Y = np.meshgrid(a.xs, a.ys)
    parts = [Part("terrain", "Terrain", terrain_mesh(np.stack([X, Y, Zt], -1), a.mask, ctx.base_lift),
                  cfg.terrain.terrain_color, cls="terrain")]

    # --- feature plugins
    for feat in REGISTRY:
        if not feat.enabled(cfg):
            continue
        made = feat.build(ctx)
        if made is None:
            continue
        if isinstance(made, list):
            for item in made:       # (name, label, colour, tris[, spec]); no spec = static part
                n, l, c, t = item[:4]
                spec = item[4] if len(item) > 4 else None
                if len(t):
                    parts.append(Part(n, l, t, c, cls=feat.name if spec else n, spec=spec))
        elif len(made):
            parts.append(Part(feat.name, feat.label, made, feat.color(cfg), cls=feat.name))

    # --- export
    files = []

    def note(path):
        files.append(dict(name=os.path.basename(path), size=os.path.getsize(path)))
    o = cfg.output
    if write_files:        # the headless API builds its own (grouped) files from the scene instead
        for p in parts:
            path = os.path.join(outdir, f"{name}_{p.name}.stl")
            export.write_stl(path, p.tris, log=log)
            note(path)
        if o.combine_stl and len(parts) > 2:
            path = os.path.join(outdir, f"{name}_base_combined.stl")
            export.write_stl(path, np.concatenate([p.tris for p in parts if p.name != "path"]), log=log)
            note(path)
        if o.export_3mf:
            path = os.path.join(outdir, f"{name}.3mf")
            export.write_3mf(path, parts, title=name, log=log)
            note(path)
        if not o.export_stl:   # STL files above are always written first; drop them if not wanted
            for p in parts:
                fn = os.path.join(outdir, f"{name}_{p.name}.stl")
                if os.path.exists(fn):
                    os.remove(fn)
            files = [f for f in files if not f["name"].endswith(".stl")]

    height = float(ctx.Zmm.max() + (cfg.path.path_height if route is not None else 0))
    info = dict(width_mm=float(a.xs[-1]), depth_mm=float(a.ys[-1]), height_mm=height,
                closed_loop=ctx.closed, place=label)
    log(f"\nModel footprint {a.xs[-1]:.0f} x {a.ys[-1]:.0f} mm, height about {height:.0f} mm")
    if route is not None:
        log("Load the path STL with the base/terrain STLs in your slicer (don't re-centre them separately), "
            "or just open the 3MF." + (" Route is a loop." if ctx.closed else ""))
    if save_scene:
        Scene.from_generation(ctx, parts, name, info, cfg).save(outdir)
    return Result(name, parts, files, info)
