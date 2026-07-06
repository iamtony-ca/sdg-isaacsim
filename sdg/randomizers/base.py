"""Randomizer interface.

Each randomizer is constructed from its config dict and is invoked once per frame to
perturb the scene (light, material, object/camera pose, distractors). Implementations
register with @register("randomizer", "<type>") so config `randomizers[].type` resolves.
"""
from __future__ import annotations

import glob
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List


def resolve_asset_list(spec: Any, exts) -> List[str]:
    """Resolve a config value into a list of existing files, for asset-pool randomizers
    (HDRIs, textures). `spec` is either a directory (globbed recursively for `exts`) or an
    explicit list of file paths (dirs inside the list are globbed too). Returns sorted unique
    existing files. Keeps randomizers asset-agnostic: point them at a folder in config, no
    hardcoded engine paths (which are version-fragile)."""
    if not spec:
        return []
    items = spec if isinstance(spec, list) else [spec]
    exts = tuple(e.lower() for e in exts)
    out: List[str] = []
    for it in items:
        it = os.path.expanduser(str(it))
        if os.path.isdir(it):
            for p in glob.glob(os.path.join(it, "**", "*"), recursive=True):
                if p.lower().endswith(exts):
                    out.append(p)
        elif os.path.isfile(it) and it.lower().endswith(exts):
            out.append(it)
    return sorted(set(out))


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
