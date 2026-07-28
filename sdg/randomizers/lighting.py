"""LightingRandomizer — per-frame lighting DR modelling real indoor (office/factory) variety.

Three layers, all optional & config-driven:
  1. Dome (ambient/IBL) — `inputs:intensity` (+ optional HDRI env map & dome rotation). The
     dome is the ambient floor: keep its intensity range's low end > 0 so the scene is never
     fully dark (real rooms always have some ambient bounce), matching office/factory reality.
  2. Colour temperature — instead of an arbitrary RGB tint, lights vary along the physically
     plausible blackbody axis (warm ~3000K incandescent/warm-LED ↔ cool ~6500K office
     fluorescent/daylight). Applied to the dome and every fixture via the standard UsdLux
     `inputs:enableColorTemperature` + `inputs:colorTemperature`.
  3. Overhead fixtures — a pre-spawned pool of local lights (rect = ceiling panels, distant =
     angled sun/window light). Each frame a random subset is enabled, repositioned on an upper
     hemisphere and aimed at the work area, giving varied directional light AND cast shadows.

★ 6.0.1 API (verified against the install, scripts/functional/create.py & modify.py):
  - rep.functional.create.{rect_light,distant_light,sphere_light,disk_light}(...) accept
      intensity, color_temperature, enable_color_temperature, width/height (rect), radius
      (sphere/disk), parent, name.                              (create.py:902-1362)
  - rep.functional.modify.visibility(prim, bool)                (modify.py:1375)
  - rep.functional.modify.pose(prim, position_value=, look_at_value=)  (modify.py:227)
  - Per-frame we set stable UsdLux attrs directly (inputs:intensity / colorTemperature /
      enableColorTemperature) — not replicator-graph state — so updates are deterministic.

config:
  {type: lighting,
   intensity: [lo,hi],              # dome ambient intensity (keep lo>0 -> never fully dark)
   color_temperature: [lo,hi],      # Kelvin; optional. warm<->cool. Applies to dome+fixtures.
   hdri: <dir | [paths/urls] | isaac_skies[:Cat,..]>,   # optional env-map pool (see below)
   hdri_rotate: true | [lo,hi],     # optional dome Y-rotation (deg); true = [0,360]
   hdri_normalize: true,            # scale dome intensity by each map's brightness (see below)
   hdri_normalize_clamp: [lo,hi],   #   correction limits (default [0.3, 3.0])
   hdri_normalize_ref: <float>,     #   reference radiance (default: the pool's median)
   fixtures: {                      # optional overhead local lights (directional + shadows)
     kinds: [rect, distant],        #   rect = ceiling panel, distant = angled sun/window
     count: [lo,hi],                #   how many active per frame
     intensity: [lo,hi],            #   per-fixture intensity (much higher than dome)
     color_temperature: [lo,hi],    #   optional; falls back to top-level color_temperature
     distance: [lo,hi],             #   metres from work-area origin (hemisphere radius)
     elevation_deg: [lo,hi],        #   fixture height angle above horizon
     size: [lo,hi]}}                #   rect panel side length (m)

`hdri` sources may be mixed: a local dir/file, a remote URL, or the keyword `isaac_skies`
(Isaac's built-in sky library). For OFFLINE use, localize a pool with
tools/fetch_isaac_assets.py and point `hdri` at the local dir.

`hdri_normalize` exists because the dome intensity MULTIPLIES the env map: with a pool that
spans ~12.6x in mean radiance (this repo's local set: kloppenheim 0.237 .. noon_grass 3.0), one
intensity range renders black on a dark map and blows out on a bright one. Enabling it measures
each map's mean radiance once at setup (linear float; cv2 or imageio) and scales the sampled
intensity by median/L_i, clamped. Only LOCAL files can be measured — remote/`isaac_skies` URLs
keep gain 1.0.
"""
from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Tuple

from ..registry import register
from .base import Randomizer, resolve_asset_list

_DOME_PATH = "/World/DomeLight"
_LIGHTS_XFORM = "/World/Lights"
_HDRI_EXTS = (".hdr", ".exr", ".png", ".jpg", ".jpeg")


@register("randomizer", "lighting")
class LightingRandomizer(Randomizer):
    def __init__(self, cfg, ctx=None):
        super().__init__(cfg, ctx)
        self._hdris: List[str] = []
        self._fixtures: List[Tuple[object, str]] = []  # (prim, kind)
        self._hdri_gain: Dict[str, float] = {}  # path -> dome-intensity correction factor

    def setup(self) -> None:
        self._hdris = resolve_asset_list(self.cfg.get("hdri"), _HDRI_EXTS)
        if self.cfg.get("hdri") and not self._hdris:
            print(f"[sdg][lighting] hdri set but no images found: {self.cfg.get('hdri')} — "
                  f"dome stays untextured.")
        self._measure_hdri_gains()
        self._spawn_fixtures()

    # ------------------------------------------------------- HDRI normalization
    def _measure_hdri_gains(self) -> None:
        """Per-HDRI dome-intensity correction so `intensity` means the same exposure for
        every map in the pool.

        The dome intensity MULTIPLIES the env map, but map brightness varies enormously (this
        repo's 16-map pool spans ~12.6x in mean radiance), so one intensity range yields black
        frames on a night map and blow-out on a noon sky. We measure each map's mean radiance
        L_i once here and scale the sampled intensity by `L_ref / L_i`, where L_ref is the
        pool's MEDIAN — so the config's numbers keep meaning "exposure for a typical map" and
        only the outliers get pulled in. Off by default (`hdri_normalize: true` to enable):
        it changes the look of every frame, so it must be an explicit choice.

        The correction is clamped (`hdri_normalize_clamp`, default [0.3, 3.0]) — fully
        equalizing a night sky to noon is neither achievable nor desirable.
        """
        if not self._hdris or not self.cfg.get("hdri_normalize"):
            return
        lo, hi = _pair(self.cfg.get("hdri_normalize_clamp", [0.3, 3.0]))
        means: Dict[str, float] = {}
        for p in self._hdris:
            v = _mean_radiance(p)
            if v is not None and v > 0.0:
                means[p] = v
        if not means:
            print("[sdg][lighting] hdri_normalize: could not read any HDRI radiance "
                  "(no cv2/imageio HDR reader?) — normalization disabled for this run.")
            return
        vals = sorted(means.values())
        ref = self.cfg.get("hdri_normalize_ref")
        ref = float(ref) if ref is not None else vals[len(vals) // 2]  # median
        for p, v in means.items():
            self._hdri_gain[p] = min(max(ref / v, lo), hi)
        unread = [p for p in self._hdris if p not in means]
        print(f"[sdg][lighting] hdri_normalize: ref(median)={ref:.3f} over {len(means)} map(s), "
              f"gain clamped to [{lo:g}, {hi:g}]"
              + (f"; {len(unread)} unreadable -> gain 1.0" if unread else ""))
        for p in sorted(means, key=lambda k: means[k]):
            print(f"[sdg][lighting]   {means[p]:7.3f} -> x{self._hdri_gain[p]:.2f}  "
                  f"{os.path.basename(p)}")

    # ------------------------------------------------------------------ setup
    def _spawn_fixtures(self) -> None:
        fx = self.cfg.get("fixtures")
        if not fx:
            return
        import omni.replicator.core as rep

        kinds = fx.get("kinds", ["rect", "distant"])
        max_count = int(_pair(fx.get("count", [1, 2]))[1])
        if max_count <= 0 or not kinds:
            return
        size_mid = sum(_pair(fx.get("size", [1.0, 2.0]))) / 2.0
        rep.functional.create.xform(name="Lights", parent="/World")
        for i in range(max_count):
            kind = kinds[i % len(kinds)]
            name = f"fixture_{i:03d}"
            if kind == "rect":
                prim = rep.functional.create.rect_light(
                    width=size_mid, height=size_mid, intensity=10000.0,
                    parent=_LIGHTS_XFORM, name=name)
            elif kind == "distant":
                prim = rep.functional.create.distant_light(
                    intensity=2000.0, parent=_LIGHTS_XFORM, name=name)
            elif kind == "sphere":
                prim = rep.functional.create.sphere_light(
                    radius=size_mid * 0.25, intensity=15000.0, parent=_LIGHTS_XFORM, name=name)
            elif kind == "disk":
                prim = rep.functional.create.disk_light(
                    radius=size_mid * 0.5, intensity=15000.0, parent=_LIGHTS_XFORM, name=name)
            else:
                print(f"[sdg][lighting] unknown fixture kind '{kind}' — skipping.")
                continue
            rep.functional.modify.visibility(prim, False)
            self._fixtures.append((prim, kind))

    # ------------------------------------------------------------------ per frame
    def apply(self, frame_idx: int) -> None:
        import omni.usd

        rng = self.ctx.rng
        stage = omni.usd.get_context().get_stage()
        ctemp = self.cfg.get("color_temperature")

        # --- dome (ambient / IBL) ------------------------------------------
        dome = stage.GetPrimAtPath(_DOME_PATH)
        if dome and dome.IsValid():
            lo, hi = _pair(self.cfg.get("intensity", [1000.0, 1000.0]))
            intensity = float(rng.uniform(lo, hi))
            _set_color_temperature(dome, ctemp, rng)
            if self._hdris:
                # NB: draw the map BEFORE applying its gain — the sampled intensity above and
                # this draw must keep their order in the RNG stream regardless of normalization.
                hdri = self._hdris[int(rng.integers(len(self._hdris)))]
                _set_texture(dome, hdri)
                intensity *= self._hdri_gain.get(hdri, 1.0)  # 1.0 = normalization off/unread
            _set_attr(dome, "inputs:intensity", intensity)
            rot = self.cfg.get("hdri_rotate")
            if rot:
                rlo, rhi = (0.0, 360.0) if rot is True else _pair(rot)
                _set_dome_rotation(dome, float(rng.uniform(rlo, rhi)))

        # --- overhead fixtures (directional light + shadows) ---------------
        self._apply_fixtures(rng, ctemp)

    def _apply_fixtures(self, rng, dome_ctemp) -> None:
        if not self._fixtures:
            return
        import omni.replicator.core as rep

        fx = self.cfg["fixtures"]
        lo, hi = (int(x) for x in _pair(fx.get("count", [1, 2])))
        n = int(rng.integers(lo, hi + 1))  # inclusive
        f_int = _pair(fx.get("intensity", [3000.0, 9000.0]))
        f_ctemp = fx.get("color_temperature", dome_ctemp)
        dist = _pair(fx.get("distance", [2.0, 5.0]))
        elev = _pair(fx.get("elevation_deg", [30.0, 80.0]))

        for idx, (prim, _kind) in enumerate(self._fixtures):
            visible = idx < n
            rep.functional.modify.visibility(prim, visible)
            if not visible:
                continue
            # place on an upper hemisphere around the work-area origin, aimed at it, so the
            # light direction (and cast shadows) vary frame to frame.
            r = float(rng.uniform(*dist))
            e = math.radians(float(rng.uniform(*elev)))
            a = float(rng.uniform(0.0, 2.0 * math.pi))
            pos = (r * math.cos(e) * math.cos(a),
                   r * math.cos(e) * math.sin(a),
                   r * math.sin(e))
            rep.functional.modify.pose(prim, position_value=pos, look_at_value=(0.0, 0.0, 0.0))
            _set_attr(prim, "inputs:intensity", float(rng.uniform(*f_int)))
            _set_color_temperature(prim, f_ctemp, rng)


# --------------------------------------------------------------------------- helpers
def _set_color_temperature(prim, ctemp, rng) -> None:
    """Enable + set blackbody colour temperature (K) from a [lo,hi] config range. No-op if
    unset, leaving the light's colour neutral white."""
    if not ctemp:
        return
    lo, hi = _pair(ctemp)
    _set_attr(prim, "inputs:enableColorTemperature", True)
    _set_attr(prim, "inputs:colorTemperature", float(rng.uniform(lo, hi)))


def _set_texture(prim, path: str) -> None:
    from pxr import Sdf

    attr = prim.GetAttribute("inputs:texture:file")
    if not attr:
        attr = prim.CreateAttribute("inputs:texture:file", Sdf.ValueTypeNames.Asset)
    attr.Set(Sdf.AssetPath(path))


_RADIANCE_CACHE: Dict[str, Optional[float]] = {}


def _mean_radiance(path: str) -> Optional[float]:
    """Mean linear radiance of an env map, or None if it cannot be read.

    Must stay in LINEAR float — an 8-bit decode would clip the sun and destroy the very
    difference we are measuring. cv2 handles .hdr directly; imageio is the fallback (it also
    covers .exr, which cv2 only reads when OPENCV_IO_ENABLE_OPENEXR is set). Remote URLs are
    skipped (no local file to read) and simply get gain 1.0.
    """
    if path in _RADIANCE_CACHE:
        return _RADIANCE_CACHE[path]
    value: Optional[float] = None
    if os.path.isfile(path):
        try:
            import cv2
            import numpy as np

            img = cv2.imread(path, cv2.IMREAD_ANYDEPTH | cv2.IMREAD_ANYCOLOR)
            if img is not None and np.issubdtype(img.dtype, np.floating):
                value = float(np.asarray(img, dtype=np.float64).mean())
        except Exception:  # noqa: BLE001 — reader problems must not break a run
            value = None
        if value is None:
            try:
                import imageio.v3 as iio
                import numpy as np

                arr = np.asarray(iio.imread(path), dtype=np.float64)
                if np.issubdtype(arr.dtype, np.floating):
                    value = float(arr.mean())
            except Exception:  # noqa: BLE001
                value = None
    _RADIANCE_CACHE[path] = value
    return value


def _set_dome_rotation(prim, deg_y: float) -> None:
    from pxr import UsdGeom

    UsdGeom.XformCommonAPI(prim).SetRotate((0.0, deg_y, 0.0))


def _set_attr(prim, name, value):
    from pxr import Gf, Sdf

    attr = prim.GetAttribute(name)
    if not attr:
        if "color" in name.lower() and "temperature" not in name.lower():
            type_name = Sdf.ValueTypeNames.Color3f
        elif isinstance(value, bool):
            type_name = Sdf.ValueTypeNames.Bool
        else:
            type_name = Sdf.ValueTypeNames.Float
        attr = prim.CreateAttribute(name, type_name)
    if isinstance(value, tuple):
        attr.Set(Gf.Vec3f(*value))
    else:
        attr.Set(value)


def _pair(v):
    if isinstance(v, list):
        return (float(v[0]), float(v[-1]))
    return (float(v), float(v))
