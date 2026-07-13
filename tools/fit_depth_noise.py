"""Fit sensors/realsense_depth.py parameters from captured D435 depth — steps [2]-[3] of the
depth-noise calibration workflow (capture is tools/capture_realsense_depth.py, step [1]).

WHAT IT DOES: for each `plane_z<d>/` capture (a flat wall at a known distance), it back-projects
the depth to a point cloud, fits a plane to a central ROI per frame, and measures:
  - bias(z)   = (measured centre depth) - (known distance)     -> systematic offset
  - sigma(z)  = std of point-to-plane residual within a frame  -> the per-pixel random noise
                that realsense_depth adds each frame
Across distances it then fits:
  - noise_quadratic k :  sigma = k * z^2   (stereo-depth noise grows with the square of range)
  - bias              :  constant (bias_mm), and also a linear bias = b0 + b1*z so you can see
                         whether a distance-dependent SCALE term is needed (the model currently
                         has constant bias only — CLAUDE.md roadmap item).
`surface_z<d>/` captures (dark/shiny/transparent) contribute the hole_fraction (invalid pixels
on a surface that should return).

It prints a per-distance table, the fitted parameters, and a ready-to-paste `sensors:` block.

WHERE TO RUN: anywhere with numpy — including /isaac-sim/python.sh (no pyrealsense2 needed;
it only reads the saved .npy/.json):
    /isaac-sim/python.sh tools/fit_depth_noise.py --name d435
    /isaac-sim/python.sh tools/fit_depth_noise.py --name d435 --roi 0.4 --csv calibration/d435/fit.csv

Validation of the result is step [4]: render the same geometry in sim with these params and
compare the sim-degraded depth-error histogram to the real one; iterate if off.
"""
from __future__ import annotations

import argparse
import csv as _csv
import glob
import json
import os

import numpy as np

WS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_session(d: str):
    with open(os.path.join(d, "meta.json")) as f:
        meta = json.load(f)
    depth = np.load(os.path.join(d, "depth.npy"))  # (N,H,W) metres, 0=invalid
    return meta, depth


def _roi_slice(h: int, w: int, frac: float):
    """Central ROI (fraction of each dimension) — avoids lens-edge distortion and wall borders."""
    rh, rw = int(h * frac), int(w * frac)
    y0, x0 = (h - rh) // 2, (w - rw) // 2
    return slice(y0, y0 + rh), slice(x0, x0 + rw)


def _plane_residual_std(depth_frame, intr, roi):
    """Back-project ROI pixels to 3D, fit a plane (SVD), return (residual_std_m, mean_z_m,
    invalid_fraction). Uses only valid (>0) pixels."""
    ys, xs = roi
    d = depth_frame[ys, xs]
    hh, ww = d.shape
    fx, fy, cx, cy = intr["fx"], intr["fy"], intr["cx"], intr["cy"]
    # pixel grid in full-image coords
    v, u = np.mgrid[ys.start:ys.stop, xs.start:xs.stop]
    valid = d > 0
    invalid_frac = float((~valid).mean())
    if valid.sum() < 50:
        return None, None, invalid_frac
    z = d[valid]
    x = (u[valid] - cx) / fx * z
    y = (v[valid] - cy) / fy * z
    pts = np.stack([x, y, z], axis=1)
    centroid = pts.mean(axis=0)
    # plane normal = smallest singular vector of the centred points
    _, _, vt = np.linalg.svd(pts - centroid, full_matrices=False)
    normal = vt[-1]
    resid = (pts - centroid) @ normal  # signed point-to-plane distance (m)
    return float(resid.std()), float(z.mean()), invalid_frac


def _fit_quadratic_through_origin(z, sigma):
    """sigma = k * z^2  ->  k = sum(z^2 * sigma) / sum(z^4)  (least squares, no intercept)."""
    z2 = z ** 2
    return float(np.sum(z2 * sigma) / np.sum(z2 ** 2))


def analyse(name: str, roi_frac: float):
    root = os.path.join(WS_ROOT, "calibration", name)
    dirs = sorted(glob.glob(os.path.join(root, "*_z*")))
    if not dirs:
        raise SystemExit(f"no captures under {root}/ — run tools/capture_realsense_depth.py first")

    planes, surfaces = [], []
    for d in dirs:
        meta, depth = _load_session(d)
        intr = meta["intrinsics"]
        n, h, w = depth.shape
        roi = _roi_slice(h, w, roi_frac)
        stds, zmeans, invalids = [], [], []
        for i in range(n):
            s, zm, inv = _plane_residual_std(depth[i], intr, roi)
            invalids.append(inv)
            if s is not None:
                stds.append(s); zmeans.append(zm)
        row = {
            "dir": os.path.basename(d),
            "type": meta["type"],
            "z_known": float(meta["known_distance_m"]),
            "z_meas": float(np.mean(zmeans)) if zmeans else float("nan"),
            "sigma": float(np.mean(stds)) if stds else float("nan"),
            "invalid_frac": float(np.mean(invalids)),
        }
        row["bias"] = row["z_meas"] - row["z_known"]
        (planes if meta["type"] == "plane" else surfaces).append(row)
    return planes, surfaces


def _print_table(rows):
    print(f"{'capture':<22}{'type':<9}{'z_known':>9}{'z_meas':>9}{'bias_mm':>9}"
          f"{'sigma_mm':>10}{'hole%':>8}")
    for r in rows:
        print(f"{r['dir']:<22}{r['type']:<9}{r['z_known']:>9.3f}{r['z_meas']:>9.3f}"
              f"{r['bias'] * 1000:>9.2f}{r['sigma'] * 1000:>10.2f}{r['invalid_frac'] * 100:>8.2f}")


def emit_config_block(k, bias_const_m, hole_fraction, name):
    lines = [
        "# --- calibrated depth degradation (paste into the D435 sensor entry) ---",
        "sensors:",
        f"  - name: {name}",
        "    type: realsense_depth",
        "    # ... resolution + intrinsics from tools/read_realsense_intrinsics.py ...",
        f"    bias_mm: {bias_const_m * 1000:.2f}",
        f"    noise_quadratic: {k:.5f}        # sigma_m = k * z^2  (fitted)",
        "    edge_dropout: true",
        f"    hole_fraction: {hole_fraction:.4f}",
        "    noise_seed: 0",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--name", default="d435", help="calibration set -> calibration/<name>/")
    ap.add_argument("--roi", type=float, default=0.4,
                    help="central ROI fraction per axis for plane fitting (default 0.4).")
    ap.add_argument("--csv", default=None, help="also write the per-distance table as CSV here.")
    args = ap.parse_args()

    planes, surfaces = analyse(args.name, args.roi)
    all_rows = planes + surfaces
    _print_table(all_rows)

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            wr = _csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            wr.writeheader(); wr.writerows(all_rows)
        print(f"\n[fit] wrote {args.csv}")

    if not planes:
        raise SystemExit("\nneed at least one `plane` capture to fit noise/bias.")

    z = np.array([r["z_meas"] for r in planes])
    sigma = np.array([r["sigma"] for r in planes])
    bias = np.array([r["bias"] for r in planes])

    k = _fit_quadratic_through_origin(z, sigma)
    bias_const = float(np.mean(bias))
    # linear bias fit b0 + b1*z to expose a distance-dependent (scale) component
    if len(z) >= 2:
        b1, b0 = np.polyfit(z, bias, 1)
    else:
        b1, b0 = 0.0, bias_const
    # hole fraction: prefer dedicated `surface` captures; else use planes (matte wall -> low)
    hole_src = surfaces if surfaces else planes
    hole_fraction = float(np.mean([r["invalid_frac"] for r in hole_src]))

    print("\n[fit] noise:  sigma_m = k * z^2   ->  k (noise_quadratic) = "
          f"{k:.5f}   (e.g. sigma@1m={k * 1:.4f}m, @2m={k * 4:.4f}m)")
    print(f"[fit] bias :  constant = {bias_const * 1000:.2f} mm   |   "
          f"linear = {b0 * 1000:.2f} mm + {b1 * 1000:.2f} mm/m * z")
    if abs(b1) * 1000 > 3.0:  # >3mm change per metre -> scale term matters
        print("[fit]  ^ NOTE: bias grows with distance (>3mm/m). Constant bias_mm is a rough fit; "
              "consider adding a scale term to realsense_depth.postprocess_depth "
              "(CLAUDE.md roadmap: 'depth degradation scale term').")
    print(f"[fit] holes:  hole_fraction = {hole_fraction:.4f}  "
          f"(from {'surface' if surfaces else 'plane'} captures)\n")

    print(emit_config_block(k, bias_const, hole_fraction, args.name))


if __name__ == "__main__":
    main()
