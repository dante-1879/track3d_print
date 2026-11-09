import numpy as np

from ..geo import bilerp, corridor_mask, lookup, resample_polyline
from ..scene import make_parts, ribbon_spec, rdp
from ..osm import demo_osm, fetch_roads
from .base import Feature, register


def build_roads(roads, area, Zmm, height, embed, major_width=1.2, keepout=None):
    """Road ribbons on the terrain. Also returns (centre-line, clearance radius) for major roads.
    keepout=(points_mm, radius_mm): road pieces inside this corridor are cut away, so roads end
    flush at the edge of the groove instead of blocking it."""
    xs, ys = area.xs, area.ys
    cell = 0.2
    masks = {}   # one keep-out grid per road width: the ribbon's own edges must stay out too

    def keep_mask(width):
        if keepout is None:
            return None
        if width not in masks:
            masks[width] = corridor_mask((int(ys[-1] // cell) + 1, int(xs[-1] // cell) + 1),
                                         [(keepout[0], keepout[1] + width / 2)], cell)
        return masks[width]
    tris, clear = [], []
    for width, ll in roads:
        xy = area.to_mm(ll[:, 0], ll[:, 1])
        idx = np.flatnonzero(area.contains(xy))
        if len(idx) < 2:
            continue
        for run in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1):
            if len(run) < 2:
                continue
            seg = xy[run]
            if np.hypot(*np.diff(seg, axis=0).T).sum() < 0.8:
                continue
            seg = resample_polyline(seg, 0.5)
            hit = keep_mask(width)
            free = np.flatnonzero(~lookup(hit, seg, cell)) if hit is not None else np.arange(len(seg))
            if len(free) < 2:
                continue
            for piece in np.split(free, np.flatnonzero(np.diff(free) > 1) + 1):
                sub = seg[piece]
                if len(sub) < 2 or np.hypot(*np.diff(sub, axis=0).T).sum() < 0.8:
                    continue
                tris.append(ribbon_spec("roads", rdp(sub, 0.04), width, height, embed))
                if width >= major_width:
                    clear.append((sub, max(width / 2 - 0.2, 0.3)))
    return tris or None, clear


@register
class RoadsFeature(Feature):
    name, label, order = "roads", "Roads", 30

    def enabled(self, cfg):
        return cfg.roads.roads

    def color(self, cfg):
        return cfg.roads.road_color

    def build(self, ctx):
        c = ctx.cfg.roads
        if ctx.demo:
            roads = demo_osm(ctx.area.lats, ctx.area.lons)[0]
        else:
            ctx.log("Fetching roads from OpenStreetMap (can take a minute for big areas)...")
            roads = fetch_roads(ctx.area.bbox, c.road_detail, ctx.log)
        if roads is None:
            ctx.log("WARNING: could not download roads; skipping them.")
            return None
        keep = (ctx.pxy, ctx.path_keepout) if ctx.pxy is not None else None
        tris, ctx.road_clear = build_roads(roads, ctx.area, ctx.Zmm, c.road_height, ctx.cfg.path.embed,
                                           keepout=keep)
        ctx.log(f"Roads: {len(roads)} ways")
        out, _ = make_parts(tris or [], "road", "Road", c.road_color, ctx.T)
        return out
