"""Async Playwright crawler: per-site navigation, cookie/consent-overlay
dismissal, screenshot capture (desktop + mobile), link discovery, and real
navigation timing.

Consent dismissal is layered, cheapest first:
  1. DuckDuckGo's `autoconsent` (vendor/autoconsent) — a maintained,
     per-CMP ruleset (OneTrust, Cookiebot, Quantcast, Didomi, Sourcepoint,
     etc.) that also powers cookie handling in Firefox/Brave. Verified
     against bbc.com (inline banner) and generic Sourcepoint iframes.
  2. A free text-match fallback (search every frame, including
     cross-origin CMP iframes, for a visible "accept/agree/got it"-style
     button) for banners autoconsent's ruleset doesn't cover. Verified
     against theguardian.com's consent-or-pay wall, which autoconsent's
     opt-in rules don't clear.
  3. An optional vision-guided click loop, supplied by the caller
     (see aesthetic_reviewer.py) and applied only to homepage screenshots,
     for anything neither of the above catches.

An earlier attempt loaded the "I-Still-Dont-Care-About-Cookies" unpacked
Manifest V3 extension via `--load-extension` in headless Chromium — it
loaded without error but its service-worker background script never
actually activated, so it silently did nothing. autoconsent ships as a
plain content-script bundle instead, so it doesn't depend on the
extension service-worker lifecycle.

Real business websites are unpredictable — every Playwright call here has
an explicit timeout, and failures are recorded per-page/per-site rather
than raised, matching the tolerance the old Lighthouse pipeline needed
("plenty of these sites are slow, broken, or misconfigured").
"""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import urlsplit

from playwright.async_api import (
    Browser,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "autoconsent"
AUTOCONSENT_JS = VENDOR_DIR / "autoconsent.playwright.js"
AUTOCONSENT_RULES = json.loads((VENDOR_DIR / "rules.json").read_text())["autoconsent"]

DESKTOP_VIEWPORT = {"width": 1366, "height": 900}
MOBILE_DEVICE_NAME = "iPhone 13"

NAV_TIMEOUT_MS = 20000
CONSENT_WAIT_MS = 6000
SETTLE_WAIT_MS = 1000
SCREENSHOT_TIMEOUT_MS = 8000
MAX_SCREENSHOT_HEIGHT_PX = 15000

_CONSENT_TEXT_PATTERNS = [
    "accept all", "accept cookies", "i agree", "allow all", "got it",
    "yes, i agree", "agree", "accept",
]

# Returns (relative_x, relative_y) in [0, 1] of an element to click to
# dismiss a detected overlay, or None if no overlay is present.
VisionOverlayCheck = Callable[[bytes], Awaitable[Optional[tuple]]]


@dataclass
class PageCrawlResult:
    url: str
    http_status: Optional[int]
    load_time_ms: Optional[float]
    links: list = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class SiteCrawlResult:
    website: str
    final_url: Optional[str] = None
    http_status: Optional[int] = None
    load_time_ms: Optional[float] = None
    pages_crawled: int = 0
    desktop_screenshot: Optional[bytes] = None
    mobile_screenshot: Optional[bytes] = None
    links: dict = field(default_factory=dict)  # link_url -> source page it was found on
    error: Optional[str] = None


def normalize_url(url: str) -> str:
    return url if urlsplit(url).scheme else f"https://{url}"


def same_site(url_a: str, url_b: str) -> bool:
    host_a = urlsplit(url_a).netloc.lower().removeprefix("www.")
    host_b = urlsplit(url_b).netloc.lower().removeprefix("www.")
    return bool(host_a) and host_a == host_b


async def _install_autoconsent(page: Page) -> asyncio.Event:
    """Wires up autoconsent's message-passing protocol on a fresh page.
    Must be called before navigation."""
    done = asyncio.Event()

    async def handle(source, message):
        msg_type = message.get("type")
        try:
            if msg_type == "init":
                await page.evaluate(
                    "(msg) => autoconsentReceiveMessage(msg)",
                    {
                        "type": "initResp",
                        "rules": AUTOCONSENT_RULES,
                        "config": {
                            "enabled": True,
                            "autoAction": "optIn",
                            "disabledCmps": [],
                            "enablePrehide": True,
                            "enableCosmeticRules": True,
                            "detectRetries": 20,
                        },
                    },
                )
            elif msg_type in ("autoconsentDone", "autoconsentError"):
                done.set()
        except Exception:
            # autoconsent keeps retrying detection in the background; once we've
            # moved on and closed the page, those late messages have nowhere to go.
            done.set()

    await page.expose_binding("autoconsentSendMessage", lambda source, message: handle(source, message))
    await page.add_init_script(path=str(AUTOCONSENT_JS))
    return done


async def _text_match_dismiss(page: Page) -> bool:
    """Fallback: click the first visible button/link whose text looks like
    a consent-accept action, searching every frame (banners are often
    rendered in a cross-origin CMP iframe)."""
    for frame in page.frames:
        try:
            candidates = await asyncio.wait_for(
                frame.query_selector_all("button, a, [role=button]"), timeout=3
            )
        except Exception:
            continue
        for el in candidates:
            try:
                if not await asyncio.wait_for(el.is_visible(), timeout=1):
                    continue
                text = ((await asyncio.wait_for(el.inner_text(), timeout=1)) or "").strip().lower()
            except Exception:
                continue
            if not text or len(text) > 40:
                continue
            if any(pattern in text for pattern in _CONSENT_TEXT_PATTERNS):
                try:
                    await el.click(timeout=1000)
                    return True
                except Exception:
                    continue
    return False


async def _dismiss_consent_overlays(page: Page, autoconsent_done: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(autoconsent_done.wait(), timeout=CONSENT_WAIT_MS / 1000)
    except asyncio.TimeoutError:
        await _text_match_dismiss(page)
    await page.wait_for_timeout(SETTLE_WAIT_MS)


async def _navigation_load_ms(page: Page) -> Optional[float]:
    try:
        value = await page.evaluate(
            "() => { const n = performance.getEntriesByType('navigation')[0]; "
            "return n ? n.loadEventEnd : null; }"
        )
    except Exception:
        return None
    return float(value) if value else None


async def _extract_links(page: Page) -> list:
    try:
        hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    except Exception:
        return []
    return [h for h in hrefs if h.startswith("http://") or h.startswith("https://")]


async def _trigger_lazy_loaded_images(page: Page) -> None:
    """Scrolls through the page so below-the-fold lazy-loaded images render
    before a full-page screenshot, instead of showing as empty placeholders.
    Caps how far it scrolls -- some mobile sites (observed on bbc.com) keep
    auto-loading additional homepage content the further down you scroll,
    which would otherwise turn this into an unbounded infinite-scroll."""
    try:
        height = await page.evaluate("() => document.body.scrollHeight")
        height = min(height or 0, MAX_SCREENSHOT_HEIGHT_PX)
        viewport = page.viewport_size or DESKTOP_VIEWPORT
        step = viewport["height"]
        for y in range(0, int(height), step):
            await page.evaluate("(y) => window.scrollTo(0, y)", y)
            await page.wait_for_timeout(150)
        await page.evaluate("() => window.scrollTo(0, 0)")
        await page.wait_for_timeout(300)
    except Exception:
        pass


async def _safe_screenshot(page: Page) -> Optional[bytes]:
    await _trigger_lazy_loaded_images(page)
    try:
        height = await page.evaluate("() => document.body.scrollHeight")
        viewport = page.viewport_size or DESKTOP_VIEWPORT
        capped_height = min(height or viewport["height"], MAX_SCREENSHOT_HEIGHT_PX)
        # full_page=True lets the capture extend beyond the viewport; clip
        # then bounds it to capped_height regardless of how tall the live DOM
        # has grown by -- e.g. from a site auto-loading more content as the
        # page scrolls.
        return await page.screenshot(
            full_page=True,
            clip={"x": 0, "y": 0, "width": viewport["width"], "height": capped_height},
            animations="disabled",
            timeout=SCREENSHOT_TIMEOUT_MS,
        )
    except Exception:
        try:
            # Falls back to just the viewport if even a clipped capture fails.
            return await page.screenshot(full_page=False, animations="disabled", timeout=SCREENSHOT_TIMEOUT_MS)
        except Exception:
            return None


def _ignore_late_playwright_errors(loop, context):
    """autoconsent keeps a detection-retry loop running in the page; when we
    close a page/context while one of its background messages is in flight,
    Playwright's own binding-dispatch machinery logs an unretrievable
    TargetClosedError. Harmless — every error we actually act on is already
    caught at its call site — so this just silences that specific noise."""
    if isinstance(context.get("exception"), PlaywrightError):
        return
    loop.default_exception_handler(context)


class SiteCrawler:
    """Owns one Chromium browser shared across many concurrent crawls (each
    site gets its own BrowserContext, so this is safe to use from several
    concurrently-running `crawl()` calls). Use as an async context manager."""

    def __init__(self, headless: bool = True):
        self._headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None

    async def __aenter__(self) -> "SiteCrawler":
        asyncio.get_event_loop().set_exception_handler(_ignore_late_playwright_errors)
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless, channel="chrome")
        return self

    async def __aexit__(self, *exc):
        await self._browser.close()
        await self._playwright.stop()

    async def _load_page(self, page: Page, url: str, dismiss_consent: bool = True) -> PageCrawlResult:
        autoconsent_done = None
        if dismiss_consent:
            try:
                autoconsent_done = await _install_autoconsent(page)
            except Exception:
                autoconsent_done = None

        try:
            response = await page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            response = None
        except Exception as exc:
            return PageCrawlResult(url=url, http_status=None, load_time_ms=None,
                                    error=f"{type(exc).__name__}: {exc}")

        if dismiss_consent:
            await _dismiss_consent_overlays(page, autoconsent_done or asyncio.Event())

        load_ms = await _navigation_load_ms(page)
        links = await _extract_links(page)
        return PageCrawlResult(
            url=page.url,
            http_status=response.status if response else None,
            load_time_ms=load_ms,
            links=links,
        )

    async def _clear_remaining_overlay(self, page: Page, vision_overlay_check: Optional[VisionOverlayCheck]) -> None:
        if not vision_overlay_check:
            return
        for _ in range(2):
            try:
                shot = await page.screenshot(animations="disabled", timeout=SCREENSHOT_TIMEOUT_MS)
            except Exception:
                return
            try:
                click_at = await vision_overlay_check(shot)
            except Exception:
                return
            if not click_at:
                return
            rel_x, rel_y = click_at
            viewport = page.viewport_size or DESKTOP_VIEWPORT
            try:
                await page.mouse.click(rel_x * viewport["width"], rel_y * viewport["height"])
                await page.wait_for_timeout(SETTLE_WAIT_MS)
            except Exception:
                return

    async def _homepage_screenshot(
        self, context, website: str, vision_overlay_check: Optional[VisionOverlayCheck]
    ):
        page = await context.new_page()
        try:
            result = await self._load_page(page, website)
            if result.error:
                return result, None
            await self._clear_remaining_overlay(page, vision_overlay_check)
            shot = await _safe_screenshot(page)
            return result, shot
        finally:
            await page.close()

    async def _mobile_screenshot(
        self, website: str, vision_overlay_check: Optional[VisionOverlayCheck]
    ) -> Optional[bytes]:
        device = self._playwright.devices[MOBILE_DEVICE_NAME]
        context = await self._browser.new_context(**device)
        try:
            page = await context.new_page()
            result = await self._load_page(page, website)
            if result.error:
                return None
            await self._clear_remaining_overlay(page, vision_overlay_check)
            return await _safe_screenshot(page)
        finally:
            await context.close()

    async def crawl(
        self,
        website: str,
        max_pages: int = 15,
        vision_overlay_check: Optional[VisionOverlayCheck] = None,
    ) -> SiteCrawlResult:
        website = normalize_url(website)
        try:
            context = await self._browser.new_context(viewport=DESKTOP_VIEWPORT)
        except Exception as exc:
            return SiteCrawlResult(website=website, error=f"{type(exc).__name__}: {exc}")

        try:
            homepage, desktop_shot = await self._homepage_screenshot(context, website, vision_overlay_check)
            if homepage.error:
                return SiteCrawlResult(website=website, error=homepage.error)

            links = {link: website for link in homepage.links}
            pages_crawled = 1

            # Cookie consent, once accepted on the homepage, persists via the
            # context's cookie jar — internal pages skip consent handling
            # entirely (and links live in the DOM regardless of any overlay).
            internal_links = [
                link for link in dict.fromkeys(homepage.links)
                if same_site(link, homepage.url) and link != homepage.url
            ]
            for link in internal_links[: max(0, max_pages - 1)]:
                sub_page = await context.new_page()
                try:
                    sub_result = await self._load_page(sub_page, link, dismiss_consent=False)
                    for discovered in sub_result.links:
                        links.setdefault(discovered, link)
                    pages_crawled += 1
                except Exception:
                    pass
                finally:
                    await sub_page.close()

            mobile_shot = await self._mobile_screenshot(website, vision_overlay_check)

            return SiteCrawlResult(
                website=website,
                final_url=homepage.url,
                http_status=homepage.http_status,
                load_time_ms=homepage.load_time_ms,
                pages_crawled=pages_crawled,
                desktop_screenshot=desktop_shot,
                mobile_screenshot=mobile_shot,
                links=links,
            )
        finally:
            await context.close()
