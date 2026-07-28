import argparse
import csv
import sys

from dotenv import load_dotenv

from .aesthetic_reviewer import DEFAULT_MODEL, AestheticReviewer
from .config import ConfigError, get_api_key
from .site_audit import plan_jobs, run_site_audit
from .site_audit_storage import SiteAuditStorage


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="maps-crawler-site-audit",
        description=(
            "Crawl the websites collected by maps_crawler, screenshot them "
            "(desktop + mobile, with cookie/consent banners cleared), score "
            "their visual design quality with Claude, and check every "
            "discovered link for validity."
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
        "--max-pages-per-site", type=int, default=15,
        help="Max pages crawled per site for link discovery, homepage included (default: 15)",
    )
    audit.add_argument(
        "--max-concurrency", type=int, default=4,
        help="Max sites crawled concurrently (default: 4 — real browser tabs are heavier than HTTP calls)",
    )
    audit.add_argument(
        "--vision-model", default=DEFAULT_MODEL,
        help=f"Anthropic vision model used for look-scoring (default: {DEFAULT_MODEL})",
    )
    audit.add_argument(
        "--force", action="store_true",
        help="Re-audit websites that already have a successful result on file",
    )
    audit.add_argument(
        "--headed", action="store_true",
        help="Show the browser window instead of running headless (useful for watching/debugging)",
    )

    out = p.add_argument_group("output")
    out.add_argument("--db", default="site_audit.db", help="SQLite db path (default: site_audit.db)")
    out.add_argument("--out-csv", default="site_audit_results.csv", help="Per-site CSV export path when done")
    out.add_argument("--links-csv", default="site_audit_links.csv", help="Per-link CSV export path when done")
    out.add_argument(
        "--screenshot-dir", default="screenshots",
        help="Directory to save desktop/mobile screenshots to (default: screenshots)",
    )

    p.add_argument(
        "--anthropic-api-key", default=None,
        help="Anthropic API key (else ANTHROPIC_API_KEY env var)",
    )

    return p


def _load_websites(path: str, column: str) -> list:
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
            args.anthropic_api_key,
            hint="Get one from https://console.anthropic.com/.",
            env_var="ANTHROPIC_API_KEY",
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

    storage = SiteAuditStorage(args.db)
    reviewer = AestheticReviewer(api_key, model=args.vision_model)

    jobs = plan_jobs(storage, websites, force=args.force)
    skipped = len(websites) - len(jobs)
    print(f"{len(websites)} unique website(s)")
    print(
        f"{len(jobs)} audit(s) to run"
        + (f" ({skipped} already completed, skipping — use --force to redo)" if skipped else "")
        + f" (max_concurrency={args.max_concurrency}, max_pages_per_site={args.max_pages_per_site})"
    )

    done = 0

    def on_progress(website, ok, error):
        nonlocal done
        done += 1
        status = "ok" if ok else f"FAILED ({error})"
        print(f"  [{done}/{len(jobs)}] {website} — {status}")

    try:
        run_site_audit(
            storage, reviewer, jobs,
            screenshot_dir=args.screenshot_dir,
            max_pages=args.max_pages_per_site,
            max_concurrency=args.max_concurrency,
            on_progress=on_progress,
            headless=not args.headed,
        )
    except KeyboardInterrupt:
        print("\nInterrupted — progress so far is saved in the database.")

    total = storage.count_audits()
    failed = storage.count_audits(errors_only=True)
    print(f"\n{total - failed} successful, {failed} failed, {total} total audit(s) in {args.db}.")

    storage.export_csv(args.out_csv)
    storage.export_links_csv(args.links_csv)
    print(f"Exported to {args.out_csv} and {args.links_csv}")
    print(f"Screenshots saved under {args.screenshot_dir}/")

    storage.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
