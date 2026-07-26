import argparse
import csv
import sys

from dotenv import load_dotenv

from .config import ConfigError, get_api_key
from .lighthouse import audit_websites, plan_jobs
from .lighthouse_client import LighthouseClient
from .lighthouse_storage import LighthouseStorage


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="maps-crawler-lighthouse",
        description=(
            "Run Google PageSpeed Insights (hosted Lighthouse) audits over the "
            "websites collected by maps_crawler."
        ),
    )

    inp = p.add_argument_group("input")
    inp.add_argument(
        "--input-csv", default="results.csv",
        help="CSV containing a website column (default: results.csv)",
    )
    inp.add_argument(
        "--website-column", default="website",
        help="Name of the column holding the URL (default: website)",
    )

    audit = p.add_argument_group("audit behavior")
    audit.add_argument(
        "--strategy", choices=["mobile", "desktop", "both"], default="both",
        help="Device strategy to audit (default: both)",
    )
    audit.add_argument(
        "--qps", type=float, default=3.0,
        help="Max PageSpeed Insights requests/second (default: 3)",
    )
    audit.add_argument(
        "--max-workers", type=int, default=8,
        help="Concurrent in-flight requests (default: 8)",
    )
    audit.add_argument(
        "--force", action="store_true",
        help="Re-audit websites that already have a successful result on file",
    )

    out = p.add_argument_group("output")
    out.add_argument("--db", default="lighthouse.db", help="SQLite db path (default: lighthouse.db)")
    out.add_argument("--out-csv", default="lighthouse_results.csv", help="CSV export path when done")
    out.add_argument(
        "--screenshot-dir", default="screenshots",
        help="Directory to save full-page screenshots to (default: screenshots)",
    )

    p.add_argument(
        "--api-key", default=None,
        help="Google API key with 'PageSpeed Insights API' enabled (else GOOGLE_MAPS_API_KEY env var)",
    )

    return p


def _load_websites(path: str, column: str) -> list[str]:
    seen = set()
    websites = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if column not in (reader.fieldnames or []):
            raise ValueError(f"Column '{column}' not found in {path}. Columns: {reader.fieldnames}")
        for row in reader:
            url = (row.get(column) or "").strip()
            if url and url not in seen:
                seen.add(url)
                websites.append(url)
    return websites


def main(argv=None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)

    try:
        api_key = get_api_key(
            args.api_key,
            hint="The key must have the 'PageSpeed Insights API' enabled in Google Cloud Console.",
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        websites = _load_websites(args.input_csv, args.website_column)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not websites:
        print("No websites found.", file=sys.stderr)
        return 1

    strategies = ["mobile", "desktop"] if args.strategy == "both" else [args.strategy]

    storage = LighthouseStorage(args.db)
    client = LighthouseClient(api_key, qps=args.qps)

    jobs = plan_jobs(storage, websites, strategies, force=args.force)
    skipped = len(websites) * len(strategies) - len(jobs)
    print(f"{len(websites)} unique website(s) x {len(strategies)} strategy(ies)")
    print(
        f"{len(jobs)} audit(s) to run"
        + (f" ({skipped} already completed, skipping — use --force to redo)" if skipped else "")
        + f" (qps={args.qps}, workers={args.max_workers})"
    )

    done = 0

    def on_progress(url, strategy, ok, error):
        nonlocal done
        done += 1
        status = "ok" if ok else f"FAILED ({error})"
        print(f"  [{done}/{len(jobs)}] {strategy:7} {url} — {status}")

    try:
        audit_websites(
            storage, client, jobs,
            screenshot_dir=args.screenshot_dir,
            max_workers=args.max_workers,
            on_progress=on_progress,
        )
    except KeyboardInterrupt:
        print("\nInterrupted — progress so far is saved in the database.")

    total = storage.count_audits()
    failed = storage.count_audits(errors_only=True)
    print(f"\n{total - failed} successful, {failed} failed, {total} total audit(s) in {args.db}.")

    storage.export_csv(args.out_csv)
    print(f"Exported to {args.out_csv}")
    print(f"Screenshots saved under {args.screenshot_dir}/")

    storage.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
