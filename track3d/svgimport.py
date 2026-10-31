"""Minimal SVG importer for logo artwork: filled paths and basic shapes -> one layer per fill colour.

Supported: path (M L H V C S Q T A Z, absolute and relative), rect, circle, ellipse, polygon, polyline,
nested <g>, transform (matrix/translate/scale/rotate/skew), fill as attribute or style.
Not supported (ignored, with a warning): strokes, gradients (grey fallback), clip paths, masks, <use>, text.
Each colour layer is rasterised and re-traced, so overlapping pieces merge into clean outlines."""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np

from . import shapes as S

NAMED = {"black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000", "blue": "#0000ff",
         "yellow": "#ffff00", "orange": "#ffa500", "gray": "#808080", "grey": "#808080", "silver": "#c0c0c0",
         "purple": "#800080", "pink": "#ffc0cb", "brown": "#a52a2a", "gold": "#ffd700", "navy": "#000080",
         "teal": "#008080", "cyan": "#00ffff", "magenta": "#ff00ff", "currentcolor": "#000000"}
_NUM = re.compile(r"[\s,]*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")
_FLAG = re.compile(r"[\s,]*([01])")
_CMD = re.compile(r"[\s,]*([MmLlHhVvCcSsQqTtAaZz])")
RASTER = 2048


@dataclass
class Layer:
    color: str
    shapes: list


@dataclass
class Artwork:
    w: float                      # viewBox width in unit-box coordinates (always 1.0)
    h: float                      # viewBox height / width
    layers: list
    warnings: list = field(default_factory=list)


# ------------------------------------------------------------------ colours / transforms
def parse_color(v):
    v = (v or "").strip().lower()
    if v in ("", "none", "transparent"):
        return None
    if v in NAMED:
        return NAMED[v]
    m = re.fullmatch(r"#([0-9a-f]{3,8})", v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h)
        return "#" + h[:6]
    m = re.fullmatch(r"rgba?\(([^)]*)\)", v)
    if m:
        p = [q.strip() for q in m.group(1).split(",")][:3]
        vals = [int(round(float(q[:-1]) * 2.55)) if q.endswith("%") else int(float(q)) for q in p]
        return "#%02x%02x%02x" % tuple(max(0, min(255, x)) for x in vals)
    return "?"


def parse_transform(s):
    M = np.eye(3)
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s or ""):
        a = [float(x) for x in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", args)]
        T = np.eye(3)
        if name == "matrix" and len(a) == 6:
            T = np.array([[a[0], a[2], a[4]], [a[1], a[3], a[5]], [0, 0, 1]])
        elif name == "translate":
            T[0, 2], T[1, 2] = a[0], (a[1] if len(a) > 1 else 0)
        elif name == "scale":
            T[0, 0], T[1, 1] = a[0], (a[1] if len(a) > 1 else a[0])
        elif name == "rotate":
            t = math.radians(a[0])
            R = np.array([[math.cos(t), -math.sin(t), 0], [math.sin(t), math.cos(t), 0], [0, 0, 1]])
            if len(a) == 3:
                T = np.array([[1, 0, a[1]], [0, 1, a[2]], [0, 0, 1]]) @ R @ np.array([[1, 0, -a[1]], [0, 1, -a[2]], [0, 0, 1]])
            else:
                T = R
        elif name == "skewX":
            T[0, 1] = math.tan(math.radians(a[0]))
        elif name == "skewY":
            T[1, 0] = math.tan(math.radians(a[0]))
        M = M @ T
    return M


# ------------------------------------------------------------------ path data
def _arc(p0, rx, ry, phi, fa, fs, p1, steps=24):
    if rx == 0 or ry == 0 or np.allclose(p0, p1):
        return [p1]
    rx, ry = abs(rx), abs(ry)
    c, s = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1, y1 = c * dx + s * dy, -s * dx + c * dy
    lam = x1 ** 2 / rx ** 2 + y1 ** 2 / ry ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1 ** 2 - ry ** 2 * x1 ** 2
    den = rx ** 2 * y1 ** 2 + ry ** 2 * x1 ** 2
    co = math.sqrt(max(num / den, 0)) * (-1 if fa == fs else 1)
    cxp, cyp = co * rx * y1 / ry, -co * ry * x1 / rx
    cx, cy = c * cxp - s * cyp + (p0[0] + p1[0]) / 2, s * cxp + c * cyp + (p0[1] + p1[1]) / 2
    ang = lambda ux, uy, vx, vy: math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
    t1 = ang(1, 0, (x1 - cxp) / rx, (y1 - cyp) / ry)
    dt = ang((x1 - cxp) / rx, (y1 - cyp) / ry, (-x1 - cxp) / rx, (-y1 - cyp) / ry)
    if not fs and dt > 0:
        dt -= 2 * math.pi
    elif fs and dt < 0:
        dt += 2 * math.pi
    n = max(int(abs(dt) / (2 * math.pi) * steps * 2), 4)
    out = []
    for i in range(1, n + 1):
        t = t1 + dt * i / n
        px, py = rx * math.cos(t), ry * math.sin(t)
        out.append((c * px - s * py + cx, s * px + c * py + cy))
    out[-1] = tuple(p1)
    return out


def parse_path(d):
    """-> list of (points (n,2), closed) in path coordinates."""
    i, n = 0, len(d)
    subs, cur = [], []
    x = y = sx = sy = 0.0
    cmd, lastc, lastq = None, None, None
    counts = dict(M=2, L=2, H=1, V=1, C=6, S=4, Q=4, T=2, A=7, Z=0)

    def read(k):
        nonlocal i
        vals = []
        for _ in range(k):
            m = _NUM.match(d, i)
            if not m:
                return None
            vals.append(float(m.group(1)))
            i = m.end()
        return vals
    while i < n:
        m = _CMD.match(d, i)
        if m:
            cmd, i = m.group(1), m.end()
        elif cmd is None:
            break
        elif cmd in "Zz":
            break
        rel = cmd.islower()
        C = cmd.upper()
        if C == "Z":
            if cur:
                subs.append((np.array(cur), True))
            cur, x, y = [], sx, sy
            lastc = lastq = None
            continue
        if C == "A":
            a = read(3)
            if a is None:
                break
            fa, fs = _FLAG.match(d, i), None
            if not fa:
                break
            i = fa.end(); fs = _FLAG.match(d, i)
            if not fs:
                break
            i = fs.end(); e = read(2)
            if e is None:
                break
            ex, ey = (x + e[0], y + e[1]) if rel else (e[0], e[1])
            cur += _arc((x, y), a[0], a[1], a[2], int(fa.group(1)), int(fs.group(1)), (ex, ey))
            x, y = ex, ey
            lastc = lastq = None
            continue
        v = read(counts[C])
        if v is None:
            break
        if C == "M":
            if cur:
                subs.append((np.array(cur), False))
            x, y = (x + v[0], y + v[1]) if rel else (v[0], v[1])
            sx, sy, cur = x, y, [(x, y)]
            cmd = "l" if rel else "L"
            lastc = lastq = None
        elif C == "L":
            x, y = (x + v[0], y + v[1]) if rel else (v[0], v[1]); cur.append((x, y)); lastc = lastq = None
        elif C == "H":
            x = x + v[0] if rel else v[0]; cur.append((x, y)); lastc = lastq = None
        elif C == "V":
            y = y + v[0] if rel else v[0]; cur.append((x, y)); lastc = lastq = None
        elif C in "CS":
            if C == "C":
                p1 = (x + v[0], y + v[1]) if rel else (v[0], v[1]); k = 2
            else:
                p1 = (2 * x - lastc[0], 2 * y - lastc[1]) if lastc else (x, y); k = 0
            p2 = (x + v[k], y + v[k + 1]) if rel else (v[k], v[k + 1])
            p3 = (x + v[k + 2], y + v[k + 3]) if rel else (v[k + 2], v[k + 3])
            t = np.linspace(0, 1, 21)[1:, None]
            P0, P1, P2, P3 = map(np.array, ((x, y), p1, p2, p3))
            pts = (1 - t) ** 3 * P0 + 3 * (1 - t) ** 2 * t * P1 + 3 * (1 - t) * t ** 2 * P2 + t ** 3 * P3
            cur += [tuple(p) for p in pts]
            x, y, lastc, lastq = p3[0], p3[1], p2, None
        elif C in "QT":
            if C == "Q":
                q = (x + v[0], y + v[1]) if rel else (v[0], v[1]); k = 2
            else:
                q = (2 * x - lastq[0], 2 * y - lastq[1]) if lastq else (x, y); k = 0
            p2 = (x + v[k], y + v[k + 1]) if rel else (v[k], v[k + 1])
            t = np.linspace(0, 1, 17)[1:, None]
            P0, Q, P2 = map(np.array, ((x, y), q, p2))
            cur += [tuple(p) for p in (1 - t) ** 2 * P0 + 2 * (1 - t) * t * Q + t ** 2 * P2]
            x, y, lastq, lastc = p2[0], p2[1], q, None
    if cur:
        subs.append((np.array(cur), False))
    return subs


# ------------------------------------------------------------------ elements
def _rings_of(el, tag):
    g = el.attrib.get
    f = lambda k, dflt=0.0: float(re.sub(r"[a-z%]+$", "", g(k, str(dflt)).strip()) or dflt)
    if tag == "path":
        return [p for p, _ in parse_path(g("d", ""))]
    if tag == "rect":
        x, y, w, h = f("x"), f("y"), f("width"), f("height")
        rx = f("rx", g("ry", 0)) if g("rx") or g("ry") else 0.0
        return [S.rounded_rect(x + w / 2, y + h / 2, w, h, rx)] if rx else [np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])]
    if tag == "circle":
        return [S.circle(f("cx"), f("cy"), f("r"), 96)]
    if tag == "ellipse":
        a = np.linspace(0, 2 * np.pi, 96, endpoint=False)
        return [np.stack([f("cx") + f("rx") * np.cos(a), f("cy") + f("ry") * np.sin(a)], 1)]
    if tag in ("polygon", "polyline"):
        v = [float(q) for q in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", g("points", ""))]
        return [np.array(v[: len(v) // 2 * 2]).reshape(-1, 2)] if len(v) >= 6 else []
    return []


def _style(el):
    out = {}
    for part in (el.attrib.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _walk(el, M, fill, items, warns):
    tag = el.tag.split("}")[-1]
    if tag in ("defs", "clipPath", "mask", "symbol", "metadata", "title", "desc", "style"):
        return
    st = _style(el)
    M = M @ parse_transform(el.attrib.get("transform"))
    fill = st.get("fill", el.attrib.get("fill", fill))
    if tag in ("use", "text", "image"):
        warns.append(f"<{tag}> is not supported and was skipped")
    rings = _rings_of(el, tag)
    if rings:
        col = parse_color(fill)
        if col is None:
            if st.get("stroke", el.attrib.get("stroke", "none")) not in ("none", ""):
                warns.append("stroke-only shapes are skipped (convert strokes to outlines in your editor)")
        else:
            if col == "?":
                warns.append(f"unsupported fill {fill!r}, using grey")
                col = "#808080"
            if st.get("fill-opacity", el.attrib.get("fill-opacity", "1")) in ("0", "0.0"):
                return
            pts = [(np.c_[r, np.ones(len(r))] @ M.T)[:, :2] for r in rings]
            items.append((col, pts))
    for ch in el:
        _walk(ch, M, fill, items, warns)


def _depth_fill(img_draw, rings_px):
    """Even-odd fill of one element's sub-paths (outer first, holes cleared)."""
    def inside(p, poly):
        x, y = p
        xs, ys = poly[:, 0], poly[:, 1]
        xj, yj = np.roll(xs, 1), np.roll(ys, 1)
        return bool((((ys > y) != (yj > y)) & (x < (xj - xs) * (y - ys) / (yj - ys + 1e-30) + xs)).sum() % 2)
    depth = [sum(inside(r[0], o) for j, o in enumerate(rings_px) if j != i and len(o) >= 3)
             for i, r in enumerate(rings_px)]
    for d, r in sorted(zip(depth, rings_px), key=lambda t: t[0]):
        if len(r) >= 3:
            img_draw.polygon([tuple(p) for p in r], fill=255 if d % 2 == 0 else 0)


def load_icon(text):
    """SVG text -> Artwork in a unit box (viewBox width = 1, centred, y up)."""
    from PIL import Image, ImageDraw
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise ValueError(f"The icon SVG could not be read: {e}") from None
    vb = [float(v) for v in re.findall(r"[-+]?\d*\.?\d+", root.attrib.get("viewBox", ""))]
    if len(vb) != 4:
        w = float(re.sub(r"[a-z%]+$", "", root.attrib.get("width", "100")))
        h = float(re.sub(r"[a-z%]+$", "", root.attrib.get("height", str(w))))
        vb = [0, 0, w, h]
    vx, vy, vw, vh = vb
    items, warns = [], []
    _walk(root, np.eye(3), root.attrib.get("fill", "black"), items, warns)
    if not items:
        raise ValueError("The icon SVG has no filled shapes. Convert strokes and text to outlines first.")
    P = RASTER / max(vw, vh)
    W, H = int(round(vw * P)), int(round(vh * P))
    masks, order = {}, []
    for col, rings in items:
        px = [np.stack([(r[:, 0] - vx) * P, (r[:, 1] - vy) * P], 1) for r in rings]
        img = Image.new("L", (W, H), 0)
        _depth_fill(ImageDraw.Draw(img), px)
        m = np.asarray(img) > 127
        if col in masks:
            masks[col] |= m
        else:
            masks[col] = m
            order.append(col)
    layers = []
    for col in order:
        sh = S.mask_to_shapes(masks[col], 1.5)
        unit = lambda Q: np.stack([(Q[:, 0] - W / 2) / (P * vw), -(Q[:, 1] - H / 2) / (P * vw)], 1)
        layers.append(Layer(col, [(unit(o), [unit(h) for h in hs]) for o, hs in sh]))
    return Artwork(1.0, vh / vw, layers, sorted(set(warns)))
