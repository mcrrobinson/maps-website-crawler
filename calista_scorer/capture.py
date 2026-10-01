"""Load a homepage in headless Chrome and take the above-the-fold screenshot Calista scores.

Read-only: the only clicks are on buttons whose text clearly means "accept cookies" or
"close popup". It never submits forms and never touches CAPTCHA widgets (those live in
cross-origin iframes, which the button search does not enter).
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
    r"checking your browser|request blocked|cf-chl", re.I)
# A page this short with a challenge widget on it is an interstitial, not a real site that
# happens to have a CAPTCHA on its contact form.
SHORT_BODY_CHARS = 400

CHALLENGE_WIDGET_JS = """() => !!document.querySelector(
  "iframe[src*='challenges.cloudflare.com'], iframe[src*='/recaptcha/'], iframe[src*='hcaptcha.com'], " +
  ".cf-turnstile, .g-recaptcha, .h-captcha, #challenge-form, #cf-challenge-running")"""


@dataclass
class Capture:
    url: str
    final_url: str | None = None
    http_status: int | None = None
    title: str = ""
    screenshot: str | None = None
    screenshot_before_dismiss: str | None = None
    challenge_reason: str | None = None
    links_found: int | None = None
    actions: list = field(default_factory=list)


def slug(url: str) -> str:
    u = urlparse(url)
    s = u.netloc.replace("www.", "").replace(".", "_").replace(":", "_")
    path = u.path.strip("/").replace("/", "_")
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
                 nav_timeout_ms: int = 45000, settle_ms: int = 2500):
        self.viewport = viewport
        self.headless = headless
        self.nav_timeout_ms = nav_timeout_ms
        self.settle_ms = settle_ms

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(channel="chrome", headless=self.headless)
        return self

    def __exit__(self, *exc):
        self._browser.close()
        self._pw.stop()

    def capture(self, url: str, out_dir: str) -> Capture:
        os.makedirs(out_dir, exist_ok=True)
        cap = Capture(url=url)
        w, h = self.viewport
        ctx = self._browser.new_context(viewport={"width": w, "height": h}, locale="en-US")
        try:
            page = ctx.new_page()
            t = time.time()
            resp = page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                cap.actions.append({"action": "networkidle_timeout"})
            page.wait_for_timeout(self.settle_ms)  # hero images, fonts, intro animations
            cap.final_url = page.url
            cap.http_status = resp.status if resp else None
            cap.actions.append({"action": "goto", "secs": round(time.time() - t, 2)})
            cap.title = page.title()
            body = page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 3000)")
            has_widget = page.evaluate(CHALLENGE_WIDGET_JS)
            cap.links_found = page.evaluate(
                "() => new Set([...document.querySelectorAll('a[href]')].map(a => a.href)"
                ".filter(h => h.startsWith('http'))).size")
            cap.challenge_reason = detect_challenge(cap.title, cap.http_status, body.strip(), has_widget)

            raw = os.path.join(out_dir, f"viewport_{w}x{h}_before_dismiss.png")
            page.screenshot(path=raw)
            cap.screenshot_before_dismiss = raw
            if cap.challenge_reason is None:
                dismiss_popups(page, cap.actions)
            shot = os.path.join(out_dir, f"viewport_{w}x{h}.png")
            page.screenshot(path=shot)
            cap.screenshot = shot
        finally:
            ctx.close()
        return cap
