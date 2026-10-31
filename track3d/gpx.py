import xml.etree.ElementTree as ET

import numpy as np


def _points(root):
    def collect(tagname):
        return [(float(el.attrib["lat"]), float(el.attrib["lon"]))
                for el in root.iter() if el.tag.split("}")[-1] == tagname]
    pts = collect("trkpt") or collect("rtept")
    if len(pts) < 2:
        raise ValueError("No track points found in the GPX file (need <trkpt> or <rtept>).")
    return np.array(pts, dtype=float)  # columns: lat, lon


def parse_gpx(path):
    return _points(ET.parse(path).getroot())


def parse_gpx_text(text):
    return _points(ET.fromstring(text))
