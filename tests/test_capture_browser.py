"""End-to-end capture against local pages in real headless Chrome (skipped if unavailable)."""
import http.server
import threading
from functools import partial

import pytest

from calista_scorer.capture import PageCapturer

FILLER = "<p>Fresh sourdough, croissants and seasonal tarts baked before sunrise.</p>" * 10
PAGES = {
    "site/index.html": """<!DOCTYPE html><html><head><title>Crumb &amp; Co. Bakery</title></head><body>
<header><nav><a href="/site/menu.html">Menu</a></nav></header>
<div id="cookie" style="position:fixed;bottom:0;left:0;right:0;padding:2rem;background:#222;color:#fff">
We use cookies. <button onclick="document.getElementById('cookie').remove()">Accept all</button></div>
<h1>Crumb &amp; Co.</h1>""" + FILLER + """
<form onsubmit="return false"><input name="email"><div class="g-recaptcha" data-sitekey="x"></div>
<button type="submit">Send</button></form></body></html>""",
    # Soft interstitial: HTTP 200 and an innocent title, so only the widget gives it away.
    "soft/index.html": """<!DOCTYPE html><html><head><title>Crumb &amp; Co.</title></head><body>
<p>One moment while we check your connection.</p><div class="cf-turnstile" data-sitekey="x"></div></body></html>""",
    # A check that passes by itself after ~1.5 s and redirects to the real page.
    "auto/index.html": """<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>
<p>Checking your browser before accessing the site.</p>
<script>setTimeout(() => location.replace('/site/'), 1500)</script></body></html>""",
    # Autoplay video whose stream never arrives: the capture must notice it never painted.
    "video/index.html": """<!DOCTYPE html><html><head><title>Studio</title></head><body><h1>Studio</h1>""" + FILLER + """
<video autoplay muted src="/missing.mp4" style="position:absolute;top:0;width:600px;height:300px"></video>
</body></html>""",
}
USER_AGENTS = []  # User-Agent header of every request the server receives


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("www")
    for rel, html in PAGES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(html)
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            USER_AGENTS.append(self.headers.get("User-Agent", ""))
            super().do_GET()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def capturer():
    pytest.importorskip("playwright")
    try:
        with PageCapturer(settle_ms=200, challenge_wait_ms=4000, media_wait_ms=1500) as c:
            yield c
    except Exception as e:  # Chrome not installed, etc.
        pytest.skip(f"headless Chrome unavailable: {e}")


def test_real_site_dismisses_cookie_banner_and_is_not_challenge(server, capturer, tmp_path):
    cap = capturer.capture(f"{server}/site/", str(tmp_path))
    assert cap.http_status == 200
    assert cap.challenge_reason is None and cap.screenshot
    assert any(a.get("text") == "Accept all" for a in cap.actions)
    assert not any(a.get("text") == "Send" for a in cap.actions)
    assert {"href": f"{server}/site/menu.html", "nav": True} in cap.links
    assert cap.timings["ttfb_ms"] is not None and cap.timings["fcp_ms"] is not None


def test_sends_normal_chrome_user_agent(server, capturer, tmp_path):
    USER_AGENTS.clear()
    capturer.capture(f"{server}/site/", str(tmp_path))
    assert USER_AGENTS and all("Chrome/" in ua and "Headless" not in ua for ua in USER_AGENTS)


def test_soft_challenge_detected_nothing_clicked_no_scorable_screenshot(server, capturer, tmp_path):
    cap = capturer.capture(f"{server}/soft/", str(tmp_path))
    assert cap.challenge_reason == "CAPTCHA widget on near-empty page"
    assert cap.screenshot is None and cap.challenge_screenshot
    assert not any(a["action"] == "click_dismiss" for a in cap.actions)
    assert {"action": "challenge_wait", "cleared": False} in cap.actions


def test_challenge_that_clears_by_itself_is_captured(server, capturer, tmp_path):
    cap = capturer.capture(f"{server}/auto/", str(tmp_path))
    assert cap.challenge_reason is None
    assert cap.final_url == f"{server}/site/" and cap.screenshot
    assert {"action": "challenge_wait", "cleared": True} in cap.actions


def test_404_is_challenge(server, capturer, tmp_path):
    cap = capturer.capture(f"{server}/missing/", str(tmp_path))
    assert cap.challenge_reason == "HTTP 404"


def test_unpainted_video_is_flagged(server, capturer, tmp_path):
    cap = capturer.capture(f"{server}/video/", str(tmp_path))
    assert cap.screenshot and cap.media_ready is False
