"""Orchestrates concurrent Lighthouse (PageSpeed Insights) audits over a list
of websites, saving scores/metrics to storage and screenshots to disk.

PSI audits are latency-bound (each call takes Google ~15-30s to run
Lighthouse server-side), not throughput-bound by our own pacing, so this
runs many requests concurrently through a thread pool while a shared rate
limiter in LighthouseClient caps actual requests/second.
"""

import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

from .lighthouse_client import LighthouseApiError, LighthouseClient, parse_lighthouse_result
from .lighthouse_storage import LighthouseStorage

Job = tuple[str, str]  # (website, strategy)
ProgressCallback = Callable[[str, str, bool, Optional[str]], None]


def plan_jobs(
    storage: LighthouseStorage,
    websites: list[str],
    strategies: list[str],
    force: bool = False,
) -> list[Job]:
    """Returns the (website, strategy) pairs that still need auditing."""
    jobs = []
    for url in websites:
        for strategy in strategies:
            if force or not storage.has_audit(url, strategy):
                jobs.append((url, strategy))
    return jobs


_MIME_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}


def _screenshot_filename(url: str, strategy: str, mime: Optional[str]) -> str:
    ext = _MIME_EXTENSIONS.get(mime or "", "img")
    digest = hashlib.sha1(f"{url}|{strategy}".encode()).hexdigest()[:16]
    return f"{digest}_{strategy}.{ext}"


def _run_one(client: LighthouseClient, url: str, strategy: str):
    try:
        payload = client.run_audit(url, strategy)
        parsed = parse_lighthouse_result(payload)
    except LighthouseApiError as exc:
        return url, strategy, None, str(exc)
    except Exception as exc:  # network hiccups, malformed JSON, etc.
        return url, strategy, None, f"{type(exc).__name__}: {exc}"
    return url, strategy, parsed, None


def audit_websites(
    storage: LighthouseStorage,
    client: LighthouseClient,
    jobs: list[Job],
    screenshot_dir: str,
    max_workers: int = 8,
    on_progress: Optional[ProgressCallback] = None,
) -> None:
    Path(screenshot_dir).mkdir(parents=True, exist_ok=True)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_run_one, client, url, strategy) for url, strategy in jobs]
        for future in as_completed(futures):
            url, strategy, parsed, error = future.result()

            screenshot_path = None
            if parsed and parsed.get("screenshot_base64"):
                filename = _screenshot_filename(url, strategy, parsed.get("screenshot_mime"))
                out_path = Path(screenshot_dir) / filename
                out_path.write_bytes(base64.b64decode(parsed["screenshot_base64"]))
                screenshot_path = str(out_path)

            storage.upsert_audit(url, strategy, parsed, screenshot_path, error)
            storage.commit()

            if on_progress:
                on_progress(url, strategy, error is None, error)
