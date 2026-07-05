"""MaterialsRandomizer — per-frame PBR material randomization on target prims.

★ 6.0.1 API (verified against the install):
  - rep.functional.create.material(mdl="OmniPBR.mdl", bind_prims=[prim], **mdl_params)
      -> pxr.Usd.Prim (a UsdShade.Material with an MDL Shader child)  (functional/create.py:1437)
  - OmniPBR.mdl inputs (verified /isaac-sim/kit/mdl/core/Base/OmniPBR.mdl):
      diffuse_color_constant (color), reflection_roughness_constant (float, def 0.5),
      metallic_constant (float, def 0.0).

Strategy: create ONE OmniPBR material per target prim at setup() and bind it, then each
frame Set the MDL shader inputs directly (no per-frame material creation -> no prim leak).

config:
  {type: materials, target: objects,
   roughness: [lo,hi], metallic: [lo,hi], base_color: hsv_jitter | none}
"""
from __future__ import annotations

import colorsys
from typing import List

from ..registry import register
from .base import Randomizer

_ROUGHNESS = "reflection_roughness_constant"
_METALLIC = "metallic_constant"
_COLOR = "diffuse_color_constant"


@register("randomizer", "materials")
class MaterialsRandomizer(Randomizer):
    def __init__(self, cfg, ctx=None):
        super().__init__(cfg, ctx)
        self._shaders = []  # one UsdShade.Shader per target prim
        self._targets = []

    def setup(self) -> None:
        import omni.replicator.core as rep
        from pxr import UsdShade

        self._targets = self._target_prims()
        for i, prim in enumerate(self._targets):
            mat_prim = rep.functional.create.material(
                mdl="OmniPBR.mdl", bind_prims=[prim], name=f"sdg_mat_{i:03d}"
            )
            shader = _find_shader(mat_prim, UsdShade)
            self._shaders.append(shader)

    def apply(self, frame_idx: int) -> None:
        from pxr import Gf, Sdf, UsdShade

        rng = self.ctx.rng
        rough = _pair(self.cfg.get("roughness", [0.5, 0.5]))
        metal = _pair(self.cfg.get("metallic", [0.0, 0.0]))
        base_color = self.cfg.get("base_color", "hsv_jitter")

        for shader in self._shaders:
            if shader is None:
                continue
            _set_input(shader, UsdShade, Sdf, _ROUGHNESS, float(rng.uniform(*rough)))
            _set_input(shader, UsdShade, Sdf, _METALLIC, float(rng.uniform(*metal)))
            if base_color == "hsv_jitter":
                h, s, v = float(rng.uniform(0, 1)), float(rng.uniform(0.5, 1.0)), float(rng.uniform(0.5, 1.0))
                r, g, b = colorsys.hsv_to_rgb(h, s, v)
                _set_input(shader, UsdShade, Sdf, _COLOR, Gf.Vec3f(r, g, b), is_color=True)

    # ------------------------------------------------------------------ helpers
    def _target_prims(self) -> List:
        target = self.cfg.get("target", "objects")
        if target == "objects":
            return [inst["prim"] for inst in self.ctx.scene.instances]
        return [inst["prim"] for inst in self.ctx.scene.instances]  # only 'objects' supported for now


def _find_shader(mat_prim, UsdShade):
    for child in mat_prim.GetChildren():
        if child.IsA(UsdShade.Shader):
            return UsdShade.Shader(child)
    # fallback: the MDL surface source
    src = UsdShade.Material(mat_prim).ComputeSurfaceSource("mdl")
    return src[0] if src and src[0] else None


def _set_input(shader, UsdShade, Sdf, name, value, is_color=False):
    inp = shader.GetInput(name)
    if not inp:
        type_name = Sdf.ValueTypeNames.Color3f if is_color else Sdf.ValueTypeNames.Float
        inp = shader.CreateInput(name, type_name)
    inp.Set(value)


def _pair(v):
    if isinstance(v, list):
        return (float(v[0]), float(v[-1]))
    return (float(v), float(v))
