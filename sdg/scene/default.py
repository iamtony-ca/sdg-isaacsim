"""DefaultSceneBuilder — the MVP scene: background/ground + object spawn.

★ 6.0.1 API (verified against the install):
  - rep.functional.create.xform / dome_light / reference   (create.py:101/1362/308)
  - rep.functional.create.reference(usd_path=, semantics={"class": ...}, name=, parent=)
      -> pxr.Usd.Prim                                        (create.py:308)
  - rep.functional.physics.apply_rigid_body(prim, with_collider=True)  (physics.py:300)
  - rep.functional.physics.apply_collider(prim)             (physics.py:495)
  - GroundPlane from isaacsim.core.experimental.objects; .prims for semantics
      (simulation_get_data.py:29,55-56)

Objects are referenced only by obj_id; assets come from assets/obj/<obj_id>/ (a USD
file resolved by SceneBuilder.resolve_asset_usd). No object name is hardcoded.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..config import ObjectSpec
from ..registry import register
from .base import SceneBuilder


@register("scene", "default")
class DefaultSceneBuilder(SceneBuilder):
    def build(self) -> None:
        import omni.replicator.core as rep
        import omni.replicator.core.functional.physics  # noqa: F401  # not auto-imported by functional/__init__

        # --- World root -----------------------------------------------------
        rep.functional.create.xform(name="World")  # -> /World

        # --- Background / ground -------------------------------------------
        bg = self.scene_cfg.get("background", "none")
        if self.scene_cfg.get("ground_plane", False) or bg == "ground_plane":
            self._add_ground_plane(rep)
        if bg not in ("none", "ground_plane", None):
            # bg is a USD env (e.g. warehouse) or an explicit .usd path.
            usd_path = _resolve_env_usd(bg)
            rep.functional.create.reference(
                usd_path=usd_path, parent=self.world_path, name="Background"
            )

        # A default dome light so the scene is lit even before lighting DR runs.
        # The lighting randomizer (if enabled) adds/overrides lights per frame.
        rep.functional.create.dome_light(intensity=1000, parent=self.world_path, name="DomeLight")

        # --- Objects (by obj_id) -------------------------------------------
        for spec in self.objects:
            self._spawn_object(rep, spec)

    # ------------------------------------------------------------------ helpers
    def _add_ground_plane(self, rep) -> None:
        from isaacsim.core.experimental.objects import GroundPlane

        gp = GroundPlane(f"{self.world_path}/GroundPlane")
        rep.functional.modify.semantics(gp.prims, {"class": "ground_plane"}, mode="add")

    def _spawn_object(self, rep, spec: ObjectSpec) -> None:
        usd_path = self.resolve_asset_usd(spec.obj_id)
        semantic_class = spec.semantic.get("class", spec.obj_id)
        keypoints_local = self.load_keypoints(spec.obj_id)  # object-local 3D kpts or None
        part_defs = self.load_parts(spec.obj_id)            # sub-prim semantic parts or []
        paths: List[str] = []

        for i in range(max(1, spec.count)):
            name = f"{spec.obj_id}_{i:03d}"
            prim = rep.functional.create.reference(
                usd_path=usd_path,
                parent=self.world_path,
                name=name,
                semantics={"class": semantic_class},
            )
            prim_path = str(prim.GetPath())
            # pose origin (object-local). Face keywords (bottom/top/center) are resolved from
            # THIS prim's actual bbox — object-agnostic, works for any mesh (principle 2).
            origin_local = self._resolve_origin_for_prim(prim, spec.origin, keypoints_local)
            # World-metre offset of the origin point from the prim's placement origin, measured
            # at spawn (identity pose). The pose randomizer rotates this by the frame rotation
            # and subtracts it so the origin point (not the bbox centre) lands at the commanded
            # position — un-buries the object. origin_local itself stays in mesh units for the
            # GT re-projection in collector.py (which maps via get_world_transform incl. scale).
            origin_offset = self._origin_world_offset(rep, prim, origin_local)
            self._apply_physics(rep, prim, spec.physics)
            parts = self._label_parts(rep, prim_path, part_defs)  # semantics on sub-prims
            paths.append(prim_path)
            self.instances.append(
                {
                    "obj_id": spec.obj_id,
                    "instance_id": len(self.instances),
                    "prim_path": prim_path,
                    "prim": prim,
                    "semantic_class": semantic_class,
                    "keypoints_local": keypoints_local,
                    "origin_local": origin_local,
                    "origin_offset": origin_offset,  # world-metre offset for placement (or None)
                    "parts": parts,  # [{name, class, prim_path}] labelled sub-prims
                }
            )
        self.object_prims[spec.obj_id] = paths

    def _resolve_origin_for_prim(self, prim, origin_spec, keypoints_local):
        """Resolve `objects[].origin` to an object-local point (or None).

        Face keywords — 'bottom' | 'top' | 'center' (or {face: bottom}) — are computed from
        the spawned prim's own bbox along the stage up-axis, so the same config works for any
        object regardless of its dimensions. Everything else (explicit [x,y,z] or
        {keypoint: i}) is delegated to SceneBuilder.resolve_origin.
        """
        face = None
        if isinstance(origin_spec, str):
            face = origin_spec.lower()
        elif isinstance(origin_spec, dict) and "face" in origin_spec:
            face = str(origin_spec["face"]).lower()
        if face is None:
            return self.resolve_origin(origin_spec, keypoints_local)

        from pxr import Usd, UsdGeom

        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(),
                                 [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
        rng = cache.ComputeUntransformedBound(prim).ComputeAlignedRange()
        lo, hi = rng.GetMin(), rng.GetMax()
        o = [(lo[i] + hi[i]) * 0.5 for i in range(3)]           # bbox centre
        up = UsdGeom.GetStageUpAxis(prim.GetStage())
        comp = 1 if up == UsdGeom.Tokens.y else 2               # Isaac default is Z-up
        if face in ("bottom", "min"):
            o[comp] = lo[comp]
        elif face in ("top", "max"):
            o[comp] = hi[comp]
        elif face in ("center", "centre"):
            pass
        else:
            raise ValueError(f"unsupported origin '{face}' (use bottom|top|center, "
                             f"[x,y,z], or {{keypoint: i}})")
        return [float(v) for v in o]

    @staticmethod
    def _origin_world_offset(rep, prim, origin_local):
        """World-metre vector from the prim's placement origin to the origin point, at the
        prim's spawn (identity) pose. Computed from the actual world transform so it is
        unit-correct regardless of any modeling scale baked into the asset. None if no origin."""
        if origin_local is None:
            return None
        from pxr import Gf

        M = rep.functional.utils.get_world_transform(prim).GetMatrix()  # row-vector local->world
        p_origin = M.ExtractTranslation()                              # prim origin in world
        p_point = M.Transform(Gf.Vec3d(float(origin_local[0]), float(origin_local[1]),
                                       float(origin_local[2])))         # origin point in world
        d = p_point - p_origin
        return [float(d[0]), float(d[1]), float(d[2])]

    def _label_parts(self, rep, prim_path: str, part_defs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Apply each part's semantic class to its sub-prim so it shows up as a separate mask
        in semantic/instance segmentation. Skips (with a warning) any part whose sub-prim path
        does not resolve — silent mislabels would be worse than a visible skip."""
        if not part_defs:
            return []
        import omni.usd

        stage = omni.usd.get_context().get_stage()
        out: List[Dict[str, Any]] = []
        for pd in part_defs:
            full = f"{prim_path}/{pd['prim']}"
            sub = stage.GetPrimAtPath(full)
            if not sub or not sub.IsValid():
                print(f"[sdg][scene] part '{pd['name']}' sub-prim not found: {full} — skipping "
                      f"(check parts.json 'prim' path against the object USD)")
                continue
            rep.functional.modify.semantics([sub], {"class": pd["class"]}, mode="add")
            out.append({"name": pd["name"], "class": pd["class"], "prim_path": full})
        return out

    def _apply_physics(self, rep, prim, physics: Dict[str, Any]) -> None:
        if not physics:
            return
        collider = physics.get("collider", False)
        gravity = physics.get("gravity", False)
        if collider and gravity:
            # Dynamic rigid body (falls under gravity), collider included.
            rep.functional.physics.apply_rigid_body(prim, with_collider=True)
        elif collider:
            # Static collider only (no rigid body dynamics).
            rep.functional.physics.apply_collider(prim)
        mass = physics.get("mass")
        if mass is not None:
            self._set_mass(prim, float(mass))

    @staticmethod
    def _set_mass(prim, mass: float) -> None:
        # Stable USD physics schema (not replicator-version-fragile).
        from pxr import UsdPhysics

        mass_api = UsdPhysics.MassAPI.Apply(prim)
        mass_api.CreateMassAttr(mass)


def _resolve_env_usd(bg: str) -> str:
    """Map a background keyword to a USD env path, or pass through an explicit path.

    Explicit .usd(a/c/z) paths/URLs pass through unchanged. Named presets (e.g. 'warehouse',
    'simple_room', 'office', 'grid') resolve against the Isaac Sim cloud assets root via
    isaacsim.storage.native.get_assets_root_path() — see sdg/assets.py::ISAAC_ENVIRONMENTS.
    """
    from ..assets import ISAAC_ENVIRONMENTS, is_url, resolve_env_preset

    if is_url(bg) or bg.lower().endswith((".usd", ".usda", ".usdc", ".usdz")):
        return bg
    url = resolve_env_preset(bg)
    if url is not None:
        return url
    raise ValueError(
        f"unknown background '{bg}'; use 'none' / 'ground_plane', a preset "
        f"({sorted(ISAAC_ENVIRONMENTS)}), or an explicit .usd path/URL."
    )
