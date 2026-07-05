"""LightingRandomizer — per-frame dome-light intensity/color randomization (MVP).

Sets the scene's /World/DomeLight `inputs:intensity` and `inputs:color` each frame via the
stable USD light attributes (no Replicator-graph dependency). Multi-light spawning across
`kinds` (distant/rect) and `count` is an S3 extension.

config:
  {type: lighting, intensity: [lo,hi], count: [lo,hi], kinds: [dome, distant, rect]}
"""
from __future__ import annotations

from ..registry import register
from .base import Randomizer

_DOME_PATH = "/World/DomeLight"


@register("randomizer", "lighting")
class LightingRandomizer(Randomizer):
    def __init__(self, cfg, ctx=None):
        super().__init__(cfg, ctx)
        self._warned_kinds = False

    def apply(self, frame_idx: int) -> None:
        import omni.usd

        rng = self.ctx.rng
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(_DOME_PATH)
        if not prim or not prim.IsValid():
            return

        lo, hi = _pair(self.cfg.get("intensity", [1000.0, 1000.0]))
        intensity = float(rng.uniform(lo, hi))
        _set_attr(prim, "inputs:intensity", intensity)

        color = tuple(float(c) for c in rng.uniform(0.6, 1.0, size=3))
        _set_attr(prim, "inputs:color", color)

        kinds = self.cfg.get("kinds", ["dome"])
        if not self._warned_kinds and any(k != "dome" for k in kinds):
            print("[sdg][lighting] only 'dome' randomization implemented; "
                  "distant/rect multi-light is S3 — ignoring extra kinds.")
            self._warned_kinds = True


def _set_attr(prim, name, value):
    from pxr import Gf, Sdf

    attr = prim.GetAttribute(name)
    if not attr:
        # inputs:color is a color3f; inputs:intensity is a float.
        type_name = Sdf.ValueTypeNames.Color3f if "color" in name else Sdf.ValueTypeNames.Float
        attr = prim.CreateAttribute(name, type_name)
    if isinstance(value, tuple):
        attr.Set(Gf.Vec3f(*value))
    else:
        attr.Set(value)


def _pair(v):
    if isinstance(v, list):
        return (float(v[0]), float(v[-1]))
    return (float(v), float(v))
