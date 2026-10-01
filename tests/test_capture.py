from calista_scorer.capture import DISMISS_RE, detect_challenge, slug

LONG_BODY = "Fresh sourdough every morning. " * 30


def test_slug():
    assert slug("https://www.katzsdelicatessen.com/") == "katzsdelicatessen_com"
    assert slug("http://localhost:8765/protected/") == "localhost_8765_protected"


def test_http_error_is_challenge():
    assert detect_challenge("Crumb & Co.", 403, LONG_BODY, False) == "HTTP 403"


def test_challenge_title():
    assert "Just a moment" in detect_challenge("Just a moment...", 200, LONG_BODY, False)


def test_short_page_with_challenge_text():
    reason = detect_challenge("crumbandco.test", 200, "Verify you are human by completing the action below.", False)
    assert reason == "challenge text on near-empty page"


def test_short_page_with_widget_and_innocent_text():
    reason = detect_challenge("Crumb & Co.", 200, "One moment while we check your connection.", True)
    assert reason == "CAPTCHA widget on near-empty page"


def test_real_site_with_captcha_on_contact_form_is_not_challenge():
    assert detect_challenge("Crumb & Co. Bakery", 200, LONG_BODY + " Contact us", True) is None


def test_normal_page():
    assert detect_challenge("Crumb & Co. Bakery", 200, LONG_BODY, False) is None


def test_dismiss_regex_only_matches_dismiss_buttons():
    for text in ["Accept all", "Accept cookies", "Got it", "No thanks", "Close", "×"]:
        assert DISMISS_RE.match(text), text
    for text in ["Verify you are human", "I'm not a robot", "Submit", "Subscribe", "Manage preferences"]:
        assert not DISMISS_RE.match(text), text
