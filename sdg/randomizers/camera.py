"""CameraRandomizer — per-frame camera placement on a look-at hemisphere/sphere.

★ 6.0.1 API: rep.functional.modify.pose(cam_prim, position_value=, look_at_value=)
(functional/modify.py:227; look_at_value orients the camera at the target). Camera world
translation is sampled in spherical coords around the objects' centroid.

config:
  {type: camera, mode: look_at, distance: [lo,hi],
   elevation_deg: [lo,hi], azimuth_deg: [lo,hi]}
"""
from __future__ import annotations

import math
from typing import Tuple

from ..registry import register
from .base import Randomizer


@register("randomizer", "camera")
class CameraRandomizer(Randomizer):
    def apply(self, frame_idx: int) -> None:
        import omni.replicator.core as rep

        rng = self.ctx.rng
        target = self._target(rep)
        dist = self.cfg.get("distance", [1.0, 1.0])
        elev = self.cfg.get("elevation_deg", [30.0, 30.0])
        azim = self.cfg.get("azimuth_deg", [-180.0, 180.0])

        for sensor in self.ctx.sensors:
            cam = getattr(sensor, "cam_prim", None)
            if cam is None:
                continue
            d = float(rng.uniform(*_pair(dist)))
            el = math.radians(float(rng.uniform(*_pair(elev))))
            az = math.radians(float(rng.uniform(*_pair(azim))))
            pos = (
                target[0] + d * math.cos(el) * math.cos(az),
                target[1] + d * math.cos(el) * math.sin(az),
                target[2] + d * math.sin(el),
            )
            rep.functional.modify.pose(cam, position_value=pos, look_at_value=target)

    def _target(self, rep) -> Tuple[float, float, float]:
        """Centroid of the object instances (falls back to world origin)."""
        insts = self.ctx.scene.instances
        if not insts:
            return (0.0, 0.0, 0.0)
        acc = [0.0, 0.0, 0.0]
        for inst in insts:
            t = rep.functional.utils.get_world_transform(inst["prim"]).GetTranslation()
            acc[0] += t[0]; acc[1] += t[1]; acc[2] += t[2]
        n = len(insts)
        return (acc[0] / n, acc[1] / n, acc[2] / n)


def _pair(v):
    if isinstance(v, list):
        return (float(v[0]), float(v[-1]))
    return (float(v), float(v))
