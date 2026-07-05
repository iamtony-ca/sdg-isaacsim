"""CameraModel interface.

A camera model creates the render product for a sensor, exposes intrinsics, and may
post-process channels. `ideal` returns GT metric depth; `realsense_depth` (optional
preset) applies a calibrated degradation layer (global bias + range-dependent noise +
edge dropout + low-reflectance holes) — OFF by default, only when a downstream needs it
(see SDG.md §4). Degradation params should be calibrated from calibration/ references,
never guessed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from ..config import SensorSpec


class CameraModel(ABC):
    def __init__(self, spec: SensorSpec):
        self.spec = spec
        self.render_product = None  # TODO(6.0.1): Replicator render product handle

    @abstractmethod
    def create(self) -> None:
        """Create camera prim + render product at the configured resolution. TODO(6.0.1)."""
        raise NotImplementedError

    @abstractmethod
    def intrinsics(self) -> Dict[str, float]:
        """Return fx, fy, cx, cy (+ distortion if any) for meta/writer."""
        raise NotImplementedError

    def postprocess_depth(self, depth):
        """Hook: ideal returns depth unchanged; realsense_depth overrides to degrade."""
        return depth
