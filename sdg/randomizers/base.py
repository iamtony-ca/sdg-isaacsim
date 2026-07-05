"""Randomizer interface.

Each randomizer is constructed from its config dict and is invoked once per frame to
perturb the scene (light, material, object/camera pose, distractors). Implementations
register with @register("randomizer", "<type>") so config `randomizers[].type` resolves.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class Randomizer(ABC):
    def __init__(self, cfg: Dict[str, Any], ctx: "Any" = None):
        self.cfg = cfg
        self.ctx = ctx  # shared scene context (prims, cameras) provided by run_sdg

    def setup(self) -> None:
        """One-time registration (e.g. rep.randomizer graph). TODO(6.0.1)."""

    @abstractmethod
    def apply(self, frame_idx: int) -> None:
        """Perturb the scene for this frame. TODO(6.0.1)."""
        raise NotImplementedError
