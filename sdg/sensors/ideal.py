"""IdealCamera — a pinhole camera producing a render product with GT (ideal) depth.

★ 6.0.1 API (verified against the install):
  - rep.functional.create.camera(position=, look_at=, focal_length=, horizontal_aperture=,
      parent=, name=) -> pxr.Usd.Prim                        (functional/create.py:165)
  - rep.create.render_product(camera, resolution=(W,H), name=) -> render product
      (sdg_getting_started_02.py:75-76; simulation_get_data.py:60)

Intrinsics: the authoritative per-frame intrinsics/extrinsics are read from the
`camera_params` annotator by the annotator collector (accounts for the real aperture and
render-product aspect). This class configures the camera from the config's intrinsics
block and returns the matching pinhole intrinsics for setup/inspection.

`realsense_depth` (calibrated degradation) is a separate S4 sensor plugin; `ideal`
returns metric GT depth via postprocess_depth() = identity (see SDG.md §4).
"""
from __future__ import annotations

import math
from typing import Dict, Tuple

from ..config import SensorSpec
from ..registry import register
from .base import CameraModel

# USD default 35mm-equivalent horizontal aperture used by rep.functional.create.camera.
_DEFAULT_H_APERTURE_MM = 20.955


@register("sensor", "ideal")
class IdealCamera(CameraModel):
    def __init__(self, spec: SensorSpec):
        super().__init__(spec)
        self.name = spec.name
        self.cam_prim = None
        self.focal_length_mm, self.h_aperture_mm = self._resolve_optics(spec)

    # ------------------------------------------------------------------ optics
    def _resolve_optics(self, spec: SensorSpec) -> Tuple[float, float]:
        """Derive (focal_length_mm, horizontal_aperture_mm) from the config intrinsics.

        Accepts either an explicit `focal_mm`, or a horizontal FOV (`hfov_deg`) which is
        converted to a focal length at the default aperture. Falls back to USD defaults.
        """
        intr = spec.intrinsics or {}
        h_aperture = float(intr.get("horizontal_aperture_mm", _DEFAULT_H_APERTURE_MM))
        focal_mm = intr.get("focal_mm")
        if focal_mm is not None:
            return float(focal_mm), h_aperture
        hfov_deg = intr.get("hfov_deg")
        if hfov_deg is not None:
            hfov = math.radians(float(hfov_deg))
            focal = h_aperture / (2.0 * math.tan(hfov / 2.0))
            return focal, h_aperture
        return 24.0, h_aperture  # rep.functional.create.camera default

    @property
    def resolution(self) -> Tuple[int, int]:
        w, h = self.spec.resolution
        return int(w), int(h)

    # ------------------------------------------------------------------ create
    def create(self) -> None:
        import omni.replicator.core as rep

        # Near clip in metres. Default 0.01 (1cm) so close-range captures aren't clipped —
        # rep.functional.create.camera defaults to (1.0, 1e6) which clips objects < 1m away.
        intr = self.spec.intrinsics or {}
        near = float(intr.get("near_clip_m", 0.01))
        far = float(intr.get("far_clip_m", 1.0e6))

        # Placeholder pose; the camera randomizer repositions per frame. look_at gives a
        # sane initial orientation before DR runs.
        self.cam_prim = rep.functional.create.camera(
            position=(1.5, 1.5, 1.5),
            look_at=(0.0, 0.0, 0.0),
            focal_length=self.focal_length_mm,
            horizontal_aperture=self.h_aperture_mm,
            clipping_range=(near, far),
            parent="/World",
            name=self.name,
        )
        self.render_product = rep.create.render_product(
            self.cam_prim, resolution=self.resolution, name=f"{self.name}_rp"
        )

    # -------------------------------------------------------------- intrinsics
    def intrinsics(self) -> Dict[str, float]:
        """Pinhole intrinsics matching the created camera (square pixels).

        fx = fy because USD derives the vertical aperture from the horizontal aperture and
        the render-product aspect (square pixels). Principal point at image centre (no
        aperture offset configured). The collector overrides these per frame from
        `camera_params` when available.
        """
        w, h = self.resolution
        fx = self.focal_length_mm * w / self.h_aperture_mm
        return {
            "fx": fx,
            "fy": fx,
            "cx": w / 2.0,
            "cy": h / 2.0,
            "width": float(w),
            "height": float(h),
        }
