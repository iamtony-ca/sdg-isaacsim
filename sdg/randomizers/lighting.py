"""LightingRandomizer — per-frame dome-light randomization (intensity/color + HDRI + rotation).

Sets the scene's /World/DomeLight attributes each frame via stable USD light attributes (no
Replicator-graph dependency): `inputs:intensity`, `inputs:color`, and — for background DR —
an environment `inputs:texture:file` (HDRI, latlong) sampled from a config pool plus a random
dome Y-rotation. An HDRI dome provides both image-based lighting AND a varied visible
background. Multi-light spawning across `kinds` (distant/rect) and `count` is an S3 extension.

config:
  {type: lighting, intensity: [lo,hi], count: [lo,hi], kinds: [dome, distant, rect],
   hdri: <dir or [paths]>,        # optional: pool of .hdr/.exr/.png/.jpg env maps
   hdri_rotate: true | [lo,hi]}   # optional: randomize dome Y-rotation (deg); true = [0,360]
"""
from __future__ import annotations

from ..registry import register
from .base import Randomizer, resolve_asset_list

_DOME_PATH = "/World/DomeLight"
_HDRI_EXTS = (".hdr", ".exr", ".png", ".jpg", ".jpeg")


@register("randomizer", "lighting")
class LightingRandomizer(Randomizer):
    def __init__(self, cfg, ctx=None):
        super().__init__(cfg, ctx)
        self._warned_kinds = False
        self._hdris = []

    def setup(self) -> None:
        self._hdris = resolve_asset_list(self.cfg.get("hdri"), _HDRI_EXTS)
        if self.cfg.get("hdri") and not self._hdris:
            print(f"[sdg][lighting] hdri set but no images found: {self.cfg.get('hdri')} — "
                  f"dome stays untextured.")

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

        # Background DR: sample an HDRI env map + randomize dome rotation.
        if self._hdris:
            path = self._hdris[int(rng.integers(len(self._hdris)))]
            _set_texture(prim, path)
        rot = self.cfg.get("hdri_rotate")
        if rot:
            rlo, rhi = (0.0, 360.0) if rot is True else _pair(rot)
            _set_dome_rotation(prim, float(rng.uniform(rlo, rhi)))

        kinds = self.cfg.get("kinds", ["dome"])
        if not self._warned_kinds and any(k != "dome" for k in kinds):
            print("[sdg][lighting] only 'dome' randomization implemented; "
                  "distant/rect multi-light is S3 — ignoring extra kinds.")
            self._warned_kinds = True


def _set_texture(prim, path: str) -> None:
    from pxr import Sdf

    attr = prim.GetAttribute("inputs:texture:file")
    if not attr:
        attr = prim.CreateAttribute("inputs:texture:file", Sdf.ValueTypeNames.Asset)
    attr.Set(Sdf.AssetPath(path))


def _set_dome_rotation(prim, deg_y: float) -> None:
    from pxr import UsdGeom

    UsdGeom.XformCommonAPI(prim).SetRotate((0.0, deg_y, 0.0))


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
