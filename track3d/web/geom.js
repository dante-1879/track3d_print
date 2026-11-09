/* Pure geometry for the editor. Mirrors track3d/scene.py + meshing.py so the preview you edit is the
   model that gets compiled. No DOM, no WebGL: works in the browser (window.T3D) and in Node. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.T3D = factory();
})(this, function () {
  "use strict";

  let triangulator = null;                      // (P:[[x,y]..]) -> [[a,b,c]..]; set by the page (earcut via three)
  const setTriangulator = fn => { triangulator = fn; };

  function b64ToFloat32(b64) {
    let bytes;
    if (typeof Buffer !== "undefined") bytes = Uint8Array.from(Buffer.from(b64, "base64"));
    else { const s = atob(b64); bytes = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) bytes[i] = s.charCodeAt(i); }
    return new Float32Array(bytes.buffer);
  }

  /* ---------------------------------------------------------------- terrain */
  class Terrain {
    constructor(nx, ny, dx, dy, z) { this.nx = nx; this.ny = ny; this.dx = dx; this.dy = dy; this.Z = z; }
    z(x, y) {                                   // bilinear, clamped: same as geo.bilerp
      const gx = Math.min(Math.max(x / this.dx, 0), this.nx - 1.000001);
      const gy = Math.min(Math.max(y / this.dy, 0), this.ny - 1.000001);
      const x0 = Math.floor(gx), y0 = Math.floor(gy), fx = gx - x0, fy = gy - y0, Z = this.Z, n = this.nx;
      return Z[y0 * n + x0] * (1 - fx) * (1 - fy) + Z[y0 * n + x0 + 1] * fx * (1 - fy)
           + Z[(y0 + 1) * n + x0] * (1 - fx) * fy + Z[(y0 + 1) * n + x0 + 1] * fx * fy;
    }
  }

  /* --------------------------------------------------------------- 2D helpers */
  function signedArea(P) {
    let s = 0;
    for (let i = 0; i < P.length; i++) { const a = P[i], b = P[(i + 1) % P.length]; s += a[0] * b[1] - b[0] * a[1]; }
    return s / 2;
  }

  function cleanPolygon(P, eps = 1e-4) {        // drop closing vertex, duplicates, collinear points
    P = P.map(p => [p[0], p[1]]);
    if (P.length > 1 && Math.hypot(P[0][0] - P[P.length - 1][0], P[0][1] - P[P.length - 1][1]) < eps) P.pop();
    for (let pass = 0; pass < 2; pass++) {
      if (P.length < 3) return null;
      let n = P.length;
      P = P.filter((p, i) => Math.hypot(p[0] - P[(i + n - 1) % n][0], p[1] - P[(i + n - 1) % n][1]) > eps);
      if (P.length < 3) return null;
      n = P.length;
      P = P.filter((b, i) => {
        const a = P[(i + n - 1) % n], c = P[(i + 1) % n];
        return Math.abs((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])) > 1e-6;
      });
    }
    return P.length >= 3 ? P : null;
  }

  function resamplePolyline(P, step) {          // geo.resample_polyline
    let Q = P.filter((p, i) => i === 0 || Math.hypot(p[0] - P[i - 1][0], p[1] - P[i - 1][1]) > 1e-9);
    if (Q.length < 2) return null;
    const s = [0];
    for (let i = 1; i < Q.length; i++) s.push(s[i - 1] + Math.hypot(Q[i][0] - Q[i - 1][0], Q[i][1] - Q[i - 1][1]));
    const n = Math.max(Math.floor(s[s.length - 1] / step), 2), out = [];
    let j = 0;
    for (let k = 0; k < n; k++) {
      const t = s[s.length - 1] * k / (n - 1);
      while (j < Q.length - 2 && s[j + 1] < t) j++;
      const f = s[j + 1] > s[j] ? Math.min(Math.max((t - s[j]) / (s[j + 1] - s[j]), 0), 1) : 0;
      out.push([Q[j][0] + (Q[j + 1][0] - Q[j][0]) * f, Q[j][1] + (Q[j + 1][1] - Q[j][1]) * f]);
    }
    return out;
  }

  function smooth5(z) {                         // smooth_1d(z, 5), edge padding
    if (z.length < 5) return z;
    const n = z.length, at = i => z[Math.min(Math.max(i, 0), n - 1)];
    return z.map((_, i) => (at(i - 2) + at(i - 1) + at(i) + at(i + 1) + at(i + 2)) / 5);
  }

  function rdp(pts, tol) {                      // open polyline Douglas-Peucker
    const n = pts.length;
    if (n < 3) return pts.map(p => p.slice());
    const keep = new Uint8Array(n); keep[0] = keep[n - 1] = 1;
    const stack = [[0, n - 1]];
    while (stack.length) {
      const [i, j] = stack.pop();
      if (j <= i + 1) continue;
      const a = pts[i], dx = pts[j][0] - a[0], dy = pts[j][1] - a[1], L = Math.hypot(dx, dy);
      let best = -1, bd = -1;
      for (let k = i + 1; k < j; k++) {
        const d = L < 1e-12 ? Math.hypot(pts[k][0] - a[0], pts[k][1] - a[1])
                            : Math.abs(dx * (pts[k][1] - a[1]) - dy * (pts[k][0] - a[0])) / L;
        if (d > bd) { bd = d; best = k; }
      }
      if (bd > tol) { keep[best] = 1; stack.push([i, best], [best, j]); }
    }
    return pts.filter((_, i) => keep[i]).map(p => p.slice());
  }

  function simplifyPolygon(P, tol) {            // split at the vertex farthest from vertex 0, simplify both halves
    if (P.length < 6) return P.map(p => p.slice());
    let far = 1, fd = -1;
    P.forEach((p, i) => { const d = Math.hypot(p[0] - P[0][0], p[1] - P[0][1]); if (d > fd) { fd = d; far = i; } });
    const h1 = rdp(P.slice(0, far + 1), tol), h2 = rdp(P.slice(far).concat([P[0]]), tol);
    const out = h1.slice(0, -1).concat(h2.slice(0, -1));
    return out.length >= 3 ? out : P.map(p => p.slice());
  }

  function segsCross(a, b, c, d) {              // proper crossing only (touching ends are ignored)
    const o = (p, q, r) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
    const d1 = o(c, d, a), d2 = o(c, d, b), d3 = o(a, b, c), d4 = o(a, b, d);
    return ((d1 > 1e-9 && d2 < -1e-9) || (d1 < -1e-9 && d2 > 1e-9)) && ((d3 > 1e-9 && d4 < -1e-9) || (d3 < -1e-9 && d4 > 1e-9));
  }
  function isSimple(P) {                        // no two edges cross
    const n = P.length;
    for (let i = 0; i < n; i++) {
      const a = P[i], b = P[(i + 1) % n];
      const x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0]), y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1]);
      for (let j = i + 2; j < n; j++) {
        if (i === 0 && j === n - 1) continue;
        const c = P[j], d = P[(j + 1) % n];
        if (Math.max(c[0], d[0]) < x0 || Math.min(c[0], d[0]) > x1 || Math.max(c[1], d[1]) < y0 || Math.min(c[1], d[1]) > y1) continue;
        if (segsCross(a, b, c, d)) return false;
      }
    }
    return true;
  }

  function insideConvex(pts, outline, tol = 0.05) {
    const n = outline.length;
    return pts.every(p => {
      for (let i = 0; i < n; i++) {
        const a = outline[i], b = outline[(i + 1) % n], ex = b[0] - a[0], ey = b[1] - a[1];
        if (ex * (p[1] - a[1]) - ey * (p[0] - a[0]) < -tol * Math.hypot(ex, ey)) return false;
      }
      return true;
    });
  }

  // elevation an object sits on at (x, y): the terrain, or its own flat plane (zfix), plus the lift (zoff)
  const ground = (spec, T, x, y) => (spec.zfix != null ? spec.zfix : T.z(x, y)) + (spec.zoff || 0);

  /* --------------------------------------------------------- splitting / reshaping */
  // Line: portion between vertices lo..hi becomes its own piece; what is left on either side stays as pieces.
  function splitLine(pts, lo, hi) {
    const n = pts.length, cp = a => a.map(p => p.slice());
    if (lo === hi) {                                   // one cut point -> two pieces
      if (lo <= 0 || lo >= n - 1) return null;
      return { portion: null, rest: [cp(pts.slice(0, lo + 1)), cp(pts.slice(lo))] };
    }
    if (lo > hi) [lo, hi] = [hi, lo];
    const rest = [];
    if (lo >= 1) rest.push(cp(pts.slice(0, lo + 1)));
    if (hi <= n - 2) rest.push(cp(pts.slice(hi)));
    return rest.length ? { portion: cp(pts.slice(lo, hi + 1)), rest } : null;
  }
  // Polygon: chord between vertices lo and hi. portion = lo..hi, rest = the other side. null when invalid
  // (adjacent corners, a chord that leaves the shape, or pieces that would cross themselves).
  function splitPolygon(pts, lo, hi) {
    const n = pts.length, cp = a => a.map(p => p.slice());
    if (lo > hi) [lo, hi] = [hi, lo];
    if (hi - lo < 2 || (lo === 0 && hi === n - 1)) return null;
    const A = cp(pts.slice(lo, hi + 1)), B = cp(pts.slice(hi).concat(pts.slice(0, lo + 1)));
    const s0 = Math.sign(signedArea(pts)), sa = signedArea(A), sb = signedArea(B);
    if (Math.sign(sa) !== s0 || Math.sign(sb) !== s0 || Math.abs(sa) < 1e-4 || Math.abs(sb) < 1e-4) return null;
    if (!isSimple(A) || !isSimple(B)) return null;
    return { portion: A, rest: B };
  }
  function subdivide(pts, closed) {                    // insert a point in the middle of every edge
    const out = [], n = pts.length;
    for (let i = 0; i < n; i++) {
      out.push(pts[i].slice());
      if (i < n - 1 || closed) { const b = pts[(i + 1) % n]; out.push([(pts[i][0] + b[0]) / 2, (pts[i][1] + b[1]) / 2]); }
    }
    return out;
  }
  function chaikin(pts, closed) {                      // corner cutting: rounds the outline, ends of lines stay put
    const n = pts.length, out = [];
    if (!closed) out.push(pts[0].slice());
    for (let i = 0; i < (closed ? n : n - 1); i++) {
      const a = pts[i], b = pts[(i + 1) % n];
      out.push([a[0] * 0.75 + b[0] * 0.25, a[1] * 0.75 + b[1] * 0.25], [a[0] * 0.25 + b[0] * 0.75, a[1] * 0.25 + b[1] * 0.75]);
    }
    if (!closed) out.push(pts[n - 1].slice());
    return out;
  }
  const centroid = pts => [pts.reduce((s, p) => s + p[0], 0) / pts.length, pts.reduce((s, p) => s + p[1], 0) / pts.length];

  /* ------------------------------------------------------------- mesh builders */
  // Both return { pos: Float32Array (triangle soup, 9 floats per triangle), zTop, tops: [[x,y,z]..] } or null.
  function buildPoly(spec, T) {
    let P = cleanPolygon(spec.pts);
    if (!P) return null;
    if (signedArea(P) < 0) P = P.slice().reverse();
    const n = P.length, z = P.map(p => ground(spec, T, p[0], p[1]));
    const zb = Math.min(...z) - spec.embed, zt = Math.max(...z) + spec.height;
    let idx = triangulator(P);
    idx = idx.map(t => {                        // make every top triangle CCW
      const a = P[t[0]], b = P[t[1]], c = P[t[2]];
      return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) < 0 ? [t[0], t[2], t[1]] : t;
    });
    const pos = new Float32Array((idx.length * 2 + n * 2) * 9);
    let o = 0;
    const put = (x, y, zz) => { pos[o++] = x; pos[o++] = y; pos[o++] = zz; };
    for (const t of idx) { put(P[t[0]][0], P[t[0]][1], zt); put(P[t[1]][0], P[t[1]][1], zt); put(P[t[2]][0], P[t[2]][1], zt); }
    for (const t of idx) { put(P[t[2]][0], P[t[2]][1], zb); put(P[t[1]][0], P[t[1]][1], zb); put(P[t[0]][0], P[t[0]][1], zb); }
    for (let i = 0; i < n; i++) {
      const a = P[i], b = P[(i + 1) % n];
      put(a[0], a[1], zt); put(a[0], a[1], zb); put(b[0], b[1], zb);
      put(a[0], a[1], zt); put(b[0], b[1], zb); put(b[0], b[1], zt);
    }
    return { pos, zTop: zt, zBase: zb, P, tops: P.map(p => [p[0], p[1], zt]) };
  }

  function centerline(spec, T) {
    const seg = resamplePolyline(spec.pts, 0.5);
    if (!seg) return null;
    const off = spec.zoff || 0;
    let z = spec.zfix != null ? seg.map(() => spec.zfix) : seg.map(p => T.z(p[0], p[1]));
    if (spec.zfix == null && spec.smooth_z) z = smooth5(z);
    return seg.map((p, i) => [p[0], p[1], z[i] + off]);
  }

  function buildRibbon(spec, T) {
    const C = centerline(spec, T);
    if (!C || C.length < 2) return null;
    const n = C.length, hw = spec.width / 2, L = [], R = [];
    for (let i = 0; i < n; i++) {                // np.gradient: central differences, one-sided at the ends
      const a = C[Math.max(i - 1, 0)], b = C[Math.min(i + 1, n - 1)];
      let tx = b[0] - a[0], ty = b[1] - a[1];
      if (i === 0) { tx = C[1][0] - C[0][0]; ty = C[1][1] - C[0][1]; }
      else if (i === n - 1) { tx = C[n - 1][0] - C[n - 2][0]; ty = C[n - 1][1] - C[n - 2][1]; }
      else { tx /= 2; ty /= 2; }
      const m = Math.max(Math.hypot(tx, ty), 1e-12); tx /= m; ty /= m;
      L.push([C[i][0] - ty * hw, C[i][1] + tx * hw]); R.push([C[i][0] + ty * hw, C[i][1] - tx * hw]);
    }
    const zt = i => C[i][2] + spec.height, zb = i => C[i][2] - spec.embed;
    const pos = new Float32Array(((n - 1) * 8 + 4) * 9);
    let o = 0;
    const v = (S, i, z) => { pos[o++] = S[i][0]; pos[o++] = S[i][1]; pos[o++] = z; };
    const tri = (A, i, za, B, j, zb_, Cc, k, zc) => { v(A, i, za); v(B, j, zb_); v(Cc, k, zc); };
    for (let i = 0; i < n - 1; i++) {
      const j = i + 1;
      tri(L, i, zt(i), R, i, zt(i), R, j, zt(j)); tri(L, i, zt(i), R, j, zt(j), L, j, zt(j));         // top
      tri(L, i, zb(i), R, j, zb(j), R, i, zb(i)); tri(L, i, zb(i), L, j, zb(j), R, j, zb(j));         // bottom
      tri(L, i, zt(i), L, j, zb(j), L, i, zb(i)); tri(L, i, zt(i), L, j, zt(j), L, j, zb(j));         // left
      tri(R, i, zt(i), R, i, zb(i), R, j, zb(j)); tri(R, i, zt(i), R, j, zb(j), R, j, zt(j));         // right
    }
    const e = n - 1;
    tri(L, 0, zt(0), L, 0, zb(0), R, 0, zb(0)); tri(L, 0, zt(0), R, 0, zb(0), R, 0, zt(0));           // start cap
    tri(L, e, zt(e), R, e, zb(e), L, e, zb(e)); tri(L, e, zt(e), R, e, zt(e), R, e, zb(e));           // end cap
    return { pos, C, L, R, zTop: Math.max(...C.map(c => c[2])) + spec.height,
             tops: C.map(c => [c[0], c[1], c[2] + spec.height]) };
  }

  function build(spec, T) { return spec.kind === "poly" ? buildPoly(spec, T) : buildRibbon(spec, T); }

  function bounds(pos) {
    const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < pos.length; i += 3) for (let k = 0; k < 3; k++) { const v = pos[i + k]; if (v < lo[k]) lo[k] = v; if (v > hi[k]) hi[k] = v; }
    return { lo, hi };
  }
  function volume(pos) {
    let s = 0;
    for (let i = 0; i < pos.length; i += 9) {
      const ax = pos[i], ay = pos[i + 1], az = pos[i + 2], bx = pos[i + 3], by = pos[i + 4], bz = pos[i + 5], cx = pos[i + 6], cy = pos[i + 7], cz = pos[i + 8];
      s += ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx);
    }
    return s / 6;
  }

  /* ------------------------------------------------------------------ rays */
  function rayBox(o, d, lo, hi, tmax) {         // slab test -> entry t or Infinity
    let t0 = 0, t1 = tmax;
    for (let k = 0; k < 3; k++) {
      if (Math.abs(d[k]) < 1e-12) { if (o[k] < lo[k] || o[k] > hi[k]) return Infinity; continue; }
      let a = (lo[k] - o[k]) / d[k], b = (hi[k] - o[k]) / d[k];
      if (a > b) { const t = a; a = b; b = t; }
      if (a > t0) t0 = a; if (b < t1) t1 = b;
      if (t0 > t1) return Infinity;
    }
    return t0;
  }
  function rayTris(o, d, pos) {                 // Moller-Trumbore over a triangle soup, both faces
    let best = Infinity;
    for (let i = 0; i < pos.length; i += 9) {
      const e1x = pos[i + 3] - pos[i], e1y = pos[i + 4] - pos[i + 1], e1z = pos[i + 5] - pos[i + 2];
      const e2x = pos[i + 6] - pos[i], e2y = pos[i + 7] - pos[i + 1], e2z = pos[i + 8] - pos[i + 2];
      const px = d[1] * e2z - d[2] * e2y, py = d[2] * e2x - d[0] * e2z, pz = d[0] * e2y - d[1] * e2x;
      const det = e1x * px + e1y * py + e1z * pz;
      if (Math.abs(det) < 1e-14) continue;
      const inv = 1 / det, tx = o[0] - pos[i], ty = o[1] - pos[i + 1], tz = o[2] - pos[i + 2];
      const u = (tx * px + ty * py + tz * pz) * inv;
      if (u < 0 || u > 1) continue;
      const qx = ty * e1z - tz * e1y, qy = tz * e1x - tx * e1z, qz = tx * e1y - ty * e1x;
      const v = (d[0] * qx + d[1] * qy + d[2] * qz) * inv;
      if (v < 0 || u + v > 1) continue;
      const t = (e2x * qx + e2y * qy + e2z * qz) * inv;
      if (t > 1e-6 && t < best) best = t;
    }
    return best;
  }
  function rayTerrain(T, o, d, tmax) {          // march the height field, refine by bisection
    const W = (T.nx - 1) * T.dx, H = (T.ny - 1) * T.dy, step = Math.min(T.dx, T.dy) * 0.7;
    const above = t => { const x = o[0] + d[0] * t, y = o[1] + d[1] * t; return o[2] + d[2] * t - T.z(x, y); };
    let prev = 0, pv = above(0);
    for (let t = step; t < tmax; t += step) {
      const x = o[0] + d[0] * t, y = o[1] + d[1] * t;
      if (x < -1 || y < -1 || x > W + 1 || y > H + 1) { prev = t; pv = above(t); continue; }
      const v = above(t);
      if (pv > 0 && v <= 0) {
        let a = prev, b = t;
        for (let k = 0; k < 24; k++) { const m = (a + b) / 2; if (above(m) > 0) a = m; else b = m; }
        return (a + b) / 2;
      }
      prev = t; pv = v;
    }
    return Infinity;
  }

  return { setTriangulator, b64ToFloat32, Terrain, signedArea, cleanPolygon, resamplePolyline, rdp, simplifyPolygon,
           isSimple, insideConvex, ground, splitLine, splitPolygon, subdivide, chaikin, centroid, buildPoly, buildRibbon, build, centerline, bounds, volume, rayBox, rayTris, rayTerrain };
});
