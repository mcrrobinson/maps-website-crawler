import asyncio
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from maps_crawler.aesthetic_reviewer import AestheticReviewer


def _fake_screenshot_bytes():
    buf = BytesIO()
    Image.new("RGB", (20, 20), color="white").save(buf, format="PNG")
    return buf.getvalue()


def _tool_use_response(name, input_data):
    block = SimpleNamespace(type="tool_use", name=name, input=input_data)
    return SimpleNamespace(content=[block])


def _text_only_response():
    return SimpleNamespace(content=[SimpleNamespace(type="text", name=None, input=None)])


class FakeMessages:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


class FakeClient:
    def __init__(self, response=None, error=None):
        self.messages = FakeMessages(response=response, error=error)


def make_reviewer(response=None, error=None):
    reviewer = AestheticReviewer(api_key="fake-key")
    reviewer._client = FakeClient(response=response, error=error)
    return reviewer


def test_score_screenshot_parses_tool_use_response():
    reviewer = make_reviewer(response=_tool_use_response(
        "submit_review", {"look_score": 8, "issues": ["cluttered footer"], "summary": "Clean and modern"}
    ))

    review = reviewer.score_screenshot(_fake_screenshot_bytes())

    assert review.look_score == 8
    assert review.issues == ["cluttered footer"]
    assert review.summary == "Clean and modern"
    assert review.error is None


def test_score_screenshot_handles_api_error():
    reviewer = make_reviewer(error=RuntimeError("rate limited"))

    review = reviewer.score_screenshot(_fake_screenshot_bytes())

    assert review.look_score is None
    assert "rate limited" in review.error


def test_score_screenshot_handles_missing_tool_use_block():
    reviewer = make_reviewer(response=_text_only_response())

    review = reviewer.score_screenshot(_fake_screenshot_bytes())

    assert review.look_score is None
    assert review.error is not None


def test_check_overlay_returns_coordinates_when_overlay_present():
    reviewer = make_reviewer(response=_tool_use_response(
        "report_overlay", {"has_overlay": True, "click_x_fraction": 0.5, "click_y_fraction": 0.9}
    ))

    result = asyncio.run(reviewer.check_overlay(_fake_screenshot_bytes()))

    assert result == (0.5, 0.9)


def test_check_overlay_returns_none_when_no_overlay():
    reviewer = make_reviewer(response=_tool_use_response("report_overlay", {"has_overlay": False}))

    result = asyncio.run(reviewer.check_overlay(_fake_screenshot_bytes()))

    assert result is None


def test_check_overlay_returns_none_on_api_error():
    reviewer = make_reviewer(error=RuntimeError("network blip"))

    result = asyncio.run(reviewer.check_overlay(_fake_screenshot_bytes()))

    assert result is None
