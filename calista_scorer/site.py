"""Assess a whole site: score several pages, several times each, and run Lighthouse.

Pages that turn out to be bot checks / error pages are skipped entirely: they are never
passed to Calista or Lighthouse. If the homepage itself is one, the site is "blocked".
"""
from __future__ import annotations

import os
import statistics
import time
import traceback
from dataclasses import dataclass

from .capture import slug
from .crawl import pick_pages, robots_checker
from .lighthouse import run_lighthouse
from .scorer import normalize_10, save_model_input

LIGHTHOUSE_MODES = ("home", "all", "off")


@dataclass
class SiteOptions:
    max_pages: int = 5
    runs: int = 3
    lighthouse: str = "home"
    lighthouse_form_factor: str = "mobile"
    page_delay_s: float = 1.0  # pause between page loads on the same site
    respect_robots: bool = True


def _median(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 3) if values else None


def assess_page(capturer, scorer, url: str, runs: int, page_dir: str) -> dict:
    """Capture and score one page `runs` times (fresh browser context each time)."""
    rec = {"url": url, "status": "error", "score": None, "score_10": None, "run_scores": [],
           "detail": "", "runs": []}
    for i in range(runs):
        try:
            cap = capturer.capture(url, page_dir, name=f"run{i + 1}")
        except Exception as e:
            rec["runs"].append({"error": f"{type(e).__name__}: {e}"[:500],
                                "trace": traceback.format_exc()[-2000:]})
            continue
        run = {"final_url": cap.final_url, "http_status": cap.http_status, "title": cap.title,
               "timings": cap.timings, "actions": cap.actions}
        rec["runs"].append(run)
        if cap.challenge_reason:
            # Not scored. Further runs would only hit the same check again.
            rec.update(status="challenge", detail=f"bot-check/error page ({cap.challenge_reason}); not scored",
                       title=cap.title, http_status=cap.http_status, final_url=cap.final_url,
                       challenge_screenshot=cap.challenge_screenshot)
            return rec
        run["screenshot"] = cap.screenshot
        run["media_ready"] = cap.media_ready
        run["score"] = round(scorer.score_file(cap.screenshot), 3)
        rec["run_scores"].append(run["score"])
        if i == 0:
            save_model_input(cap.screenshot, os.path.join(page_dir, "model_input.png"))
            rec["links"] = cap.links

    ok_runs = [r for r in rec["runs"] if "score" in r]
    if not ok_runs:
        rec["detail"] = rec["runs"][-1].get("error", "") if rec["runs"] else "no runs"
        return rec
    # A run whose hero video/images never painted scores a half-empty page; only count
    # those if no run painted fully.
    painted = [r for r in ok_runs if r["media_ready"]] or ok_runs
    first = painted[0]
    rec.update(status="ok", score=_median([r["score"] for r in painted]), title=first["title"],
               http_status=first["http_status"], final_url=first["final_url"],
               screenshot=first["screenshot"],
               score_spread=round(max(rec["run_scores"]) - min(rec["run_scores"]), 3))
    rec["score_10"] = normalize_10(rec["score"])
    for key in ("ttfb_ms", "dom_content_loaded_ms", "load_ms", "fcp_ms", "lcp_ms"):
        rec[key] = _median([r["timings"].get(key) for r in ok_runs])
    rec["transfer_bytes"] = first["timings"].get("transfer_bytes")
    notes = []
    if len(ok_runs) < runs:
        notes.append(f"{runs - len(ok_runs)} of {runs} runs failed")
    if len(painted) < len(ok_runs):
        notes.append(f"{len(ok_runs) - len(painted)} run(s) excluded: media never painted")
    elif not all(r["media_ready"] for r in ok_runs):
        notes.append("media never fully painted in any run")
    rec["detail"] = "; ".join(notes)
    return rec


def summarize(url: str, pages: list[dict]) -> dict:
    home = pages[0]
    scored = [p for p in pages if p["status"] == "ok"]
    site = {"url": url, "pages_total": len(pages), "pages_scored": len(scored),
            "pages_challenge": sum(p["status"] == "challenge" for p in pages),
            "pages_error": sum(p["status"] == "error" for p in pages)}
    if home["status"] != "ok":
        site.update(status="blocked" if home["status"] == "challenge" else "error",
                    detail=f"homepage: {home['detail']}")
        return site
    scores = [p["score"] for p in scored]
    site.update(
        status="ok", detail="",
        site_score=round(statistics.mean(scores), 3), site_score_10=normalize_10(statistics.mean(scores)),
        homepage_score=home["score"], min_page_score=min(scores), max_page_score=max(scores),
        worst_page=min(scored, key=lambda p: p["score"])["url"],
        homepage_load_ms=home.get("load_ms"), homepage_lcp_ms=home.get("lcp_ms"),
        median_page_load_ms=_median([p.get("load_ms") for p in scored]),
        slowest_page_load_ms=max((p["load_ms"] for p in scored if p.get("load_ms")), default=None),
    )
    audited = [p["lighthouse"] for p in scored if (p.get("lighthouse") or {}).get("status") == "ok"]
    for key in ("performance", "accessibility", "best_practices", "seo", "fcp_ms", "lcp_ms",
                "speed_index_ms", "tbt_ms", "cls"):
        site[f"lh_{key}"] = _median([a.get(key) for a in audited])
    site["lh_pages_audited"] = len(audited)
    return site


def assess_site(capturer, scorer, url: str, out_dir: str, opts: SiteOptions,
                lighthouse_runner=run_lighthouse, robots=robots_checker, log=print) -> dict:
    site_dir = os.path.join(out_dir, "screenshots", slug(url))
    t0 = time.time()
    home = assess_page(capturer, scorer, url, opts.runs, os.path.join(site_dir, "_home"))
    log(f"  {home['status']:<9} {str(home['score']):>6}  {url}  {home['detail']}")
    pages = [home]
    if home["status"] == "ok" and opts.max_pages > 1:
        allowed = robots(url) if opts.respect_robots else (lambda u: True)
        for page_url in pick_pages(home.get("final_url") or url, home.get("links", []),
                                   opts.max_pages, allowed)[1:]:
            time.sleep(opts.page_delay_s)
            rec = assess_page(capturer, scorer, page_url, opts.runs, os.path.join(site_dir, slug(page_url)))
            log(f"  {rec['status']:<9} {str(rec['score']):>6}  {page_url}  {rec['detail']}")
            pages.append(rec)

    if opts.lighthouse != "off":
        targets = [p for p in pages if p["status"] == "ok"][: 1 if opts.lighthouse == "home" else None]
        for p in targets:
            report = os.path.join(out_dir, "lighthouse", f"{slug(p['url'])}.{opts.lighthouse_form_factor}.json")
            p["lighthouse"] = lighthouse_runner(p.get("final_url") or p["url"], report, opts.lighthouse_form_factor)
            lh = p["lighthouse"]
            log(f"  lighthouse {lh['status']:<7} perf={lh.get('performance')} "
                f"lcp={lh.get('lcp_ms')}ms  {p['url']}  {lh.get('runtime_error') or ''}")

    for p in pages:
        p.pop("links", None)
    site = summarize(url, pages)
    site["seconds"] = round(time.time() - t0, 1)
    site["pages"] = pages
    return site
