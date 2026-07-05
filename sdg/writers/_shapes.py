"""Shared per-object shape extraction for detection/segmentation writers (COCO, YOLO).

Derives a per-object binary mask + bbox + polygons from the normalized Frame dict:
  - mask   : from frame["instance"] (id map) sliced by obj["_instance_ids"]
  - bbox   : tight box of the mask, or fall back to obj["bbox_2d"]
  - polygon: contour(s) of the mask via OpenCV (cv2), COCO/YOLO-seg compatible

cv2 ships with the Isaac bundle (see DEPENDENCIES.md); polygon extraction degrades to
None if cv2 is unavailable, so bbox-only writers still work.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def frame_size(frame: Dict[str, Any]) -> Tuple[int, int]:
    intr = frame.get("intrinsics") or {}
    w = int(intr.get("width", 0))
    h = int(intr.get("height", 0))
    if (not w or not h) and frame.get("rgb") is not None:
        arr = np.asarray(frame["rgb"])
        h, w = int(arr.shape[0]), int(arr.shape[1])
    return w, h


def object_mask(frame: Dict[str, Any], obj: Dict[str, Any]) -> Optional[np.ndarray]:
    inst = frame.get("instance")
    ids = obj.get("_instance_ids")
    if inst is None or not ids:
        return None
    mask = np.isin(np.asarray(inst), ids)
    return mask if mask.any() else None


def mask_bbox(mask: np.ndarray) -> Optional[List[int]]:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    return [x0, y0, x1 - x0 + 1, y1 - y0 + 1]  # [x, y, w, h]


def object_bbox(frame: Dict[str, Any], obj: Dict[str, Any], mask: Optional[np.ndarray]):
    """Return ([x,y,w,h], area_px, mask). Prefers the mask; falls back to bbox_2d."""
    if mask is not None:
        bb = mask_bbox(mask)
        if bb is not None:
            return bb, int(mask.sum()), mask
    bb = obj.get("bbox_2d")
    if bb is not None:
        return list(bb), int(bb[2] * bb[3]), None
    return None, 0, None


def mask_polygons(mask: np.ndarray, min_points: int = 3) -> List[List[float]]:
    """Contour polygons of a binary mask, each as a flat [x1,y1,x2,y2,...] list."""
    try:
        import cv2
    except ImportError:
        return []
    m = np.ascontiguousarray(mask.astype(np.uint8))
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polys: List[List[float]] = []
    for c in contours:
        pts = c.reshape(-1, 2)
        if len(pts) >= min_points:
            polys.append([float(v) for v in pts.flatten()])
    return polys
