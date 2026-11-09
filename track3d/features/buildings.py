import math

import numpy as np

from ..geo import bilerp, corridor_mask, lookup
from ..meshing import clean_polygon, clip_convex, min_rect, offset_polygon, signed_area
from ..scene import make_parts, poly_spec
from ..osm import demo_footprints, demo_osm, fetch_buildings, fetch_footprints
from .base import Feature, register


def _jitter(x, y):
    """Deterministic 0..1 pseudo-random value from a position (stable between runs)."""
    v = math.sin(x * 12.9898 + y * 78.233) * 43758.5453
    return v - math.floor(v)


# --------------------------------------------------------------------- footprints
def build_footprints(fps, area, Zmm, cfg, embed, clear, log=print, keepout=None):
    """Real building outlines, extruded. Small ones are scaled up to a printable size, every
    outline is grown slightly so near-touching neighbours fuse into blocks, and edge buildings
    are clipped to the tile instead of dropped."""
    W, H = area.xs[-1], area.ys[-1]
    dx, dy = area.xs[1] - area.xs[0], area.ys[1] - area.ys[0]
    outline = area.outline(inset=1.5 * dx if area.shape not in ("rectangle", "square") else 0.0)
    cell = 0.25
    hit = corridor_mask((int(H // cell) + 1, int(W // cell) + 1), clear, cell)
    phit = corridor_mask(hit.shape, [keepout], cell) if keepout else None
    polys, levels, tiny, blocked, on_path = [], [], 0, 0, 0
    for lv, ll in fps:
        xy = clean_polygon(area.to_mm(ll[:, 0], ll[:, 1]))
        if xy is None:
            continue
        if xy[:, 0].max() < 0 or xy[:, 1].max() < 0 or xy[:, 0].min() > W or xy[:, 1].min() > H:
            continue
        if signed_area(xy) < 0:
            xy = xy[::-1]
        ctr = xy.mean(0)
        if 0 <= ctr[0] <= W and 0 <= ctr[1] <= H and hit[min(int(ctr[1] // cell), hit.shape[0] - 1),
                                                           min(int(ctr[0] // cell), hit.shape[1] - 1)]:
            blocked += 1
            continue
        r = min_rect(xy)
        if r is None:
            continue
        short, long_ = 2 * float(r[1].min()), 2 * float(r[1].max())
        if long_ < cfg.min_building * 0.6:
            tiny += 1
            continue
        if short < cfg.min_building:
            xy = ctr + (xy - ctr) * min(cfg.min_building / max(short, 1e-6), 3.0)
        xy = offset_polygon(xy, cfg.building_grow)
        if not area.contains(xy).all():
            xy = clip_convex(xy, outline)
            xy = clean_polygon(xy) if xy is not None else None
            if xy is None:
                continue
        if phit is not None:   # no part of a building may reach into the path's groove
            probe = np.vstack([xy, (xy + np.roll(xy, -1, axis=0)) / 2, xy.mean(0)[None]])
            if lookup(phit, probe, cell).any():
                on_path += 1
                continue
        polys.append(xy)
        levels.append(lv)
    log(f"Buildings: {len(polys):,} printed ({tiny:,} too small, {blocked:,} on major roads, {on_path:,} on the route)")
    if not polys:
        return None

    out = []
    for P, lv in zip(polys, levels):
        c = P.mean(0)
        frac = (min(max(lv, 1.0), 6.0) - 1.0) / 5.0 if lv is not None else 0.1 + 0.45 * _jitter(c[0], c[1])
        out.append(poly_spec("buildings", P, cfg.block_height_min + frac * (cfg.block_height_max - cfg.block_height_min),
                             embed))
    return out or None


# ------------------------------------------------------------------------- blocks
def build_blocks(bpts, area, Zmm, cfg, embed, clear, per_building_m2=120.0, keepout=None):
    """Fused density blocks for big areas: touching cells, three height tiers, small variation."""
    cell, xs, ys = cfg.block_size, area.xs, area.ys
    nxc, nyc = int(xs[-1] // cell), int(ys[-1] // cell)
    if nxc < 1 or nyc < 1 or len(bpts) == 0:
        return None
    b = area.to_mm(bpts[:, 0], bpts[:, 1])
    ix, iy = np.floor(b[:, 0] / cell).astype(int), np.floor(b[:, 1] / cell).astype(int)
    ok = (ix >= 0) & (ix < nxc) & (iy >= 0) & (iy < nyc)
    counts = np.zeros((nyc, nxc))
    np.add.at(counts, (iy[ok], ix[ok]), 1)
    cover = counts * per_building_m2 / ((cell / area.s) ** 2)
    mask = (cover >= cfg.block_density) & ~corridor_mask((nyc, nxc), clear, cell)
    if keepout:   # whole cell must stay clear of the groove
        mask &= ~corridor_mask((nyc, nxc), [(keepout[0], keepout[1] + cell * 0.75)], cell)
    centres = np.stack(np.meshgrid((np.arange(nxc) + .5) * cell, (np.arange(nyc) + .5) * cell), -1).reshape(-1, 2)
    mask &= area.contains(centres).reshape(nyc, nxc)
    jy, jx = np.nonzero(mask)
    if len(jx) == 0:
        return None
    gap = 0.04 * cell
    x0, y0 = jx * cell + gap, jy * cell + gap
    x1, y1 = (jx + 1) * cell - gap, (jy + 1) * cell - gap
    dx, dy = xs[1] - xs[0], ys[1] - ys[0]
    tier = np.digitize(np.clip(cover[jy, jx] / 0.5, 0, 1), [0.33, 0.66]) / 2.0          # 0, .5, 1
    jit = np.array([_jitter(a, b_) for a, b_ in zip(jx, jy)]) * 0.25
    h = cfg.block_height_min + (cfg.block_height_max - cfg.block_height_min) * np.clip(tier * 0.75 + jit, 0, 1)
    out = []
    for i in range(len(jx)):
        rect = [[x0[i], y0[i]], [x1[i], y0[i]], [x1[i], y1[i]], [x0[i], y1[i]]]
        out.append(poly_spec("buildings", rect, h[i], 0.0))
    return out


@register
class BuildingsFeature(Feature):
    name, label, order = "buildings", "Buildings", 40

    def enabled(self, cfg):
        return cfg.buildings.buildings

    def color(self, cfg):
        return cfg.buildings.building_color

    def build(self, ctx):
        c, a = ctx.cfg.buildings, ctx.area
        use_fp = c.building_style == "footprints" or (c.building_style == "auto" and a.long_side <= 8000)
        if ctx.demo:
            bpts = demo_osm(a.lats, a.lons)[1]
            if use_fp:
                bpts = demo_footprints(bpts, a.kx, a.ky, k=a.long_side / 3000.0)
        else:
            ctx.log("Fetching buildings from OpenStreetMap...")
            bpts = fetch_footprints(a.bbox, ctx.log) if use_fp else fetch_buildings(a.bbox, ctx.log)
        if bpts is None:
            ctx.log("WARNING: could not download buildings; skipping them.")
            return None
        # keep the route and major roads free of buildings
        clear = list(ctx.road_clear)
        keep = (ctx.pxy, ctx.path_keepout) if ctx.pxy is not None else None
        embed = ctx.cfg.path.embed
        if use_fp:
            specs = build_footprints(bpts, a, ctx.Zmm, c, embed, clear, ctx.log, keepout=keep)
            out, fb = make_parts(specs or [], "building", "Building", c.building_color, ctx.T)
            if fb:
                ctx.log(f"  {fb} outlines simplified to rectangles")
            return out
        ctx.log(f"Buildings: {len(bpts):,} found (blocks style)")
        specs = build_blocks(bpts, a, ctx.Zmm, c, embed, clear, keepout=keep)
        if specs is None:
            ctx.log("No building blocks produced (lower the block density threshold).")
        out, _ = make_parts(specs or [], "building", "Building block", c.building_color, ctx.T)
        return out
