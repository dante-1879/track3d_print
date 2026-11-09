# track3d

GPX route or place name -> 3D-printable terrain (STL per part, 3MF with colours).

    pip install -r requirements.txt   # numpy, pillow, mapbox-earcut (earcut is only for the logo)
    python -m track3d ui                         # web interface at http://127.0.0.1:8765
    python -m track3d route.gpx --shape circle --size 180
    python -m track3d --place "New York" --radius 8
    python -m track3d --demo                     # offline test
    python -m track3d --help                     # every setting is a flag
    python -m track3d route.gpx --save-config my.json   (then)  --config my.json

## Layout

| module | job |
|---|---|
| `config.py` | all settings in sections; CLI flags, JSON and the UI form are generated from it |
| `geo.py` | projection, tile shapes (rectangle, square, circle, hexagon, octagon) |
| `elevation.py`, `osm.py`, `gpx.py`, `geocode.py` | data sources |
| `meshing.py` | pure geometry (terrain solid, ribbons, boxes, groove) |
| `features/` | one plugin per printable part: `path`, `water`, `green`, `roads`, `buildings`, `logo` |
| `shapes.py`, `assets/` | bitmap tracing, text to outlines, the traced 化 glyph and the bundled serif font |
| `export.py` | STL and 3MF writers |
| `pipeline.py` | runs everything; knows nothing about CLI or UI |
| `cli.py`, `web/` | the two front ends |

## Adding a feature (water, contours, ...)

1. Add a settings dataclass (on/off bool first) to `config.py`, and add it to `SECTIONS` and `Settings`.
   It then appears as CLI flags and as a UI section automatically.
2. Create `features/water.py` with a `@register` class (`name`, `label`, `order`, `enabled`,
   `color`, `build(ctx) -> triangles`) and import it in `features/__init__.py`.

## Underside logo

Enable "Add a logo to the underside" in the UI, or `--logo` on the command line. A thin plate
(`--logo-depth`, default 0.6 mm) at the bottom of the base has the logo recessed into it by default.
`--logo-mode inlay` instead fills the cut-out with separate colour layers (tile, icon + text, dot).
The engraved mode leaves the recess empty. Layouts: stacked, stacked_reverse, icon_left,
icon_right, icon_only, text_only. Text, size, spacing, boldness, position, rotation, mirror and colours are
all settings (see `--help`). The default mirror is "horizontal", which reads correctly when you turn the
finished model over like a page. The logo shrinks automatically if it would not fit the base.
The icon is `assets/icon.svg` by default (replace it with your own, or pick a file in the UI / use
`--logo-icon-source svg --logo-svg my.svg`). Each fill colour in the SVG becomes one printable colour layer; a
full-size square first layer becomes the tile (rounded with `--logo-tile-radius`). Use filled shapes only:
convert strokes and text to outlines first. `--logo-icon-source character` uses a single character instead.
The text font defaults to the bundled Liberation Serif (SIL OFL); point `--logo-font` at any .ttf/.otf.

## Use it from other pages (web service)

`python -m track3d ui --host 0.0.0.0 --no-browser` (or `docker compose up`) serves the editor at `/` and one headless
route, **`POST /api/build`**, that returns the finished files (zip / 3MF / STL / edit file) from a single call. A small
script (`/track3d-client.js`) lets other pages call it or embed the full editor in an `<iframe>`.
See **[API.md](API.md)** for the request fields, options, security notes and deployment, and `examples/`.

## In-page editor

Every building, road, river, lake, green area and the route is an individual object stored as parameters
(outline or centre-line, height, width, base depth). After generating:

1. **Enter edit mode** and click a single object to select it.
2. Drag the body to move it. Drag a white dot to move a corner, a small grey dot to add one, or drag an edge
   itself (Shift keeps it moving along its normal). Shift+click several corners to move them together.
   Alt-click or right-click a corner removes it. Drag the orange dot up/down for height; Shift+drag it to raise or
   lower the whole object. On roads and rivers drag the green dot sideways for width.
   The side panel has numeric X/Y position, "Sits on" (terrain surface + lift, or a flat level at a given Z, measured
   up from the print bed), height / width / base depth, rotate, scale, simplify, subdivide, smooth, duplicate and delete.
   **Split**: select one point on a road/river and press *Split at point*, or two points and press *Separate part* to
   turn the section between them into its own object. On a building, lake or green area, select two corners and press
   *Divide shape* to cut it along the line between them. **Add object** places new buildings, green areas, lakes,
   rivers and roads. Ctrl+Z / Ctrl+Y undo and redo.
3. **Exit edit mode** to look at the whole model; edits are kept.
4. **Compile new design** rebuilds the printable files from the edited parameters (one STL per colour, a combined
   STL, a coloured 3MF and an edit file). If the route was moved, the groove in the terrain is re-cut along it.
5. **Export edit file** saves a self-contained `.track3d.json`; **Open edit file** reloads it later (no GPX or
   map download needed). **Discard edits** returns to the generated model.

Generation and compile share one function (`track3d/scene.py: compile_object`), so the preview you edit is the
model that gets exported. Outlines that cross themselves are flagged in the panel and, at compile time, replaced by
their bounding rectangle. Terrain and logo parts are not shape-editable. Hiding a class in the side panel is a
view setting only; delete objects to drop them from the export.

## Route planner

The web UI also serves a route planner at `/planner` (button "Draw a route").
Click to add points; foot / bike / road modes snap each leg to the OpenStreetMap network (via a routing
proxy in the local server), "Straight" draws exactly where you click. Drag points, click the line to insert
one, right-click or double-click a point to delete, undo/redo with Ctrl+Z / Ctrl+Y, close the loop, out-and-back,
reverse. It shows distance, ascent/descent and an elevation profile, a distance target (5K ... marathon) and
kilometre markers. "Download GPX" saves the route; "Make 3D model" sends it straight to the model maker.
Existing GPX files can be imported and edited. The route is autosaved in the browser. Map tiles come from
OpenStreetMap, OpenTopoMap and Esri, routing from routing.openstreetmap.de, and a connection is required.
