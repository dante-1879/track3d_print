import json
import urllib.parse
import urllib.request


def search(query, limit=5):
    """Place search via OpenStreetMap Nominatim -> [{name, lat, lon}]."""
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": query, "format": "json", "limit": limit})
    req = urllib.request.Request(url, headers={"User-Agent": "track3d/2.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())
    return [dict(name=d["display_name"], lat=float(d["lat"]), lon=float(d["lon"])) for d in data]
