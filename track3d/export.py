"""Writers: binary STL and multi-object 3MF (with per-part colours)."""
import struct
import zipfile
from xml.sax.saxutils import escape

import numpy as np


def write_stl(path, tris, header="track3d", log=print):
    tris = tris.astype(np.float32)
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-20)
    rec = np.zeros(len(tris), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, tris
    with open(path, "wb") as f:
        f.write(header.encode()[:80].ljust(80, b" "))
        f.write(struct.pack("<I", len(tris)))
        f.write(rec.tobytes())
    log(f"Wrote {path}  ({len(tris):,} triangles)")


def _indexed(tris):
    """Merge duplicate vertices (0.001 mm grid) -> (V float32 (m,3), F int (k,3))."""
    V = tris.reshape(-1, 3).astype(np.float64)
    q = np.clip(np.round((V + 1.0) * 1000.0), 0, (1 << 20) - 1).astype(np.int64)
    key = (q[:, 0] << 40) | (q[:, 1] << 20) | q[:, 2]
    _, first, inv = np.unique(key, return_index=True, return_inverse=True)
    F = inv.reshape(-1, 3)
    ok = (F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])  # drop collapsed triangles
    return V[first], F[ok]


def write_3mf(path, parts, title="track3d", log=print):
    """parts: iterable of objects with .name, .tris, .color ('#rrggbb')."""
    parts = list(parts)
    mats = "".join(f'<base name="{escape(p.name)}" displaycolor="{p.color.upper()}FF"/>' for p in parts)
    objs, items = [], []
    for k, p in enumerate(parts):
        V, F = _indexed(p.tris)
        vx = "".join(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in V.tolist())
        tr = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in F.tolist())
        oid = k + 2
        objs.append(f'<object id="{oid}" type="model" name="{escape(p.name)}" pid="1" pindex="{k}">'
                    f'<mesh><vertices>{vx}</vertices><triangles>{tr}</triangles></mesh></object>')
        items.append(f'<item objectid="{oid}"/>')
    model = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<metadata name="Title">{escape(title)}</metadata>'
             f'<resources><basematerials id="1">{mats}</basematerials>{"".join(objs)}</resources>'
             f'<build>{"".join(items)}</build></model>')
    ctypes = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
              '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
              '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
              '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ctypes)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)
    log(f"Wrote {path}  ({len(parts)} coloured parts)")
