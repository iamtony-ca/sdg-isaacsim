"""RealSense-style depth degradation sensor (optional preset, SDG.md §4).

Same camera/render product as `ideal`, but `postprocess_depth` turns GT metric depth into
a noisy sensor-like depth by composing four effects (each toggleable via config):

  1. global bias        : constant offset (mm) — systematic depth error
  2. range noise        : zero-mean Gaussian with sigma = noise_quadratic * z^2 (metres),
                          i.e. error grows with the square of distance (stereo-depth trait)
  3. edge dropout       : holes along depth discontinuities (occlusion boundaries)
  4. low-reflectance holes: random speckle holes (dark/specular/transparent surfaces)

★ These defaults are illustrative, NOT calibrated. For faithful modelling of a specific
device, calibrate the parameters against real GT-vs-sensor captures in `calibration/`
(SDG.md §4 warns against arbitrary values). Reproducible via `noise_seed`.

config (sensor entry):
  - name: cam0
    type: realsense_depth
    resolution: [1280, 720]
    intrinsics: {hfov_deg: 69}
    bias_mm: 0.0
    noise_quadratic: 0.001        # sigma_m at z metres = 0.001 * z^2
    edge_dropout: true
    edge_grad_thresh_m: 0.05      # depth gradient (m/px) above which pixels drop out
    edge_dilate_px: 2
    hole_fraction: 0.005          # fraction of valid pixels randomly zeroed
    noise_seed: 0
"""
from __future__ import annotations

import numpy as np

from ..config import SensorSpec
from ..registry import register
from .ideal import IdealCamera


@register("sensor", "realsense_depth")
class RealsenseDepthCamera(IdealCamera):
    def __init__(self, spec: SensorSpec):
        super().__init__(spec)
        e = spec.extra or {}
        self.bias_m = float(e.get("bias_mm", 0.0)) / 1000.0
        self.noise_quadratic = float(e.get("noise_quadratic", 0.001))
        self.edge_dropout = bool(e.get("edge_dropout", True))
        self.edge_grad_thresh_m = float(e.get("edge_grad_thresh_m", 0.05))
        self.edge_dilate_px = int(e.get("edge_dilate_px", 2))
        self.hole_fraction = float(e.get("hole_fraction", 0.005))
        self._rng = np.random.default_rng(int(e.get("noise_seed", 0)))

    def postprocess_depth(self, depth):
        d = np.asarray(depth, dtype=np.float32).copy()
        valid = d > 0
        if not valid.any():
            return d

        # (1) global bias + (2) range-dependent Gaussian noise
        if self.bias_m:
            d[valid] += self.bias_m
        if self.noise_quadratic:
            sigma = self.noise_quadratic * (d * d)
            d[valid] += self._rng.normal(0.0, 1.0, size=d.shape)[valid] * sigma[valid]

        # (3) edge dropout: holes where depth changes sharply
        if self.edge_dropout:
            d[self._edge_mask(depth)] = 0.0

        # (4) low-reflectance speckle holes on the remaining valid pixels
        if self.hole_fraction > 0:
            holes = (self._rng.random(d.shape) < self.hole_fraction) & (d > 0)
            d[holes] = 0.0

        d[d < 0] = 0.0
        d[~valid] = 0.0  # keep original background as no-return
        return d

    def _edge_mask(self, depth) -> np.ndarray:
        gt = np.asarray(depth, dtype=np.float32)
        try:
            import cv2
        except ImportError:  # gradient fallback without cv2
            gy, gx = np.gradient(gt)
            mag = np.hypot(gx, gy)
            return (mag > self.edge_grad_thresh_m) & (gt > 0)
        gx = cv2.Sobel(gt, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gt, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(gx, gy)
        edges = ((mag > self.edge_grad_thresh_m) & (gt > 0)).astype(np.uint8)
        if self.edge_dilate_px > 0:
            k = np.ones((self.edge_dilate_px * 2 + 1, self.edge_dilate_px * 2 + 1), np.uint8)
            edges = cv2.dilate(edges, k)
        return edges.astype(bool)
