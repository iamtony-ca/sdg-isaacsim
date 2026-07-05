"""Writer interface + the normalized per-frame dict contract.

run_sdg hands each writer a `Frame` dict with these keys (present only if the
corresponding annotator is enabled in config). All arrays are numpy; poses are 4x4
row-major world/relative transforms.

    frame = {
        "frame_id":   int,
        "sensor":     str,                 # sensor name (cam0, ...)
        "intrinsics": {fx, fy, cx, cy, width, height},
        "camera_pose_world": 4x4,          # camera in world
        "rgb":        HxWx3  uint8,        # if annotators.rgb
        "depth":      HxW     float32 (m), # if annotators.depth  (metric, ideal or degraded)
        "semantic":   HxW     uint16,      # class id map, if semantic_segmentation
        "instance":   HxW     uint16,      # instance id map, if instance_segmentation
        "objects": [                       # per-object GT (if pose_6d/bbox/keypoints)
            {"obj_id": str, "instance_id": int,
             "pose_cam": 4x4,              # object in camera frame (6D pose GT), metres
             "bbox_2d": [x,y,w,h],         # if bbox_2d
             "bbox_3d": {                  # if bbox_3d — all metres
                 "extents_min": [x,y,z], "extents_max": [x,y,z],  # box local frame
                 "transform_local_to_world": 4x4,                 # column-vector
                 "corners_cam": [[x,y,z]*8],                       # camera frame
                 "corners_2d": [[u,v,vis]*8],                      # projected to pixels
                 "occlusion": float},                             # 0=vis..1=occluded
             "keypoints_2d": [[u,v,vis],...],  # if keypoints — projected to pixels
             "keypoints_3d": [[x,y,z],...]},   # camera frame, metres
        ],
    }

    Pose/point convention: column-vector (p' = T @ p), translations in METRES. bbox_3d and
    keypoints are derived from the object pose + camera projection (camera_params), not from
    a separate Replicator annotator (keypoints need assets/obj/<obj_id>/keypoints.json).

A Writer is object- and task-agnostic: it serializes whatever channels are present.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class Writer(ABC):
    def __init__(self, output_dir: str, cfg: Dict[str, Any]):
        self.output_dir = output_dir
        self.cfg = cfg

    def open(self) -> None:
        """Prepare directories / open index files."""

    @abstractmethod
    def write(self, frame: Dict[str, Any]) -> None:
        raise NotImplementedError

    def close(self, dataset_meta: Dict[str, Any]) -> None:
        """Finalize (write dataset-level index)."""
