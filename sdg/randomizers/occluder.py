"""OccluderRandomizer — per-frame GUARANTEED partial occlusion of the target(s).

Unlike `distractors` (which scatter anywhere and may or may not cover the target), an
occluder is placed ON the line between the camera and the target, so it reliably blocks
part of the target's projection. The amount is biased by `occlusion_frac`; the TRUE amount
is the measured `visib_fract` (collector amodal vs visible), which is the GT — occlusion_frac
is only a placement knob, not an exact guarantee.

Design (mirrors `distractors`): pre-spawn a fixed pool at setup() (max of `count`, hidden),
then each frame reveal a random subset and place/scale each on the camera->target ray.

Placement per visible occluder (world metres):
  - aim point T + radius r_t from the target's world AABB (or a named part; see target_region)
  - camera centroid C (from each sensor's cam_prim world position)
  - centre = T + t*(C-T), t in [0.25,0.45]  -> between target and camera
  - angular size = sqrt(frac) * target-angular-size  -> projected area ~ frac
    (world half-size s = sqrt(frac) * r_t * (1-t); occluder scaled to reach s regardless of
     its native mesh size, measured once from its local AABB)
  - lateral jitter (perp to the ray, up to `jitter`*r_t*(1-t)) so it grazes an edge instead
    of dead-centre -> keeps the target PARTIALLY visible rather than fully hidden.

Because occluders are separate prims under <world>/Occluders, the target's amodal mask
(computed by the collector, which hides that container) is unaffected, so visib_fract
correctly reflects the occlusion. Requires annotators.amodal: true for visib_fract GT.

★ 6.0.1 API (verified against the install, omni.replicator.core-1.13.27):
  - rep.functional.create.{cube,sphere,cylinder,cone,capsule}(position=, scale=, rotation=,
      semantics=, visible=, name=, parent=)                       (functional/create.py)
  - rep.functional.create.reference(usd_path=, semantics=, name=, parent=)   (create.py:308)
  - rep.functional.modify.pose(prim, position_value=, rotation_value=, scale_value=)  (modify.py:227)
  - rep.functional.modify.visibility(prim, value: bool)                       (modify.py:1375)
  - rep.functional.utils.get_world_transform(prim)  (auto-imported; do NOT import explicitly)

config:
  {type: occluder,
   pool: [prim:cube | prim:sphere | prim:cylinder | prim:cone | prim:capsule
          | <obj_id> | <path.usd>, ...],
   count: [lo, hi],                 # occluders shown per frame (lo=0 -> some frames unoccluded)
   occlusion_frac: [lo, hi],        # target coverage bias in (0,1]; default [0.15, 0.5]
   target_region: any | <part class>,   # aim at whole object (default) or a labelled part
                                         #   (needs parts.json; falls back to whole + warn)
   depth_range: [lo, hi],           # t along camera->target ray; default [0.25, 0.45]
   jitter: <0..1>,                  # lateral graze as fraction of target radius; default 0.6
   semantic_class: occluder | null} # null => leave unlabelled
Empty pool => no-op (warns once).
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

from ..registry import register
from .base import Randomizer
from ..scene.base import WS_ROOT

_PRIM_PREFIX = "prim:"
_USD_EXTS = (".usd", ".usda", ".usdc", ".usdz")


@register("randomizer", "occluder")
class OccluderRandomizer(Randomizer):
    def __init__(self, cfg, ctx=None):
        super().__init__(cfg, ctx)
        self._prims: List = []
        self._he: List[Optional[float]] = []  # per-occluder local half-extent (lazy)
        self._max = 0
        self._warned_region = False

    # ------------------------------------------------------------------ setup
    def setup(self) -> None:
        import omni.replicator.core as rep

        pool = self.cfg.get("pool", []) or []
        if not pool:
            print("[sdg][occluder] empty pool — no occluders will be added.")
            return
        self._max = int(_pair(self.cfg.get("count", [0, 0]))[1])
        if self._max <= 0:
            print("[sdg][occluder] count upper bound is 0 — no occluders will be added.")
            return
        sem_class = self.cfg.get("semantic_class", "occluder")
        parent = f"{self.ctx.scene.world_path}/Occluders"
        rep.functional.create.xform(name="Occluders", parent=self.ctx.scene.world_path)

        for i in range(self._max):
            entry = pool[i % len(pool)]
            name = f"occluder_{i:03d}"
            sem = {"class": sem_class} if sem_class else None
            prim = self._spawn(rep, entry, name, parent, sem)
            rep.functional.modify.visibility(prim, False)
            self._prims.append(prim)
            self._he.append(None)

    def _spawn(self, rep, entry: str, name: str, parent: str, sem):
        """Spawn one occluder prim from a pool entry (primitive keyword / obj_id / usd path)."""
        entry = str(entry)
        kw = {"name": name, "parent": parent, "visible": False}
        if sem:
            kw["semantics"] = sem
        if entry.lower().startswith(_PRIM_PREFIX):
            shape = entry[len(_PRIM_PREFIX):].strip().lower()
            factory = {
                "cube": rep.functional.create.cube,
                "sphere": rep.functional.create.sphere,
                "cylinder": rep.functional.create.cylinder,
                "cone": rep.functional.create.cone,
                "capsule": rep.functional.create.capsule,
            }.get(shape)
            if factory is None:
                print(f"[sdg][occluder] unknown primitive '{entry}', using cube")
                factory = rep.functional.create.cube
            return factory(**kw)
        # USD asset or obj_id (reuse scene resolver, like distractors)
        usd_path = entry if entry.lower().endswith(_USD_EXTS) else None
        if usd_path is None:
            usd_path = self.ctx.scene.resolve_asset_usd(entry)
        elif not usd_path.startswith("/"):
            import os
            usd_path = os.path.join(WS_ROOT, usd_path)
        return rep.functional.create.reference(usd_path=usd_path, **kw)

    # ------------------------------------------------------------------ apply
    def apply(self, frame_idx: int) -> None:
        import omni.replicator.core as rep
        from pxr import Gf

        if not self._prims:
            return
        rng = self.ctx.rng

        C = self._camera_centroid(rep)
        T, r_t = self._aim(rep, self.cfg.get("target_region", "any"))
        if C is None or T is None or r_t <= 0:
            for p in self._prims:  # cannot place -> hide all this frame
                rep.functional.modify.visibility(p, False)
            return

        ray = Gf.Vec3d(*C) - Gf.Vec3d(*T)
        d_t = ray.GetLength()
        if d_t < 1e-6:
            for p in self._prims:
                rep.functional.modify.visibility(p, False)
            return
        u, v = _perp_basis(ray / d_t)

        lo, hi = (int(x) for x in _pair(self.cfg.get("count", [0, 0])))
        n = int(rng.integers(lo, hi + 1))  # inclusive upper bound
        frac_lo, frac_hi = _pair(self.cfg.get("occlusion_frac", [0.15, 0.5]))
        t_lo, t_hi = _pair(self.cfg.get("depth_range", [0.25, 0.45]))
        jitter = float(self.cfg.get("jitter", 0.6))

        for idx, prim in enumerate(self._prims):
            if idx >= n:
                rep.functional.modify.visibility(prim, False)
                continue
            frac = min(max(float(rng.uniform(frac_lo, frac_hi)), 1e-3), 1.0)
            t = float(rng.uniform(t_lo, t_hi))
            s = math.sqrt(frac) * r_t * (1.0 - t)                # desired world half-size
            ang = float(rng.uniform(0.0, 2.0 * math.pi))
            jmag = float(rng.uniform(0.0, jitter)) * r_t * (1.0 - t)
            offset = (math.cos(ang) * u + math.sin(ang) * v) * jmag
            centre = Gf.Vec3d(*T) + ray * t + offset
            he = self._half_extent(idx)
            scale = max(s / he, 1e-4) if he > 0 else max(s, 1e-4)
            rep.functional.modify.pose(
                prim,
                position_value=(float(centre[0]), float(centre[1]), float(centre[2])),
                rotation_value=_uniform_so3(rng),
                scale_value=float(scale),
            )
            rep.functional.modify.visibility(prim, True)

    # ------------------------------------------------------------------ helpers
    def _camera_centroid(self, rep):
        acc = [0.0, 0.0, 0.0]
        k = 0
        for sensor in self.ctx.sensors:
            cam = getattr(sensor, "cam_prim", None)
            if cam is None:
                continue
            tr = rep.functional.utils.get_world_transform(cam).GetTranslation()
            acc[0] += tr[0]; acc[1] += tr[1]; acc[2] += tr[2]; k += 1
        return (acc[0] / k, acc[1] / k, acc[2] / k) if k else None

    def _aim(self, rep, region) -> Tuple[Optional[Tuple[float, float, float]], float]:
        """Aim point + radius from the target world AABB, or a labelled part (target_region)."""
        prims = [inst["prim"] for inst in self.ctx.scene.instances]
        if region and region != "any":
            part_prims = self._part_prims(region)
            if part_prims:
                prims = part_prims
            elif not self._warned_region:
                print(f"[sdg][occluder] target_region '{region}' not found in parts — "
                      f"aiming at whole object (add parts.json to target the part).")
                self._warned_region = True
        return _world_aabb(prims)

    def _part_prims(self, region) -> List:
        import omni.usd
        stage = omni.usd.get_context().get_stage()
        out = []
        for inst in self.ctx.scene.instances:
            for part in inst.get("parts", []) or []:
                if region in (part.get("class"), part.get("name")):
                    p = stage.GetPrimAtPath(part["prim_path"])
                    if p and p.IsValid():
                        out.append(p)
        return out

    def _half_extent(self, idx: int) -> float:
        """Lazily measure & cache an occluder's native (unscaled) half-extent from its local
        AABB, so per-frame scaling reaches the target world size regardless of shape/asset."""
        if self._he[idx] is not None:
            return self._he[idx]
        from pxr import Usd, UsdGeom
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(),
                                  [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
        rng = cache.ComputeUntransformedBound(self._prims[idx]).ComputeAlignedRange()
        size = rng.GetSize()
        he = 0.5 * max(float(size[0]), float(size[1]), float(size[2]))
        if he > 1e-6:
            self._he[idx] = he  # cache only once a valid (loaded) bound is available
        return he if he > 1e-6 else 0.5  # fallback until the referenced asset loads


def _world_aabb(prims) -> Tuple[Optional[Tuple[float, float, float]], float]:
    if not prims:
        return None, 0.0
    from pxr import Gf, Usd, UsdGeom
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(),
                             [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    combined = Gf.Range3d()
    for p in prims:
        combined.UnionWith(cache.ComputeWorldBound(p).ComputeAlignedRange())
    if combined.IsEmpty():
        return None, 0.0
    c = combined.GetMidpoint()
    # Silhouette-radius proxy: half the mean of the two largest AABB dimensions. The bounding
    # sphere (half space-diagonal) badly overestimates a compact object's projected extent,
    # which made occluders ~target-sized and over-covered; this view-robust proxy tracks the
    # actual silhouette so occlusion_frac maps sensibly to the measured visib_fract.
    size = combined.GetSize()
    dims = sorted((abs(float(size[0])), abs(float(size[1])), abs(float(size[2]))), reverse=True)
    r = 0.25 * (dims[0] + dims[1])
    return (float(c[0]), float(c[1]), float(c[2])), float(r)


def _perp_basis(dir_vec):
    """Two orthonormal vectors perpendicular to a unit direction."""
    from pxr import Gf
    a = Gf.Vec3d(0, 0, 1) if abs(dir_vec[2]) < 0.9 else Gf.Vec3d(1, 0, 0)
    u = Gf.Cross(dir_vec, a).GetNormalized()
    v = Gf.Cross(dir_vec, u).GetNormalized()
    return u, v


def _uniform_so3(rng):
    from pxr import Gf
    u1, u2, u3 = rng.uniform(0.0, 1.0, size=3)
    q1 = math.sqrt(1.0 - u1) * math.sin(2.0 * math.pi * u2)
    q2 = math.sqrt(1.0 - u1) * math.cos(2.0 * math.pi * u2)
    q3 = math.sqrt(u1) * math.sin(2.0 * math.pi * u3)
    q4 = math.sqrt(u1) * math.cos(2.0 * math.pi * u3)
    return Gf.Rotation(Gf.Quatd(float(q4), Gf.Vec3d(float(q1), float(q2), float(q3))))


def _pair(v):
    if isinstance(v, list):
        return (float(v[0]), float(v[-1]))
    return (float(v), float(v))
