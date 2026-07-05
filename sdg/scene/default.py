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
            self._apply_physics(rep, prim, spec.physics)
            paths.append(prim_path)
            self.instances.append(
                {
                    "obj_id": spec.obj_id,
                    "instance_id": len(self.instances),
                    "prim_path": prim_path,
                    "prim": prim,
                    "semantic_class": semantic_class,
                    "keypoints_local": keypoints_local,
                }
            )
        self.object_prims[spec.obj_id] = paths

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

    Only explicit paths are resolved for now; named presets (e.g. 'warehouse') that
    depend on the Nucleus/assets root are left for S3 (needs the 6.0.1 assets-root
    lookup verified before hardcoding a URL — see CLAUDE.md 'no API guessing').
    """
    if bg.lower().endswith((".usd", ".usda", ".usdc", ".usdz")):
        return bg
    raise ValueError(
        f"background preset '{bg}' not wired yet; pass an explicit .usd path or use "
        f"'none'/'ground_plane' (named env presets land in S3)."
    )
