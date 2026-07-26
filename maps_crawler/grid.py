"""Generates an outward-expanding grid of search tiles centered on an origin.

Tiles are arranged in concentric square rings (ring 0 = the origin tile,
ring 1 = the 8 tiles surrounding it, ring 2 = the 16 tiles around that, ...).
Ring order means the crawl always finishes searching everything within
radius N before moving on to radius N+1.
"""

from dataclasses import dataclass
from math import cos, radians

METERS_PER_DEGREE_LAT = 111_320.0


@dataclass(frozen=True)
class Tile:
    ring: int
    lat: float
    lng: float
    radius_m: float


def _offset_latlng(origin_lat: float, origin_lng: float, dx_m: float, dy_m: float) -> tuple[float, float]:
    """Offsets a lat/lng by dx/dy meters (east/north)."""
    dlat = dy_m / METERS_PER_DEGREE_LAT
    meters_per_degree_lng = METERS_PER_DEGREE_LAT * cos(radians(origin_lat))
    dlng = dx_m / meters_per_degree_lng if meters_per_degree_lng else 0.0
    return origin_lat + dlat, origin_lng + dlng


def generate_grid(
    origin_lat: float,
    origin_lng: float,
    tile_radius_m: float,
    max_rings: int,
    overlap_factor: float = 1.6,
):
    """Yields Tile objects ring by ring, outward from the origin.

    overlap_factor controls tile center spacing relative to tile_radius_m.
    A value below 2.0 makes neighboring circular tiles overlap slightly so
    there are no gaps between them (circles inscribed in a square grid
    would otherwise leave uncovered corners).
    """
    if tile_radius_m <= 0:
        raise ValueError("tile_radius_m must be positive")
    if max_rings < 0:
        raise ValueError("max_rings must be >= 0")

    step_m = tile_radius_m * overlap_factor

    yield Tile(ring=0, lat=origin_lat, lng=origin_lng, radius_m=tile_radius_m)

    for ring in range(1, max_rings + 1):
        coords = set()
        for i in range(-ring, ring + 1):
            coords.add((i, ring))
            coords.add((i, -ring))
        for j in range(-ring, ring + 1):
            coords.add((ring, j))
            coords.add((-ring, j))

        for i, j in sorted(coords):
            dx_m = i * step_m
            dy_m = j * step_m
            lat, lng = _offset_latlng(origin_lat, origin_lng, dx_m, dy_m)
            yield Tile(ring=ring, lat=lat, lng=lng, radius_m=tile_radius_m)
