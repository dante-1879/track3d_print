import numpy as np

from ..geo import bilerp, resample_polyline
from ..meshing import clean_polygon, clip_convex, signed_area
from ..scene import make_parts, poly_spec, rdp, ribbon_spec
from ..osm import demo_water, fetch_water
from .base import Feature, register


def _clip_segment(a, b, W, H):
    """Clip one line segment to the rectangular map bounds."""
    d = b - a
    lo, hi = 0.0, 1.0
    for p, q in ((-d[0], a[0]), (d[0], W - a[0]),
                 (-d[1], a[1]), (d[1], H - a[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
        if lo > hi:
            return None
    return a + lo * d, a + hi * d


def build_water(bodies, area, Zmm, height, width, embed, log=print):
    """Turn OSM water outlines into a raised, separately printable coloured part."""
    W, H = area.xs[-1], area.ys[-1]
    dx, dy = area.xs[1] - area.xs[0], area.ys[1] - area.ys[0]
    outline = area.outline(inset=1.5 * dx if area.shape not in ("rectangle", "square") else 0.0)
    out = []
    skipped = 0
    for kind, ll in bodies:
        if kind == "line":
            xy = area.to_mm(ll[:, 0], ll[:, 1])
            clipped = []
            if area.shape in ("rectangle", "square"):
                for a, b in zip(xy[:-1], xy[1:]):
                    segment = _clip_segment(a, b, W, H)
                    if segment is not None:
                        clipped.append(segment)
            else:
                inside = area.contains(xy)
                clipped = [(xy[i], xy[i + 1]) for i in np.flatnonzero(inside[:-1] & inside[1:])]
            paths = []
            for a, b in clipped:
                if paths and np.allclose(paths[-1][-1], a):
                    paths[-1] = np.vstack([paths[-1], b])
                else:
                    paths.append(np.array([a, b]))
            for path in paths:
                if len(resample_polyline(path, 0.5)) < 2:
                    continue
                out.append(ribbon_spec("water", rdp(path, 0.04), width, height, embed))
            continue
        P = clean_polygon(area.to_mm(ll[:, 0], ll[:, 1]))
        if P is None or P[:, 0].max() < 0 or P[:, 1].max() < 0 or P[:, 0].min() > W or P[:, 1].min() > H:
            skipped += 1
            continue
        if signed_area(P) < 0:
            P = P[::-1]
        if not area.contains(P).all():
            P = clean_polygon(clip_convex(P, outline))
        if P is None:
            skipped += 1
            continue
        out.append(poly_spec("water", P, height, embed))
    return out or None, skipped


@register
class WaterFeature(Feature):
    name, label, order = "water", "Water", 25

    def enabled(self, cfg):
        return cfg.water.water

    def color(self, cfg):
        return cfg.water.water_color

    def build(self, ctx):
        if ctx.demo:
            bodies = demo_water(ctx.area.lats, ctx.area.lons)
        else:
            ctx.log("Fetching water bodies from OpenStreetMap...")
            bodies = fetch_water(ctx.area.bbox, ctx.log)
        if bodies is None:
            ctx.log("WARNING: could not download water bodies; skipping them.")
            return None
        specs, skipped = build_water(bodies, ctx.area, ctx.Zmm, ctx.cfg.water.water_height,
                                     ctx.cfg.water.waterway_width, ctx.cfg.path.embed, ctx.log)
        out, _ = make_parts(specs or [], "water", "Water body", ctx.cfg.water.water_color, ctx.T, fallback="skip")
        lost = len(specs or []) - len(out) + skipped
        ctx.log(f"Water: {len(out):,} printed" + (f" ({lost:,} skipped)" if lost else ""))
        return out
