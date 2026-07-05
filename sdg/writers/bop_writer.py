"""BOP-format writer (Benchmark for 6D Object Pose Estimation).

Emits one BOP "scene" per run:

    <output_dir>/
      camera.json                         # dataset-level intrinsics (from frame 0)
      obj_id_map.json                     # obj_id string -> BOP integer id (reproducibility)
      <split>/<scene_id:06d>/
        scene_camera.json                 # per-image: cam_K, depth_scale, cam_R_w2c, cam_t_w2c
        scene_gt.json                     # per-image: [{obj_id, cam_R_m2c, cam_t_m2c}, ...]
        scene_gt_info.json                # per-image: [{bbox_obj, bbox_visib, visib_fract, px_*}]
        rgb/000000.png
        depth/000000.png                  # uint16, millimetres (depth_scale = 1.0)
        mask/000000_000000.png            # per-gt-object mask (uint8 0/255)
        mask_visib/000000_000000.png      # per-gt-object visible mask

Conventions (BOP spec): poses are model->camera in the OpenCV camera frame (+X right,
+Y down, +Z forward); rotations are row-major 3x3 flattened to 9 floats; translations
are in MILLIMETRES. cam_K is row-major [fx,0,cx, 0,fy,cy, 0,0,1].

Our internal Frame uses the Omniverse/USD camera frame (+Y up, -Z forward) and metres, so
each pose is converted by M = diag(1,-1,-1) (a 180° rotation about X) and t is scaled ×1000.

Requirements on the run config for a complete BOP dataset:
  annotators: rgb, depth, instance_segmentation, pose_6d  (masks need instance_segmentation).
Missing channels degrade gracefully (skipped + one-time warning).

Amodal note: BOP distinguishes `mask` (object alone) from `mask_visib` (object in scene).
Without per-object isolated renders we only have the visible mask, so `mask` == `mask_visib`
and `px_count_all` == `px_count_visib`. `visib_fract` uses the bbox_3d occlusion ratio when
available (1 - occlusion), else 1.0 for any visible object. This is documented, not exact.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

import numpy as np

from ..registry import register
from .base import Writer

# Omniverse/USD camera frame -> OpenCV camera frame (flip Y and Z; 180° about X).
_M_USD_TO_CV = np.diag([1.0, -1.0, -1.0, 1.0])


def _save_png(path: str, array: "np.ndarray") -> None:
    try:
        from PIL import Image
    except ImportError as e:  # pragma: no cover
        raise ImportError("Pillow required for PNG output: pip install pillow") from e
    Image.fromarray(array).save(path)


@register("writer", "bop")
class BopWriter(Writer):
    def open(self) -> None:
        self.split = self.cfg.get("split", "train_pbr")
        self.scene_id = int(self.cfg.get("scene_id", 0))
        self.scene_dir = os.path.join(self.output_dir, self.split, f"{self.scene_id:06d}")
        for sub in ("rgb", "depth", "mask", "mask_visib"):
            os.makedirs(os.path.join(self.scene_dir, sub), exist_ok=True)

        self._scene_camera: Dict[str, Any] = {}
        self._scene_gt: Dict[str, Any] = {}
        self._scene_gt_info: Dict[str, Any] = {}
        self._obj_id_map: Dict[str, int] = {}
        self._camera_json: Optional[Dict[str, Any]] = None
        self._warned: set = set()
        self._amodal_used = False
        self._count = 0

    # --------------------------------------------------------------- per frame
    def write(self, frame: Dict[str, Any]) -> None:
        img_id = int(frame["frame_id"])
        key = str(img_id)
        intr = frame.get("intrinsics") or {}
        width = int(intr.get("width", 0))
        height = int(intr.get("height", 0))

        # ---- images -----------------------------------------------------
        if "rgb" in frame:
            _save_png(os.path.join(self.scene_dir, "rgb", f"{img_id:06d}.png"),
                      np.asarray(frame["rgb"], dtype=np.uint8))
        depth_mm = None
        if "depth" in frame:
            depth_m = np.asarray(frame["depth"], dtype=np.float32)
            depth_mm = np.clip(depth_m * 1000.0, 0, 65535).astype(np.uint16)
            _save_png(os.path.join(self.scene_dir, "depth", f"{img_id:06d}.png"), depth_mm)
        elif "depth" not in self._warned:
            print("[sdg][bop] no depth channel — BOP depth/ will be empty (enable annotators.depth).")
            self._warned.add("depth")

        # ---- scene_camera ----------------------------------------------
        cam_K = _cam_K(intr)
        R_w2c, t_w2c = self._world_to_cam_cv(frame.get("camera_pose_world"))
        self._scene_camera[key] = {
            "cam_K": cam_K,
            "depth_scale": 1.0,  # stored depth already in mm
            "cam_R_w2c": R_w2c,
            "cam_t_w2c": t_w2c,
        }
        if self._camera_json is None and width and height:
            self._camera_json = {
                "fx": intr.get("fx"), "fy": intr.get("fy"),
                "cx": intr.get("cx"), "cy": intr.get("cy"),
                "depth_scale": 1.0, "width": width, "height": height,
            }

        # ---- per-object gt / gt_info / masks ---------------------------
        instance_map = frame.get("instance")
        instance_map = np.asarray(instance_map) if instance_map is not None else None
        gt_list: List[Dict[str, Any]] = []
        info_list: List[Dict[str, Any]] = []

        for obj in frame.get("objects", []):
            pose_cam = obj.get("pose_cam")
            if pose_cam is None:
                if "pose" not in self._warned:
                    print("[sdg][bop] object without pose_cam skipped — enable annotators.pose_6d.")
                    self._warned.add("pose")
                continue
            gt_idx = len(gt_list)
            gt_list.append(self._gt_entry(obj, pose_cam))
            info_list.append(
                self._gt_info_entry(obj, instance_map, depth_mm, width, height, img_id, gt_idx)
            )

        self._scene_gt[key] = gt_list
        self._scene_gt_info[key] = info_list
        self._count += 1

    def close(self, dataset_meta: Dict[str, Any]) -> None:
        _dump(os.path.join(self.scene_dir, "scene_camera.json"), self._scene_camera)
        _dump(os.path.join(self.scene_dir, "scene_gt.json"), self._scene_gt)
        _dump(os.path.join(self.scene_dir, "scene_gt_info.json"), self._scene_gt_info)
        _dump(os.path.join(self.output_dir, "obj_id_map.json"), self._obj_id_map)
        if self._camera_json is not None:
            _dump(os.path.join(self.output_dir, "camera.json"), self._camera_json)
        _dump(os.path.join(self.output_dir, "bop_info.json"), {
            "format": "bop",
            "split": self.split,
            "scene_id": self.scene_id,
            "num_images": self._count,
            "obj_id_map": self._obj_id_map,
            "config_snapshot": "config_snapshot.yaml",
            "amodal_masks": self._amodal_used,
            "note": ("mask=amodal, mask_visib=visible (true visib_fract)." if self._amodal_used
                     else "mask == mask_visib (no amodal render); enable annotators.amodal for true masks.")
                    + " depth in mm, depth_scale=1.0.",
            **dataset_meta,
        })

    # ------------------------------------------------------------------ helpers
    def _gt_entry(self, obj: Dict[str, Any], pose_cam) -> Dict[str, Any]:
        T = _M_USD_TO_CV @ np.asarray(pose_cam, dtype=np.float64).reshape(4, 4)  # model->cam (OpenCV, m)
        R = T[:3, :3]
        t_mm = T[:3, 3] * 1000.0
        return {
            "obj_id": self._obj_id_int(obj["obj_id"]),
            "cam_R_m2c": R.flatten().tolist(),   # row-major 3x3
            "cam_t_m2c": t_mm.tolist(),
        }

    def _gt_info_entry(self, obj, instance_map, depth_mm, width, height, img_id, gt_idx) -> Dict[str, Any]:
        ids = obj.get("_instance_ids")
        visib = None
        if instance_map is not None and ids:
            m = np.isin(instance_map, ids)
            visib = m if m.any() else None
        # amodal mask (object rendered in isolation), when S4 amodal capture ran
        amodal = obj.get("_amodal_mask")
        amodal = np.asarray(amodal, dtype=bool) if amodal is not None else None
        if amodal is not None:
            self._amodal_used = True

        bbox_visib = _mask_bbox(visib) or obj.get("bbox_2d", [-1, -1, -1, -1])
        bbox_obj = _mask_bbox(amodal) or bbox_visib
        px_visib = int(visib.sum()) if visib is not None else 0
        px_all = int(amodal.sum()) if amodal is not None else px_visib
        if visib is not None and depth_mm is not None:
            px_valid = int((visib & (depth_mm > 0)).sum())
        else:
            px_valid = px_visib

        # mask_visib = visible mask; mask = amodal (falls back to visible if no amodal pass)
        self._save_mask("mask_visib", visib, width, height, img_id, gt_idx)
        self._save_mask("mask", amodal if amodal is not None else visib, width, height, img_id, gt_idx)

        # visib_fract: true ratio when amodal present, else bbox_3d occlusion, else 1/0
        if px_all > 0 and amodal is not None:
            visib_fract = float(px_visib) / float(px_all)
        else:
            occ = obj.get("bbox_3d", {}).get("occlusion") if isinstance(obj.get("bbox_3d"), dict) else None
            visib_fract = float(max(0.0, 1.0 - occ)) if (occ is not None and occ >= 0) else (1.0 if px_visib else 0.0)

        return {
            "bbox_obj": list(bbox_obj),
            "bbox_visib": list(bbox_visib),
            "px_count_all": px_all,
            "px_count_valid": px_valid,
            "px_count_visib": px_visib,
            "visib_fract": visib_fract,
        }

    def _save_mask(self, sub, mask_bool, width, height, img_id, gt_idx) -> None:
        if mask_bool is None:
            if not (width and height):
                return
            mask_bool = np.zeros((height, width), dtype=bool)
        img = np.asarray(mask_bool).astype(np.uint8) * 255
        _save_png(os.path.join(self.scene_dir, sub, f"{img_id:06d}_{gt_idx:06d}.png"), img)

    def _world_to_cam_cv(self, camera_pose_world):
        if camera_pose_world is None:
            return np.eye(3).flatten().tolist(), [0.0, 0.0, 0.0]
        cam2world = np.asarray(camera_pose_world, dtype=np.float64).reshape(4, 4)
        world2cam = np.linalg.inv(cam2world)
        T = _M_USD_TO_CV @ world2cam
        return T[:3, :3].flatten().tolist(), (T[:3, 3] * 1000.0).tolist()

    def _obj_id_int(self, obj_id_str: str) -> int:
        if obj_id_str in self._obj_id_map:
            return self._obj_id_map[obj_id_str]
        m = re.search(r"(\d+)$", str(obj_id_str))
        val = int(m.group(1)) if m else len(self._obj_id_map) + 1
        used = set(self._obj_id_map.values())
        while val in used:
            val += 1
        self._obj_id_map[obj_id_str] = val
        return val


def _mask_bbox(mask):
    if mask is None:
        return None
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    return [x0, y0, x1 - x0 + 1, y1 - y0 + 1]


def _cam_K(intr: Dict[str, Any]) -> List[float]:
    fx = float(intr.get("fx", 0.0)); fy = float(intr.get("fy", 0.0))
    cx = float(intr.get("cx", 0.0)); cy = float(intr.get("cy", 0.0))
    return [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]


def _dump(path: str, obj: Any) -> None:
    with open(path, "w") as f:
        json.dump(obj, f)
