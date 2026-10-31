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
| `features/` | one plugin per printable part: `path`, `roads`, `buildings`, `logo` |
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
(`--logo-depth`, default 0.6 mm) at the bottom of the base has the logo cut out, and each logo layer
(tile, icon + text, dot) fills its cut-out flush, so the bottom is flat and prints without supports.
`--logo-mode engrave` leaves the recess empty instead. Layouts: stacked, stacked_reverse, icon_left,
icon_right, icon_only, text_only. Text, size, spacing, boldness, position, rotation, mirror and colours are
all settings (see `--help`). The default mirror is "horizontal", which reads correctly when you turn the
finished model over like a page. The logo shrinks automatically if it would not fit the base.
The icon is `assets/icon.svg` by default (replace it with your own, or pick a file in the UI / use
`--logo-icon-source svg --logo-svg my.svg`). Each fill colour in the SVG becomes one printable colour layer; a
full-size square first layer becomes the tile (rounded with `--logo-tile-radius`). Use filled shapes only:
convert strokes and text to outlines first. `--logo-icon-source character` uses a single character instead.
The text font defaults to the bundled Liberation Serif (SIL OFL); point `--logo-font` at any .ttf/.otf.

## Route planner

The web UI also serves a route planner at `/planner` (button "Draw a route").
Click to add points; foot / bike / road modes snap each leg to the OpenStreetMap network (via a routing
proxy in the local server), "Straight" draws exactly where you click. Drag points, click the line to insert
one, right-click or double-click a point to delete, undo/redo with Ctrl+Z / Ctrl+Y, close the loop, out-and-back,
reverse. It shows distance, ascent/descent and an elevation profile, a distance target (5K ... marathon) and
kilometre markers. "Download GPX" saves the route; "Make 3D model" sends it straight to the model maker.
Existing GPX files can be imported and edited. The route is autosaved in the browser. Map tiles come from
OpenStreetMap, OpenTopoMap and Esri, routing from routing.openstreetmap.de, and a connection is required.
