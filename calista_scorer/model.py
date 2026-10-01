"""Port of the Calista rating-based website-aesthetics CNN (Delitzas et al., IJHCS 2023).

Architecture rebuilt from rating-based-models/approach1/model1.ipynb of
https://github.com/calista-ai/website-aesthetics-research (Keras 2.2.5 / TF 1.14),
re-implemented twice so the port can be cross-checked:

  * build_tf_model()     -- modern TF 2.x / Keras 3 functional model (used for scoring)
  * build_torch_model()  -- independent PyTorch re-implementation (cross-check only,
                            see scripts/validate_port.py; torch is not a runtime dependency)

Weights are read directly from the original .h5 with h5py and assigned by layer
name (the saved file names the FC layers dense_1/dense_2/dense_3, not fc6/7/8).
"""
from __future__ import annotations

import h5py
import numpy as np

WIDTH, HEIGHT = 256, 192

CONV_LAYERS = ["conv1", "conv2_1", "conv2_2", "conv3", "conv4_1", "conv4_2", "conv5_1", "conv5_2"]
DENSE_LAYERS = ["dense_1", "dense_2", "dense_3"]


def load_h5_weights(path: str) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    out = {}
    with h5py.File(path, "r") as f:
        g = f["model_weights"]
        for name in CONV_LAYERS + DENSE_LAYERS:
            out[name] = (g[name][name]["kernel:0"][:], g[name][name]["bias:0"][:])
    return out


def preprocess(bgr_image: np.ndarray) -> np.ndarray:
    """Matches the notebook: cv2 BGR image, resized to 256x192 (INTER_AREA, as in the
    dataset preprocessing), scaled by 1/255, shape (1,192,256,3)."""
    import cv2

    img = cv2.resize(bgr_image, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)
    return (img.astype(np.float32) / 255.0).reshape(1, HEIGHT, WIDTH, 3)


# ---------------------------------------------------------------- TensorFlow

def build_tf_model(weights_path: str):
    import tensorflow as tf
    import keras
    from keras import layers

    class LRN(layers.Layer):
        """Exact semantics of the notebook's custom LRN (channels_last):
        avg-pool x^2 spatially with a (n//2, n//2) window, stride 1, SAME padding,
        sum over channels, broadcast back, x / (k + alpha/n * sum)^beta."""

        def __init__(self, n=5, alpha=0.0001, beta=0.75, k=2, **kw):
            super().__init__(**kw)
            self.n, self.alpha, self.beta, self.k = n, alpha, beta, k

        def call(self, x):
            half_n = self.n // 2
            pooled = tf.nn.avg_pool2d(tf.square(x), ksize=half_n, strides=1, padding="SAME")
            summed = tf.reduce_sum(pooled, axis=3, keepdims=True)
            averaged = (self.alpha / self.n) * summed  # broadcast == repeat_elements
            return x / tf.pow(self.k + averaged, self.beta)

    def split(lo, hi):
        return layers.Lambda(lambda t: t[:, :, :, lo:hi])

    inp = layers.Input(shape=(HEIGHT, WIDTH, 3), dtype="float32", name="im_data")
    x = layers.Conv2D(96, 11, strides=4, activation="relu", name="conv1")(inp)
    x = layers.MaxPooling2D(3, strides=2, padding="same")(x)
    x = LRN(name="norm1")(x)
    a = layers.Conv2D(128, 5, padding="same", activation="relu", name="conv2_1")(split(0, 48)(x))
    b = layers.Conv2D(128, 5, padding="same", activation="relu", name="conv2_2")(split(48, 96)(x))
    x = layers.Concatenate(name="conv_2")([a, b])
    x = layers.MaxPooling2D(3, strides=2)(x)
    x = LRN(name="norm2")(x)
    x = layers.Conv2D(384, 3, padding="same", activation="relu", name="conv3")(x)
    a = layers.Conv2D(192, 3, padding="same", activation="relu", name="conv4_1")(split(0, 192)(x))
    b = layers.Conv2D(192, 3, padding="same", activation="relu", name="conv4_2")(split(192, 384)(x))
    x = layers.Concatenate(name="conv_4")([a, b])
    a = layers.Conv2D(128, 3, padding="same", activation="relu", name="conv5_1")(split(0, 192)(x))
    b = layers.Conv2D(128, 3, padding="same", activation="relu", name="conv5_2")(split(192, 384)(x))
    x = layers.Concatenate(name="conv_5")([a, b])
    x = layers.MaxPooling2D(3, strides=2)(x)
    x = layers.Flatten()(x)
    x = layers.Dense(1024, activation="relu", name="dense_1")(x)
    x = layers.Dense(512, activation="relu", name="dense_2")(x)
    out = layers.Dense(1, name="dense_3")(x)  # dropout layers are identity at inference
    model = keras.Model(inp, out)

    w = load_h5_weights(weights_path)
    for name, (k, bias) in w.items():
        layer = model.get_layer(name)
        exp = [v.shape for v in layer.get_weights()]
        assert exp == [k.shape, bias.shape], (name, exp, k.shape)
        layer.set_weights([k, bias])
    return model


# ---------------------------------------------------------------- PyTorch (cross-check)

def build_torch_model(weights_path: str):
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    def same_pad(x, k, s):
        # TF 'SAME' padding: total = max((ceil(in/s)-1)*s + k - in, 0); extra goes after.
        h, w = x.shape[-2:]
        ph = max((-(-h // s) - 1) * s + k - h, 0)
        pw = max((-(-w // s) - 1) * s + k - w, 0)
        return (pw // 2, pw - pw // 2, ph // 2, ph - ph // 2)

    class TorchCalista(nn.Module):
        def __init__(self):
            super().__init__()
            self.convs = nn.ModuleDict({
                "conv1": nn.Conv2d(3, 96, 11, 4),
                "conv2_1": nn.Conv2d(48, 128, 5, padding=2), "conv2_2": nn.Conv2d(48, 128, 5, padding=2),
                "conv3": nn.Conv2d(256, 384, 3, padding=1),
                "conv4_1": nn.Conv2d(192, 192, 3, padding=1), "conv4_2": nn.Conv2d(192, 192, 3, padding=1),
                "conv5_1": nn.Conv2d(192, 128, 3, padding=1), "conv5_2": nn.Conv2d(192, 128, 3, padding=1),
            })
            self.fc = nn.ModuleDict({"dense_1": nn.Linear(8960, 1024), "dense_2": nn.Linear(1024, 512),
                                     "dense_3": nn.Linear(512, 1)})

        @staticmethod
        def lrn(x, n=5, alpha=1e-4, beta=0.75, k=2.0):
            sq = x * x
            pad = same_pad(sq, n // 2, 1)
            ones = torch.ones_like(sq[:, :1])
            # avg pool excluding padded cells (TF semantics)
            s = F.avg_pool2d(F.pad(sq, pad), n // 2, 1, divisor_override=1)
            cnt = F.avg_pool2d(F.pad(ones, pad), n // 2, 1, divisor_override=1)
            pooled = s / cnt
            summed = pooled.sum(1, keepdim=True)
            return x / torch.pow(k + (alpha / n) * summed, beta)

        def forward(self, x_nhwc):
            c = self.convs
            x = x_nhwc.permute(0, 3, 1, 2)
            x = F.relu(c["conv1"](x))
            x = F.max_pool2d(F.pad(x, same_pad(x, 3, 2), value=float("-inf")), 3, 2)
            x = self.lrn(x)
            x = torch.cat([F.relu(c["conv2_1"](x[:, :48])), F.relu(c["conv2_2"](x[:, 48:]))], 1)
            x = F.max_pool2d(x, 3, 2)
            x = self.lrn(x)
            x = F.relu(c["conv3"](x))
            x = torch.cat([F.relu(c["conv4_1"](x[:, :192])), F.relu(c["conv4_2"](x[:, 192:]))], 1)
            x = torch.cat([F.relu(c["conv5_1"](x[:, :192])), F.relu(c["conv5_2"](x[:, 192:]))], 1)
            x = F.max_pool2d(x, 3, 2)
            x = x.permute(0, 2, 3, 1).reshape(x.shape[0], -1)  # Keras flatten order (NHWC)
            x = F.relu(self.fc["dense_1"](x))
            x = F.relu(self.fc["dense_2"](x))
            return self.fc["dense_3"](x)

    m = TorchCalista().eval()
    w = load_h5_weights(weights_path)
    with torch.no_grad():
        for name in CONV_LAYERS:
            k, b = w[name]
            m.convs[name].weight.copy_(torch.from_numpy(k).permute(3, 2, 0, 1))  # HWIO -> OIHW
            m.convs[name].bias.copy_(torch.from_numpy(b))
        for name in DENSE_LAYERS:
            k, b = w[name]
            m.fc[name].weight.copy_(torch.from_numpy(k).T)
            m.fc[name].bias.copy_(torch.from_numpy(b))
    return m
