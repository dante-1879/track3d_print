"""Feature plugin interface. A feature turns the shared Context into one printable part."""
REGISTRY = []


def register(cls):
    REGISTRY.append(cls())
    REGISTRY.sort(key=lambda f: f.order)
    return cls


class Feature:
    name = ""        # part name, used in file names
    label = ""       # shown in the UI
    order = 50       # build order (lower first)

    def enabled(self, cfg):
        return True

    def color(self, cfg):
        return "#cccccc"

    def prepare(self, ctx):
        """Called before the terrain is meshed; may adjust ctx (e.g. ctx.base_lift)."""

    def build(self, ctx):
        """Return triangles (N,3,3) in mm, None if there is nothing to print, or a list of
        (name, label, color, triangles) tuples when the feature makes several parts."""
        raise NotImplementedError
