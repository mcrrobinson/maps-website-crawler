import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maps_crawler.grid import generate_grid


def haversine_m(lat1, lng1, lat2, lng2):
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def test_ring_0_is_origin():
    tiles = list(generate_grid(37.0, -122.0, tile_radius_m=500, max_rings=0))
    assert len(tiles) == 1
    assert tiles[0].ring == 0
    assert tiles[0].lat == 37.0
    assert tiles[0].lng == -122.0


def test_ring_tile_counts():
    tiles = list(generate_grid(37.0, -122.0, tile_radius_m=500, max_rings=2))
    by_ring = {}
    for t in tiles:
        by_ring.setdefault(t.ring, []).append(t)

    assert len(by_ring[0]) == 1
    assert len(by_ring[1]) == 8
    assert len(by_ring[2]) == 16


def test_rings_are_yielded_in_order():
    tiles = list(generate_grid(37.0, -122.0, tile_radius_m=500, max_rings=3))
    rings_seen = [t.ring for t in tiles]
    assert rings_seen == sorted(rings_seen)


def test_ring_distance_increases_outward():
    tile_radius_m = 500
    tiles = list(generate_grid(37.0, -122.0, tile_radius_m=tile_radius_m, max_rings=3, overlap_factor=1.6))

    max_dist_by_ring = {}
    for t in tiles:
        d = haversine_m(37.0, -122.0, t.lat, t.lng)
        max_dist_by_ring[t.ring] = max(max_dist_by_ring.get(t.ring, 0), d)

    dists = [max_dist_by_ring[r] for r in sorted(max_dist_by_ring)]
    assert dists == sorted(dists)
    assert dists[0] == 0


def test_no_duplicate_tile_centers_within_a_ring():
    tiles = list(generate_grid(37.0, -122.0, tile_radius_m=500, max_rings=2))
    by_ring = {}
    for t in tiles:
        by_ring.setdefault(t.ring, []).append((round(t.lat, 8), round(t.lng, 8)))

    for ring, coords in by_ring.items():
        assert len(coords) == len(set(coords)), f"duplicate tile centers in ring {ring}"


def test_invalid_args_raise():
    import pytest

    with pytest.raises(ValueError):
        list(generate_grid(37.0, -122.0, tile_radius_m=0, max_rings=1))
    with pytest.raises(ValueError):
        list(generate_grid(37.0, -122.0, tile_radius_m=500, max_rings=-1))
