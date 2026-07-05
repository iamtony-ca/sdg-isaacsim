"""PoseRandomizer — per-frame object pose randomization.

★ 6.0.1 API: rep.functional.modify.pose(prim, position_value=, rotation_value=,
rotation_order="XYZ") (functional/modify.py:227). rotation_value accepts euler degrees,
a quaternion, or a pxr.Gf.Rotation. For truly uniform orientation ("uniform_so3") we
sample a uniform quaternion (Shoemake) and pass a Gf.Rotation.

config:
  {type: pose, target: objects,
   position: {x: [lo,hi], y: [lo,hi], z: [lo,hi]},
   rotation: uniform_so3 | uniform_euler | none}
"""
from __future__ import annotations

import math
from typing import Any, Dict

from ..registry import register
from .base import Randomizer


@register("randomizer", "pose")
class PoseRandomizer(Randomizer):
    def apply(self, frame_idx: int) -> None:
        import omni.replicator.core as rep

        rng = self.ctx.rng
        pos_cfg = self.cfg.get("position", {})
        rot_mode = self.cfg.get("rotation", "uniform_so3")

        for inst in self.ctx.scene.instances:
            pos = (
                _u(rng, pos_cfg.get("x", [0.0, 0.0])),
                _u(rng, pos_cfg.get("y", [0.0, 0.0])),
                _u(rng, pos_cfg.get("z", [0.0, 0.0])),
            )
            rot = self._sample_rotation(rng, rot_mode)
            if rot is None:
                rep.functional.modify.pose(inst["prim"], position_value=pos)
            else:
                rep.functional.modify.pose(
                    inst["prim"], position_value=pos, rotation_value=rot, rotation_order="XYZ"
                )

    def _sample_rotation(self, rng, mode):
        from pxr import Gf

        if mode in (None, "none", False):
            return None
        if mode == "uniform_euler":
            e = rng.uniform(0.0, 360.0, size=3)
            return (float(e[0]), float(e[1]), float(e[2]))  # euler degrees, XYZ
        # default: uniform SO(3) via Shoemake's method -> quaternion -> Gf.Rotation
        u1, u2, u3 = rng.uniform(0.0, 1.0, size=3)
        q1 = math.sqrt(1.0 - u1) * math.sin(2.0 * math.pi * u2)
        q2 = math.sqrt(1.0 - u1) * math.cos(2.0 * math.pi * u2)
        q3 = math.sqrt(u1) * math.sin(2.0 * math.pi * u3)
        q4 = math.sqrt(u1) * math.cos(2.0 * math.pi * u3)
        # Gf.Quatd(real, imaginary_vec)
        return Gf.Rotation(Gf.Quatd(float(q4), Gf.Vec3d(float(q1), float(q2), float(q3))))


def _u(rng, rng_pair) -> float:
    lo, hi = (rng_pair + [rng_pair[0]])[:2] if isinstance(rng_pair, list) else (rng_pair, rng_pair)
    return float(rng.uniform(float(lo), float(hi)))
