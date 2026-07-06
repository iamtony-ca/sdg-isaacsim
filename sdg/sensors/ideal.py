"""IdealCamera — a pinhole camera producing a render product with GT (ideal) depth.

★ 6.0.1 API (verified against the install):
  - rep.functional.create.camera(position=, look_at=, focal_length=, horizontal_aperture=,
      horizontal_aperture_offset=, vertical_aperture_offset=, parent=, name=) -> pxr.Usd.Prim
      (functional/create.py:165). NOTE: there is NO vertical_aperture parameter — create()
      leaves the USD `verticalAperture` at its schema default (15.2908mm), which yields
      NON-square pixels (fy != fx) for a non-square resolution. We therefore set the
      `verticalAperture` attribute ourselves via UsdGeom.Camera after creation.
  - rep.create.render_product(camera, resolution=(W,H), name=) -> render product
      (sdg_getting_started_02.py:75-76; simulation_get_data.py:60)

Intrinsics: the authoritative per-frame intrinsics/extrinsics are read from the
`camera_params` annotator by the annotator collector. This class configures the camera to
MATCH the requested pixel intrinsics. Three input modes (config `sensors[].intrinsics`):
  1. explicit  {fx, fy, cx, cy}   — match a calibrated real camera (e.g. from calibration/).
                                     fy defaults to fx, cx/cy to the image centre.
  2. {focal_mm: <mm>}             — physical focal length; square pixels, centred principal.
  3. {hfov_deg: <deg>}            — horizontal FOV; square pixels, centred principal.
In modes 2/3 the vertical aperture is matched to the resolution aspect so pixels are SQUARE
(fx == fy) — matching how real pinhole/OpenCV intrinsics are usually reported.

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
        # optics: focal_length_mm, h_aperture_mm, v_aperture_mm, h_offset_mm, v_offset_mm (all mm)
        self.optics = self._resolve_optics(spec)

    # ------------------------------------------------------------------ optics
    def _resolve_optics(self, spec: SensorSpec) -> Dict[str, float]:
        """Resolve USD camera optics (mm) that reproduce the requested pixel intrinsics.

        Returns focal_length_mm, h/v aperture (mm), h/v aperture offset (mm). The pinhole
        relations (with W,H the render resolution) are:
            fx = focal * W / h_aperture      cx = W * (0.5 + h_offset / h_aperture)
            fy = focal * H / v_aperture      cy = H * (0.5 + v_offset / v_aperture)
        focal_length is a free gauge (any positive value reproduces the same fx/fy/cx/cy as
        long as the apertures scale with it); we keep a nominal 24mm.
        """
        intr = spec.intrinsics or {}
        w, h = self.resolution
        focal = float(intr.get("focal_mm") or 24.0)  # nominal; only aperture ratios matter

        fx = intr.get("fx")
        if fx is not None:
            # Mode 1: explicit calibrated pixel intrinsics.
            fx = float(fx)
            fy = float(intr["fy"]) if intr.get("fy") is not None else fx
            cx = float(intr.get("cx", w / 2.0))
            cy = float(intr.get("cy", h / 2.0))
            ha = focal * w / fx
            va = focal * h / fy
        else:
            # Modes 2/3: focal_mm or hfov_deg. Vertical aperture matched to aspect -> square
            # pixels (fy == fx), unlike create.camera's default vertical aperture.
            ha = float(intr.get("horizontal_aperture_mm", _DEFAULT_H_APERTURE_MM))
            hfov_deg = intr.get("hfov_deg")
            if intr.get("focal_mm") is None and hfov_deg is not None:
                focal = ha / (2.0 * math.tan(math.radians(float(hfov_deg)) / 2.0))
            va = ha * h / w  # square pixels
            cx = float(intr.get("cx", w / 2.0))
            cy = float(intr.get("cy", h / 2.0))

        return {
            "focal_length_mm": focal,
            "h_aperture_mm": ha,
            "v_aperture_mm": va,
            "h_offset_mm": ha * (cx / w - 0.5),
            "v_offset_mm": va * (cy / h - 0.5),
        }

    @property
    def resolution(self) -> Tuple[int, int]:
        w, h = self.spec.resolution
        return int(w), int(h)

    # ------------------------------------------------------------------ create
    def create(self) -> None:
        import omni.replicator.core as rep
        from pxr import UsdGeom

        # Near clip in metres. Default 0.01 (1cm) so close-range captures aren't clipped —
        # rep.functional.create.camera defaults to (1.0, 1e6) which clips objects < 1m away.
        intr = self.spec.intrinsics or {}
        near = float(intr.get("near_clip_m", 0.01))
        far = float(intr.get("far_clip_m", 1.0e6))
        o = self.optics

        # Placeholder pose; the camera randomizer repositions per frame. look_at gives a
        # sane initial orientation before DR runs.
        self.cam_prim = rep.functional.create.camera(
            position=(1.5, 1.5, 1.5),
            look_at=(0.0, 0.0, 0.0),
            focal_length=o["focal_length_mm"],
            horizontal_aperture=o["h_aperture_mm"],
            horizontal_aperture_offset=o["h_offset_mm"],
            vertical_aperture_offset=o["v_offset_mm"],
            clipping_range=(near, far),
            parent="/World",
            name=self.name,
        )
        # create.camera has no vertical_aperture param, so it leaves USD's default
        # (15.2908mm) -> non-square pixels. Set it explicitly to honour fy/cy.
        UsdGeom.Camera(self.cam_prim).GetVerticalApertureAttr().Set(float(o["v_aperture_mm"]))
        self.render_product = rep.create.render_product(
            self.cam_prim, resolution=self.resolution, name=f"{self.name}_rp"
        )

    # -------------------------------------------------------------- intrinsics
    def intrinsics(self) -> Dict[str, float]:
        """Pinhole intrinsics matching the configured camera (from self.optics).

        Reproduces the requested {fx, fy, cx, cy}: in modes 2/3 fx == fy (square pixels) and
        the principal point is centred unless cx/cy were given. The collector overrides these
        per frame from the `camera_params` annotator (authoritative) when available.
        """
        w, h = self.resolution
        o = self.optics
        return {
            "fx": o["focal_length_mm"] * w / o["h_aperture_mm"],
            "fy": o["focal_length_mm"] * h / o["v_aperture_mm"],
            "cx": w * (0.5 + o["h_offset_mm"] / o["h_aperture_mm"]),
            "cy": h * (0.5 + o["v_offset_mm"] / o["v_aperture_mm"]),
            "width": float(w),
            "height": float(h),
        }
