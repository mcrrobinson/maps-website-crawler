"""Command-line entry point: score websites (or local screenshots) with Calista."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import traceback

from .capture import DEFAULT_VIEWPORT, PageCapturer, slug
from .scorer import AestheticScorer, normalize_10, save_model_input
from .weights import DEFAULT_WEIGHTS, download_weights

FIELDS = ["url", "status", "score", "score_10", "final_url", "http_status", "title", "detail",
          "links_found", "screenshot"]


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


def assess(capturer: PageCapturer, scorer: AestheticScorer, url: str, artifacts_dir: str) -> dict:
    """Capture and score one site. Challenge / error pages are reported but never scored."""
    rec = {"url": url, "status": "error", "score": None, "score_10": None, "detail": ""}
    try:
        cap = capturer.capture(url, os.path.join(artifacts_dir, slug(url)))
    except Exception as e:
        rec["detail"] = f"{type(e).__name__}: {e}"[:500]
        rec["trace"] = traceback.format_exc()[-2000:]
        return rec
    rec.update(final_url=cap.final_url, http_status=cap.http_status, title=cap.title,
               links_found=cap.links_found, screenshot=cap.screenshot, actions=cap.actions)
    raw = scorer.score_file(cap.screenshot)
    save_model_input(cap.screenshot, os.path.join(os.path.dirname(cap.screenshot), "model_input.png"))
    if cap.challenge_reason:
        rec["status"] = "challenge"
        rec["detail"] = (f"bot-check/error page ({cap.challenge_reason}); not scored "
                         f"(raw model output would be {raw:.3f})")
    else:
        rec["status"] = "ok"
        rec["score"] = round(raw, 3)
        rec["score_10"] = normalize_10(raw)
    return rec


def write_results(results: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if path.endswith(".json"):
        with open(path, "w") as f:
            json.dump(results, f, indent=2)
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)


def cmd_score(args) -> int:
    urls = read_urls(args.urls, args.sites_file)
    if not urls:
        print("no URLs given (pass URLs or --sites-file)", file=sys.stderr)
        return 2
    scorer = AestheticScorer(args.weights)
    results = []
    with PageCapturer(viewport=args.viewport, headless=not args.headful) as capturer:
        for url in urls:
            rec = assess(capturer, scorer, url, args.artifacts_dir)
            print(f"{rec['status']:<9} {str(rec['score']):>6}  {url}  {rec['detail']}", flush=True)
            results.append(rec)
    write_results(results, args.out)
    print(f"wrote {args.out}")
    return 0 if all(r["status"] != "error" for r in results) else 1


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

    p = sub.add_parser("score", help="screenshot and score website homepages")
    p.add_argument("urls", nargs="*")
    p.add_argument("--sites-file", help="file with one URL per line (# comments allowed)")
    p.add_argument("--out", default="results.csv", help="results file (.csv or .json)")
    p.add_argument("--artifacts-dir", default="screenshots")
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
    return args.func(args)
