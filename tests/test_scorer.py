import os

import numpy as np
import pytest

from calista_scorer.scorer import AestheticScorer, normalize_10
from calista_scorer.weights import DEFAULT_WEIGHTS


def test_normalize_10():
    assert normalize_10(1.0) == 1.0
    assert normalize_10(9.0) == 10.0
    assert normalize_10(5.0) == 5.5
    assert normalize_10(None) is None


def test_missing_weights_gives_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="download-weights"):
        AestheticScorer(str(tmp_path / "nope.h5"))


@pytest.mark.skipif(not os.path.exists(DEFAULT_WEIGHTS), reason="Calista weights not downloaded")
def test_model_scores_any_size_image_in_range():
    pytest.importorskip("tensorflow")
    scorer = AestheticScorer(DEFAULT_WEIGHTS)
    rng = np.random.default_rng(0)
    for shape in [(800, 1280, 3), (192, 256, 3)]:
        s = scorer.score_image(rng.integers(0, 255, shape, dtype=np.uint8))
        assert 1.0 <= s <= 9.0
