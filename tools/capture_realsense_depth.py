#!/usr/bin/env python3
"""Capture RealSense (D435) depth for noise calibration — step [1] of the depth-noise
calibration workflow (see the companion tools/fit_depth_noise.py, step [2]-[3]).

WHY: sensors/realsense_depth.py degrades sim GT depth with bias + range-dependent noise +
holes, but its parameters are placeholders. To make them faithful we measure the REAL
device: point the D435 at a flat matte wall at several KNOWN distances and record a stack of
depth frames. A plane has exactly-known geometry, so any deviation of the measured depth from
that plane IS the sensor's error — that's what fit_depth_noise.py turns into bias_mm /
noise_quadratic / hole_fraction.

WHERE TO RUN: on the host/env where the D435 is plugged in, in a python with pyrealsense2
(NOT /isaac-sim/python.sh — this reads the device, not the simulator):
    pip install pyrealsense2 numpy

CAPTURE PROTOCOL (do one run per distance):
    # a flat wall at a measured perpendicular distance, ~5 distances spanning your work range
    python3 tools/capture_realsense_depth.py --type plane --distance 0.40 --frames 30
    python3 tools/capture_realsense_depth.py --type plane --distance 0.75 --frames 30
    python3 tools/capture_realsense_depth.py --type plane --distance 1.50 --frames 30
    # (optional) a dark / shiny / transparent surface to measure dropout holes
    python3 tools/capture_realsense_depth.py --type surface --distance 0.60 --frames 30

Each run writes calibration/<name>/<type>_z<dist>/ with:
    meta.json   intrinsics (fx,fy,cx,cy), depth_scale, known_distance_m, type, resolution, serial
    depth.npy   float32 (N,H,W) depth in METRES (0 = no return / invalid)
    color.png   last colour frame (visual reference only)
fit_depth_noise.py then reads all those session dirs.

★ pyrealsense2 API used (device-side, not Isaac):
    rs.pipeline / rs.config.enable_stream(depth z16, color bgr8)
    depth_sensor.get_depth_scale()  -> metres per raw z16 unit
    video_stream_profile.get_intrinsics() -> fx, fy, ppx(cx), ppy(cy), coeffs
"""
from __future__ import annotations

import argparse
import json
import os
import sys

WS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _require(mod: str):
    try:
        return __import__(mod)
    except ImportError:
        sys.exit(
            f"{mod} not found. On the machine the D435 is plugged into:\n"
            f"    pip install pyrealsense2 numpy\n"
            "(Run this device-side, NOT under /isaac-sim/python.sh.)"
        )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--type", choices=["plane", "surface"], default="plane",
                    help="plane: flat wall at a known distance (bias+noise). "
                         "surface: dark/shiny/transparent target (hole fraction).")
    ap.add_argument("--distance", type=float, required=True,
                    help="measured perpendicular distance to the target, in METRES "
                         "(tape-measure the wall; used as ground truth).")
    ap.add_argument("--frames", type=int, default=30, help="depth frames to stack (temporal).")
    ap.add_argument("--warmup", type=int, default=30,
                    help="frames to discard first (auto-exposure / laser settle).")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--serial", default=None, help="device serial if several are connected.")
    ap.add_argument("--name", default="d435", help="calibration set name -> calibration/<name>/")
    args = ap.parse_args()

    rs = _require("pyrealsense2")
    np = _require("numpy")
    # pyrealsense2 sometimes imports as a submodule; normalise.
    if not hasattr(rs, "pipeline"):
        import pyrealsense2.pyrealsense2 as rs  # type: ignore

    if not rs.context().query_devices():
        sys.exit("No RealSense device detected. Check the USB3 connection / permissions.")

    pipeline = rs.pipeline()
    cfg = rs.config()
    if args.serial:
        cfg.enable_device(args.serial)
    cfg.enable_stream(rs.stream.depth, args.width, args.height, rs.format.z16, args.fps)
    cfg.enable_stream(rs.stream.color, args.width, args.height, rs.format.bgr8, args.fps)

    profile = pipeline.start(cfg)
    try:
        dev = profile.get_device()
        serial = dev.get_info(rs.camera_info.serial_number)
        depth_sensor = next(s for s in dev.query_sensors() if s.is_depth_sensor())
        depth_scale = float(depth_sensor.as_depth_sensor().get_depth_scale())  # m per z16 unit
        dintr = profile.get_stream(rs.stream.depth).as_video_stream_profile().get_intrinsics()

        for _ in range(args.warmup):
            pipeline.wait_for_frames()

        stack = np.empty((args.frames, args.height, args.width), dtype=np.float32)
        color_last = None
        for i in range(args.frames):
            frames = pipeline.wait_for_frames()
            d = frames.get_depth_frame()
            c = frames.get_color_frame()
            stack[i] = np.asanyarray(d.get_data()).astype(np.float32) * depth_scale  # -> metres
            if c:
                color_last = np.asanyarray(c.get_data())
            print(f"\r[capture] frame {i + 1}/{args.frames}", end="", flush=True)
        print()
    finally:
        pipeline.stop()

    out_dir = os.path.join(WS_ROOT, "calibration", args.name,
                           f"{args.type}_z{args.distance:.2f}")
    os.makedirs(out_dir, exist_ok=True)
    np.save(os.path.join(out_dir, "depth.npy"), stack)
    meta = {
        "type": args.type,
        "known_distance_m": args.distance,
        "n_frames": args.frames,
        "resolution": [args.width, args.height],
        "depth_scale_m": depth_scale,
        "serial": serial,
        "intrinsics": {"fx": dintr.fx, "fy": dintr.fy, "cx": dintr.ppx, "cy": dintr.ppy,
                       "width": dintr.width, "height": dintr.height,
                       "model": str(dintr.model), "coeffs": list(dintr.coeffs)},
    }
    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    if color_last is not None:
        try:
            import cv2
            cv2.imwrite(os.path.join(out_dir, "color.png"), color_last)
        except ImportError:
            pass

    valid = float((stack > 0).mean())
    print(f"[capture] wrote {out_dir}")
    print(f"[capture]   {args.frames} frames @ {args.width}x{args.height}, "
          f"valid-pixel fraction ~{valid:.3f}")
    print(f"[capture] repeat at other distances, then run: "
          f"tools/fit_depth_noise.py --name {args.name}")


if __name__ == "__main__":
    main()
