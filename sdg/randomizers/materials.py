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

BINDING: one material is bound to each target's ROOT prim with `strongerThanDescendants`
(rep.functional.modify.material's default), which DOES override the per-mesh material a CAD
import brings in — verified on a STEP import whose Mesh carried its own `weakerThanDescendants`
binding, on a mesh split into `materialBind` GeomSubsets, and through USD native instancing
(HOOPS wraps geometry in an instanceable prim, so the gprims are read-only instance proxies —
binding on the root still wins). So partial recolouring is NOT a binding-strength problem;
it is almost always a TARGET problem: whatever is left uncoloured was never a target. Occluders
and distractors in particular are separate prims — list them in `target` if you want them
randomized too (`prim:cube` occluders otherwise render default-white in every frame).

config:
  {type: materials, target: objects | ground | occluders | distractors | all | [<list>],
   prim_paths: [/World/Foo, ...],  # optional: extra roots (each subtree gets one material)
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
_TARGETS = ("objects", "ground", "occluders", "distractors", "all")


@register("randomizer", "materials")
class MaterialsRandomizer(Randomizer):
    def __init__(self, cfg, ctx=None):
        super().__init__(cfg, ctx)
        self._shaders = []  # one UsdShade.Shader per target prim
        self._targets = []
        self._textures = []
        self._bound = False

    def setup(self) -> None:
        self._textures = resolve_asset_list(self.cfg.get("textures"), _TEX_EXTS)
        if self.cfg.get("textures") and not self._textures:
            print(f"[sdg][materials] textures set but none found: {self.cfg.get('textures')}")
        # Targets are resolved and bound on the FIRST apply(), not here: occluder/distractor
        # prims are created in THEIR setup(), and randomizers are set up in config order, so
        # resolving now would silently miss them whenever `materials` is listed first. Every
        # setup() runs before any apply() (run_sdg._run_inside_app), so first-apply is safe.

    def _bind(self) -> None:
        import omni.replicator.core as rep
        from pxr import UsdShade

        self._bound = True
        self._targets = self._target_prims()
        if not self._targets:
            print(f"[sdg][materials] target {self.cfg.get('target', 'objects')!r} matched no "
                  f"prims — nothing will be randomized by this entry")
            return
        # Tag the material names with the target so two `materials` entries (e.g. objects +
        # ground) in one config cannot collide on a prim name.
        raw_tag = str(self.cfg.get("target", "objects"))
        tag = "".join(ch if ch.isalnum() else "_" for ch in raw_tag)[:24] or "t"
        for i, prim in enumerate(self._targets):
            mat_prim = rep.functional.create.material(
                mdl="OmniPBR.mdl", bind_prims=[prim], name=f"sdg_mat_{tag}_{i:03d}"
            )
            shader = _find_shader(mat_prim, UsdShade)
            if shader is None:
                print(f"[sdg][materials] no MDL shader found for the material bound to "
                      f"{prim.GetPath()} — it will not be randomized")
            self._shaders.append(shader)

    def apply(self, frame_idx: int) -> None:
        from pxr import Gf, Sdf, UsdShade

        if not self._bound:
            self._bind()
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
        """Resolve `target` (a keyword or a list of them) + `prim_paths` to root prims.

        Each returned prim gets ONE material bound to it with strongerThanDescendants, so a
        root covers its whole subtree — that is why occluders/distractors are collected as
        their spawned roots rather than as individual gprims.
        """
        target = self.cfg.get("target", "objects")
        wanted = {str(t).lower() for t in (target if isinstance(target, list) else [target])}
        unknown = wanted - set(_TARGETS)
        if unknown:
            print(f"[sdg][materials] unknown target(s) {sorted(unknown)} — valid: {sorted(_TARGETS)}")
        all_ = "all" in wanted
        world = self.ctx.scene.world_path
        prims = []
        if all_ or "objects" in wanted:
            prims += [inst["prim"] for inst in self.ctx.scene.instances]
        if all_ or "ground" in wanted:
            prims += _ground_prims(world)
        # Occluders/distractors are spawned by their own randomizers under a known group xform;
        # they carry no material of their own, so without this they stay default-white forever.
        if all_ or "occluders" in wanted:
            prims += _group_children(f"{world}/Occluders")
        if all_ or "distractors" in wanted:
            prims += _group_children("/World/Distractors")
        for p in self.cfg.get("prim_paths", []) or []:
            prims += _group_children(str(p), self_if_leaf=True)
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


def _group_children(path: str, self_if_leaf: bool = False) -> List:
    """Direct children of a group xform (one spawned prim each), or [] if it does not exist.

    Used for the /World/Occluders and /World/Distractors groups: each child is one spawned
    occluder/distractor, and binding on that child covers its whole subtree.
    """
    import omni.usd

    stage = omni.usd.get_context().get_stage()
    root = stage.GetPrimAtPath(path)
    if not root or not root.IsValid():
        return []
    kids = list(root.GetAllChildren())
    if not kids and self_if_leaf:
        return [root]
    return kids


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
