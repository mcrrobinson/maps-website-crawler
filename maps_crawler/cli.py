import argparse
import os
import sys

from dotenv import load_dotenv

from .config import ConfigError, get_api_key
from .crawler import crawl, resolve_origin
from .geocode import GeocodeError
from .places_client import PlacesClient
from .storage import Storage


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="maps-crawler",
        description=(
            "Crawl businesses outward from a starting location using the Google "
            "Places API, collecting name/address/website for each."
        ),
    )

    origin = p.add_argument_group("starting location (pick one)")
    origin.add_argument("--address", help="Address or place name to geocode as the origin")
    origin.add_argument("--lat", type=float, help="Origin latitude")
    origin.add_argument("--lng", type=float, help="Origin longitude")

    search = p.add_argument_group("search behavior")
    search.add_argument(
        "--tile-radius", type=float, default=700,
        help="Search radius per tile, in meters (default: 700)",
    )
    search.add_argument(
        "--max-rings", type=int, default=6,
        help="Maximum number of rings to expand outward (default: 6)",
    )
    search.add_argument(
        "--types", default=None,
        help="Comma-separated Places 'included types' to filter to "
             "(e.g. restaurant,cafe,store). Default: all types.",
    )
    search.add_argument(
        "--max-places", type=int, default=None,
        help="Stop once this many total places have been collected",
    )
    search.add_argument(
        "--early-stop-empty-rings", type=int, default=2,
        help="Stop after this many consecutive rings yield zero new places "
             "(0 disables early stopping, default: 2)",
    )
    search.add_argument(
        "--force", action="store_true",
        help="Re-search tiles even if already recorded as visited in the DB",
    )
    search.add_argument(
        "--qps", type=float, default=8.0,
        help="Max requests per second to the Places API (default: 8)",
    )

    out = p.add_argument_group("output")
    out.add_argument("--db", default="places.db", help="SQLite database path (default: places.db)")
    out.add_argument("--csv", default=None, help="Export results to this CSV path when done")
    out.add_argument(
        "--with-website-only", action="store_true",
        help="When exporting CSV, only include places that have a website",
    )

    p.add_argument("--api-key", default=None, help="Google Maps API key (else GOOGLE_MAPS_API_KEY env var)")

    return p


def _apply_env_origin_defaults(args) -> None:
    """Falls back to ADDRESS/LAT/LNG from .env, but only if the user passed
    none of --address/--lat/--lng on the command line (CLI always wins)."""
    if args.address is not None or args.lat is not None or args.lng is not None:
        return

    env_address = os.environ.get("ADDRESS")
    env_lat = os.environ.get("LAT")
    env_lng = os.environ.get("LNG")

    if env_address:
        args.address = env_address
    elif env_lat and env_lng:
        args.lat = float(env_lat)
        args.lng = float(env_lng)


def main(argv=None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    _apply_env_origin_defaults(args)

    try:
        api_key = get_api_key(
            args.api_key,
            hint="The key must have the 'Places API (New)' and 'Geocoding API' enabled in Google Cloud Console.",
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        origin_lat, origin_lng = resolve_origin(args.address, args.lat, args.lng, api_key)
    except (ValueError, GeocodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Starting at {origin_lat:.6f}, {origin_lng:.6f}")
    print(f"Tile radius: {args.tile_radius}m | max rings: {args.max_rings} | "
          f"max radius: ~{args.tile_radius * 1.6 * args.max_rings:.0f}m")

    included_types = [t.strip() for t in args.types.split(",")] if args.types else None

    storage = Storage(args.db)
    client = PlacesClient(api_key, qps=args.qps)

    def on_progress(tile, result_count, new_count, total_new):
        print(
            f"  ring {tile.ring:>2} tile ({tile.lat:.5f}, {tile.lng:.5f}): "
            f"{result_count} results, {new_count} new (total new: {total_new})"
        )

    try:
        total_new = crawl(
            storage,
            client,
            origin_lat,
            origin_lng,
            tile_radius_m=args.tile_radius,
            max_rings=args.max_rings,
            included_types=included_types,
            max_places=args.max_places,
            early_stop_empty_rings=args.early_stop_empty_rings,
            force=args.force,
            on_progress=on_progress,
        )
    except KeyboardInterrupt:
        print("\nInterrupted — progress so far is saved in the database.")
        total_new = None

    total_places = storage.count_places()
    with_website = storage.count_places(with_website_only=True)
    print(f"\nDiscovered {total_new if total_new is not None else '(interrupted)'} new place(s) this run.")
    print(f"Database now has {total_places} place(s) total, {with_website} with a website.")

    if args.csv:
        storage.export_csv(args.csv, with_website_only=args.with_website_only)
        print(f"Exported to {args.csv}")

    storage.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
