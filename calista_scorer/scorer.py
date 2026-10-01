"""Run the Calista CNN on screenshots and convert its output to a 1-10 scale."""
from __future__ import annotations

import os

import numpy as np

from .model import HEIGHT, WIDTH, build_tf_model, preprocess

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

NATIVE_MIN, NATIVE_MAX = 1.0, 9.0


def normalize_10(score: float | None) -> float | None:
    """Map Calista's native 1-9 rating onto 1-10. Outputs cluster around 2.5-6.5 in
    practice, so treat the result as a ranking signal rather than an absolute grade."""
    if score is None:
        return None
    return round(1 + (score - NATIVE_MIN) * 9 / (NATIVE_MAX - NATIVE_MIN), 3)


class AestheticScorer:
    def __init__(self, weights_path: str):
        if not os.path.exists(weights_path):
            raise FileNotFoundError(
                f"Calista weights not found at {weights_path}; run "
                "`python -m calista_scorer download-weights` first")
        self._model = build_tf_model(weights_path)

    def score_image(self, bgr_image: np.ndarray) -> float:
        """Score a BGR image (as loaded by cv2) of any size; it is resized to 256x192."""
        return float(self._model(preprocess(bgr_image), training=False).numpy().ravel()[0])

    def score_file(self, path: str) -> float:
        import cv2

        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"could not read image {path}")
        return self.score_image(img)


def save_model_input(path_in: str, path_out: str) -> None:
    """Write the exact 256x192 image the CNN sees, for debugging scores."""
    import cv2

    img = cv2.imread(path_in, cv2.IMREAD_COLOR)
    cv2.imwrite(path_out, cv2.resize(img, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA))
