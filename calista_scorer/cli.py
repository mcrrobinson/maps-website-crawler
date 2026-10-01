"""Command-line entry point: assess websites (or score local screenshots) with Calista."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import traceback

from .capture import DEFAULT_VIEWPORT, PageCapturer
from .lighthouse import LighthouseUnavailable, lighthouse_bin
from .scorer import AestheticScorer, normalize_10
from .site import LIGHTHOUSE_MODES, SiteOptions, assess_site
from .weights import DEFAULT_WEIGHTS, download_weights

SITE_FIELDS = [
    "url", "status", "site_score", "site_score_10", "homepage_score", "min_page_score", "max_page_score",
    "worst_page", "pages_scored", "pages_challenge", "pages_error", "homepage_load_ms", "homepage_lcp_ms",
    "median_page_load_ms", "slowest_page_load_ms", "lh_performance", "lh_accessibility", "lh_best_practices",
    "lh_seo", "lh_fcp_ms", "lh_lcp_ms", "lh_speed_index_ms", "lh_tbt_ms", "lh_cls", "lh_pages_audited",
    "seconds", "detail"]
PAGE_FIELDS = [
    "site", "url", "status", "score", "score_10", "score_spread", "run_scores", "final_url", "http_status",
    "title", "ttfb_ms", "fcp_ms", "lcp_ms", "dom_content_loaded_ms", "load_ms", "transfer_bytes",
    "lh_status", "lh_performance", "lh_accessibility", "lh_best_practices", "lh_seo", "lh_fcp_ms",
    "lh_lcp_ms", "lh_speed_index_ms", "lh_tbt_ms", "lh_cls", "lh_server_response_ms", "lh_total_bytes",
    "screenshot", "detail"]


def read_urls(urls: list[str], sites_file: str | None) -> list[str]:
    out = list(urls)
    if sites_file:
        with open(sites_file) as f:
            out += [line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")]
    return out


def parse_viewport(value: str) -> tuple[int, int]:
    try:
        w, h = (int(p) for p in value.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"viewport must look like 1280x800, got {value!r}")
    return w, h


def page_rows(sites: list[dict]) -> list[dict]:
    rows = []
    for site in sites:
        for p in site.get("pages", []):
            row = {k: v for k, v in p.items() if k not in ("runs", "lighthouse")}
            row["site"] = site["url"]
            row["run_scores"] = " ".join(str(s) for s in p.get("run_scores", []))
            lh = p.get("lighthouse") or {}
            row["lh_status"] = lh.get("status")
            row.update({f"lh_{k}": v for k, v in lh.items() if k not in ("status",)})
            rows.append(row)
    return rows


def write_results(sites: list[dict], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "results.json"), "w") as f:
        json.dump(sites, f, indent=2)
    for name, fields, rows in [("sites.csv", SITE_FIELDS, sites), ("pages.csv", PAGE_FIELDS, page_rows(sites))]:
        with open(os.path.join(out_dir, name), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)


def cmd_score(args) -> int:
    urls = read_urls(args.urls, args.sites_file)
    if not urls:
        print("no URLs given (pass URLs or --sites-file)", file=sys.stderr)
        return 2
    if args.lighthouse != "off":
        try:
            lighthouse_bin()
        except LighthouseUnavailable as e:
            print(f"{e} (or pass --lighthouse off)", file=sys.stderr)
            return 2
    opts = SiteOptions(max_pages=args.max_pages, runs=args.runs, lighthouse=args.lighthouse,
                       lighthouse_form_factor=args.lighthouse_form_factor,
                       respect_robots=not args.ignore_robots)
    scorer = AestheticScorer(args.weights)
    sites = []
    with PageCapturer(viewport=args.viewport, headless=not args.headful) as capturer:
        for url in urls:
            print(url, flush=True)
            try:
                site = assess_site(capturer, scorer, url, args.out_dir, opts,
                                   log=lambda m: print(m, flush=True))
            except Exception as e:
                site = {"url": url, "status": "error", "detail": f"{type(e).__name__}: {e}"[:500],
                        "trace": traceback.format_exc()[-2000:], "pages": []}
            print(f"  => {site['status']}  site_score={site.get('site_score')}  "
                  f"pages={site.get('pages_scored', 0)}/{site.get('pages_total', 0)}  "
                  f"lighthouse_perf={site.get('lh_performance')}  {site.get('detail', '')}", flush=True)
            sites.append(site)
            write_results(sites, args.out_dir)  # keep partial results if a later site hangs
    print(f"wrote {args.out_dir}/sites.csv, pages.csv, results.json")
    return 0 if all(s["status"] != "error" for s in sites) else 1


def cmd_score_image(args) -> int:
    scorer = AestheticScorer(args.weights)
    for path in args.images:
        s = scorer.score_file(path)
        print(f"{s:.3f}  ({normalize_10(s)}/10)  {path}")
    return 0


def cmd_download_weights(args) -> int:
    print(download_weights(args.weights, force=args.force))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="calista_scorer",
                                 description="Score website aesthetics with the Calista rating-based CNN.")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("score", help="assess websites: several pages, several runs, plus Lighthouse")
    p.add_argument("urls", nargs="*")
    p.add_argument("--sites-file", help="file with one URL per line (# comments allowed)")
    p.add_argument("--out-dir", default="results", help="where sites.csv, pages.csv, results.json, "
                                                        "screenshots/ and lighthouse/ go")
    p.add_argument("--max-pages", type=int, default=5, help="pages per site, including the homepage")
    p.add_argument("--runs", type=int, default=3, help="captures per page; the median score is kept")
    p.add_argument("--lighthouse", choices=LIGHTHOUSE_MODES, default="home",
                   help="run Lighthouse on the homepage, every scored page, or not at all")
    p.add_argument("--lighthouse-form-factor", choices=("mobile", "desktop"), default="mobile")
    p.add_argument("--ignore-robots", action="store_true", help="don't check robots.txt for sub-pages")
    p.add_argument("--viewport", type=parse_viewport, default=DEFAULT_VIEWPORT, help="e.g. 1280x800")
    p.add_argument("--headful", action="store_true", help="show the browser window")
    p.add_argument("--weights", default=DEFAULT_WEIGHTS)
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("score-image", help="score existing screenshot files")
    p.add_argument("images", nargs="+")
    p.add_argument("--weights", default=DEFAULT_WEIGHTS)
    p.set_defaults(func=cmd_score_image)

    p = sub.add_parser("download-weights", help="fetch the authors' released weights (~96 MB)")
    p.add_argument("--weights", default=DEFAULT_WEIGHTS)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_download_weights)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "runs", 1) < 1 or getattr(args, "max_pages", 1) < 1:
        build_parser().error("--runs and --max-pages must be at least 1")
    return args.func(args)
