"""Importing a module here registers its feature. To add one (water, contours, ...), create a
module with a @register class and add it to this import line."""
from .base import REGISTRY, Feature, register  # noqa: F401
from . import path, roads, water, green, buildings, logo  # noqa: F401,E402
