"""SceneBuilder interface.

A scene builder turns the config's `scene` + `objects` sections into a live stage:
background/ground, spawned target objects (by obj_id, from assets/obj/<obj_id>/),
physics props, and semantic labels for segmentation annotators.

After build(), `self.instances` holds one entry per spawned object instance:
    {"obj_id": str, "instance_id": int, "prim_path": str, "prim": Usd.Prim,
     "semantic_class": str}
This is the shared context randomizers (pose target) and the annotator collector
(per-object 6D pose GT) read from. Object identity is always `obj_id` — never a
hardcoded object name (see CLAUDE.md principle 2).
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from ..config import ObjectSpec

# ws root = two levels up from this file (.../sdg_ws/sdg/scene/base.py -> .../sdg_ws)
WS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class SceneBuilder(ABC):
    def __init__(self, scene_cfg: Dict[str, Any], objects: List[ObjectSpec]):
        self.scene_cfg = scene_cfg
        self.objects = objects
        self.world_path = "/World"
        self.object_prims: Dict[str, list] = {}   # obj_id -> [prim paths]
        self.instances: List[Dict[str, Any]] = []  # flat per-instance context (see module docstring)

    @abstractmethod
    def build(self) -> None:
        """Create background/ground and spawn objects."""
        raise NotImplementedError

    def asset_dir(self, obj_id: str) -> str:
        """Convention: assets/obj/<obj_id>/ holds mesh/USD/material/semantic/exemplar.

        Resolved against the ws root so runs work regardless of cwd.
        """
        return os.path.join(WS_ROOT, "assets", "obj", obj_id)

    def resolve_asset_usd(self, obj_id: str) -> str:
        """Find the USD file for an object inside assets/obj/<obj_id>/.

        Preference order: <obj_id>.usd*, mesh.usd*, obj.usd*, model.usd*, then first *.usd*.
        Raises FileNotFoundError with an actionable message if none exists — adding a
        new object is meant to be "drop a USD in assets/obj/<obj_id>/ + name it in config".
        """
        d = self.asset_dir(obj_id)
        if not os.path.isdir(d):
            raise FileNotFoundError(f"asset dir missing for obj_id='{obj_id}': {d}")
        exts = (".usd", ".usda", ".usdc", ".usdz")
        names = sorted(os.listdir(d))
        for pref in (obj_id, "mesh", "obj", "model"):
            for n in names:
                if n.startswith(pref) and n.lower().endswith(exts):
                    return os.path.join(d, n)
        for n in names:
            if n.lower().endswith(exts):
                return os.path.join(d, n)
        raise FileNotFoundError(
            f"no USD asset (*.usd/.usda/.usdc/.usdz) in {d} for obj_id='{obj_id}'. "
            f"Drop the object's USD there (see assets/obj/<obj_id>/README.md)."
        )

    def load_keypoints(self, obj_id: str) -> Optional[List[List[float]]]:
        """Load object-local 3D keypoints from assets/obj/<obj_id>/keypoints.json, if present.

        Accepts any of:
          [[x,y,z], ...]
          {"keypoints": [[x,y,z], ...]}
          {"keypoints": [{"name": "...", "point": [x,y,z]}, ...]}
        Points are in the object's local frame (same units as its mesh). Returns None when
        no keypoints file exists (keypoint GT is simply skipped for that object).
        """
        path = os.path.join(self.asset_dir(obj_id), "keypoints.json")
        if not os.path.isfile(path):
            return None
        with open(path, "r") as f:
            data = json.load(f)
        raw = data.get("keypoints", data) if isinstance(data, dict) else data
        pts: List[List[float]] = []
        for kp in raw:
            p = kp.get("point") if isinstance(kp, dict) else kp
            pts.append([float(p[0]), float(p[1]), float(p[2])])
        return pts or None
