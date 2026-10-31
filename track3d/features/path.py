import numpy as np

from ..geo import bilerp, resample_polyline, smooth_1d
from ..meshing import carve_groove, ribbon_mesh
from .base import Feature, register


def prepare_centerline(ctx):
    """Smooth the GPX track into a path centre-line (n,3) sampled on the uncarved terrain."""
    a, cfg = ctx.area, ctx.cfg.path
    pxy = a.to_mm(ctx.route[:, 0], ctx.route[:, 1])
    ctx.closed = bool(np.linalg.norm(pxy[0] - pxy[-1]) < cfg.path_width)
    pxy = resample_polyline(pxy, 0.4)
    pxy = smooth_1d(pxy, max(5, int(cfg.path_width / 0.4) * 2 + 1), closed=False)
    pxy = resample_polyline(pxy, 0.5)
    dx, dy = a.xs[1] - a.xs[0], a.ys[1] - a.ys[0]
    z = smooth_1d(bilerp(ctx.Zmm, pxy[:, 0] / dx, pxy[:, 1] / dy), 5)
    ctx.pxy, ctx.C = pxy, np.column_stack([pxy, z])


def carve_terrain(ctx):
    """Cut the groove into the terrain top surface. Returns the surface to mesh."""
    cfg = ctx.cfg.path
    if ctx.C is not None:   # roads and buildings must not enter the slot the path sits in
        ctx.path_keepout = cfg.path_width / 2 + (cfg.clearance if cfg.groove_depth > 0 else 0.0) + 0.1
    if ctx.C is None or cfg.groove_depth <= 0:
        ctx.path_embed = cfg.embed
        return ctx.Zmm
    ctx.path_embed = cfg.groove_depth
    ctx.log(f"Groove: {cfg.path_width + 2 * cfg.clearance:.2f} mm wide, {cfg.groove_depth:.2f} mm deep "
            f"(path is {cfg.path_width:.2f} mm wide)")
    return carve_groove(ctx.Zmm, ctx.area.xs, ctx.area.ys, ctx.C,
                        cfg.path_width / 2 + cfg.clearance, cfg.groove_depth)


@register
class PathFeature(Feature):
    name, label, order = "path", "Route path", 10

    def enabled(self, cfg):
        return True

    def color(self, cfg):
        return cfg.path.path_color

    def build(self, ctx):
        if ctx.C is None:
            return None
        c = ctx.cfg.path
        return ribbon_mesh(ctx.C, c.path_width, c.path_height, ctx.path_embed)
