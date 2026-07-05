"""QA overlay tool — draw GT annotations onto RGB for visual sanity checks.

Reads a `generic`-format dataset (rgb/ + meta/) and renders, per frame, an overlay of:
  - bbox_2d          green rectangle
  - bbox_3d          cyan wireframe (from meta corners_2d) + occlusion label
  - keypoints_2d     yellow dots (visible) / dim (behind/out of frame)
  - pose_cam         RGB object axes (X red, Y green, Z blue), projected pinhole

Pure Python (numpy + Pillow); no Isaac. cv2 not required. Run with the bundle python:

    /isaac-sim/python.sh tools/visualize.py datasets/<run> [--max 20] [--axis-len 0.1]

Output: <dataset>/qa/000000.png ...

Projection for pose axes uses the USD camera convention (camera looks down -Z, +Y up) and
the meta intrinsics (fx, fy, cx, cy). bbox_3d/keypoints are already projected in meta.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    from PIL import Image, ImageDraw
except ImportError as e:  # pragma: no cover
    raise SystemExit("Pillow required: /isaac-sim/python.sh -m pip install pillow") from e

# 12 edges of the axis-aligned box, given corner index bit layout (bit0=x, bit1=y, bit2=z)
_CUBE_EDGES = [(c, c ^ b) for c in range(8) for b in (1, 2, 4) if c < (c ^ b)]
_AXIS_COLORS = [(255, 60, 60), (60, 255, 60), (80, 120, 255)]  # X, Y, Z


def _project_cam(P: np.ndarray, intr: Dict[str, float]) -> Optional[Tuple[float, float]]:
    """Project a camera-frame point (USD frame, -Z forward, metres) to pixels."""
    X, Y, Z = float(P[0]), float(P[1]), float(P[2])
    if Z >= -1e-6:  # at/behind the image plane
        return None
    zf = -Z
    u = intr["cx"] + intr["fx"] * X / zf
    v = intr["cy"] - intr["fy"] * Y / zf
    return (u, v)


def _draw_bbox_2d(d: "ImageDraw.ImageDraw", obj: Dict[str, Any]) -> None:
    bb = obj.get("bbox_2d")
    if bb:
        x, y, w, h = bb
        d.rectangle([x, y, x + w, y + h], outline=(0, 220, 0), width=2)


def _draw_bbox_3d(d: "ImageDraw.ImageDraw", obj: Dict[str, Any]) -> None:
    b3 = obj.get("bbox_3d")
    if not isinstance(b3, dict):
        return
    corners = b3.get("corners_2d")
    if not corners or len(corners) < 8:
        return
    pts = [(c[0], c[1]) for c in corners]
    for a, b in _CUBE_EDGES:
        d.line([pts[a], pts[b]], fill=(0, 230, 230), width=1)
    occ = b3.get("occlusion")
    if occ is not None and occ >= 0:
        d.text((pts[0][0], pts[0][1]), f"occ {occ:.2f}", fill=(0, 230, 230))


def _draw_keypoints(d: "ImageDraw.ImageDraw", obj: Dict[str, Any]) -> None:
    for i, kp in enumerate(obj.get("keypoints_2d", []) or []):
        u, v, vis = kp
        color = (255, 220, 0) if vis else (120, 110, 40)
        d.ellipse([u - 3, v - 3, u + 3, v + 3], fill=color)


def _draw_pose_axes(d: "ImageDraw.ImageDraw", obj: Dict[str, Any], intr: Dict[str, float], axis_len: float) -> None:
    pose = obj.get("pose_cam")
    if not pose:
        return
    T = np.asarray(pose, dtype=np.float64).reshape(4, 4)
    R, t = T[:3, :3], T[:3, 3]
    origin = _project_cam(t, intr)
    if origin is None:
        return
    for k in range(3):
        ep = R[:, k] * axis_len + t
        pp = _project_cam(ep, intr)
        if pp is not None:
            d.line([origin, pp], fill=_AXIS_COLORS[k], width=2)


def render_frame(rgb_path: str, meta: Dict[str, Any], out_path: str, axis_len: float) -> None:
    img = Image.open(rgb_path).convert("RGB")
    d = ImageDraw.Draw(img)
    intr = meta.get("intrinsics") or {}
    for obj in meta.get("objects", []):
        _draw_bbox_2d(d, obj)
        _draw_bbox_3d(d, obj)
        _draw_keypoints(d, obj)
        if intr:
            _draw_pose_axes(d, obj, intr, axis_len)
    img.save(out_path)


def main() -> None:
    ap = argparse.ArgumentParser(description="Overlay generic-dataset GT onto RGB for QA.")
    ap.add_argument("dataset", help="path to a generic-format dataset dir (has rgb/ and meta/)")
    ap.add_argument("--max", type=int, default=None, help="limit number of frames")
    ap.add_argument("--out", default="qa", help="output subdir name (default: qa)")
    ap.add_argument("--axis-len", type=float, default=0.1, help="pose axis length in metres")
    args = ap.parse_args()

    rgb_dir = os.path.join(args.dataset, "rgb")
    meta_dir = os.path.join(args.dataset, "meta")
    if not (os.path.isdir(rgb_dir) and os.path.isdir(meta_dir)):
        raise SystemExit(f"not a generic dataset (need rgb/ and meta/): {args.dataset}")
    out_dir = os.path.join(args.dataset, args.out)
    os.makedirs(out_dir, exist_ok=True)

    metas = sorted(f for f in os.listdir(meta_dir) if f.endswith(".json"))
    if args.max is not None:
        metas = metas[: args.max]
    n = 0
    for mf in metas:
        stem = os.path.splitext(mf)[0]
        rgb_path = os.path.join(rgb_dir, stem + ".png")
        if not os.path.isfile(rgb_path):
            continue
        with open(os.path.join(meta_dir, mf)) as f:
            meta = json.load(f)
        render_frame(rgb_path, meta, os.path.join(out_dir, stem + ".png"), args.axis_len)
        n += 1
    print(f"[visualize] wrote {n} overlay(s) -> {out_dir}")


if __name__ == "__main__":
    main()
