"""Generic, object-agnostic folder-structure writer (the MVP output format).

Serializes whatever channels are present in the normalized Frame dict (see base.py):

    <output_dir>/
      rgb/000000.png            uint8 RGB
      depth/000000.png          16-bit PNG, millimetres
      semantic/000000.png       16-bit class id map
      instance/000000.png       16-bit instance id map
      meta/000000.json          intrinsics, camera_pose_world, per-object 6D pose/bbox/kpts
      dataset.json              cameras, classes, obj_ids, frame count, config pointer

Pure Python (numpy + Pillow). No Isaac dependency, so it is testable standalone.
Image encoding degrades gracefully if Pillow is missing (raises a clear error only
when actually asked to write an image).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

import numpy as np

from ..registry import register
from .base import Writer


def _save_png(path: str, array: "np.ndarray") -> None:
    try:
        from PIL import Image
    except ImportError as e:  # pragma: no cover
        raise ImportError("Pillow required for PNG output: pip install pillow") from e
    Image.fromarray(array).save(path)


@register("writer", "generic")
class GenericWriter(Writer):
    def open(self) -> None:
        for sub in ("rgb", "depth", "semantic", "instance", "meta"):
            os.makedirs(os.path.join(self.output_dir, sub), exist_ok=True)
        self._classes: Dict[str, int] = {}
        self._obj_ids = set()
        self._count = 0

    def write(self, frame: Dict[str, Any]) -> None:
        fid = frame["frame_id"]
        stem = f"{fid:06d}"

        if "rgb" in frame:
            _save_png(os.path.join(self.output_dir, "rgb", stem + ".png"),
                      np.asarray(frame["rgb"], dtype=np.uint8))

        if "depth" in frame:
            depth_m = np.asarray(frame["depth"], dtype=np.float32)
            depth_mm = np.clip(depth_m * 1000.0, 0, 65535).astype(np.uint16)
            _save_png(os.path.join(self.output_dir, "depth", stem + ".png"), depth_mm)

        if "semantic" in frame:
            _save_png(os.path.join(self.output_dir, "semantic", stem + ".png"),
                      np.asarray(frame["semantic"], dtype=np.uint16))

        if "instance" in frame:
            _save_png(os.path.join(self.output_dir, "instance", stem + ".png"),
                      np.asarray(frame["instance"], dtype=np.uint16))

        meta = {
            "frame_id": fid,
            "sensor": frame.get("sensor"),
            "intrinsics": frame.get("intrinsics"),
            "camera_pose_world": _listify(frame.get("camera_pose_world")),
            "objects": [_obj_meta(o) for o in frame.get("objects", [])],
        }
        for k in ("semantic_id_to_labels", "instance_id_to_labels"):
            if frame.get(k):
                meta[k] = frame[k]
        with open(os.path.join(self.output_dir, "meta", stem + ".json"), "w") as f:
            json.dump(meta, f, indent=2)

        for o in frame.get("objects", []):
            self._obj_ids.add(o.get("obj_id"))
        self._count += 1

    def close(self, dataset_meta: Dict[str, Any]) -> None:
        dataset = {
            "format": "generic",
            "num_frames": self._count,
            "obj_ids": sorted(x for x in self._obj_ids if x is not None),
            "config_snapshot": "config_snapshot.yaml",
            **dataset_meta,
        }
        with open(os.path.join(self.output_dir, "dataset.json"), "w") as f:
            json.dump(dataset, f, indent=2)


def _listify(v):
    return _jsonify(v)


def _jsonify(v):
    """Recursively convert numpy arrays/scalars (and nested dict/list) to JSON-safe types."""
    if v is None:
        return None
    if isinstance(v, dict):
        return {k: _jsonify(x) for k, x in v.items()}
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (list, tuple)):
        return [_jsonify(x) for x in v]
    if isinstance(v, np.generic):
        return v.item()
    return v


def _obj_meta(o: Dict[str, Any]) -> Dict[str, Any]:
    out = {"obj_id": o.get("obj_id"), "instance_id": o.get("instance_id")}
    # bbox_3d is now a self-describing dict; keypoints are lists. All serialized as-is.
    for k in ("pose_cam", "bbox_2d", "bbox_3d", "keypoints_2d", "keypoints_3d"):
        if k in o and o[k] is not None:
            out[k] = _jsonify(o[k])
    return out
