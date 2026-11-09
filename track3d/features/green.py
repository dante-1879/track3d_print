import numpy as np

from ..geo import bilerp
from ..meshing import clean_polygon, clip_convex, signed_area
from ..scene import compile_object, make_parts, poly_spec
from ..osm import demo_landuse, fetch_landuse
from .base import Feature, register


def build_green(polygons, area, Zmm, height, embed, log=print):
    W, H = area.xs[-1], area.ys[-1]
    dx, dy = area.xs[1] - area.xs[0], area.ys[1] - area.ys[0]
    outline = area.outline(inset=1.5 * dx if area.shape not in ("rectangle", "square") else 0.0)
    out, skipped = [], 0
    for ll in polygons:
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
        out.append(poly_spec("green", P, height, embed))
    return out or None, skipped


@register
class GreenFeature(Feature):
    name, label, order = "green", "Green areas", 20

    def enabled(self, cfg):
        return cfg.green.green

    def color(self, cfg):
        return cfg.green.green_color

    def build(self, ctx):
        if ctx.demo:
            polygons = demo_landuse(ctx.area.lats, ctx.area.lons)
        else:
            ctx.log("Fetching green areas from OpenStreetMap...")
            polygons = fetch_landuse(ctx.area.bbox, ctx.log)
        if polygons is None:
            ctx.log("WARNING: could not download green areas; skipping them.")
            return None
        specs, skipped = build_green(polygons, ctx.area, ctx.Zmm, ctx.cfg.green.green_height,
                                     ctx.cfg.path.embed, ctx.log)
        out, _ = make_parts(specs or [], "green", "Green area", ctx.cfg.green.green_color, ctx.T, fallback="skip")
        ctx.log(f"Green areas: {len(out):,} printed" + (f" ({len(specs or []) - len(out) + skipped:,} skipped)"
                                                      if len(specs or []) - len(out) + skipped else ""))
        return out
