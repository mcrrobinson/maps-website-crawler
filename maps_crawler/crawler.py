"""Orchestrates an outward crawl: origin -> expanding grid -> Places API -> storage."""

from typing import Callable, Optional

from .geocode import geocode_address
from .grid import Tile, generate_grid
from .places_client import PlacesClient
from .storage import Storage

ProgressCallback = Callable[[Tile, int, int, int], None]


def resolve_origin(
    address: Optional[str],
    lat: Optional[float],
    lng: Optional[float],
    api_key: str,
) -> tuple[float, float]:
    if address:
        return geocode_address(address, api_key)
    if lat is not None and lng is not None:
        return lat, lng
    raise ValueError("Provide either --address, or both --lat and --lng")


def crawl(
    storage: Storage,
    client: PlacesClient,
    origin_lat: float,
    origin_lng: float,
    tile_radius_m: float,
    max_rings: int,
    included_types: Optional[list[str]] = None,
    max_places: Optional[int] = None,
    early_stop_empty_rings: int = 2,
    force: bool = False,
    on_progress: Optional[ProgressCallback] = None,
) -> int:
    """Runs the grid crawl. Returns the number of newly discovered places."""
    total_new = 0
    current_ring = -1
    ring_had_new = False
    empty_ring_streak = 0

    for tile in generate_grid(origin_lat, origin_lng, tile_radius_m, max_rings):
        if tile.ring != current_ring:
            if current_ring >= 0:
                empty_ring_streak = 0 if ring_had_new else empty_ring_streak + 1
                if early_stop_empty_rings and empty_ring_streak >= early_stop_empty_rings:
                    break
            current_ring = tile.ring
            ring_had_new = False

        if max_places is not None and storage.count_places() >= max_places:
            break

        if not force and storage.is_tile_visited(tile):
            continue

        places = client.search_nearby(tile.lat, tile.lng, tile.radius_m, included_types)

        new_count = 0
        for place in places:
            if storage.upsert_place(place, tile.ring):
                new_count += 1
                total_new += 1

        if new_count:
            ring_had_new = True

        storage.mark_tile_visited(tile, len(places))
        storage.commit()

        if on_progress:
            on_progress(tile, len(places), new_count, total_new)

    return total_new
