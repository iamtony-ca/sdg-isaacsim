"""BackgroundRandomizer — swap the 3D environment (warehouse/office/room/...) behind the object.

Pre-references every env in the `pool` once at setup() (hidden), then each `interval` frames
reveals a random one and hides the rest — the pre-spawn + visibility-toggle pattern (see
distractors.py) avoids reloading heavy USD stages inside the frame loop.

ONLINE vs OFFLINE are kept separate and chosen by what a `pool` entry is (resolved by
sdg.assets.resolve_background):
  - OFFLINE: a local dir (assets/env/usd/<name>, produced by tools/fetch_isaac_assets.py
             --envs) or an explicit .usd path -> no network at generation time.
  - ONLINE : a preset name (warehouse/office/simple_room/hospital/grid...) or a .usd URL ->
             resolved against the Isaac cloud assets root (network required).
Don't mix modes in one run — put local paths in an offline config, preset names in an online one.

★ 6.0.1 API (verified): rep.functional.create.reference(usd_path=, parent=, name=) (create.py:308);
  rep.functional.modify.visibility(prim, bool) (modify.py:1375).

Scene note: an env USD brings its own floor/walls, so use it INSTEAD of the textured ground
plane — set `scene.ground_plane: false`. The env floor is visual only (no collider), so give
objects `physics: {collider: false, gravity: false}` and let the pose randomizer place them
just above the floor (z ~ [0, 0.05]); otherwise a gravity-enabled object falls through.

config:
  {type: background,
   pool: [assets/env/usd/simple_room, assets/env/usd/office],  # OFFLINE (local)
   #  or  [simple_room, office, warehouse]                     # ONLINE (cloud presets)
   interval: 1}   # frames between switches (1 = every frame). Heavy envs -> raise this.
Empty pool => no-op (warns once).
"""
from __future__ import annotations

from typing import List, Optional

from ..registry import register
from .base import Randomizer

_BG_XFORM = "/World/Backgrounds"


@register("randomizer", "background")
class BackgroundRandomizer(Randomizer):
    def __init__(self, cfg, ctx=None):
        super().__init__(cfg, ctx)
        self._prims: List = []
        self._current: Optional[int] = None

    def setup(self) -> None:
        import omni.replicator.core as rep
        from .. import assets

        pool = self.cfg.get("pool", []) or []
        if not pool:
            print("[sdg][background] empty pool — no environment backgrounds added.")
            return
        resolved = []
        for entry in pool:
            try:
                path, mode = assets.resolve_background(entry)
                resolved.append((entry, path, mode))
            except ValueError as e:
                print(f"[sdg][background] {e}")
        if not resolved:
            return

        rep.functional.create.xform(name="Backgrounds", parent="/World")
        for i, (entry, path, mode) in enumerate(resolved):
            print(f"[sdg][background] [{mode}] {entry} -> {path}", flush=True)
            prim = rep.functional.create.reference(
                usd_path=path, parent=_BG_XFORM, name=f"bg_{i:03d}")
            rep.functional.modify.visibility(prim, False)
            self._prims.append(prim)

    def apply(self, frame_idx: int) -> None:
        import omni.replicator.core as rep

        if not self._prims:
            return
        interval = max(1, int(self.cfg.get("interval", 1)))
        # Only re-pick on interval boundaries; keep the same background in between.
        if self._current is None or frame_idx % interval == 0:
            self._current = int(self.ctx.rng.integers(len(self._prims)))
            for idx, prim in enumerate(self._prims):
                rep.functional.modify.visibility(prim, idx == self._current)
