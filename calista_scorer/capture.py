"""Load a page in headless Chrome and take the above-the-fold screenshot Calista scores.

Read-only: the only clicks are on buttons whose text clearly means "accept cookies" or
"close popup". It never submits forms and never touches CAPTCHA widgets (those live in
cross-origin iframes, which the button search does not enter).

The browser sends Chrome's normal user-agent: the headless build's says "HeadlessChrome",
which some firewalls (Cloudflare rules, for one) reject outright. Beyond that, bot checks
are never worked around. If one appears we give the site's own check a few seconds to
finish by itself (some "Just a moment..." pages redirect automatically), then report the
page as a challenge so the caller can skip it.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

DEFAULT_VIEWPORT = (1280, 800)

DISMISS_RE = re.compile(
    r"^\s*(accept( all)?( cookies)?|allow all|i agree|agree|got it|ok(ay)?|close|no thanks|dismiss|×|x)\s*$",
    re.I)
CHALLENGE_RE = re.compile(
    r"just a moment|attention required|access denied|verify you are human|are you a robot|captcha|"
    r"checking your browser|request blocked|cf-chl|you have been blocked", re.I)
# A page this short with a challenge widget on it is an interstitial, not a real site that
# happens to have a CAPTCHA on its contact form.
SHORT_BODY_CHARS = 400

CHALLENGE_WIDGET_JS = """() => !!document.querySelector(
  "iframe[src*='challenges.cloudflare.com'], iframe[src*='/recaptcha/'], iframe[src*='hcaptcha.com'], " +
  ".cf-turnstile, .g-recaptcha, .h-captcha, #challenge-form, #cf-challenge-running")"""

# True once every image/video visible in the viewport has something painted. Video heroes
# otherwise get screenshotted as a blank box when the stream is slow to start. A paused
# video with a poster counts as painted (click-to-play).
MEDIA_READY_JS = """() => [...document.querySelectorAll('img, video')].filter(e => {
  const r = e.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight;
}).every(e => e.tagName === 'IMG' ? e.complete : (e.readyState >= 2 || (e.paused && !e.autoplay && !!e.poster)))"""

LINKS_JS = """() => [...document.querySelectorAll('a[href]')].map(a => ({
  href: a.href, nav: !!a.closest('nav, header, [role=navigation]')}))"""

# Observed (unthrottled) timings from this visit. LCP is only reported via a buffered
# PerformanceObserver, and stops updating after user input, so read it before any clicks.
TIMINGS_JS = """() => new Promise(resolve => {
  let lcp = null;
  try {
    new PerformanceObserver(list => {
      const e = list.getEntries(); if (e.length) lcp = e[e.length - 1].startTime;
    }).observe({type: 'largest-contentful-paint', buffered: true});
  } catch (e) {}
  setTimeout(() => {
    const n = performance.getEntriesByType('navigation')[0] || {};
    const fcp = performance.getEntriesByName('first-contentful-paint')[0];
    const r = v => (v == null || v <= 0) ? null : Math.round(v);
    resolve({ttfb_ms: r(n.responseStart), dom_content_loaded_ms: r(n.domContentLoadedEventEnd),
             load_ms: r(n.loadEventEnd), fcp_ms: r(fcp && fcp.startTime), lcp_ms: r(lcp),
             transfer_bytes: n.transferSize || null});
  }, 100);
})"""


@dataclass
class Capture:
    url: str
    final_url: str | None = None
    http_status: int | None = None
    title: str = ""
    screenshot: str | None = None
    challenge_screenshot: str | None = None  # kept as evidence only; never scored
    challenge_reason: str | None = None
    media_ready: bool = True  # False if visible images/videos never painted before the screenshot
    links: list = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    actions: list = field(default_factory=list)


def slug(url: str) -> str:
    u = urlparse(url)
    s = u.netloc.replace("www.", "").replace(".", "_").replace(":", "_")
    path = re.sub(r"[^A-Za-z0-9_-]+", "_", u.path.strip("/"))
    return f"{s}_{path}" if path else s


def detect_challenge(title: str, http_status: int | None, body_text: str, has_widget: bool) -> str | None:
    """Return why the page looks like a bot check / error page, or None if it looks real."""
    if http_status is not None and http_status >= 400:
        return f"HTTP {http_status}"
    if CHALLENGE_RE.search(title or ""):
        return f"challenge title {title!r}"
    if len(body_text) < SHORT_BODY_CHARS:
        if CHALLENGE_RE.search(body_text):
            return "challenge text on near-empty page"
        if has_widget:
            return "CAPTCHA widget on near-empty page"
    return None


def dismiss_popups(page, actions: list) -> None:
    for _ in range(2):
        clicked = False
        for loc in [page.get_by_role("button"), page.locator("[aria-label*='close' i], [aria-label*='dismiss' i]")]:
            try:
                n = min(loc.count(), 60)
            except Exception:
                continue
            for i in range(n):
                el = loc.nth(i)
                try:
                    if not el.is_visible():
                        continue
                    txt = (el.inner_text(timeout=500) or "").strip()
                    aria = (el.get_attribute("aria-label", timeout=500) or "").strip()
                    if el.evaluate("e => !!e.closest('form') && e.type === 'submit'"):
                        continue
                    if DISMISS_RE.match(txt) or (not txt and re.search(r"close|dismiss", aria, re.I)):
                        el.click(timeout=1500)
                        actions.append({"action": "click_dismiss", "text": txt[:40], "aria": aria[:40]})
                        clicked = True
                        page.wait_for_timeout(700)
                        break
                except Exception:
                    continue
            if clicked:
                break
        if not clicked:
            break
    if actions and actions[-1].get("action") == "click_dismiss":
        # The cursor is left where the close button was, which can open hover menus.
        page.mouse.move(1, page.viewport_size["height"] - 2)
        page.wait_for_timeout(1000)
        actions.append({"action": "park_mouse"})


class PageCapturer:
    """Context manager owning one headless Chrome for a batch of captures."""

    def __init__(self, viewport: tuple[int, int] = DEFAULT_VIEWPORT, headless: bool = True,
                 nav_timeout_ms: int = 45000, settle_ms: int = 2500, challenge_wait_ms: int = 10000,
                 media_wait_ms: int = 8000, user_agent: str | None = None):
        self.viewport = viewport
        self.user_agent = user_agent  # None: the browser's own, minus "Headless"
        self.headless = headless
        self.nav_timeout_ms = nav_timeout_ms
        self.settle_ms = settle_ms
        self.challenge_wait_ms = challenge_wait_ms
        self.media_wait_ms = media_wait_ms

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(channel="chrome", headless=self.headless)
        if self.user_agent is None:
            page = self._browser.new_page()
            self.user_agent = page.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
            page.close()
        return self

    def __exit__(self, *exc):
        self._browser.close()
        self._pw.stop()

    @staticmethod
    def _check(page, status: int | None) -> str | None:
        body = page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 3000)")
        return detect_challenge(page.title(), status, body.strip(), page.evaluate(CHALLENGE_WIDGET_JS))

    def _wait_for_media(self, page) -> bool:
        """Wait for visible media to paint, including video players embedded in iframes
        (Vimeo/YouTube background heroes)."""
        h = page.viewport_size["height"]

        def frame_ready(frame) -> bool:
            if frame is not page.main_frame:
                box = frame.frame_element().bounding_box()
                if not box or box["width"] == 0 or box["y"] >= h or box["y"] + box["height"] <= 0:
                    return True
            return frame.evaluate(MEDIA_READY_JS)

        deadline = time.time() + self.media_wait_ms / 1000
        while time.time() < deadline:
            try:
                if all(frame_ready(f) for f in page.frames if not f.is_detached()):
                    page.wait_for_timeout(500)  # let the first video frame paint
                    return True
            except Exception:  # frames attaching/detaching mid-check
                pass
            page.wait_for_timeout(250)
        return False

    def capture(self, url: str, out_dir: str, name: str = "viewport") -> Capture:
        os.makedirs(out_dir, exist_ok=True)
        cap = Capture(url=url)
        w, h = self.viewport
        ctx = self._browser.new_context(viewport={"width": w, "height": h}, locale="en-US",
                                        user_agent=self.user_agent)
        try:
            page = ctx.new_page()
            # Track the latest main-document status so a challenge that redirects to the
            # real page after passing is judged on the page it ends up on.
            doc_status = {}
            page.on("response", lambda r: doc_status.__setitem__("s", r.status)
                    if r.request.is_navigation_request() and r.frame == page.main_frame else None)
            t = time.time()
            resp = page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                cap.actions.append({"action": "networkidle_timeout"})
            page.wait_for_timeout(self.settle_ms)  # hero images, fonts, intro animations
            cap.actions.append({"action": "goto", "secs": round(time.time() - t, 2)})

            reason = self._check(page, doc_status.get("s", resp.status if resp else None))
            if reason:
                # Give the site's own check time to finish by itself. Nothing is clicked.
                deadline = time.time() + self.challenge_wait_ms / 1000
                while reason and time.time() < deadline:
                    page.wait_for_timeout(1000)
                    try:
                        reason = self._check(page, doc_status.get("s"))
                    except Exception:  # page navigating mid-check
                        continue
                cap.actions.append({"action": "challenge_wait", "cleared": reason is None})
                if reason is None:
                    page.wait_for_timeout(self.settle_ms)
            cap.challenge_reason = reason
            cap.final_url = page.url
            cap.http_status = doc_status.get("s", resp.status if resp else None)
            cap.title = page.title()
            if reason:
                cap.challenge_screenshot = os.path.join(out_dir, f"{name}_challenge.png")
                page.screenshot(path=cap.challenge_screenshot)
                return cap

            cap.media_ready = self._wait_for_media(page)
            if not cap.media_ready:
                cap.actions.append({"action": "media_wait_timeout"})
            cap.timings = page.evaluate(TIMINGS_JS)
            cap.links = page.evaluate(LINKS_JS)
            dismiss_popups(page, cap.actions)
            shot = os.path.join(out_dir, f"{name}_{w}x{h}.png")
            page.screenshot(path=shot)
            cap.screenshot = shot
        finally:
            ctx.close()
        return cap
