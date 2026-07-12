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
    """Resolve a config value into a list of asset paths, for asset-pool randomizers
    (HDRIs, textures). Each item of `spec` (a single value or a list) may be:
      - a local directory      -> globbed recursively for `exts`,
      - a local file path      -> kept if its extension matches,
      - a remote URL           -> passed through (omniverse://, http(s)://, file://),
      - `isaac_skies[:Cat,..]` -> expanded to Isaac Sim's built-in HDRI sky library URLs.
    Returns a de-duplicated list (local files sorted; URL/library order preserved). Keeps
    randomizers asset-agnostic: point them at a folder OR the engine library in config, with
    no hardcoded (version-fragile) cloud path baked into code."""
    if not spec:
        return []
    from .. import assets as _assets

    items = spec if isinstance(spec, list) else [spec]
    exts = tuple(e.lower() for e in exts)
    local: List[str] = []
    remote: List[str] = []  # URL/library items keep their given order (no on-disk check)
    for it in items:
        if _assets.is_sky_keyword(it):
            remote += _assets.sky_urls(it)
            continue
        if _assets.is_url(it):
            remote.append(it)
            continue
        it = os.path.expanduser(str(it))
        if os.path.isdir(it):
            for p in glob.glob(os.path.join(it, "**", "*"), recursive=True):
                if p.lower().endswith(exts):
                    local.append(p)
        elif os.path.isfile(it) and it.lower().endswith(exts):
            local.append(it)
    # de-dup while preserving remote order after sorted locals
    seen = set()
    out: List[str] = []
    for p in sorted(set(local)) + remote:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


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
