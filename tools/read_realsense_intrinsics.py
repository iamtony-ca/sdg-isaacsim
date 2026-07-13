#!/usr/bin/env python3
"""Read intrinsics from a physically-connected Intel RealSense (e.g. D435) and emit a
config-ready `sensors:` block for this SDG framework.

WHY: sensors/ideal.py can MATCH a real camera pixel-for-pixel when given calibrated
{fx, fy, cx, cy}. Those values are per-unit (they differ between two D435s), so read them
from the actual device rather than hardcoding — this script does exactly that and prints a
snippet you paste into config/<run>.yaml.

WHERE TO RUN: NOT Isaac's python. Run on the host/env where the RealSense is plugged in,
in a python that has pyrealsense2:
    pip install pyrealsense2
    python3 tools/read_realsense_intrinsics.py

Common uses:
    # default: color stream 1280x720, print sensor block for `realsense_depth`
    python3 tools/read_realsense_intrinsics.py

    # a specific stream/resolution, and pick a device by serial if several are connected
    python3 tools/read_realsense_intrinsics.py --stream color --width 1920 --height 1080
    python3 tools/read_realsense_intrinsics.py --serial 123456789012

    # what you'll actually train on: depth aligned to the color frame (single viewpoint,
    # same intrinsics the aligned depth map uses) — matches our single-camera model
    python3 tools/read_realsense_intrinsics.py --stream aligned_depth_to_color

    # also dump raw JSON (all streams) for calibration/ records
    python3 tools/read_realsense_intrinsics.py --json calibration/d435_<serial>.json

NOTE ON DISTORTION: RealSense color/depth streams are already rectified to a near-pinhole
(Brown-Conrady coeffs are typically ~0). Our camera model is pure pinhole and ignores the
`coeffs`; this script prints them so you can confirm they're negligible. If a unit reports
non-trivial coeffs and you need them, that's a collector-level change (see CLAUDE.md).
"""
from __future__ import annotations

import argparse
import json
import sys

# Map our CLI --stream choice to a pyrealsense2 stream + a note.
_STREAMS = {
    "color": "the RGB imager (use this if RGB and depth are captured/consumed separately)",
    "depth": "the depth imager's own frame (left IR viewpoint; NOT aligned to color)",
    "aligned_depth_to_color": "depth resampled into the color frame — RECOMMENDED for our "
    "single-camera model (rgb + depth share one set of intrinsics)",
}


def _require_rs():
    try:
        import pyrealsense2 as rs  # noqa: F401
        return rs
    except ImportError:
        sys.exit(
            "pyrealsense2 not found. Install it in the python that talks to the camera:\n"
            "    pip install pyrealsense2\n"
            "(Run this script there, NOT under /isaac-sim/python.sh — it reads the device, "
            "not the simulator.)"
        )


def _intr_to_dict(intr) -> dict:
    """rs.intrinsics -> plain dict (fx, fy, cx=ppx, cy=ppy, width, height, model, coeffs)."""
    return {
        "width": int(intr.width),
        "height": int(intr.height),
        "fx": round(float(intr.fx), 4),
        "fy": round(float(intr.fy), 4),
        "cx": round(float(intr.ppx), 4),  # principal point x
        "cy": round(float(intr.ppy), 4),  # principal point y
        "distortion_model": str(intr.model),
        "coeffs": [round(float(c), 6) for c in intr.coeffs],
    }


def read_intrinsics(rs, stream: str, width: int, height: int, fps: int, serial: str | None):
    """Start a short pipeline, grab the stream profile, return (intr_dict, depth_min_m)."""
    pipeline = rs.pipeline()
    cfg = rs.config()
    if serial:
        cfg.enable_device(serial)

    want_aligned = stream == "aligned_depth_to_color"
    # For an aligned-depth request the frames flow through color intrinsics, so enable both.
    if stream == "color" or want_aligned:
        cfg.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
    if stream == "depth" or want_aligned:
        cfg.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)

    profile = pipeline.start(cfg)
    try:
        if want_aligned:
            # Intrinsics of the aligned depth == the color stream's intrinsics.
            vsp = profile.get_stream(rs.stream.color).as_video_stream_profile()
        elif stream == "depth":
            vsp = profile.get_stream(rs.stream.depth).as_video_stream_profile()
        else:
            vsp = profile.get_stream(rs.stream.color).as_video_stream_profile()
        intr = _intr_to_dict(vsp.get_intrinsics())

        # Minimum measurable distance -> near_clip_m. Query the depth sensor if present.
        depth_min = None
        dev = profile.get_device()
        for s in dev.query_sensors():
            if s.is_depth_sensor():
                ds = s.as_depth_sensor()
                if ds.supports(rs.option.min_distance):
                    depth_min = round(float(ds.get_option(rs.option.min_distance)), 4)
                break
        return intr, depth_min
    finally:
        pipeline.stop()


def all_streams_json(rs, serial: str | None) -> dict:
    """Enumerate every device + its available stream intrinsics (for calibration/ records)."""
    ctx = rs.context()
    out = {"devices": []}
    for dev in ctx.query_devices():
        d = {
            "name": dev.get_info(rs.camera_info.name),
            "serial": dev.get_info(rs.camera_info.serial_number),
            "firmware": dev.get_info(rs.camera_info.firmware_version),
            "profiles": [],
        }
        if serial and d["serial"] != serial:
            continue
        for s in dev.query_sensors():
            for p in s.get_stream_profiles():
                vp = p.as_video_stream_profile()
                if not vp:
                    continue
                try:
                    intr = _intr_to_dict(vp.get_intrinsics())
                except Exception:
                    continue
                d["profiles"].append(
                    {"stream": p.stream_name(), "fps": p.fps(), **intr}
                )
        out["devices"].append(d)
    return out


def emit_config_block(intr: dict, depth_min, stream: str, name: str) -> str:
    """Ready-to-paste `sensors:` YAML matching sdg/config.py::SensorSpec."""
    near = depth_min if depth_min else 0.105  # D435 default-mode min ~0.105m
    coeffs_nonzero = any(abs(c) > 1e-4 for c in intr["coeffs"])
    lines = [
        "# --- paste into config/<run>.yaml (values read from the physical device) ---",
        "sensors:",
        f"  - name: {name}",
        "    type: realsense_depth        # ideal intrinsics + calibrated depth degradation",
        f"    resolution: [{intr['width']}, {intr['height']}]",
        "    intrinsics: {{fx: {fx}, fy: {fy}, cx: {cx}, cy: {cy}}}".format(**intr),
        f"    near_clip_m: {near}          # device min_distance (stream: {stream})",
        "    # --- depth degradation: calibrate against real GT-vs-sensor captures ---",
        "    bias_mm: 0.0                  # TODO calibrate (systematic offset)",
        "    noise_quadratic: 0.001        # TODO calibrate: sigma_m = k * z^2",
        "    edge_dropout: true",
        "    hole_fraction: 0.005          # TODO calibrate",
        "    noise_seed: 0",
    ]
    note = [
        "",
        f"# distortion_model={intr['distortion_model']} coeffs={intr['coeffs']}",
    ]
    if coeffs_nonzero:
        note.append(
            "# ^ NON-trivial distortion coeffs. Our model is pinhole (ignores them). If your"
        )
        note.append(
            "#   downstream needs distortion, undistort real images to match, or extend the collector."
        )
    else:
        note.append("# ^ coeffs ~0 -> pinhole model matches this stream well.")
    return "\n".join(lines + note)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stream", choices=list(_STREAMS), default="color",
                    help="which stream's intrinsics to emit (default: color). "
                         + " | ".join(f"{k}: {v}" for k, v in _STREAMS.items()))
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--serial", default=None, help="device serial (if several are connected)")
    ap.add_argument("--name", default="d435", help="sensor `name` in the emitted block")
    ap.add_argument("--json", metavar="PATH", default=None,
                    help="also dump all-streams intrinsics as JSON here (e.g. calibration/d435.json)")
    args = ap.parse_args()

    rs = _require_rs()

    if not rs.context().query_devices():
        sys.exit("No RealSense device detected. Check the USB3 connection / permissions.")

    if args.json:
        data = all_streams_json(rs, args.serial)
        with open(args.json, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[wrote] {args.json}  ({len(data['devices'])} device(s))", file=sys.stderr)

    intr, depth_min = read_intrinsics(
        rs, args.stream, args.width, args.height, args.fps, args.serial
    )
    print(emit_config_block(intr, depth_min, args.stream, args.name))


if __name__ == "__main__":
    main()
