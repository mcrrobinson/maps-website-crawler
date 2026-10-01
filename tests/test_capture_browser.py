"""End-to-end capture against local pages in real headless Chrome (skipped if unavailable)."""
import http.server
import threading
from functools import partial

import pytest

from calista_scorer.capture import PageCapturer

PAGES = {
    "site/index.html": """<!DOCTYPE html><html><head><title>Crumb &amp; Co. Bakery</title></head><body>
<div id="cookie" style="position:fixed;bottom:0;left:0;right:0;padding:2rem;background:#222;color:#fff">
We use cookies. <button onclick="document.getElementById('cookie').remove()">Accept all</button></div>
<h1>Crumb &amp; Co.</h1>""" + "<p>Fresh sourdough, croissants and seasonal tarts baked before sunrise.</p>" * 10 + """
<form onsubmit="return false"><input name="email"><div class="g-recaptcha" data-sitekey="x"></div>
<button type="submit">Send</button></form></body></html>""",
    # Soft interstitial: HTTP 200 and an innocent title, so only the widget gives it away.
    "soft/index.html": """<!DOCTYPE html><html><head><title>Crumb &amp; Co.</title></head><body>
<p>One moment while we check your connection.</p><div class="cf-turnstile" data-sitekey="x"></div></body></html>""",
}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("www")
    for rel, html in PAGES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(html)
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def capturer():
    pytest.importorskip("playwright")
    try:
        with PageCapturer(settle_ms=200) as c:
            yield c
    except Exception as e:  # Chrome not installed, etc.
        pytest.skip(f"headless Chrome unavailable: {e}")


def test_real_site_dismisses_cookie_banner_and_is_not_challenge(server, capturer, tmp_path):
    cap = capturer.capture(f"{server}/site/", str(tmp_path))
    assert cap.http_status == 200
    assert cap.challenge_reason is None
    assert any(a.get("text") == "Accept all" for a in cap.actions)
    assert not any(a.get("text") == "Send" for a in cap.actions)


def test_soft_challenge_detected_and_nothing_clicked(server, capturer, tmp_path):
    cap = capturer.capture(f"{server}/soft/", str(tmp_path))
    assert cap.challenge_reason == "CAPTCHA widget on near-empty page"
    assert not any(a["action"] == "click_dismiss" for a in cap.actions)


def test_404_is_challenge(server, capturer, tmp_path):
    cap = capturer.capture(f"{server}/missing/", str(tmp_path))
    assert cap.challenge_reason == "HTTP 404"
