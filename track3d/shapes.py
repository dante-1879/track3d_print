"""2D shape toolkit for the logo: trace bitmaps to outlines, simplify, nest holes, text -> outlines.
A *shape* is (outer, [holes]) with (n,2) float arrays; units are whatever the caller uses."""
from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np

ASSETS = Path(__file__).parent / "assets"


# ------------------------------------------------------------------ bitmap -> loops
def trace_mask(B):
    """Boundary loops of a boolean image. Returns a list of (n,2) float arrays in pixel-corner
    coordinates (x right, y down). Loops run clockwise on screen around foreground, so outer
    boundaries have positive shoelace area and holes negative."""
    B = np.pad(np.asarray(B, bool), 1)
    fg = B[1:-1, 1:-1]
    ii, jj = np.nonzero(fg)
    up, down = ~B[:-2, 1:-1][ii, jj], ~B[2:, 1:-1][ii, jj]
    left, right = ~B[1:-1, :-2][ii, jj], ~B[1:-1, 2:][ii, jj]
    edges = {}

    def add(mask, x0, y0, x1, y1):
        for a, b, c, d in zip(x0[mask].tolist(), y0[mask].tolist(), x1[mask].tolist(), y1[mask].tolist()):
            edges.setdefault((a, b), []).append((c, d))
    j, i = jj, ii
    add(up, j, i, j + 1, i)                 # top edge, moving right
    add(right, j + 1, i, j + 1, i + 1)      # right edge, moving down
    add(down, j + 1, i + 1, j, i + 1)       # bottom edge, moving left
    add(left, j, i + 1, j, i)               # left edge, moving up
    loops = []
    while edges:
        start = next(iter(edges))
        cur, prev_dir, pts = start, None, [start]
        while True:
            nxt = edges.get(cur)
            if not nxt:
                break
            if len(nxt) == 1 or prev_dir is None:
                e = nxt.pop(0)
            else:   # touching corners: hug the foreground (turn right) so shapes stay separate
                def turn(c):
                    d = (c[0] - cur[0], c[1] - cur[1])
                    return prev_dir[0] * d[1] - prev_dir[1] * d[0]
                e = max(nxt, key=turn)
                nxt.remove(e)
            if not nxt:
                del edges[cur]
            prev_dir = (e[0] - cur[0], e[1] - cur[1])
            cur = e
            if cur == start:
                break
            pts.append(cur)
        if len(pts) >= 3:
            loops.append(np.array(pts, float))
    return loops


def shoelace(P):
    x, y = P[:, 0], P[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def rdp(P, eps):
    """Douglas-Peucker on a closed loop."""
    n = len(P)
    if n < 8:
        return P
    far = int(np.argmax(np.hypot(*(P - P[0]).T)))
    keep = np.zeros(n, bool)
    keep[[0, far]] = True

    def run(lo, hi, pts, idx):
        stack = [(0, len(pts) - 1)]
        while stack:
            a, b = stack.pop()
            if b <= a + 1:
                continue
            seg = pts[b] - pts[a]
            L = math.hypot(*seg)
            d = np.abs(seg[0] * (pts[a + 1:b, 1] - pts[a, 1]) - seg[1] * (pts[a + 1:b, 0] - pts[a, 0])) / L \
                if L > 1e-12 else np.hypot(*(pts[a + 1:b] - pts[a]).T)
            k = int(np.argmax(d))
            if d[k] > eps:
                keep[idx[a + 1 + k]] = True
                stack += [(a, a + 1 + k), (a + 1 + k, b)]
    idx1 = np.arange(0, far + 1)
    idx2 = np.concatenate([np.arange(far, n), [0]])
    run(0, 0, P[idx1], idx1)
    run(0, 0, P[idx2], idx2)
    return P[keep]


def nest(loops):
    """Loops (outer = positive area, hole = negative) -> list of shapes (outer, [holes])."""
    outers = [(abs(shoelace(L)), L) for L in loops if shoelace(L) > 0]
    holes = [L for L in loops if shoelace(L) < 0]
    outers.sort(key=lambda t: t[0])
    shapes = [[o, []] for _, o in outers]

    def inside(p, poly):
        x, y = p
        c = False
        xs, ys = poly[:, 0], poly[:, 1]
        xj, yj = np.roll(xs, 1), np.roll(ys, 1)
        cond = ((ys > y) != (yj > y)) & (x < (xj - xs) * (y - ys) / (yj - ys + 1e-30) + xs)
        return bool(cond.sum() % 2)
    for h in holes:
        for sh in shapes:      # smallest enclosing outer first
            if inside(h[0], sh[0]):
                sh[1].append(h)
                break
    return [(o, hs) for o, hs in shapes]


def mask_to_shapes(B, eps=0.7):
    loops = [rdp(L, eps) for L in trace_mask(B)]
    return nest([L for L in loops if len(L) >= 3 and abs(shoelace(L)) > 4])


# ------------------------------------------------------------------ primitive shapes
def circle(cx, cy, r, n=48):
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.stack([cx + r * np.cos(a), cy + r * np.sin(a)], 1)


def rounded_rect(cx, cy, w, h, r, n=10):
    r = min(r, w / 2, h / 2)
    out = []
    for (ox, oy, a0) in ((1, 1, 0), (-1, 1, 90), (-1, -1, 180), (1, -1, 270)):
        a = np.radians(a0 + np.linspace(0, 90, n))
        out.append(np.stack([cx + ox * (w / 2 - r) + r * np.cos(a), cy + oy * (h / 2 - r) + r * np.sin(a)], 1))
    return np.concatenate(out)


# ------------------------------------------------------------------ text & glyphs
def find_font(path=""):
    if path:
        if not os.path.exists(path):
            raise ValueError(f"Logo font not found: {path}")
        return path
    return str(ASSETS / "LiberationSerif-Regular.ttf")


CJK_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "C:/Windows/Fonts/yumin.ttf", "C:/Windows/Fonts/msmincho.ttc", "C:/Windows/Fonts/msyh.ttc",
    "/System/Library/Fonts/ヒラギノ明朝 ProN.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]


def text_shapes(text, font_path, em_px=400, spacing=0.0, weight=0.0, eps=0.9):
    """Render text with Pillow and trace it. Returns (shapes, advance) in pixel units at em_px
    (x right, y UP, origin at the left end of the baseline)."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(font_path, em_px)
    pad = int(em_px * 0.4)
    xs = [font.getlength(text[:i]) + i * spacing * em_px for i in range(len(text))]
    total = font.getlength(text) + max(len(text) - 1, 0) * spacing * em_px
    W, H = int(total) + 2 * pad, int(em_px * 1.8) + 2 * pad
    base = pad + int(em_px * 1.1)
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)
    sw = int(round(weight * em_px))
    for ch, x in zip(text, xs):
        if ch.strip():
            d.text((pad + x, base), ch, font=font, fill=255, anchor="ls", stroke_width=sw, stroke_fill=255)
    shapes = mask_to_shapes(np.asarray(img) > 127, eps)
    flip = lambda P: np.stack([P[:, 0] - pad, base - P[:, 1]], 1)   # to y-up, origin at baseline start
    return [(flip(o), [flip(h) for h in hs]) for o, hs in shapes], total


def load_builtin_mark():
    """The traced 化 glyph: shapes in a unit box (height 1, centred, y up)."""
    import json
    data = json.loads((ASSETS / "mark_ka.json").read_text())
    return [(np.array(o), [np.array(h) for h in hs]) for o, hs in data]


def bbox(shapes):
    pts = np.concatenate([o for o, _ in shapes])
    return pts.min(0), pts.max(0)
