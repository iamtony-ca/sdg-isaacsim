"""YOLO-format writer (Ultralytics detection, optional segmentation).

    <output_dir>/
      images/<split>/000000.png ...
      labels/<split>/000000.txt ...
      data.yaml

Detection label lines (one per object), all values normalized to [0,1] by image size:
    <class_id> <x_center> <y_center> <width> <height>
Segmentation label lines (when `writer.segmentation` is truthy and masks are available):
    <class_id> <x1> <y1> <x2> <y2> ...          # normalized polygon points

class_id is 0-based and contiguous, assigned per obj_id in first-seen order and written to
data.yaml (`names`, `nc`). data.yaml `train`/`val` both point at the written split; split a
run into train/val downstream (or run separate configs with different `writer.split`).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

import numpy as np

from ..registry import register
from .base import Writer
from . import _shapes


def _save_png(path: str, array: "np.ndarray") -> None:
    try:
        from PIL import Image
    except ImportError as e:  # pragma: no cover
        raise ImportError("Pillow required for PNG output: pip install pillow") from e
    Image.fromarray(array).save(path)


@register("writer", "yolo")
class YoloWriter(Writer):
    def open(self) -> None:
        self.split = self.cfg.get("split", "train")
        self.want_seg = bool(self.cfg.get("segmentation", False))
        self.images_dir = os.path.join(self.output_dir, "images", self.split)
        self.labels_dir = os.path.join(self.output_dir, "labels", self.split)
        os.makedirs(self.images_dir, exist_ok=True)
        os.makedirs(self.labels_dir, exist_ok=True)
        self._classes: Dict[str, int] = {}  # obj_id -> class_id (0-based)

    def write(self, frame: Dict[str, Any]) -> None:
        img_id = int(frame["frame_id"])
        w, h = _shapes.frame_size(frame)
        stem = f"{img_id:06d}"
        if "rgb" in frame:
            _save_png(os.path.join(self.images_dir, stem + ".png"), np.asarray(frame["rgb"], dtype=np.uint8))

        lines: List[str] = []
        for obj in frame.get("objects", []):
            mask = _shapes.object_mask(frame, obj)
            bbox, _area, mask = _shapes.object_bbox(frame, obj, mask)
            if bbox is None or not w or not h:
                continue
            cls = self._class_id(obj["obj_id"])
            if self.want_seg and mask is not None:
                polys = _shapes.mask_polygons(mask)
                if polys:
                    # Ultralytics seg: one line per polygon (largest first is fine)
                    for poly in polys:
                        pts = " ".join(f"{poly[i]/w:.6f} {poly[i+1]/h:.6f}" for i in range(0, len(poly) - 1, 2))
                        lines.append(f"{cls} {pts}")
                    continue
            x, y, bw, bh = bbox
            xc = (x + bw / 2.0) / w
            yc = (y + bh / 2.0) / h
            lines.append(f"{cls} {xc:.6f} {yc:.6f} {bw / w:.6f} {bh / h:.6f}")

        with open(os.path.join(self.labels_dir, stem + ".txt"), "w") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))

    def close(self, dataset_meta: Dict[str, Any]) -> None:
        names = {cid: name for name, cid in sorted(self._classes.items(), key=lambda kv: kv[1])}
        lines = [
            f"path: {os.path.abspath(self.output_dir)}",
            f"train: images/{self.split}",
            f"val: images/{self.split}",
            f"nc: {len(names)}",
            "names:",
        ]
        for cid in sorted(names):
            lines.append(f"  {cid}: {names[cid]}")
        with open(os.path.join(self.output_dir, "data.yaml"), "w") as f:
            f.write("\n".join(lines) + "\n")

    def _class_id(self, obj_id: str) -> int:
        if obj_id not in self._classes:
            self._classes[obj_id] = len(self._classes)  # 0-based
        return self._classes[obj_id]
