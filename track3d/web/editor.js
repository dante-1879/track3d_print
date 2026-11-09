/* 3D viewer + per-object editor. Needs THREE, T3D (geom.js) and the DOM from index.html.
   Every building, road, river, lake, green area and the route is its own object, held as parameters
   (outline / centre-line, height, width, base depth). The preview is built from those parameters in the
   browser; "compile" asks the server to rebuild the printable files from the very same parameters. */
const viewer = (() => {
  const $ = s => document.querySelector(s);
  const host = $("#view");
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  host.prepend(renderer.domElement);
  const canvas = renderer.domElement;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, 0.5, 5000); camera.up.set(0, 0, 1);
  const controls = new THREE.OrbitControls(camera, canvas); controls.enableDamping = true;
  scene.add(new THREE.HemisphereLight(0xffffff, 0x445560, 0.9));
  const sun = new THREE.DirectionalLight(0xffffff, 0.8); sun.position.set(-200, -300, 400); scene.add(sun);
  const world = new THREE.Group(); scene.add(world);        // batches, selected meshes, STL parts
  const overlay = new THREE.Group(); scene.add(overlay);    // outline + handles (drawn on top)
  T3D.setTriangulator(P => THREE.ShapeUtils.triangulateShape(P.map(p => new THREE.Vector2(p[0], p[1])), []));

  /* ----------------------------------------------------------------- state */
  let S = null;                       // { id, meta, T, base }
  const objs = new Map();             // id -> { id, spec, geom, bb, inBatch, mesh, invalid, outside }
  const cls = new Map();              // name -> { name, label, color, visible, editable, mat, selMat, batch, dirty, stl }
  const baseline = new Map();         // id -> JSON of the spec when loaded / last compiled
  const edited = new Set();           // ids that differ from the baseline (including created / deleted)
  let editMode = false, selected = null, adding = null, drag = null, selV = new Set(), home = null;
  const undoStack = [], redoStack = [];
  let rebuildTimer = 0;
  const hooks = { onChange: () => {}, onSelect: () => {}, onMessage: () => {} };
  const clone = o => JSON.parse(JSON.stringify(o));
  const spin = () => new Promise(r => setTimeout(r, 0));

  /* ------------------------------------------------------------- rendering */
  let need = true;                             // render on demand: nothing moves -> no GPU work
  function resize() {
    const w = host.clientWidth, h = host.clientHeight;
    renderer.setSize(w, h, false); camera.aspect = w / Math.max(h, 1); camera.updateProjectionMatrix(); need = true;
  }
  new ResizeObserver(resize).observe(host); resize();
  const redraw = () => { need = true; };
  controls.addEventListener("change", redraw);
  (function loop() { requestAnimationFrame(loop); const moved = controls.update(); if (moved || need) { need = false; renderer.render(scene, camera); } })();

  const dotTex = (() => {
    const c = document.createElement("canvas"); c.width = c.height = 64;
    const g = c.getContext("2d");
    g.beginPath(); g.arc(32, 32, 28, 0, 7); g.fillStyle = "#fff"; g.fill();
    g.lineWidth = 7; g.strokeStyle = "#17262e"; g.stroke();
    return new THREE.CanvasTexture(c);
  })();
  const pointMat = (size, tex) => new THREE.PointsMaterial({ size: size * renderer.getPixelRatio(), sizeAttenuation: false,
    map: tex, vertexColors: true, transparent: true, alphaTest: 0.2, depthTest: false });
  const hV = new THREE.Points(new THREE.BufferGeometry(), pointMat(12, dotTex));   // vertices
  const hM = new THREE.Points(new THREE.BufferGeometry(), pointMat(8, dotTex));    // edge midpoints
  const hS = new THREE.Points(new THREE.BufferGeometry(), pointMat(17, dotTex));   // height / width
  const outlineMat = new THREE.LineBasicMaterial({ color: 0xffb000, depthTest: false });
  const outline = new THREE.Line(new THREE.BufferGeometry(), outlineMat);
  const ghost = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0x2bb3a3, depthTest: false }));
  [hM, hV, hS, outline, ghost].forEach((o, i) => { o.renderOrder = 900 + i; o.frustumCulled = false; o.visible = false; overlay.add(o); });

  const geomOf = pos => { const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(pos, 3)); g.computeBoundingSphere(); return g; };
  const lineGeom = pts => { const a = new Float32Array(pts.length * 3); pts.forEach((p, i) => a.set(p, i * 3)); return geomOf(a); };

  function makeClass(c) {
    const mat = new THREE.MeshStandardMaterial({ color: c.color, roughness: 0.85, metalness: 0, flatShading: true });
    const selMat = new THREE.MeshStandardMaterial({ color: c.color, roughness: 0.6, metalness: 0, flatShading: true, emissive: 0x6a4300 });
    selMat.polygonOffset = true; selMat.polygonOffsetFactor = -2; selMat.polygonOffsetUnits = -2;
    return { name: c.name, label: c.label, color: c.color, visible: true, editable: !!c.editable, mat, selMat, batch: null, dirty: false, stl: null };
  }

  /* ------------------------------------------------------ geometry per object */
  function ensureGeom(o) {
    o.geom = T3D.build(o.spec, S.T);
    if (!o.geom) { o.bb = null; o.invalid = true; return; }
    const b = T3D.bounds(o.geom.pos); o.bb = b;
    o.invalid = o.spec.kind === "poly" && o.spec.pts.length <= 800 ? !T3D.isSimple(o.geom.P) : false;
    o.outside = !T3D.insideConvex(o.spec.pts, S.meta.area.outline);
  }

  function rebuildBatch(c) {
    const list = [...objs.values()].filter(o => o.spec.cls === c.name && o.geom && o.id !== selected);
    let total = 0; list.forEach(o => total += o.geom.pos.length);
    const pos = new Float32Array(total), ranges = new Map();
    let off = 0;
    for (const o of list) { pos.set(o.geom.pos, off); ranges.set(o.id, [off, off + o.geom.pos.length]); off += o.geom.pos.length; o.inBatch = true; }
    if (c.batch) { world.remove(c.batch.mesh); c.batch.mesh.geometry.dispose(); }
    const mesh = new THREE.Mesh(geomOf(pos), c.mat);
    mesh.visible = c.visible; mesh.frustumCulled = false; world.add(mesh);
    c.batch = { mesh, pos, ranges };
    for (const o of list) if (o.mesh) { world.remove(o.mesh); o.mesh.geometry.dispose(); o.mesh = null; }
    c.dirty = false; redraw();
  }
  function scheduleRebuild(delay = 260) {
    clearTimeout(rebuildTimer);
    rebuildTimer = setTimeout(() => { for (const c of cls.values()) if (c.dirty) rebuildBatch(c); }, delay);
  }
  function detach(o) {                       // take an object out of its class batch (zero its triangles)
    const c = cls.get(o.spec.cls);
    if (o.inBatch && c.batch) {
      const r = c.batch.ranges.get(o.id);
      if (r) {
        c.batch.pos.fill(0, r[0], r[1]);
        const attr = c.batch.mesh.geometry.attributes.position;
        attr.updateRange.offset = r[0]; attr.updateRange.count = r[1] - r[0]; attr.needsUpdate = true;
        c.batch.ranges.delete(o.id);
      }
    }
    o.inBatch = false;
  }
  function refreshMesh(o) {                  // individual mesh for selected / just-edited objects
    const c = cls.get(o.spec.cls);
    if (!o.geom) { if (o.mesh) { world.remove(o.mesh); o.mesh.geometry.dispose(); o.mesh = null; } return; }
    if (!o.mesh) { o.mesh = new THREE.Mesh(geomOf(o.geom.pos), c.mat); o.mesh.frustumCulled = false; world.add(o.mesh); }
    else { o.mesh.geometry.dispose(); o.mesh.geometry = geomOf(o.geom.pos); }
    o.mesh.material = o.id === selected ? c.selMat : c.mat;
    o.mesh.visible = c.visible;
  }
  function touch(o) {                        // spec changed -> rebuild its geometry and display
    redraw();
    ensureGeom(o); detach(o); refreshMesh(o);
    cls.get(o.spec.cls).dirty = true; scheduleRebuild();
    if (o.id === selected) updateOverlay();
  }

  /* ------------------------------------------------------------- bookkeeping */
  function noteEdit(id) {
    const o = objs.get(id), base = baseline.get(id);
    const same = o && base !== undefined && JSON.stringify(o.spec) === base;
    if ((!o && base === undefined) || same) edited.delete(id); else edited.add(id);
  }
  function emit() { hooks.onChange({ edited: edited.size, canUndo: undoStack.length > 0, canRedo: redoStack.length > 0, selected }); }
  function push(changes) {
    changes = changes.filter(c => JSON.stringify(c.before) !== JSON.stringify(c.after));
    if (!changes.length) return;
    undoStack.push(changes); if (undoStack.length > 300) undoStack.shift();
    redoStack.length = 0; changes.forEach(c => noteEdit(c.id)); emit();
  }
  function setSpec(id, spec) {               // create / replace / delete one object
    let o = objs.get(id);
    if (!spec) {
      if (!o) return;
      detach(o); if (o.mesh) { world.remove(o.mesh); o.mesh.geometry.dispose(); }
      objs.delete(id); cls.get(o.spec.cls).dirty = true; scheduleRebuild();
      if (selected === id) select(null);
      return;
    }
    if (!o) { o = { id, spec: clone(spec), geom: null, bb: null, inBatch: false, mesh: null }; objs.set(id, o); }
    else o.spec = clone(spec);
    touch(o);
  }
  function applyChanges(changes, dir) {
    for (const c of (dir === "undo" ? changes.slice().reverse() : changes)) setSpec(c.id, dir === "undo" ? c.before : c.after);
    changes.forEach(c => noteEdit(c.id));
    if (selected && !objs.has(selected)) select(null); else if (selected) { updateOverlay(); hooks.onSelect(info()); }
    emit();
  }

  /* ------------------------------------------------------------------ select */
  function select(id) {
    if (!editMode) id = null;
    if (id === selected) { updateOverlay(); hooks.onSelect(info()); return; }
    const prev = selected && objs.get(selected);
    selected = id; selV = new Set();
    if (prev) { cls.get(prev.spec.cls).dirty = true; if (prev.mesh) prev.mesh.material = cls.get(prev.spec.cls).mat; scheduleRebuild(60); }
    const o = id && objs.get(id);
    if (o) { detach(o); refreshMesh(o); }
    updateOverlay(); hooks.onSelect(info()); emit();
  }
  const info = () => {
    const o = selected && objs.get(selected);
    return o ? { id: o.id, spec: o.spec, invalid: o.invalid, outside: o.outside, cls: cls.get(o.spec.cls), selV: [...selV].sort((a, b) => a - b), centroid: T3D.centroid(o.spec.pts) } : null;
  };

  /* ------------------------------------------------------------ handles/overlay */
  function tops(o) {                         // handle positions follow spec.pts (not the cleaned outline)
    const s = o.spec;
    if (s.kind === "poly") return s.pts.map(p => [p[0], p[1], o.geom ? o.geom.zTop : 0]);
    return s.pts.map(p => [p[0], p[1], T3D.ground(s, S.T, p[0], p[1]) + s.height]);
  }
  function specials(o) {                     // { height:[x,y,z,base], width?:[x,y,z] }
    const s = o.spec, t = tops(o), out = {};
    if (s.kind === "poly") {
      const cx = s.pts.reduce((a, p) => a + p[0], 0) / s.pts.length, cy = s.pts.reduce((a, p) => a + p[1], 0) / s.pts.length;
      out.height = [cx, cy, o.geom ? o.geom.zTop : 0, (o.geom ? o.geom.zTop : 0) - s.height];
    } else {
      const m = Math.floor(s.pts.length / 2), a = s.pts[Math.max(m - 1, 0)], b = s.pts[Math.min(m + 1, s.pts.length - 1)];
      let tx = b[0] - a[0], ty = b[1] - a[1]; const l = Math.hypot(tx, ty) || 1; tx /= l; ty /= l;
      out.height = [t[m][0], t[m][1], t[m][2], t[m][2] - s.height, m];
      out.width = [t[m][0] + ty * s.width / 2, t[m][1] - tx * s.width / 2, t[m][2], m, ty, -tx];
    }
    return out;
  }
  function updateOverlay() {
    redraw();
    const o = selected && objs.get(selected);
    const show = !!(o && editMode);
    [hV, hM, hS, outline].forEach(x => x.visible = show);
    if (!show) return;
    const t = tops(o), closed = o.spec.kind === "poly", n = t.length;
    const vcol = new Float32Array(n * 3);
    t.forEach((_, i) => vcol.set(selV.has(i) ? [1, 0.55, 0] : [1, 1, 1], i * 3));
    hV.geometry.dispose(); hV.geometry = lineGeom(t); hV.geometry.setAttribute("color", new THREE.BufferAttribute(vcol, 3));
    const mids = [];
    for (let i = 0; i < (closed ? n : n - 1); i++) { const a = t[i], b = t[(i + 1) % n]; mids.push([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2]); }
    hM.geometry.dispose(); hM.geometry = lineGeom(mids);
    hM.geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(mids.length * 3).fill(0.55), 3));
    const sp = specials(o), sp3 = [sp.height.slice(0, 3)], scol = [1, 0.55, 0];
    if (sp.width) { sp3.push(sp.width.slice(0, 3)); scol.push(0.2, 0.85, 0.45); }
    hS.geometry.dispose(); hS.geometry = lineGeom(sp3); hS.geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(scol), 3));
    const line = closed ? t.concat([t[0]]) : t;
    outline.geometry.dispose(); outline.geometry = lineGeom(line);
    outlineMat.color.set(o.invalid ? 0xff3b30 : 0xffb000);
  }

  /* --------------------------------------------------------------- pointer math */
  function rayAt(e) {
    const r = canvas.getBoundingClientRect();
    const v = new THREE.Vector3(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1, 0.5).unproject(camera);
    const d = v.sub(camera.position).normalize();
    return { o: [camera.position.x, camera.position.y, camera.position.z], d: [d.x, d.y, d.z] };
  }
  const atT = (r, t) => [r.o[0] + r.d[0] * t, r.o[1] + r.d[1] * t, r.o[2] + r.d[2] * t];
  function planeZ(r, z) { return Math.abs(r.d[2]) < 1e-6 ? null : atT(r, (z - r.o[2]) / r.d[2]); }
  function toScreen(p) {
    const v = new THREE.Vector3(p[0], p[1], p[2]).project(camera), r = canvas.getBoundingClientRect();
    return v.z > 1 ? null : [r.left + (v.x + 1) / 2 * r.width, r.top + (1 - v.y) / 2 * r.height];
  }
  const far = () => (S ? Math.hypot(S.meta.area.width, S.meta.area.depth) * 6 : 5000);

  function pick(r) {
    let best = { t: T3D.rayTerrain(S.T, r.o, r.d, far()), id: null };
    for (const o of objs.values()) {
      if (!o.geom || !o.bb || !cls.get(o.spec.cls).visible) continue;
      if (T3D.rayBox(r.o, r.d, o.bb.lo, o.bb.hi, best.t) === Infinity) continue;
      const t = T3D.rayTris(r.o, r.d, o.geom.pos);
      if (t < best.t) best = { t, id: o.id };
    }
    return best;
  }
  function hitHandle(e) {                    // nearest handle under the pointer, in screen pixels
    const o = selected && objs.get(selected); if (!o) return null;
    const cands = [], sp = specials(o), t = tops(o);
    cands.push(["height", 0, sp.height, 14]); if (sp.width) cands.push(["width", 0, sp.width, 14]);
    t.forEach((p, i) => cands.push(["vertex", i, p, 10]));
    const closed = o.spec.kind === "poly";
    for (let i = 0; i < (closed ? t.length : t.length - 1); i++) { const a = t[i], b = t[(i + 1) % t.length]; cands.push(["mid", i, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2], 8]); }
    let best = null, bd = 1e9;
    for (const [kind, i, p, rad] of cands) {
      const s = toScreen(p); if (!s) continue;
      const d = Math.hypot(s[0] - e.clientX, s[1] - e.clientY);
      const pri = kind === "height" || kind === "width" ? 0 : kind === "vertex" ? 1 : 2;     // specials win ties
      if (d <= rad && d - pri * 0.01 < bd) { bd = d; best = { kind, i, p }; }
    }
    if (!best) {                                       // no handle: is the pointer on an outline edge?
      const n = t.length, m = closed ? n : n - 1; let ed = 5;
      for (let i = 0; i < m; i++) {
        const A = toScreen(t[i]), B = toScreen(t[(i + 1) % n]); if (!A || !B) continue;
        const vx = B[0] - A[0], vy = B[1] - A[1], L2 = vx * vx + vy * vy;
        const u = L2 ? Math.min(Math.max(((e.clientX - A[0]) * vx + (e.clientY - A[1]) * vy) / L2, 0), 1) : 0;
        const d = Math.hypot(A[0] + vx * u - e.clientX, A[1] + vy * u - e.clientY);
        if (d < ed) { ed = d; const q = t[(i + 1) % n]; best = { kind: "edge", i, p: [(t[i][0] + q[0]) / 2, (t[i][1] + q[1]) / 2, (t[i][2] + q[2]) / 2] }; }
      }
    }
    return best;
  }

  /* ---------------------------------------------------------------- dragging */
  const MIN_H = 0.1, MIN_W = 0.2;
  const r2 = v => Math.round(v * 100) / 100;
  function pointerDown(e) {
    if (!editMode || e.button !== 0 || !S) return;
    if (adding) { addClick(e); e.stopImmediatePropagation(); e.preventDefault(); return; }
    const h = hitHandle(e), r = rayAt(e), o = selected && objs.get(selected);
    const stop = () => { e.stopImmediatePropagation(); e.preventDefault(); canvas.setPointerCapture?.(e.pointerId); };
    const base = { before: o && clone(o.spec), o, x0: e.clientX, y0: e.clientY, moved: false };
    if (h && o) {
      stop();
      if (h.kind === "vertex" && e.altKey) { removeVertex(o, h.i); return; }
      if (h.kind === "mid") {                          // insert a corner and drag it
        o.spec.pts.splice(h.i + 1, 0, [h.p[0], h.p[1]]); touch(o); selV = new Set([h.i + 1]);
        drag = { ...base, type: "verts", idxs: [h.i + 1], p0: [h.p[0], h.p[1]], pts0: clone(o.spec.pts), z: h.p[2], moved: true };
        updateOverlay(); hooks.onSelect(info()); return;
      }
      if (h.kind === "vertex") {                       // click selects, Shift+click adds, drag moves every selected corner
        if (e.shiftKey) { selV.has(h.i) ? selV.delete(h.i) : selV.add(h.i); updateOverlay(); hooks.onSelect(info()); return; }
        if (!selV.has(h.i)) selV = new Set([h.i]);
        drag = { ...base, type: "verts", idxs: [...selV], hit: h.i, p0: [h.p[0], h.p[1]], pts0: clone(o.spec.pts), z: h.p[2] };
        updateOverlay(); hooks.onSelect(info()); return;
      }
      if (h.kind === "edge") {                         // drag an edge: both of its corners move together
        const n = o.spec.pts.length, p = planeZ(r, h.p[2]) || h.p;
        drag = { ...base, type: "verts", edge: true, idxs: [h.i, (h.i + 1) % n], p0: [p[0], p[1]], pts0: clone(o.spec.pts), z: h.p[2] };
        return;
      }
      const sp = specials(o), s = o.spec;
      if (h.kind === "height") drag = { ...base, type: e.shiftKey ? "lift" : "height", base: sp.height[3], at: h.p, lift0: s.zfix != null ? s.zfix : (s.zoff || 0), moved: true };
      else drag = { ...base, type: "width", m: sp.width[3], moved: true };
      return;
    }
    const hit = pick(r);
    if (hit.id) {
      stop(); select(hit.id);
      const p = atT(r, hit.t), ob = objs.get(hit.id);
      drag = { type: "body", p0: p, z: p[2], pts0: clone(ob.spec.pts), before: clone(ob.spec), o: ob, x0: e.clientX, y0: e.clientY, moved: false };
    } else drag = { type: "click", x0: e.clientX, y0: e.clientY };   // may become an orbit; deselect only on a plain click
  }
  function pointerMove(e) {
    if (!S) return;
    if (adding) { ghostTo(e); return; }
    if (!drag) {
      if (editMode && selected) { const h = hitHandle(e); canvas.style.cursor = h ? (h.kind === "mid" ? "copy" : h.kind === "edge" ? "move" : "grab") : ""; }
      return;
    }
    if (drag.type === "click") return;
    const r = rayAt(e), o = drag.o;
    if (drag.type === "body") {
      if (!drag.moved && Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) < 4) return;
      drag.moved = true; controls.enabled = false;
      const p = planeZ(r, drag.z); if (!p) return;
      o.spec.pts = drag.pts0.map(q => [q[0] + p[0] - drag.p0[0], q[1] + p[1] - drag.p0[1]]);
    } else if (drag.type === "verts") {
      if (!drag.moved && Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) < 3) return;
      drag.moved = true;
      const p = planeZ(r, drag.z); if (!p) return;
      let dx = p[0] - drag.p0[0], dy = p[1] - drag.p0[1];
      if (drag.edge && e.shiftKey) {                   // Shift: push the edge straight along its normal
        const a = drag.pts0[drag.idxs[0]], b = drag.pts0[drag.idxs[1]];
        let tx = b[0] - a[0], ty = b[1] - a[1]; const l = Math.hypot(tx, ty) || 1; tx /= l; ty /= l;
        const d = dx * -ty + dy * tx; dx = -ty * d; dy = tx * d;
      }
      for (const i of drag.idxs) o.spec.pts[i] = [drag.pts0[i][0] + dx, drag.pts0[i][1] + dy];
    } else if (drag.type === "height" || drag.type === "lift") {   // vertical plane facing the camera through the handle
      const nx = camera.position.x - drag.at[0], ny = camera.position.y - drag.at[1], l = Math.hypot(nx, ny) || 1;
      const n = [nx / l, ny / l, 0], den = r.d[0] * n[0] + r.d[1] * n[1];
      if (Math.abs(den) < 1e-6) return;
      const t = ((drag.at[0] - r.o[0]) * n[0] + (drag.at[1] - r.o[1]) * n[1]) / den, zNew = r.o[2] + r.d[2] * t;
      if (drag.type === "height") o.spec.height = Math.max(MIN_H, r2(zNew - drag.base));
      else {                                           // Shift+drag: raise / lower the whole object (or its flat level)
        const v = r2(drag.lift0 + (zNew - drag.at[2]));
        if (o.spec.zfix != null) o.spec.zfix = v; else if (v) o.spec.zoff = v; else delete o.spec.zoff;
      }
    } else if (drag.type === "width") {
      const sp = specials(drag.o), c = tops(o)[drag.m], p = planeZ(r, c[2]); if (!p) return;
      const nx = sp.width[4], ny = sp.width[5];
      o.spec.width = Math.max(MIN_W, r2(2 * Math.abs((p[0] - c[0]) * nx + (p[1] - c[1]) * ny)));
    }
    touch(o); hooks.onSelect(info());
  }
  function pointerUp(e) {
    controls.enabled = true;
    if (!drag) return;
    const d = drag; drag = null;
    if (d.type === "click") {
      if (Math.hypot(e.clientX - d.x0, e.clientY - d.y0) < 4 && selected) select(null);
      return;
    }
    const o = d.o;
    if (d.type === "verts" && !d.moved && d.hit != null) {          // plain click on a corner: just select that one
      if (selV.size > 1) selV = new Set([d.hit]);
      updateOverlay(); hooks.onSelect(info()); return;
    }
    if (o.spec.kind === "poly") { ensureGeom(o); if (o.geom) o.invalid = !T3D.isSimple(o.geom.P); }
    push([{ id: o.id, before: d.before, after: clone(o.spec) }]);
    updateOverlay(); hooks.onSelect(info());
  }
  canvas.addEventListener("pointerdown", pointerDown, true);     // capture: runs before OrbitControls
  window.addEventListener("pointermove", pointerMove);
  window.addEventListener("pointerup", pointerUp);
  canvas.addEventListener("dblclick", () => { if (adding && adding.def.kind === "ribbon") finishAdd(); });
  canvas.addEventListener("contextmenu", e => {
    if (!editMode) return;
    if (adding) { e.preventDefault(); if (adding.def.kind === "ribbon") finishAdd(); else cancelAdd(); return; }
    const h = hitHandle(e), o = selected && objs.get(selected);
    if (h && h.kind === "vertex" && o) { e.preventDefault(); removeVertex(o, h.i); }
  });

  function removeVertex(o, i) {
    const min = o.spec.kind === "poly" ? 3 : 2;
    if (o.spec.pts.length <= min) { hooks.onMessage(`A ${o.spec.kind === "poly" ? "shape" : "line"} needs at least ${min} points.`); return; }
    const before = clone(o.spec); o.spec.pts.splice(i, 1); selV = new Set(); touch(o);
    if (o.spec.kind === "poly") o.invalid = o.geom ? !T3D.isSimple(o.geom.P) : true;
    push([{ id: o.id, before, after: clone(o.spec) }]); updateOverlay(); hooks.onSelect(info());
  }

  /* --------------------------------------------------------------- add objects */
  function newId(prefix) {
    let n = 0; const re = new RegExp("^" + prefix + "_(\\d+)$");
    for (const id of objs.keys()) { const m = re.exec(id); if (m) n = Math.max(n, +m[1]); }
    return `${prefix}_${String(n + 1).padStart(4, "0")}`;
  }
  const PREFIX = { buildings: "building", green: "green", water: "water", roads: "road" };
  const SIZE = { building: [3, 2.4], green: [12, 9], lake: [9, 6] };
  function addObject(def, pts) {
    const id = newId(PREFIX[def.cls]);
    const spec = { id, cls: def.cls, kind: def.kind, label: `New ${def.label.toLowerCase()}`, pts, height: def.height, embed: def.embed };
    if (def.kind === "ribbon") spec.width = def.width;
    push([{ id, before: null, after: spec }]); setSpec(id, spec); select(id);
  }
  function addClick(e) {
    const r = rayAt(e), t = T3D.rayTerrain(S.T, r.o, r.d, far());
    if (!isFinite(t)) return;
    const p = atT(r, t);
    if (adding.def.kind === "poly") {
      const [w, h] = SIZE[adding.def.key] || [4, 3];
      const pts = [[p[0] - w / 2, p[1] - h / 2], [p[0] + w / 2, p[1] - h / 2], [p[0] + w / 2, p[1] + h / 2], [p[0] - w / 2, p[1] + h / 2]];
      const def = adding.def; cancelAdd(); addObject(def, pts);
    } else { adding.pts.push([p[0], p[1]]); ghostTo(e); }
  }
  function ghostTo(e) {
    if (!adding || adding.def.kind !== "ribbon" || !adding.pts.length) return;
    const r = rayAt(e), t = T3D.rayTerrain(S.T, r.o, r.d, far()), pts = adding.pts.map(p => [p[0], p[1], S.T.z(p[0], p[1]) + 0.6]);
    if (isFinite(t)) { const p = atT(r, t); pts.push([p[0], p[1], p[2] + 0.6]); }
    ghost.geometry.dispose(); ghost.geometry = lineGeom(pts); ghost.visible = true;
  }
  function finishAdd() {
    if (!adding) return;
    const def = adding.def; let pts = adding.pts;
    if (pts.length > 1 && Math.hypot(pts[pts.length - 1][0] - pts[pts.length - 2][0], pts[pts.length - 1][1] - pts[pts.length - 2][1]) < 0.05) pts = pts.slice(0, -1);   // dblclick adds a duplicate
    cancelAdd();
    if (pts.length >= 2) addObject(def, pts); else hooks.onMessage("A line needs at least two points.");
  }
  function cancelAdd() { adding = null; ghost.visible = false; canvas.style.cursor = ""; hooks.onMessage(""); }

  /* ---------------------------------------------------------------- keyboard */
  window.addEventListener("keydown", e => {
    if (!editMode) return;
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName) && e.target.type !== "range";
    if (e.key === "Escape") { if (adding) cancelAdd(); else select(null); return; }
    if (e.key === "Enter" && adding) { finishAdd(); return; }
    if (typing) return;
    const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    if (mod && k === "z") { e.preventDefault(); e.shiftKey ? api.redo() : api.undo(); }
    else if (mod && k === "y") { e.preventDefault(); api.redo(); }
    else if (mod && k === "d") { e.preventDefault(); api.duplicate(); }
    else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); api.removeSelected(); }
  });

  /* ------------------------------------------------------------------- loading */
  const loader = new THREE.STLLoader();
  const statics = new Map();         // class name -> mesh (terrain + logo parts)
  function loadStl(name, url) {
    return new Promise(ok => loader.load(url, g => {
      const c = cls.get(name); if (!c) return ok();
      const old = statics.get(name); if (old) { world.remove(old); old.geometry.dispose(); }
      const m = new THREE.Mesh(g, c.mat); m.visible = c.visible; statics.set(name, m); world.add(m); redraw(); ok();
    }, undefined, () => { hooks.onMessage(`Could not load ${url}`); ok(); }));
  }
  function frame() {
    const a = S.meta.area, h = S.meta.info?.height_mm || 12, d = Math.hypot(a.width, a.depth, h);
    const tg = new THREE.Vector3(a.width / 2, a.depth / 2, h * 0.2);
    controls.target.copy(tg); camera.position.set(tg.x - d * 0.55, tg.y - d * 0.95, tg.z + d * 0.7);
    camera.far = d * 20; camera.updateProjectionMatrix(); controls.update(); redraw();
    home = { p: camera.position.clone(), t: tg.clone() };
  }
  function clearAll() {
    select(null); cancelAdd();
    for (const o of objs.values()) if (o.mesh) { world.remove(o.mesh); o.mesh.geometry.dispose(); }
    for (const c of cls.values()) if (c.batch) { world.remove(c.batch.mesh); c.batch.mesh.geometry.dispose(); }
    for (const m of statics.values()) { world.remove(m); m.geometry.dispose(); }
    objs.clear(); cls.clear(); baseline.clear(); edited.clear(); statics.clear();
    undoStack.length = 0; redoStack.length = 0;
  }

  const api = {
    async load(data, base, keepView = false) {
      const colors = Object.fromEntries([...cls.values()].map(c => [c.name, c.color]));
      clearAll();
      const t = data.terrain;
      S = { id: data.id, meta: data, base, T: new T3D.Terrain(t.nx, t.ny, t.dx, t.dy, T3D.b64ToFloat32(t.z_b64)) };
      data.classes.forEach(c => { const k = makeClass(keepView && colors[c.name] ? { ...c, color: colors[c.name] } : c); cls.set(c.name, k); });
      for (const spec of data.objects) {
        const o = { id: spec.id, spec: clone(spec), geom: null, bb: null, inBatch: false, mesh: null };
        objs.set(o.id, o); baseline.set(o.id, JSON.stringify(o.spec));
      }
      let n = 0;
      for (const o of objs.values()) { ensureGeom(o); if (++n % 4000 === 0) await spin(); }
      for (const c of cls.values()) rebuildBatch(c);
      const stl = (name, file) => loadStl(name, `${base}/${file}?v=${Date.now()}`);
      await Promise.all([stl("terrain", t.stl), ...data.statics.map(s => stl(s.name, s.stl))]);
      if (!keepView) frame();
      emit(); hooks.onSelect(null);
      return [...cls.values()].map(c => ({ name: c.name, label: c.label, color: c.color, count: [...objs.values()].filter(o => o.spec.cls === c.name).length, editable: c.editable }));
    },
    async reloadStatic(terrainStl, staticStls) {
      const base = S.base, v = Date.now();
      await Promise.all([loadStl("terrain", `${base}/${terrainStl}?v=${v}`), ...Object.entries(staticStls || {}).map(([n, f]) => loadStl(n, `${base}/${f}?v=${v}`))]);
    },
    markCompiled() { baseline.clear(); edited.clear(); for (const o of objs.values()) baseline.set(o.id, JSON.stringify(o.spec)); emit(); },
    hooks,
    reset() { if (home) { camera.position.copy(home.p); controls.target.copy(home.t); redraw(); } },
    underside() {
      if (!home) return; const d = home.p.distanceTo(home.t) * 1.05;
      controls.target.set(home.t.x, home.t.y, 0); camera.position.set(home.t.x, home.t.y + d * 0.35, -d * 0.9); redraw();
    },
    setColor(name, color) {
      const c = cls.get(name); if (!c) return;
      c.color = color; c.mat.color.set(color); c.selMat.color.set(color); redraw();
    },
    toggle(name, on) {
      const c = cls.get(name); if (!c) return; c.visible = on; redraw();
      if (c.batch) c.batch.mesh.visible = on;
      statics.get(name) && (statics.get(name).visible = on);
      for (const o of objs.values()) if (o.spec.cls === name && o.mesh) o.mesh.visible = on;
      if (selected && objs.get(selected)?.spec.cls === name && !on) select(null);
    },
    setEditMode(on) {
      editMode = !!on; if (!editMode) { cancelAdd(); select(null); }
      updateOverlay(); canvas.style.cursor = ""; emit();
    },
    isEditMode: () => editMode,
    startAdd(key) {
      const def = (S?.meta.add_types || []).find(a => a.key === key); if (!def || !editMode) return;
      select(null); adding = { def, pts: [] }; canvas.style.cursor = "crosshair";
      hooks.onMessage(def.kind === "poly" ? `Click the map to place a ${def.label.toLowerCase()} (Esc cancels).`
        : `Click points along the ${def.label.toLowerCase()}; double-click, Enter or right-click to finish (Esc cancels).`);
    },
    cancelAdd,
    deselect() { select(null); },
    undo() { const c = undoStack.pop(); if (!c) return; redoStack.push(c); applyChanges(c, "undo"); },
    redo() { const c = redoStack.pop(); if (!c) return; undoStack.push(c); applyChanges(c, "redo"); },
    removeSelected() {
      const o = selected && objs.get(selected); if (!o) return;
      const before = clone(o.spec); setSpec(o.id, null); push([{ id: o.id, before, after: null }]);
    },
    duplicate() {
      const o = selected && objs.get(selected); if (!o) return;
      const prefix = o.id.replace(/_\d+$/, ""), id = newId(prefix), spec = clone(o.spec);
      spec.id = id; spec.label = o.spec.label + " copy"; spec.pts = spec.pts.map(p => [p[0] + 1.2, p[1] + 1.2]); delete spec.smooth_z;
      if (o.spec.cls === "path") { hooks.onMessage("The route path can't be duplicated."); return; }
      push([{ id, before: null, after: spec }]); setSpec(id, spec); select(id);
    },
    edit(fields) {                              // numeric edits from the panel
      const o = selected && objs.get(selected); if (!o) return;
      const before = clone(o.spec);
      for (const [k, v] of Object.entries(fields)) if (Number.isFinite(v)) o.spec[k] = k === "height" ? Math.max(MIN_H, v) : k === "width" ? Math.max(MIN_W, v) : Math.max(0, v);
      touch(o); push([{ id: o.id, before, after: clone(o.spec) }]); hooks.onSelect(info());
    },
    transform: (() => {                         // rotate / scale about the centroid, live, committed on release
      let g = null;
      return {
        begin() { const o = selected && objs.get(selected); if (!o) return; const p = o.spec.pts; g = { before: clone(o.spec), pts: clone(p), c: [p.reduce((s, q) => s + q[0], 0) / p.length, p.reduce((s, q) => s + q[1], 0) / p.length] }; },
        apply(deg, scale) {
          const o = selected && objs.get(selected); if (!o || !g) return;
          const a = deg * Math.PI / 180, cs = Math.cos(a), sn = Math.sin(a);
          o.spec.pts = g.pts.map(([x, y]) => { const dx = (x - g.c[0]) * scale, dy = (y - g.c[1]) * scale; return [g.c[0] + dx * cs - dy * sn, g.c[1] + dx * sn + dy * cs]; });
          touch(o);
        },
        end() {
          const o = selected && objs.get(selected); if (!o || !g) return;
          if (o.spec.kind === "poly") { ensureGeom(o); }
          push([{ id: o.id, before: g.before, after: clone(o.spec) }]); g = null; updateOverlay(); hooks.onSelect(info());
        }
      };
    })(),
    simplify() {
      const o = selected && objs.get(selected); if (!o) return;
      const before = clone(o.spec), n0 = o.spec.pts.length;
      o.spec.pts = o.spec.kind === "poly" ? T3D.simplifyPolygon(o.spec.pts, 0.08) : T3D.rdp(o.spec.pts, 0.08);
      touch(o); push([{ id: o.id, before, after: clone(o.spec) }]); hooks.onSelect(info());
      hooks.onMessage(`Simplified ${n0} → ${o.spec.pts.length} points.`);
    },
    split() {                                   // cut the selected object at / between the selected corners
      const o = selected && objs.get(selected); if (!o) return;
      const s = o.spec, idx = [...selV].sort((a, b) => a - b), say = hooks.onMessage;
      if (s.cls === "path") return say("The route is one continuous object and can't be split.");
      let res = null;
      if (s.kind === "ribbon") {
        if (idx.length < 1 || idx.length > 2) return say("Select one point (cut here) or two points (separate the part between them): Shift+click the white dots.");
        res = T3D.splitLine(s.pts, idx[0], idx[idx.length - 1]);
        if (!res) return say("Pick points that are inside the line, not both of its ends.");
      } else {
        if (idx.length !== 2) return say("Select two corners (Shift+click) to divide the shape along the line between them.");
        res = T3D.splitPolygon(s.pts, idx[0], idx[1]);
        if (res) res = { portion: res.portion, rest: [res.rest] };
        if (!res) return say("That line leaves the shape or the corners touch - pick two corners that can see each other across the inside.");
      }
      const prefix = o.id.replace(/_\d+$/, ""), before = clone(s), changes = [];
      const pieces = (res.portion ? [res.portion] : []).concat(res.rest);
      const keepIdx = res.portion ? 1 : 0;              // the original object keeps the first remainder
      s.pts = pieces[keepIdx]; touch(o); changes.push({ id: o.id, before, after: clone(s) });
      let focus = o.id;
      pieces.forEach((pts, k) => {
        if (k === keepIdx) return;
        const id = newId(prefix), ns = clone(before); ns.id = id; ns.pts = pts; delete ns.smooth_z;
        ns.label = (before.label || o.id).replace(/ part \d+$/, "") + (k === 0 && res.portion ? " (separated)" : " part " + (k + 1));
        setSpec(id, ns); changes.push({ id, before: null, after: ns }); if (k === 0 && res.portion) focus = id;
      });
      push(changes); selV = new Set(); select(focus); updateOverlay(); hooks.onSelect(info());
      say(`Split into ${pieces.length} objects.`);
    },
    smooth() {
      const o = selected && objs.get(selected); if (!o) return;
      if (o.spec.pts.length > 1500) return hooks.onMessage("Too many points to smooth - simplify first.");
      const before = clone(o.spec); o.spec.pts = T3D.chaikin(o.spec.pts, o.spec.kind === "poly"); selV = new Set();
      touch(o); push([{ id: o.id, before, after: clone(o.spec) }]); hooks.onSelect(info());
    },
    subdivide() {
      const o = selected && objs.get(selected); if (!o) return;
      if (o.spec.pts.length > 1500) return hooks.onMessage("Already has plenty of points.");
      const before = clone(o.spec); o.spec.pts = T3D.subdivide(o.spec.pts, o.spec.kind === "poly"); selV = new Set();
      touch(o); push([{ id: o.id, before, after: clone(o.spec) }]); hooks.onSelect(info());
    },
    setPosition(x, y) {                         // move the object so its centre sits at (x, y)
      const o = selected && objs.get(selected); if (!o || !Number.isFinite(x) || !Number.isFinite(y)) return;
      const c = T3D.centroid(o.spec.pts), before = clone(o.spec);
      o.spec.pts = o.spec.pts.map(p => [p[0] + x - c[0], p[1] + y - c[1]]);
      touch(o); push([{ id: o.id, before, after: clone(o.spec) }]); hooks.onSelect(info());
    },
    setElevation(mode, val) {                   // "follow" the terrain (+ lift) or sit "fixed" on a flat plane at a level
      const o = selected && objs.get(selected); if (!o) return;
      const s = o.spec, before = clone(s), c = T3D.centroid(s.pts);
      if (mode === "fixed") {
        if (s.zfix == null) { s.zfix = r2(Math.max(...s.pts.map(p => T3D.ground(s, S.T, p[0], p[1])))); delete s.zoff; }   // highest ground under it: the top stays put
        if (Number.isFinite(val)) s.zfix = val;
      } else {
        if (s.zfix != null) delete s.zfix;
        if (Number.isFinite(val)) { if (val) s.zoff = val; else delete s.zoff; }
      }
      touch(o); push([{ id: o.id, before, after: clone(s) }]); hooks.onSelect(info());
    },
    getObjects: () => [...objs.values()].map(o => clone(o.spec)),
    getColors: () => Object.fromEntries([...cls.values()].map(c => [c.name, c.color])),
    // test / diagnostics helpers
    _debug: { setSelV(a) { selV = new Set(a); updateOverlay(); hooks.onSelect(info()); }, hit: hitHandle, objs, cls, camera, controls, canvas, pickAt: (x, y) => pick(rayAt({ clientX: x, clientY: y })).id, toScreen, tops, specials, get selected() { return selected; }, get S() { return S; } },
  };
  return api;
})();
