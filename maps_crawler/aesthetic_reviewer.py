"""Claude-vision-based website look scoring, plus a vision-guided overlay
dismissal helper used by site_crawler.py as a last resort when neither
autoconsent nor its text-match fallback clears a blocking overlay.

Kept behind this narrow interface (score_screenshot / check_overlay) so a
different scorer — a locally-hosted aesthetics model, for instance — could
replace the Claude calls later without touching the crawler or
orchestration. See the plan notes on why a dedicated aesthetics model
(Webthetics) wasn't practical to adopt directly: it's a 2019 research
artifact built on Windows Caffe with no real maintenance.
"""

import asyncio
import base64
from dataclasses import dataclass, field
from io import BytesIO
from typing import Optional

import anthropic
from PIL import Image

# Our own full-page screenshots of very tall sites can legitimately exceed
# Pillow's default decompression-bomb pixel threshold; these come from our
# own crawler, not untrusted uploads, so the check is unnecessary here.
Image.MAX_IMAGE_PIXELS = None

DEFAULT_MODEL = "claude-sonnet-5"

# Anthropic rejects images over 10MB (base64-encoded). Full-page screenshots
# of tall or image-heavy sites can be tens of MB as lossless PNG, so we
# recompress to JPEG and cap the long edge -- 1568px is the point beyond
# which Claude downscales images server-side anyway, so sending more is
# just wasted bytes.
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_LONG_EDGE = 1568


def _prepare_image_for_api(image_bytes: bytes) -> tuple:
    """Returns (jpeg_bytes, media_type), resized/recompressed to fit under
    MAX_IMAGE_BYTES."""
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    if max(image.size) > MAX_LONG_EDGE:
        scale = MAX_LONG_EDGE / max(image.size)
        new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(new_size, Image.LANCZOS)

    quality = 85
    while True:
        buf = BytesIO()
        image.save(buf, format="JPEG", quality=quality)
        data = buf.getvalue()
        if len(data) <= MAX_IMAGE_BYTES or quality <= 30:
            return data, "image/jpeg"
        quality -= 15

LOOK_PROMPT = (
    "You are assessing the visual design quality of a small business's "
    "website from a screenshot. Judge only what's visible: layout, "
    "spacing, typography, color choices, image quality, how dated or "
    "professional it looks, clutter, and (if this is a mobile screenshot) "
    "how well the layout adapts to a phone screen. Do not judge the "
    "business itself, only the website's design. Call submit_review with "
    "your assessment."
)

LOOK_TOOL = {
    "name": "submit_review",
    "description": "Submit a structured aesthetic review of a webpage screenshot.",
    "input_schema": {
        "type": "object",
        "properties": {
            "look_score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "1 = looks broken/unprofessional/very dated, 10 = polished, modern, professional design",
            },
            "issues": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific design problems observed, e.g. 'outdated template', 'cluttered layout', 'low-quality images'",
            },
            "summary": {
                "type": "string",
                "description": "One or two sentence summary of the site's visual quality",
            },
        },
        "required": ["look_score", "issues", "summary"],
    },
}

OVERLAY_PROMPT = (
    "This is a screenshot of a webpage. Determine whether a cookie-consent "
    "banner, newsletter signup modal, age-gate, or other overlay is "
    "blocking the main page content. If yes, call report_overlay with "
    "has_overlay=true and the fractional (x, y) position of the button "
    "that would dismiss it — each between 0 and 1, as a fraction of image "
    "width/height — preferring an 'accept'/'agree'/'close'/'no thanks' "
    "action over one that opens more settings. If no such overlay is "
    "present, call report_overlay with has_overlay=false."
)

OVERLAY_TOOL = {
    "name": "report_overlay",
    "description": "Report whether a blocking overlay is present and where to click to dismiss it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "has_overlay": {"type": "boolean"},
            "click_x_fraction": {"type": "number", "minimum": 0, "maximum": 1},
            "click_y_fraction": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["has_overlay"],
    },
}


@dataclass
class LookReview:
    look_score: Optional[int] = None
    issues: list = field(default_factory=list)
    summary: Optional[str] = None
    error: Optional[str] = None


def _image_block(image_bytes: bytes, media_type: str) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(image_bytes).decode("ascii"),
        },
    }


def _first_tool_use(response, tool_name: str) -> Optional[dict]:
    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            return block.input
    return None


class AestheticReviewer:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def score_screenshot(self, image_bytes: bytes, media_type: str = "image/png") -> LookReview:
        try:
            prepared_bytes, prepared_media_type = _prepare_image_for_api(image_bytes)
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                tools=[LOOK_TOOL],
                tool_choice={"type": "tool", "name": "submit_review"},
                messages=[{
                    "role": "user",
                    "content": [_image_block(prepared_bytes, prepared_media_type), {"type": "text", "text": LOOK_PROMPT}],
                }],
            )
        except Exception as exc:
            return LookReview(error=f"{type(exc).__name__}: {exc}")

        data = _first_tool_use(response, "submit_review")
        if data is None:
            return LookReview(error="No submit_review tool call in response")
        return LookReview(
            look_score=data.get("look_score"),
            issues=data.get("issues") or [],
            summary=data.get("summary"),
        )

    def _check_overlay_sync(self, image_bytes: bytes, media_type: str) -> Optional[tuple]:
        try:
            prepared_bytes, prepared_media_type = _prepare_image_for_api(image_bytes)
            response = self._client.messages.create(
                model=self._model,
                max_tokens=512,
                tools=[OVERLAY_TOOL],
                tool_choice={"type": "tool", "name": "report_overlay"},
                messages=[{
                    "role": "user",
                    "content": [_image_block(prepared_bytes, prepared_media_type), {"type": "text", "text": OVERLAY_PROMPT}],
                }],
            )
        except Exception:
            return None

        data = _first_tool_use(response, "report_overlay")
        if not data or not data.get("has_overlay"):
            return None
        x, y = data.get("click_x_fraction"), data.get("click_y_fraction")
        if x is None or y is None:
            return None
        return (float(x), float(y))

    async def check_overlay(self, image_bytes: bytes, media_type: str = "image/png") -> Optional[tuple]:
        """Async wrapper for site_crawler.py's vision_overlay_check hook."""
        return await asyncio.to_thread(self._check_overlay_sync, image_bytes, media_type)
