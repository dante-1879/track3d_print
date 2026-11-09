"""Command line: every setting becomes a flag automatically (see config.py)."""
import argparse
import os
import sys
from dataclasses import fields

from . import gpx, pipeline
from .config import SECTIONS, Settings


def build_parser():
    ap = argparse.ArgumentParser(
        prog="track3d", description="GPX route or place -> 3D-printable terrain (STL / 3MF). "
                                    "Use `track3d ui` for the web interface.")
    ap.add_argument("gpx", nargs="?", help="GPX file of the route (optional if --place / --center / --demo)")
    ap.add_argument("--place", help="search a place name instead of using a GPX")
    ap.add_argument("--center", metavar="LAT,LON", help="map centre instead of a GPX")
    ap.add_argument("--radius", type=float, metavar="KM", help="radius for --place / --center (default 5)")
    ap.add_argument("--demo", action="store_true", help="offline demo with synthetic terrain")
    ap.add_argument("--config", help="load settings from a JSON file")
    ap.add_argument("--save-config", metavar="FILE", help="write the effective settings to a JSON file and exit")
    for key, (_label, cls) in SECTIONS.items():
        g = ap.add_argument_group(key)
        for f in fields(cls):
            m, flag = f.metadata, "--" + f.name.replace("_", "-")
            h = (m.get("label", "") + (f" - {m['help']}" if m.get("help") else "")) + f" (default {f.default!r})"
            if isinstance(f.default, bool):
                if f.default:
                    g.add_argument("--no-" + f.name.replace("_", "-"), dest=f.name, action="store_false",
                                   default=argparse.SUPPRESS, help="disable: " + m.get("label", f.name))
                else:
                    g.add_argument(flag, dest=f.name, action="store_true", default=argparse.SUPPRESS, help=h)
            elif "choices" in m:
                g.add_argument(flag, dest=f.name, choices=[str(c) for c in m["choices"]],
                               default=argparse.SUPPRESS, help=h)
            else:
                g.add_argument(flag, dest=f.name, type=type(f.default), default=argparse.SUPPRESS, help=h)
    return ap


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("ui", "serve"):
        from .web.server import serve
        p = argparse.ArgumentParser(prog="track3d ui", description="Web UI + headless build API (POST /api/build). "
                                    "Each option also reads a TRACK3D_* environment variable.")
        p.add_argument("--port", type=int, default=8765)
        p.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to accept connections from other machines")
        p.add_argument("--no-browser", action="store_true")
        p.add_argument("--workdir", default="track3d_output", help="where jobs are written")
        p.add_argument("--cors-origin", default=None, metavar="ORIGINS",
                       help="comma-separated page origins allowed to call the API from a browser, or * (env TRACK3D_CORS_ORIGINS)")
        p.add_argument("--api-key", default=None, metavar="KEYS", help="require this key for /api/build; comma-separated for several (env TRACK3D_API_KEY)")
        p.add_argument("--max-builds", type=int, default=None, help="builds running at once (default 2)")
        p.add_argument("--queue-wait", type=float, default=None, help="seconds a request waits for a free slot before 503 (default 30)")
        p.add_argument("--ttl-hours", type=float, default=None, help="hours API results are kept (default 24)")
        p.add_argument("--max-body-mb", type=float, default=None, help="largest accepted request (default 40)")
        p.add_argument("--frame-ancestors", default=None, metavar="ORIGINS",
                       help="restrict which pages may embed the UI in an <iframe> (CSP frame-ancestors; default: any)")
        a = p.parse_args(argv[1:])
        return serve(a.host, a.port, a.workdir, open_browser=not a.no_browser, cors=a.cors_origin, api_key=a.api_key,
                     max_builds=a.max_builds, queue_wait=a.queue_wait, ttl_hours=a.ttl_hours,
                     frame_ancestors=a.frame_ancestors, max_body_mb=a.max_body_mb)

    ap = build_parser()
    ns = vars(ap.parse_args(argv))
    cfg = Settings.load(ns.pop("config")) if ns.get("config") else Settings()
    ns.pop("config", None)
    gpx_path, save = ns.pop("gpx"), ns.pop("save_config")
    if ns.pop("demo"):
        cfg.input.demo = True
    if (place := ns.pop("place")):
        cfg.input.place = place
    if (c := ns.pop("center")):
        cfg.input.center_lat, cfg.input.center_lon = (float(v) for v in c.split(","))
    if (r := ns.pop("radius")) is not None:
        cfg.input.radius_km = r
    for k, v in ns.items():
        cfg.set_flat(k, v)
    if save:
        cfg.save(save)
        print(f"Saved settings to {save}")
        return 0
    try:
        route = gpx.parse_gpx(gpx_path) if gpx_path else None
        hint = os.path.splitext(os.path.basename(gpx_path))[0] if gpx_path else None
        pipeline.run(cfg, route=route, name_hint=hint)
    except ValueError as e:
        sys.exit(f"Error: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
