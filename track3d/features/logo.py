"""Underside logo: a bottom plate with the logo cut out, plus colour parts that fill the cut-out.

    z = 0 .. depth      plate (terrain colour) with holes, + tile / mark+text / dot inlays
    z = depth .. top    the terrain solid (its bottom is lifted by `depth`)

The bottom face stays perfectly flat, so it prints without supports and in several colours."""
import math

import numpy as np

from .. import shapes as S
from ..meshing import extrude_shape
from .base import Feature, register

TILE_DOT = (0.386, 0.38)       # dot centre relative to the tile side (from the original artwork)
GLYPH_DY = -0.03               # optical offset of the glyph inside the tile
EM_PX = 400


def _tf(P, k=1.0, dx=0.0, dy=0.0):
    return P * k + np.array([dx, dy])


def _shape_map(shapes, fn):
    return [(fn(o), [fn(h) for h in hs]) for o, hs in shapes]


def _mark_shapes(c, s):
    """Unit-box icon shapes (largest side = 1, centred, y up)."""
    if c.logo_mark == "化" and not s.logo_mark_font:
        return S.load_builtin_mark()
    font = s.logo_mark_font
    if not font:
        font = next((f for f in S.CJK_CANDIDATES if __import__("os").path.exists(f)), "")
        if not font and all(ord(ch) < 0x250 for ch in c.logo_mark):
            font = S.find_font("")
        if not font:
            raise ValueError("No CJK font found for the icon character. Set 'Icon font file' to a font that "
                             "contains it (e.g. Noto Serif CJK, Yu Mincho, Hiragino Mincho).")
    sh, _ = S.text_shapes(c.logo_mark, font, EM_PX, 0.0, 0.0)
    if not sh:
        raise ValueError(f"The icon font has no glyph for {c.logo_mark!r}.")
    lo, hi = S.bbox(sh)
    mid, size = (lo + hi) / 2, float(max(hi - lo))
    return _shape_map(sh, lambda P: (P - mid) / size)


def _read_svg(value):
    if value.lstrip().startswith("<"):
        return value
    import os
    if not os.path.exists(value):
        raise ValueError(f"Icon SVG not found: {value}")
    return open(value, encoding="utf-8").read()


def _is_rect(ring, tol=0.02):
    lo, hi = ring.min(0), ring.max(0)
    on_box = (np.abs(ring[:, 0] - lo[0]) < tol) | (np.abs(ring[:, 0] - hi[0]) < tol)
    on_box &= (np.abs(ring[:, 1] - lo[1]) < tol) | (np.abs(ring[:, 1] - hi[1]) < tol)
    return len(ring) <= 5 and bool(on_box.all())


def _icon(cfg, log):
    """Icon geometry centred on the origin: tile ring (or None) and foreground colour layers."""
    c, st = cfg.logo, cfg.logo_style
    side = c.logo_icon_size * c.logo_scale
    if c.logo_icon_source == "character":
        glyph = _shape_map(_mark_shapes(c, st), lambda P: _tf(P, side * st.logo_mark_size, 0, GLYPH_DY * side))
        layers = [dict(name="logo_mark", label="Logo icon and text", color=st.logo_color, shapes=glyph)]
        if st.logo_dot:
            dot = S.circle(TILE_DOT[0] * side, TILE_DOT[1] * side, st.logo_dot_size * side / 2)
            layers.append(dict(name="logo_dot", label="Logo dot", color=st.logo_dot_color, shapes=[(dot, [])]))
        tile = S.rounded_rect(0, 0, side, side, st.logo_tile_radius * side) if st.logo_tile else None
        return dict(side=side, tile=tile, tile_color=st.logo_tile_color, layers=layers)

    from .. import svgimport
    if c.logo_icon_source == "svg":
        if not c.logo_svg.strip():
            raise ValueError("Icon source is 'svg' but no SVG was chosen. Pick a file under 'Custom icon (SVG)'.")
        text = _read_svg(c.logo_svg)
    else:
        text = (S.ASSETS / "icon.svg").read_text(encoding="utf-8")
    art = svgimport.load_icon(text)
    for w in art.warnings:
        log(f"Icon SVG: {w}")
    scale = lambda shapes: _shape_map(shapes, lambda P: P * side)
    bg, fg = None, list(art.layers)
    first = fg[0].shapes[0][0] if fg and fg[0].shapes else None
    if first is not None and len(fg[0].shapes) == 1:     # a full-size first layer is the tile
        lo, hi = first.min(0), first.max(0)
        if (hi[0] - lo[0]) >= 0.98 * art.w and (hi[1] - lo[1]) >= 0.98 * art.h:
            bg, fg = fg[0], fg[1:]
    tile = None
    if bg is not None and st.logo_tile:
        o = bg.shapes[0][0]
        tile = (S.rounded_rect(0, 0, side, side * art.h, st.logo_tile_radius * side * min(1, art.h))
                if _is_rect(o) else o * side)
    if not st.logo_dot:
        fg = fg[:1]
    layers = []
    for i, layer in enumerate(fg):
        name, label = (("logo_mark", "Logo icon and text") if i == 0 else
                       ("logo_dot", "Logo accent") if i == 1 else (f"logo_accent{i}", f"Logo accent {i}"))
        col = layer.color
        if not st.logo_svg_colors:
            col = st.logo_color if i == 0 else st.logo_dot_color
        layers.append(dict(name=name, label=label, color=col, shapes=scale(layer.shapes)))
    tile_color = bg.color if (bg is not None and st.logo_svg_colors) else st.logo_tile_color
    return dict(side=side, tile=tile, tile_color=tile_color, layers=layers)


def _layout(cfg, area, log=print):
    """All logo geometry around the base centre. Returns (icon, text, ipos, tpos, place)."""
    c, st = cfg.logo, cfg.logo_style
    k = c.logo_scale
    layout = c.logo_layout
    want_icon = layout != "text_only"
    want_text = layout != "icon_only" and bool(c.logo_text.strip())
    icon = _icon(cfg, log) if want_icon else None
    if icon is not None:    # extent of the icon (tile, else its coloured shapes)
        pts = [icon["tile"]] if icon["tile"] is not None else [o for l in icon["layers"] for o, _ in l["shapes"]]
        lo, hi = np.concatenate(pts).min(0), np.concatenate(pts).max(0)
        icon["w"], icon["h"], icon["mid"] = float(hi[0] - lo[0]), float(hi[1] - lo[1]), (lo + hi) / 2

    text = None
    if want_text:
        font = S.find_font(st.logo_font)
        sh, _adv = S.text_shapes(c.logo_text, font, EM_PX, st.logo_spacing, st.logo_text_weight)
        if not sh:
            raise ValueError("The text produced no outlines (does the font contain these characters?).")
        mm = c.logo_text_size * k / EM_PX
        sh = _shape_map(sh, lambda P: P * mm)
        lo, hi = S.bbox(sh)
        mid = (lo + hi) / 2
        text = dict(shapes=_shape_map(sh, lambda P: P - mid), w=float(hi[0] - lo[0]), h=float(hi[1] - lo[1]))

    gap = c.logo_gap * k
    ipos = tpos = np.zeros(2)
    if icon and text:
        if layout in ("stacked", "stacked_reverse"):
            sign = -1 if layout == "stacked" else 1
            tpos = np.array([0.0, sign * (icon["h"] / 2 + gap + text["h"] / 2)])
        else:
            sign = 1 if layout == "icon_left" else -1
            tpos = np.array([sign * (icon["w"] / 2 + gap + text["w"] / 2), 0.0])
    if icon:
        ipos = ipos - icon["mid"]           # centre the icon's own extent on its anchor
    pts = []
    if icon:
        pts.append(np.array([icon["mid"] * 0 - np.array([icon["w"], icon["h"]]) / 2,
                             np.array([icon["w"], icon["h"]]) / 2]).reshape(-1, 2) + (ipos + icon["mid"]))
    if text:
        pts.append(np.array([-0.5 * np.array([text["w"], text["h"]]), 0.5 * np.array([text["w"], text["h"]])]) + tpos)
    allp = np.concatenate(pts)
    centre = (allp.min(0) + allp.max(0)) / 2
    ipos, tpos = ipos - centre, tpos - centre

    # +0.01 deg: glyph baselines/stems sit on exact pixel rows, so vertices of different letters are
    # exactly collinear, which makes the triangulator leave T-junctions. A 2 micron twist avoids that.
    th = math.radians(c.logo_rotation + 0.01)
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    mir = np.array([-1.0 if c.logo_mirror == "horizontal" else 1.0, -1.0 if c.logo_mirror == "vertical" else 1.0])
    cx, cy = area.xs[-1] / 2 + c.logo_x, area.ys[-1] / 2 + c.logo_y

    def place(P, origin, shrink):
        return ((P + origin) * mir) @ R.T * shrink + np.array([cx, cy])
    return icon, text, ipos, tpos, place


def _fits(rings, outline):
    for i in range(len(outline)):
        a, b = outline[i], outline[(i + 1) % len(outline)]
        for R in rings:
            if (((b[0] - a[0]) * (R[:, 1] - a[1]) - (b[1] - a[1]) * (R[:, 0] - a[0])) < 0).any():
                return False
    return True


def _tile_outline(area):
    """Exact outline of the printed base (matches the terrain's staircase edge for round shapes)."""
    W, H = area.xs[-1], area.ys[-1]
    if area.mask is None:
        return np.array([[0, 0], [W, 0], [W, H], [0, H]], float)
    loops = S.trace_mask(area.mask)
    big = max(loops, key=lambda L: abs(S.shoelace(L)))
    from ..meshing import clean_polygon
    return clean_polygon(np.stack([area.xs[big[:, 0].astype(int)], area.ys[big[:, 1].astype(int)]], 1))


@register
class LogoFeature(Feature):
    name, label, order = "logo", "Logo", 60

    def enabled(self, cfg):
        return cfg.logo.logo

    def prepare(self, ctx):
        cfg, a = ctx.cfg, ctx.area
        c = cfg.logo
        depth = min(c.logo_depth, cfg.area.base - 0.6)
        if depth < c.logo_depth - 1e-9 and depth >= 0.2:
            ctx.log(f"Logo depth reduced to {depth:.1f} mm (the base is only {cfg.area.base:.1f} mm thick)")
        if depth < 0.2:
            raise ValueError("The base is too thin for a logo; raise Base thickness or lower the logo depth.")
        ctx.base_lift = depth
        icon, text, ipos, tpos, place = _layout(cfg, a, ctx.log)
        rings = []          # (ring, origin) of everything that must fit on the base
        if icon:
            if icon["tile"] is not None:
                rings.append((icon["tile"], ipos))
            else:
                rings += [(o, ipos) for l in icon["layers"] for o, _ in l["shapes"]]
        if text:
            rings += [(o, tpos) for o, _ in text["shapes"]]
        if not rings:
            raise ValueError("The logo is empty: enable text or an icon.")
        margin = 2.0
        inset = a.outline(inset=margin + (1.5 * (a.xs[1] - a.xs[0]) if a.mask is not None else 0))
        shrink = 1.0
        while True:
            probe = [place(R, o, shrink) for R, o in rings]
            if _fits(probe, inset) or shrink < 0.15:
                break
            shrink *= 0.97
        if shrink < 0.15:
            raise ValueError("The logo does not fit on the base; reduce its size or offsets.")
        if shrink < 1.0:
            ctx.log(f"Logo scaled to {shrink * 100:.0f}% to fit the base")
        ctx.logo = dict(icon=icon, text=text, ipos=ipos, tpos=tpos, place=place, shrink=shrink, depth=depth)
        allp = np.concatenate(probe)
        w = allp.max(0) - allp.min(0)
        ctx.log(f"Logo: {w[0]:.0f} x {w[1]:.0f} mm on the underside, {depth:.1f} mm deep")

    def build(self, ctx):
        cfg, a = ctx.cfg, ctx.area
        st, L = cfg.logo_style, ctx.logo
        icon, text, ipos, tpos, place, sh, d = (L["icon"], L["text"], L["ipos"], L["tpos"], L["place"],
                                                L["shrink"], L["depth"])
        P = lambda arr, origin: place(arr, origin, sh)
        PS = lambda shapes, origin: [(P(o, origin), [P(h, origin) for h in hs]) for o, hs in shapes]

        layers = [dict(l, shapes=PS(l["shapes"], ipos)) for l in icon["layers"]] if icon else []   # icon art
        tile = P(icon["tile"], ipos) if icon and icon["tile"] is not None else None
        txt = PS(text["shapes"], tpos) if text else []
        art = [s_ for l in layers for s_ in l["shapes"]]            # every coloured icon shape

        # rings cut out of the plate, and the plate "islands" left inside letters / counters
        if cfg.logo.logo_mode == "engrave":
            holes = [o for o, _ in art] + [o for o, _ in txt]
            islands = [h for _, hs in art for h in hs] + [h for _, hs in txt for h in hs]
        elif tile is not None:
            holes, islands = [tile], []
        else:
            holes, islands = [o for o, _ in art], [h for _, hs in art for h in hs]
        if cfg.logo.logo_mode != "engrave":
            holes += [o for o, _ in txt]
            islands += [h for _, hs in txt for h in hs]

        plate = [extrude_shape(_tile_outline(a), holes, 0.0, d)]
        plate += [extrude_shape(i, [], 0.0, d) for i in islands]
        out = [("base_plate", "Base plate", cfg.terrain.terrain_color, np.concatenate(plate))]
        if cfg.logo.logo_mode == "engrave":
            return out

        if tile is not None:     # tile = the square minus everything drawn on top of it
            t = [extrude_shape(tile, [o for o, _ in art], 0.0, d)]
            t += [extrude_shape(h, [], 0.0, d) for _, hs in art for h in hs]    # tile showing through counters
            out.append(("logo_tile", "Logo tile", icon["tile_color"], np.concatenate(t)))
        if txt and not layers:
            layers = [dict(name="logo_mark", label="Logo text", color=st.logo_color, shapes=[])]
        if txt:                  # text shares the colour of the first icon layer
            layers[0] = dict(layers[0], shapes=layers[0]["shapes"] + txt)
        for l in layers:
            parts = [extrude_shape(o, hs, 0.0, d) for o, hs in l["shapes"]]
            if parts:
                out.append((l["name"], l["label"], l["color"], np.concatenate(parts)))
        return out
