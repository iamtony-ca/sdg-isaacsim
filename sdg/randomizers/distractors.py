"""DistractorsRandomizer — per-frame clutter objects (show/hide + scatter).

Pre-spawns a fixed pool of distractor instances at setup() (max of `count`), then each
frame reveals a random subset and scatters their pose. Pre-spawn + toggle avoids the cost
of creating/destroying prims every frame.

★ 6.0.1 API (verified against the install):
  - rep.functional.create.reference(usd_path=, semantics=, name=, parent=)  (create.py:308)
  - rep.functional.modify.visibility(prim, value: bool)                     (modify.py:1375)
  - rep.functional.modify.pose(prim, position_value=, rotation_value=)      (modify.py:227)

config:
  {type: distractors, pool: [<obj_id | path.usd>, ...], count: [lo,hi],
   extents: [[xmin,ymin,zmin],[xmax,ymax,zmax]],   # optional scatter box
   semantic_class: distractor | null}              # null => leave unlabelled
Empty pool => no-op (warns once).
"""
from __future__ import annotations

import math
import os
from typing import List

from ..registry import register
from .base import Randomizer
from ..scene.base import WS_ROOT

_DEFAULT_EXTENTS = [[-0.5, -0.5, 0.0], [0.5, 0.5, 0.4]]


@register("randomizer", "distractors")
class DistractorsRandomizer(Randomizer):
    def __init__(self, cfg, ctx=None):
        super().__init__(cfg, ctx)
        self._prims: List = []
        self._max = 0

    def setup(self) -> None:
        import omni.replicator.core as rep

        pool = self.cfg.get("pool", []) or []
        if not pool:
            print("[sdg][distractors] empty pool — no distractors will be added.")
            return
        usd_paths = [self._resolve(p) for p in pool]
        count = self.cfg.get("count", [0, 0])
        self._max = int(_pair(count)[1])
        sem_class = self.cfg.get("semantic_class", "distractor")

        rep.functional.create.xform(name="Distractors", parent="/World")
        for i in range(self._max):
            usd_path = usd_paths[i % len(usd_paths)]
            kwargs = {"usd_path": usd_path, "parent": "/World/Distractors", "name": f"distractor_{i:03d}"}
            if sem_class:
                kwargs["semantics"] = {"class": sem_class}
            prim = rep.functional.create.reference(**kwargs)
            rep.functional.modify.visibility(prim, False)
            self._prims.append(prim)

    def apply(self, frame_idx: int) -> None:
        import omni.replicator.core as rep

        if not self._prims:
            return
        rng = self.ctx.rng
        lo, hi = (int(x) for x in _pair(self.cfg.get("count", [0, 0])))
        n = int(rng.integers(lo, hi + 1))  # inclusive upper bound
        ext = self.cfg.get("extents", _DEFAULT_EXTENTS)
        (xmn, ymn, zmn), (xmx, ymx, zmx) = ext[0], ext[1]

        for idx, prim in enumerate(self._prims):
            visible = idx < n
            rep.functional.modify.visibility(prim, visible)
            if not visible:
                continue
            pos = (
                float(rng.uniform(xmn, xmx)),
                float(rng.uniform(ymn, ymx)),
                float(rng.uniform(zmn, zmx)),
            )
            rep.functional.modify.pose(prim, position_value=pos, rotation_value=_uniform_so3(rng))

    # ------------------------------------------------------------------ helpers
    def _resolve(self, entry: str) -> str:
        """Pool entry -> USD path. Accepts an explicit *.usd* path or an obj_id folder."""
        if entry.lower().endswith((".usd", ".usda", ".usdc", ".usdz")):
            return entry if os.path.isabs(entry) else os.path.join(WS_ROOT, entry)
        # treat as obj_id under assets/obj/<id>/ (reuse the scene builder's resolver)
        return self.ctx.scene.resolve_asset_usd(entry)


def _uniform_so3(rng):
    from pxr import Gf

    u1, u2, u3 = rng.uniform(0.0, 1.0, size=3)
    q1 = math.sqrt(1.0 - u1) * math.sin(2.0 * math.pi * u2)
    q2 = math.sqrt(1.0 - u1) * math.cos(2.0 * math.pi * u2)
    q3 = math.sqrt(u1) * math.sin(2.0 * math.pi * u3)
    q4 = math.sqrt(u1) * math.cos(2.0 * math.pi * u3)
    return Gf.Rotation(Gf.Quatd(float(q4), Gf.Vec3d(float(q1), float(q2), float(q3))))


def _pair(v):
    if isinstance(v, list):
        return (float(v[0]), float(v[-1]))
    return (float(v), float(v))
