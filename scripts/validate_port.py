"""Sanity-check the Calista port against the authors' own published predictions.

1. TF port vs independent PyTorch port on the same inputs (should agree to ~1e-4).
2. Reproduce results/rating-based/predictions.csv (author's model outputs on the
   rating-based test set, using the already-resized 256x192 test images).
3. Reproduce results/out-of-sample/predictions_rb.csv (24 out-of-sample screenshots).
Writes validation/logs/validation.json.

Needs torch plus the authors' repos cloned next to this script's parent:
  git clone --depth 1 https://github.com/calista-ai/website-aesthetics-research validation/src_repo
  git clone --depth 1 https://github.com/calista-ai/website-aesthetics-datasets validation/datasets_repo
"""
import csv
import json
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from calista_scorer.model import build_tf_model, build_torch_model, preprocess  # noqa: E402

W = os.path.join(ROOT, "weights", "calista_rating_based.h5")
VAL = os.path.join(ROOT, "validation")
DS = os.path.join(VAL, "datasets_repo")
RES = os.path.join(VAL, "src_repo", "results")


def main():
    import torch

    tfm = build_tf_model(W)
    tm = build_torch_model(W)

    def both(x):
        a = float(tfm(x, training=False).numpy().ravel()[0])
        with torch.no_grad():
            b = float(tm(torch.from_numpy(x)).numpy().ravel()[0])
        return a, b

    report = {}

    # 1+2: in-sample test set
    rows = list(csv.DictReader(open(os.path.join(RES, "rating-based", "predictions.csv"))))
    pub, ours_tf, ours_t, missing = [], [], [], 0
    for r in rows:
        rel = r["images"].split("resized/", 1)[1]
        p = os.path.join(DS, "rating-based-dataset", "preprocess", "resized", rel)
        img = cv2.imread(p, cv2.IMREAD_COLOR)
        if img is None:
            missing += 1
            continue
        x = preprocess(img)
        a, b = both(x)
        pub.append(float(r["predictions"]))
        ours_tf.append(a)
        ours_t.append(b)
    pub, ours_tf, ours_t = map(np.array, (pub, ours_tf, ours_t))
    report["in_sample_test"] = {
        "n": int(len(pub)), "missing": missing,
        "max_abs_diff_tf_vs_published": float(np.max(np.abs(ours_tf - pub))),
        "mean_abs_diff_tf_vs_published": float(np.mean(np.abs(ours_tf - pub))),
        "pearson_tf_vs_published": float(np.corrcoef(ours_tf, pub)[0, 1]),
        "max_abs_diff_tf_vs_torch": float(np.max(np.abs(ours_tf - ours_t))),
        "pred_range": [float(ours_tf.min()), float(ours_tf.max())],
        "first5": [{"published": float(p_), "ours_tf": float(a_), "ours_torch": float(b_)}
                   for p_, a_, b_ in zip(pub[:5], ours_tf[:5], ours_t[:5])],
    }

    # 3: out-of-sample (raw 1366-wide screenshots -> resized by us)
    rows = list(csv.DictReader(open(os.path.join(RES, "out-of-sample", "predictions_rb.csv"))))
    pub2, ours2, gt2 = [], [], []
    for r in rows:
        img = cv2.imread(os.path.join(DS, "out-of-sample", f"{r['id']}.png"), cv2.IMREAD_COLOR)
        if img is None:
            continue
        a, _ = both(preprocess(img))
        pub2.append(float(r["prediction"]))
        ours2.append(a)
        gt2.append(float(r["mean_rating"]))
    pub2, ours2, gt2 = map(np.array, (pub2, ours2, gt2))
    report["out_of_sample"] = {
        "n": int(len(pub2)),
        "max_abs_diff_vs_published": float(np.max(np.abs(ours2 - pub2))),
        "mean_abs_diff_vs_published": float(np.mean(np.abs(ours2 - pub2))),
        "pearson_vs_published": float(np.corrcoef(ours2, pub2)[0, 1]),
        "pearson_ours_vs_human_mean": float(np.corrcoef(ours2, gt2)[0, 1]),
        "pearson_published_vs_human_mean": float(np.corrcoef(pub2, gt2)[0, 1]),
        "rows": [{"published": float(p_), "ours": round(float(o_), 3), "human": float(g_)}
                 for p_, o_, g_ in zip(pub2, ours2, gt2)],
    }

    os.makedirs(os.path.join(VAL, "logs"), exist_ok=True)
    with open(os.path.join(VAL, "logs", "validation.json"), "w") as f:
        json.dump(report, f, indent=2)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk not in ("rows", "first5")}
                      for k, v in report.items()}, indent=2))
    print("first5:", report["in_sample_test"]["first5"])


if __name__ == "__main__":
    main()
