"""All settings live here, grouped in sections. The CLI flags, the JSON config files and the
web UI form are all generated from these dataclasses, so adding a field here adds it everywhere."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, fields


def F(default, label, help="", **meta):
    return field(default=default, metadata=dict(label=label, help=help, **meta))


@dataclass
class InputSettings:
    """Where the map comes from. A GPX route is passed separately; otherwise place or centre."""
    place: str = F("", "Place name", "Searched with OpenStreetMap Nominatim")
    center_lat: float = F(0.0, "Centre latitude")
    center_lon: float = F(0.0, "Centre longitude")
    radius_km: float = F(5.0, "Radius (km)", "Used for place / coordinate maps", min=0.5, max=100, step=0.5)
    demo: bool = F(False, "Demo mode", "Offline synthetic terrain")


@dataclass
class AreaSettings:
    shape: str = F("rectangle", "Tile shape", "Outline of the printed tile",
                   choices=["rectangle", "square", "circle", "hexagon", "octagon"])
    size: float = F(150.0, "Model size (mm)", "Longest side / diameter", min=40, max=300, step=5)
    resolution: int = F(480, "Terrain grid points", "Higher = smoother and bigger files",
                        min=120, max=900, step=20)
    padding: float = F(0.12, "Margin around route", "Fraction of the route size", min=0, max=0.6, step=0.02)
    base: float = F(4.0, "Base thickness (mm)", min=1, max=15, step=0.5)


@dataclass
class TerrainSettings:
    exaggeration: float = F(1.5, "Vertical exaggeration", min=0.5, max=6, step=0.1)
    smooth: int = F(1, "Smoothing passes", "0 = off", min=0, max=6)
    flatten_sea: bool = F(False, "Flatten below sea level", "Clamp elevations under 0 m (coastal maps)")
    terrain_color: str = F("#c9b68c", "Colour", kind="color")


@dataclass
class PathSettings:
    path_width: float = F(2.0, "Width (mm)", min=0.6, max=6, step=0.1)
    path_height: float = F(1.8, "Height above terrain (mm)", min=0.2, max=5, step=0.1)
    groove_depth: float = F(0.8, "Groove depth (mm)", "Channel in the terrain the path slots into; 0 = none",
                            min=0, max=3, step=0.1)
    clearance: float = F(0.25, "Groove clearance (mm)", "Extra width each side", min=0, max=1, step=0.05)
    embed: float = F(0.4, "Embed without groove (mm)", "How far the path sinks in when there is no groove",
                     min=0, max=2, step=0.1)
    path_color: str = F("#e4572e", "Colour", kind="color")


@dataclass
class RoadSettings:
    roads: bool = F(True, "Include roads", "From OpenStreetMap")
    road_detail: int = F(2, "Road detail", "1 = major, 2 = + secondary, 3 = + residential",
                         choices=[1, 2, 3])
    road_height: float = F(0.35, "Height (mm)", min=0.1, max=1.5, step=0.05)
    road_color: str = F("#f2efe6", "Colour", kind="color")


@dataclass
class BuildingSettings:
    buildings: bool = F(True, "Include buildings", "From OpenStreetMap")
    building_style: str = F("auto", "Style", "footprints = real outlines (best up to ~8 km), "
                            "blocks = fused density blocks for big areas", choices=["auto", "footprints", "blocks"])
    min_building: float = F(0.6, "Smallest building (mm)", "Smaller outlines are scaled up to this size so they print",
                            min=0.3, max=3, step=0.1)
    building_grow: float = F(0.12, "Merge gap (mm)", "Grows every outline outwards so neighbours that nearly "
                             "touch fuse into one block (0 = keep them separate)", min=0, max=0.5, step=0.02)
    block_size: float = F(1.0, "Block cell size (mm)", "Blocks style only", min=0.5, max=6, step=0.5)
    block_density: float = F(0.06, "Block density threshold", "Blocks style only; lower = more blocks",
                             min=0.01, max=0.5, step=0.01)
    block_height_min: float = F(0.8, "Min height (mm)", min=0.2, max=3, step=0.1)
    block_height_max: float = F(2.4, "Max height (mm)", min=0.4, max=8, step=0.1)
    building_color: str = F("#9aa7b2", "Colour", kind="color")


@dataclass
class WaterSettings:
    water: bool = F(True, "Include water bodies", "Lakes, ponds, reservoirs and basins from OpenStreetMap")
    water_height: float = F(0.25, "Height (mm)", "Raised above the terrain so the separate colour is visible",
                            min=0.05, max=1.5, step=0.05)
    waterway_width: float = F(0.8, "River width (mm)", "Printed width for rivers, streams and canals",
                              min=0.2, max=4, step=0.1)
    water_color: str = F("#4aa3df", "Colour", kind="color")


@dataclass
class GreenSettings:
    green: bool = F(True, "Include green areas", "Parks, forests, grassland, farmland and cemeteries from OpenStreetMap")
    green_height: float = F(0.12, "Height (mm)", "Raised slightly so the separate colour is visible",
                            min=0.05, max=1.0, step=0.05)
    green_color: str = F("#78a85a", "Colour", kind="color")


@dataclass
class LogoSettings:
    logo: bool = F(False, "Add a logo to the underside",
                   "Cuts the logo into the bottom of the base and fills it flush, so the bottom stays flat")
    logo_mode: str = F("engrave", "Mode", "engrave = recessed logo; inlay = separate colour parts",
                       choices=["engrave", "inlay"])
    logo_layout: str = F("stacked", "Layout", "Where the text sits relative to the icon",
                         choices=["stacked", "stacked_reverse", "icon_left", "icon_right", "icon_only", "text_only"])
    logo_text: str = F("Bakemono Studios", "Text")
    logo_icon_source: str = F("bundled", "Icon source", "bundled = the Bakemono icon.svg, svg = your own SVG file, "
                              "character = a single character from a font", choices=["bundled", "svg", "character"])
    logo_svg: str = F("", "Custom icon (SVG)", "Filled shapes only; each fill colour becomes one printable colour "
                      "layer. A full-size square background becomes the tile.", kind="textfile")
    logo_mark: str = F("化", "Icon character", "Used when the icon source is 'character'")
    logo_scale: float = F(1.0, "Overall scale", "Scales the whole logo; it also shrinks automatically if it "
                          "would not fit the base", min=0.3, max=2.5, step=0.05)
    logo_icon_size: float = F(20.0, "Icon size (mm)", min=5, max=80, step=1)
    logo_text_size: float = F(8.4, "Text size (mm)", "Font size (em); capitals are about 70% of this",
                              min=2, max=30, step=0.2)
    logo_gap: float = F(6.5, "Gap icon to text (mm)", min=0, max=30, step=0.5)
    logo_depth: float = F(0.6, "Depth (mm)", "Thickness of the bottom plate that holds the logo",
                          min=0.3, max=2.0, step=0.1)
    logo_x: float = F(0.0, "Offset left/right (mm)", min=-100, max=100, step=1)
    logo_y: float = F(0.0, "Offset up/down (mm)", min=-100, max=100, step=1)
    logo_rotation: float = F(0.0, "Rotation (degrees)", min=-180, max=180, step=5)
    logo_mirror: str = F("horizontal", "Mirror", "Flip so it reads correctly when you turn the model over: "
                         "horizontal = turn it like a page, vertical = tip it forward",
                         choices=["horizontal", "vertical", "none"])


@dataclass
class LogoStyleSettings:
    logo_font: str = F("", "Text font file", "Path to a .ttf/.otf; blank = built-in serif (Liberation Serif)")
    logo_mark_font: str = F("", "Icon font file", "Only needed for characters other than 化")
    logo_text_weight: float = F(0.025, "Text boldness", "Thickens thin letters so they print cleanly",
                                min=0, max=0.08, step=0.005)
    logo_spacing: float = F(0.1, "Letter spacing", "In units of the font size", min=-0.05, max=0.4, step=0.01)
    logo_svg_colors: bool = F(True, "Use the colours from the SVG", "Off = use the three colours below")
    logo_tile: bool = F(True, "Rounded tile behind the icon")
    logo_tile_radius: float = F(0.2, "Tile corner radius", "Fraction of the tile side", min=0, max=0.5, step=0.02)
    logo_mark_size: float = F(0.62, "Icon size inside tile", "Character icon only; fraction of the tile", min=0.2, max=0.95, step=0.02)
    logo_dot: bool = F(True, "Dot in the top-right corner")
    logo_dot_size: float = F(0.07, "Dot diameter", "Character icon only; fraction of the tile side", min=0.02, max=0.2, step=0.01)
    logo_color: str = F("#f1ede2", "Icon and text colour", kind="color")
    logo_tile_color: str = F("#17181a", "Tile colour", kind="color")
    logo_dot_color: str = F("#e03a2e", "Dot colour", kind="color")


@dataclass
class OutputSettings:
    name: str = F("", "File name prefix", "Blank = automatic")
    outdir: str = F(".", "Output folder", ui=False)
    export_3mf: bool = F(True, "3MF (all parts, with colours)")
    export_stl: bool = F(True, "STL per part", ui=False)
    combine_stl: bool = F(True, "Combined STL (everything except the path)")


SECTIONS = {
    "area": ("Tile", AreaSettings),
    "terrain": ("Terrain", TerrainSettings),
    "path": ("Route path", PathSettings),
    "roads": ("Roads", RoadSettings),
    "buildings": ("Buildings", BuildingSettings),
    "water": ("Water bodies", WaterSettings),
    "green": ("Green areas", GreenSettings),
    "logo": ("Logo on the underside", LogoSettings),
    "logo_style": ("Logo style", LogoStyleSettings),
    "output": ("Output", OutputSettings),
}


@dataclass
class Settings:
    input: InputSettings = field(default_factory=InputSettings)
    area: AreaSettings = field(default_factory=AreaSettings)
    terrain: TerrainSettings = field(default_factory=TerrainSettings)
    path: PathSettings = field(default_factory=PathSettings)
    roads: RoadSettings = field(default_factory=RoadSettings)
    buildings: BuildingSettings = field(default_factory=BuildingSettings)
    water: WaterSettings = field(default_factory=WaterSettings)
    green: GreenSettings = field(default_factory=GreenSettings)
    logo: LogoSettings = field(default_factory=LogoSettings)
    logo_style: LogoStyleSettings = field(default_factory=LogoStyleSettings)
    output: OutputSettings = field(default_factory=OutputSettings)

    # ---- (de)serialisation
    def to_dict(self):
        return {k: {f.name: getattr(getattr(self, k), f.name) for f in fields(getattr(self, k))}
                for k in ("input", *SECTIONS)}

    @classmethod
    def from_dict(cls, d):
        s = cls()
        for key, vals in (d or {}).items():
            sec = getattr(s, key, None)
            if sec is None or not isinstance(vals, dict):
                continue
            for f in fields(sec):
                if f.name in vals:
                    setattr(sec, f.name, _coerce(f, vals[f.name]))
        return s

    def set_flat(self, name, value):
        """Set a field by its (globally unique) name, e.g. 'path_width'."""
        for key in ("input", *SECTIONS):
            sec = getattr(self, key)
            for f in fields(sec):
                if f.name == name:
                    setattr(sec, name, _coerce(f, value))
                    return
        raise KeyError(name)

    def save(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))


def _coerce(f, value):
    d = f.default
    if isinstance(d, bool):
        return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
    if isinstance(d, int):
        v = int(float(value))
    elif isinstance(d, float):
        v = float(value)
    else:
        v = str(value)
    ch = f.metadata.get("choices")
    if ch is not None and v not in ch:
        raise ValueError(f"{f.name}: {v!r} is not one of {ch}")
    return v


def schema():
    """JSON description of every UI-visible field (the web form is built from this)."""
    out = []
    for key, (label, cls) in SECTIONS.items():
        items = []
        for f in fields(cls):
            m = f.metadata
            if m.get("ui") is False:
                continue
            d = f.default
            kind = m.get("kind") or ("choice" if "choices" in m else
                                     "bool" if isinstance(d, bool) else
                                     "int" if isinstance(d, int) else
                                     "float" if isinstance(d, float) else "str")
            items.append(dict(name=f.name, label=m.get("label", f.name), help=m.get("help", ""), kind=kind,
                              default=d, choices=m.get("choices"), min=m.get("min"), max=m.get("max"),
                              step=m.get("step")))
        out.append(dict(key=key, label=label, fields=items))
    return out


def _check_unique():
    seen = set()
    for key in ("input", *SECTIONS):
        cls = InputSettings if key == "input" else SECTIONS[key][1]
        for f in fields(cls):
            assert f.name not in seen, f"duplicate setting name {f.name}"
            seen.add(f.name)


_check_unique()
