"""PoseRandomizer — per-frame object pose randomization.

★ 6.0.1 API: rep.functional.modify.pose(prim, position_value=, rotation_value=,
rotation_order="XYZ") (functional/modify.py:227). rotation_value accepts euler degrees,
a quaternion, or a pxr.Gf.Rotation. For truly uniform orientation ("uniform_so3") we
sample a uniform quaternion (Shoemake) and pass a Gf.Rotation.

config:
  {type: pose, target: objects,
   position: {x: [lo,hi], y: [lo,hi], z: [lo,hi]},
   rotation: uniform_so3 | uniform_euler | yaw | none,
   yaw_deg: [lo, hi]}   # only for rotation: yaw (about the Z/up axis); default [0, 360]

rotation modes:
  none        — keep the asset's authored orientation (no rotation).
  yaw         — rotate ONLY about the stage up-axis (Z). Keeps the object upright, so a
                bottom-resting object (see objects[].origin: bottom) stays on the ground while
                its heading varies. Range via `yaw_deg` (degrees), default full [0, 360].
  uniform_euler — independent XYZ euler spin (full tumble).
  uniform_so3 — truly uniform orientation on SO(3) (full tumble; can poke through the ground).
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
        from pxr import Gf

        rng = self.ctx.rng
        pos_cfg = self.cfg.get("position", {})
        rot_mode = self.cfg.get("rotation", "uniform_so3")

        for inst in self.ctx.scene.instances:
            pos = (
                _u(rng, pos_cfg.get("x", [0.0, 0.0])),
                _u(rng, pos_cfg.get("y", [0.0, 0.0])),
                _u(rng, pos_cfg.get("z", [0.0, 0.0])),
            )
            rot = self._sample_rotation(rng, rot_mode)  # Gf.Rotation or None
            # `objects[].origin` redefines the object frame's origin (e.g. bottom-face centre).
            # Place THAT point at the commanded position (not the mesh's bbox centre): rotate the
            # spawn-measured world offset by this frame's rotation and subtract it, so the object
            # is un-buried (not just the GT). pose GT reports the same origin (collector.py), so
            # placement and 6D-pose stay consistent.
            offset = inst.get("origin_offset")  # world metres at identity pose, or None
            if offset is not None:
                R = Gf.Matrix3d(rot) if rot is not None else Gf.Matrix3d(1.0)
                off = Gf.Vec3d(float(offset[0]), float(offset[1]), float(offset[2])) * R
                pos = (pos[0] - off[0], pos[1] - off[1], pos[2] - off[2])
            if rot is None:
                rep.functional.modify.pose(inst["prim"], position_value=pos)
            else:
                rep.functional.modify.pose(inst["prim"], position_value=pos, rotation_value=rot)

    def _sample_rotation(self, rng, mode):
        from pxr import Gf

        if mode in (None, "none", False):
            return None
        if mode in ("yaw", "z_only", "uniform_yaw"):
            # Rotate only about the stage up-axis (Z-up in Isaac): object stays upright.
            # Pairs with objects[].origin: bottom -> always rests on the ground, heading varies.
            lo, hi = self.cfg.get("yaw_deg", [0.0, 360.0])
            a = float(rng.uniform(float(lo), float(hi)))
            return Gf.Rotation(Gf.Vec3d(0, 0, 1), a)
        if mode == "uniform_euler":
            e = rng.uniform(0.0, 360.0, size=3)
            # compose XYZ into a Gf.Rotation so callers get one rotation representation.
            return (Gf.Rotation(Gf.Vec3d(1, 0, 0), float(e[0]))
                    * Gf.Rotation(Gf.Vec3d(0, 1, 0), float(e[1]))
                    * Gf.Rotation(Gf.Vec3d(0, 0, 1), float(e[2])))
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
