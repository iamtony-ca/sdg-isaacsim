"""COCO-format writer (object detection + optional instance segmentation).

    <output_dir>/
      images/000000.png ...
      annotations/instances_<split>.json

instances_<split>.json follows the COCO detection schema:
  images:      [{id, file_name, width, height}]
  categories:  [{id, name, supercategory}]      # id 1-based, per obj_id (first-seen order)
  annotations: [{id, image_id, category_id, bbox:[x,y,w,h], area, iscrowd,
                 segmentation}]                  # segmentation = polygons (cv2) if masks on

Segmentation is emitted as polygons (list of [x1,y1,...]) from the instance mask when
`instance_segmentation` is enabled and `writer.segmentation` is truthy (default true).
Without masks, annotations carry bbox + area only (segmentation omitted). Category ids map
obj_id strings to 1-based ints (recorded in the output for reproducibility).
"""
from __future__ import annotations

import json
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


@register("writer", "coco")
class CocoWriter(Writer):
    def open(self) -> None:
        self.split = self.cfg.get("split", "train")
        self.want_seg = bool(self.cfg.get("segmentation", True))
        self.images_dir = os.path.join(self.output_dir, "images")
        self.ann_dir = os.path.join(self.output_dir, "annotations")
        os.makedirs(self.images_dir, exist_ok=True)
        os.makedirs(self.ann_dir, exist_ok=True)

        self._images: List[Dict[str, Any]] = []
        self._annotations: List[Dict[str, Any]] = []
        self._categories: Dict[str, int] = {}  # obj_id -> category_id (1-based)
        self._ann_id = 1

    def write(self, frame: Dict[str, Any]) -> None:
        img_id = int(frame["frame_id"])
        w, h = _shapes.frame_size(frame)
        file_name = f"{img_id:06d}.png"
        if "rgb" in frame:
            _save_png(os.path.join(self.images_dir, file_name), np.asarray(frame["rgb"], dtype=np.uint8))
        self._images.append({"id": img_id, "file_name": file_name, "width": w, "height": h})

        for obj in frame.get("objects", []):
            mask = _shapes.object_mask(frame, obj)
            bbox, area, mask = _shapes.object_bbox(frame, obj, mask)
            if bbox is None:
                continue  # not visible / no localization for this object
            ann = {
                "id": self._ann_id,
                "image_id": img_id,
                "category_id": self._category_id(obj["obj_id"]),
                "bbox": [float(v) for v in bbox],
                "area": float(area),
                "iscrowd": 0,
            }
            if self.want_seg and mask is not None:
                polys = _shapes.mask_polygons(mask)
                if polys:
                    ann["segmentation"] = polys
            self._annotations.append(ann)
            self._ann_id += 1

    def close(self, dataset_meta: Dict[str, Any]) -> None:
        categories = [
            {"id": cid, "name": name, "supercategory": "object"}
            for name, cid in sorted(self._categories.items(), key=lambda kv: kv[1])
        ]
        coco = {
            "info": {"description": "SDG COCO dataset", "split": self.split,
                     "config_snapshot": "config_snapshot.yaml"},
            "licenses": [],
            "images": self._images,
            "categories": categories,
            "annotations": self._annotations,
        }
        with open(os.path.join(self.ann_dir, f"instances_{self.split}.json"), "w") as f:
            json.dump(coco, f)

    def _category_id(self, obj_id: str) -> int:
        if obj_id not in self._categories:
            self._categories[obj_id] = len(self._categories) + 1  # 1-based
        return self._categories[obj_id]
