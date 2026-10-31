"""Pure geometry: arrays in, triangle arrays (N,3,3) out. No I/O, no settings."""
import math

import numpy as np

from .geo import bilerp, resample_polyline  # noqa: F401  (re-exported for features)


def terrain_mesh(P, mask=None, bottom=0.0):
    """P: (ny, nx, 3) top-surface vertices. mask: (ny-1, nx-1) bool of cells to keep (None = all).
    Returns a closed solid with a flat base at z=bottom."""
    ny, nx, _ = P.shape
    if mask is None:
        t1 = np.stack([P[:-1, :-1], P[:-1, 1:], P[1:, 1:]], axis=2).reshape(-1, 3, 3)
        t2 = np.stack([P[:-1, :-1], P[1:, 1:], P[1:, :-1]], axis=2).reshape(-1, 3, 3)
        ring = np.concatenate([P[0, :-1], P[:-1, -1], P[-1, :0:-1], P[:0:-1, 0]])  # CCW from above
        bot = ring.copy()
        bot[:, 2] = bottom
        nxt = np.roll(np.arange(len(ring)), -1)
        w1 = np.stack([ring, bot, bot[nxt]], axis=1)
        w2 = np.stack([ring, bot[nxt], ring[nxt]], axis=1)
        c = np.array([P[..., 0].mean(), P[..., 1].mean(), bottom])
        fan = np.stack([np.broadcast_to(c, bot.shape), bot[nxt], bot], axis=1)
        return np.concatenate([t1, t2, w1, w2, fan])

    i, j = np.nonzero(mask)
    p00, p01, p10, p11 = P[i, j], P[i, j + 1], P[i + 1, j], P[i + 1, j + 1]

    def flat(p):
        q = p.copy()
        q[:, 2] = bottom
        return q
    b00, b01, b10, b11 = flat(p00), flat(p01), flat(p10), flat(p11)
    parts = [np.stack([p00, p01, p11], 1), np.stack([p00, p11, p10], 1),   # top
             np.stack([b00, b11, b01], 1), np.stack([b00, b10, b11], 1)]   # bottom
    M = np.pad(mask, 1)
    for open_side, a, b in ((~M[i, j + 1], p00, p01),        # south edge, runs +x
                            (~M[i + 1, j + 2], p01, p11),    # east edge, runs +y
                            (~M[i + 2, j + 1], p11, p10),    # north edge, runs -x
                            (~M[i + 1, j], p10, p00)):       # west edge, runs -y
        a, b = a[open_side], b[open_side]
        a0, b0 = flat(a), flat(b)
        parts += [np.stack([a, a0, b0], 1), np.stack([a, b0, b], 1)]
    return np.concatenate(parts)


def ribbon_mesh(C, width, height, embed):
    """C: (n,3) centre-line on the terrain surface. Closed rectangular-section ribbon."""
    t = np.gradient(C[:, :2], axis=0)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-12)
    nrm = np.stack([-t[:, 1], t[:, 0]], axis=1) * (width / 2)
    zt, zb = C[:, 2] + height, C[:, 2] - embed
    L, R = C[:, :2] + nrm, C[:, :2] - nrm
    LT = np.column_stack([L, zt]); RT = np.column_stack([R, zt])
    LB = np.column_stack([L, zb]); RB = np.column_stack([R, zb])
    a, b = slice(0, -1), slice(1, None)
    tris = [
        np.stack([LT[a], RT[a], RT[b]], 1), np.stack([LT[a], RT[b], LT[b]], 1),   # top
        np.stack([LB[a], RB[b], RB[a]], 1), np.stack([LB[a], LB[b], RB[b]], 1),   # bottom
        np.stack([LT[a], LB[b], LB[a]], 1), np.stack([LT[a], LT[b], LB[b]], 1),   # left
        np.stack([RT[a], RB[a], RB[b]], 1), np.stack([RT[a], RB[b], RT[b]], 1),   # right
        np.stack([LT[:1], LB[:1], RB[:1]], 1), np.stack([LT[:1], RB[:1], RT[:1]], 1),  # start cap
        np.stack([LT[-1:], RB[-1:], LB[-1:]], 1), np.stack([LT[-1:], RT[-1:], RB[-1:]], 1),  # end cap
    ]
    return np.concatenate(tris)


def prisms_mesh(Cn, zb, zt):
    """Boxes from 4 CCW base corners Cn (N,4,2) and bottom/top heights (N,)."""
    def P(i, z):
        return np.column_stack([Cn[:, i, 0], Cn[:, i, 1], z])
    b0, b1, b2, b3 = P(0, zb), P(1, zb), P(2, zb), P(3, zb)
    t0, t1, t2, t3 = P(0, zt), P(1, zt), P(2, zt), P(3, zt)
    faces = [(t0, t1, t2), (t0, t2, t3), (b0, b2, b1), (b0, b3, b2),
             (b0, b1, t1), (b0, t1, t0), (b1, b2, t2), (b1, t2, t1),
             (b2, b3, t3), (b2, t3, t2), (b3, b0, t0), (b3, t0, t3)]
    return np.concatenate([np.stack(f, axis=1) for f in faces])


def boxes_mesh(x0, y0, x1, y1, zb, zt):
    """Axis-aligned boxes (vectorised). All args shape (N,)."""
    def P(x, y, z):
        return np.stack([x, y, z], axis=-1)
    b000, b100, b110, b010 = P(x0, y0, zb), P(x1, y0, zb), P(x1, y1, zb), P(x0, y1, zb)
    t000, t100, t110, t010 = P(x0, y0, zt), P(x1, y0, zt), P(x1, y1, zt), P(x0, y1, zt)
    faces = [(t000, t100, t110), (t000, t110, t010), (b000, b110, b100), (b000, b010, b110),
             (b000, b100, t100), (b000, t100, t000), (b100, b110, t110), (b100, t110, t100),
             (b110, b010, t010), (b110, t010, t110), (b010, b000, t000), (b010, t000, t010)]
    return np.concatenate([np.stack(f, axis=1) for f in faces])


def min_rect(xy):
    """Minimum-area oriented rectangle of 2D points -> (centre, half_extents, angle) or None."""
    pts = sorted(set(map(tuple, np.round(xy, 4))))
    if len(pts) < 3:
        return None

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    H = np.array(lower[:-1] + upper[:-1])
    if len(H) < 3:
        return None
    e = np.roll(H, -1, axis=0) - H
    best = None
    for th in np.arctan2(e[:, 1], e[:, 0]):
        c, s_ = math.cos(th), math.sin(th)
        r = np.stack([c * H[:, 0] + s_ * H[:, 1], -s_ * H[:, 0] + c * H[:, 1]], axis=1)
        mn, mx = r.min(0), r.max(0)
        area = float(np.prod(mx - mn))
        if best is None or area < best[0]:
            best = (area, th, mn, mx)
    _, th, mn, mx = best
    c, s_ = math.cos(th), math.sin(th)
    cr = (mn + mx) / 2
    ctr = np.array([cr[0] * c - cr[1] * s_, cr[0] * s_ + cr[1] * c])
    return ctr, (mx - mn) / 2, th


def carve_groove(Zmm, xs, ys, C, half_w, depth):
    """Lower the terrain grid along the path centre-line C (n,3): a groove of half-width
    half_w and the given depth below the path's own height."""
    dx, dy = xs[1] - xs[0], ys[1] - ys[0]
    ny, nx = Zmm.shape
    rx, ry = int(math.ceil(half_w / dx)) + 1, int(math.ceil(half_w / dy)) + 1
    cand = np.zeros((ny, nx), bool)
    gx, gy = np.rint(C[:, 0] / dx).astype(int), np.rint(C[:, 1] / dy).astype(int)
    for ox in range(-rx, rx + 1):
        for oy in range(-ry, ry + 1):
            cx, cy = gx + ox, gy + oy
            v = (cx >= 0) & (cx < nx) & (cy >= 0) & (cy < ny)
            cand[cy[v], cx[v]] = True
    jy, jx = np.nonzero(cand)
    px, py = xs[jx], ys[jy]
    best_d = np.full(len(jx), np.inf)
    best_z = np.zeros(len(jx))
    rows = np.arange(len(jx))
    for i in range(0, len(C), 50):
        ch = C[i:i + 50]
        d = np.hypot(px[:, None] - ch[None, :, 0], py[:, None] - ch[None, :, 1])
        k = d.argmin(axis=1)
        dm = d[rows, k]
        upd = dm < best_d
        best_d[upd] = dm[upd]
        best_z[upd] = ch[k[upd], 2]
    sel = best_d <= half_w
    Zc = Zmm.copy()
    Zc[jy[sel], jx[sel]] = np.minimum(best_z[sel] - depth, Zmm[jy[sel], jx[sel]])
    return Zc


# ------------------------------------------------------------- polygon extrusion
def signed_area(P):
    x, y = P[:, 0], P[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def clean_polygon(P, eps=1e-4):
    """Drop the closing vertex, duplicates and collinear points. Returns (n,2) or None."""
    P = np.asarray(P, float)
    if len(P) > 1 and np.hypot(*(P[0] - P[-1])) < eps:
        P = P[:-1]
    for _ in range(2):
        if len(P) < 3:
            return None
        keep = np.hypot(*(P - np.roll(P, 1, axis=0)).T) > eps
        P = P[keep]
        if len(P) < 3:
            return None
        a, b, c = np.roll(P, 1, axis=0), P, np.roll(P, -1, axis=0)
        cr = np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - b[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - b[:, 0]))
        P = P[cr > 1e-6]
    return P if len(P) >= 3 else None


def offset_polygon(P, d):
    """Move a CCW polygon outwards by d (mitred; fine for the small distances used here)."""
    if d == 0:
        return P

    def nrm(e):
        return np.stack([e[:, 1], -e[:, 0]], 1) / np.maximum(np.hypot(e[:, 0], e[:, 1]), 1e-12)[:, None]
    n1, n2 = nrm(P - np.roll(P, 1, axis=0)), nrm(np.roll(P, -1, axis=0) - P)
    den = np.maximum(1.0 + (n1 * n2).sum(1), 0.5)
    return P + d * (n1 + n2) / den[:, None]


def clip_convex(poly, clip):
    """Sutherland-Hodgman: clip polygon to a convex CCW polygon. Returns (n,2) or None."""
    out = [tuple(p) for p in poly]
    for i in range(len(clip)):
        a, b = clip[i], clip[(i + 1) % len(clip)]
        if not out:
            break

        def side(p):
            return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])

        def cut(p, q):
            sp, sq = side(p), side(q)
            t = sp / (sp - sq)
            return (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]))
        inp, out, prev = out, [], out[-1]
        for cur in inp:
            if side(cur) >= 0:
                if side(prev) < 0:
                    out.append(cut(prev, cur))
                out.append(cur)
            elif side(prev) >= 0:
                out.append(cut(prev, cur))
            prev = cur
    return np.array(out) if len(out) >= 3 else None


def triangulate(P):
    """Ear clipping of a simple CCW polygon -> (m,3) vertex indices, or None if it fails."""
    pts = [(float(x), float(y)) for x, y in P]
    idx = list(range(len(pts)))
    tris = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    while len(idx) > 3:
        n = len(idx)
        for k in range(n):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % n]
            p0, p1, p2 = pts[i0], pts[i1], pts[i2]
            if cross(p0, p1, p2) <= 1e-12:
                continue
            ok = True
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                q = pts[j]
                if cross(p0, p1, q) >= -1e-12 and cross(p1, p2, q) >= -1e-12 and cross(p2, p0, q) >= -1e-12:
                    ok = False
                    break
            if ok:
                tris.append((i0, i1, i2))
                idx.pop(k)
                break
        else:
            return None
    tris.append(tuple(idx))
    return np.array(tris)


def extrude_polygon(P, T, zb, zt):
    """Prism from CCW polygon P (n,2), triangulation T, flat bottom zb and flat top zt."""
    n = len(P)
    top = np.column_stack([P, np.full(n, zt)])
    bot = np.column_stack([P, np.full(n, zb)])
    nxt = np.roll(np.arange(n), -1)
    return np.concatenate([top[T], bot[T][:, ::-1],
                           np.stack([top, bot, bot[nxt]], 1), np.stack([top, bot[nxt], top[nxt]], 1)])


def extrude_shape(outer, holes, zb, zt):
    """Prism of a polygon with holes (earcut triangulation). Rings are re-oriented so the
    material is always on the left: outer CCW, holes CW."""
    try:
        import mapbox_earcut as earcut
    except ImportError:
        raise ValueError("The logo needs the 'mapbox-earcut' package: pip install mapbox-earcut") from None

    def orient(R, ccw):
        return R if (signed_area(R) > 0) == ccw else R[::-1]
    def ring(R, ccw):
        R = clean_polygon(np.asarray(R, float))      # no duplicate or collinear points
        return None if R is None else orient(R, ccw)
    outer = ring(outer, True)
    holes = [h for h in (ring(h, False) for h in holes) if h is not None]
    if outer is None:
        return np.zeros((0, 3, 3))
    rings = [outer] + holes
    V = np.concatenate(rings)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    T = V[earcut.triangulate_float64(V, ends).reshape(-1, 3)]
    flip = ((T[:, 1, 0] - T[:, 0, 0]) * (T[:, 2, 1] - T[:, 0, 1])
            - (T[:, 1, 1] - T[:, 0, 1]) * (T[:, 2, 0] - T[:, 0, 0])) < 0
    T[flip] = T[flip][:, ::-1]

    def z(A, h):
        return np.concatenate([A, np.full(A.shape[:-1] + (1,), h)], axis=-1)
    parts = [z(T, zt), z(T, zb)[:, ::-1]]
    for R in rings:
        nx_ = np.roll(R, -1, axis=0)
        at, ab, bt, bb = z(R, zt), z(R, zb), z(nx_, zt), z(nx_, zb)
        parts += [np.stack([at, ab, bb], 1), np.stack([at, bb, bt], 1)]
    return np.concatenate(parts)
