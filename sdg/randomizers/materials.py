"""MaterialsRandomizer — per-frame PBR material randomization on target prims.

★ 6.0.1 API (verified against the install):
  - rep.functional.create.material(mdl="OmniPBR.mdl", bind_prims=[prim], **mdl_params)
      -> pxr.Usd.Prim (a UsdShade.Material with an MDL Shader child)  (functional/create.py:1437)
  - OmniPBR.mdl inputs (verified /isaac-sim/kit/mdl/core/Base/OmniPBR.mdl):
      diffuse_color_constant (color), reflection_roughness_constant (float, def 0.5),
      metallic_constant (float, def 0.0).

Strategy: create ONE OmniPBR material per target prim at setup() and bind it, then each
frame Set the MDL shader inputs directly (no per-frame material creation -> no prim leak).
Texture DR: with a `textures` pool, each frame binds a random `diffuse_texture` (asset) to a
target with probability `texture_prob` (white base color so the image shows), else clears it
and jitters a solid color. Targeting `ground`/`all` also randomizes the ground plane — the
dominant visible surface — so backgrounds vary too.

  CAVEAT — texture images need UVs. STL/CAD imports usually have none, so we use OmniPBR
  `project_uvw` in WORLD space (planar). That looks clean on flat surfaces (the ground) but
  SMEARS on faces parallel to the projection axis (e.g. the vertical sides of an object). So
  prefer `textures` for the `ground`, and rely on color/roughness/metallic randomization for
  UV-less objects (verified to vary independently of lighting). UV-mapped object meshes get
  proper texturing.

config:
  {type: materials, target: objects | ground | all,
   roughness: [lo,hi], metallic: [lo,hi], base_color: hsv_jitter | none,
   textures: <dir or [paths]>,   # optional: pool of .png/.jpg diffuse textures
   texture_prob: 0.7}            # optional: per-frame chance a target uses a texture
"""
from __future__ import annotations

import colorsys
from typing import List

from ..registry import register
from .base import Randomizer, resolve_asset_list

_ROUGHNESS = "reflection_roughness_constant"
_METALLIC = "metallic_constant"
_COLOR = "diffuse_color_constant"
_DIFFUSE_TEX = "diffuse_texture"
_TEX_EXTS = (".png", ".jpg", ".jpeg")


@register("randomizer", "materials")
class MaterialsRandomizer(Randomizer):
    def __init__(self, cfg, ctx=None):
        super().__init__(cfg, ctx)
        self._shaders = []  # one UsdShade.Shader per target prim
        self._targets = []
        self._textures = []

    def setup(self) -> None:
        import omni.replicator.core as rep
        from pxr import UsdShade

        self._textures = resolve_asset_list(self.cfg.get("textures"), _TEX_EXTS)
        if self.cfg.get("textures") and not self._textures:
            print(f"[sdg][materials] textures set but none found: {self.cfg.get('textures')}")
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
        tex_prob = float(self.cfg.get("texture_prob", 0.7))

        for shader in self._shaders:
            if shader is None:
                continue
            _set_input(shader, UsdShade, Sdf, _ROUGHNESS, float(rng.uniform(*rough)))
            _set_input(shader, UsdShade, Sdf, _METALLIC, float(rng.uniform(*metal)))
            use_tex = self._textures and float(rng.uniform(0, 1)) < tex_prob
            if use_tex:
                # bind a random diffuse texture; white base so the image shows unmodulated
                path = self._textures[int(rng.integers(len(self._textures)))]
                _set_tex(shader, UsdShade, Sdf, _DIFFUSE_TEX, path)
                # project_uvw: projected texturing so images show on UV-less meshes (STL/CAD
                # imports have no UVs). world_or_object=True -> WORLD-space (metre) projection,
                # so tile size is consistent regardless of an asset's baked mesh scale (object
                # space would tile thousands of times on a mm-authored mesh). texture_scale =
                # tiles per metre; randomize for size variety.
                _set_bool(shader, UsdShade, Sdf, "project_uvw", True)
                _set_bool(shader, UsdShade, Sdf, "world_or_object", True)
                sc = float(rng.uniform(*_pair(self.cfg.get("texture_scale", [4.0, 20.0]))))
                _set_input(shader, UsdShade, Sdf, "texture_scale", Gf.Vec2f(sc, sc), vtype=Sdf.ValueTypeNames.Float2)
                _set_input(shader, UsdShade, Sdf, _COLOR, Gf.Vec3f(1.0, 1.0, 1.0), is_color=True)
            else:
                if self._textures:
                    _set_tex(shader, UsdShade, Sdf, _DIFFUSE_TEX, "")  # clear -> solid color
                if base_color == "hsv_jitter":
                    h, s, v = float(rng.uniform(0, 1)), float(rng.uniform(0.5, 1.0)), float(rng.uniform(0.5, 1.0))
                    r, g, b = colorsys.hsv_to_rgb(h, s, v)
                    _set_input(shader, UsdShade, Sdf, _COLOR, Gf.Vec3f(r, g, b), is_color=True)

    # ------------------------------------------------------------------ helpers
    def _target_prims(self) -> List:
        target = self.cfg.get("target", "objects")
        prims = []
        if target in ("objects", "all"):
            prims += [inst["prim"] for inst in self.ctx.scene.instances]
        if target in ("ground", "all"):
            prims += _ground_prims(self.ctx.scene.world_path)
        return prims


def _find_shader(mat_prim, UsdShade):
    for child in mat_prim.GetChildren():
        if child.IsA(UsdShade.Shader):
            return UsdShade.Shader(child)
    # fallback: the MDL surface source
    src = UsdShade.Material(mat_prim).ComputeSurfaceSource("mdl")
    return src[0] if src and src[0] else None


def _set_input(shader, UsdShade, Sdf, name, value, is_color=False, vtype=None):
    inp = shader.GetInput(name)
    if not inp:
        type_name = vtype or (Sdf.ValueTypeNames.Color3f if is_color else Sdf.ValueTypeNames.Float)
        inp = shader.CreateInput(name, type_name)
    inp.Set(value)


def _set_bool(shader, UsdShade, Sdf, name, value):
    inp = shader.GetInput(name)
    if not inp:
        inp = shader.CreateInput(name, Sdf.ValueTypeNames.Bool)
    inp.Set(bool(value))


def _set_tex(shader, UsdShade, Sdf, name, path):
    """Set an MDL texture_2d input (asset). Empty path clears it (-> solid color)."""
    inp = shader.GetInput(name)
    if not inp:
        inp = shader.CreateInput(name, Sdf.ValueTypeNames.Asset)
    inp.Set(Sdf.AssetPath(path))


def _ground_prims(world_path: str) -> List:
    """Mesh/Gprim prims under the scene's GroundPlane, for material binding. Binding to an
    Xform ancestor may not affect the child mesh, so collect imageable geom prims."""
    import omni.usd
    from pxr import UsdGeom

    stage = omni.usd.get_context().get_stage()
    root = stage.GetPrimAtPath(f"{world_path}/GroundPlane")
    if not root or not root.IsValid():
        return []
    prims = []
    if root.IsA(UsdGeom.Gprim):
        prims.append(root)
    for p in root.GetAllChildren():
        for d in [p] + list(p.GetAllChildren()):
            if d.IsA(UsdGeom.Gprim) and d not in prims:
                prims.append(d)
    return prims or [root]


def _pair(v):
    if isinstance(v, list):
        return (float(v[0]), float(v[-1]))
    return (float(v), float(v))
