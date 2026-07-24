"""FrameCollector — attach replicator annotators and build the normalized Frame dict.

Turns the config `annotators` flags into attached replicator annotators on a sensor's
render product, then on each captured step reads their `get_data()` and normalizes them
into the Frame dict consumed by writers (see sdg/writers/base.py).

★ 6.0.1 API + data formats (verified against omni.replicator.core 1.13.27):
  - rep.annotators.get(name, init_params=...) ; .attach(rp) ; .get_data() ; .detach()
      (sdg_getting_started_02.py:79-82; simulation_get_data.py:81-84)
  - camera_params.get_data() -> dict with keys: cameraFocalLength, cameraAperture[h,v],
      cameraApertureOffset[x,y], renderProductResolution[W,H], metersPerSceneUnit,
      cameraViewTransform (16 floats, row-major, WORLD->CAMERA view matrix), cameraModel.
      (annotators_default.py:1815-1909)
  - distance_to_image_plane.get_data() -> float32 (H,W), STAGE UNITS, background=+inf/NaN.
      Metres = value * metersPerSceneUnit. (annotators.py:1908; kitti.py:384 nan_to_num)
  - semantic_segmentation (colorize=False) -> {"data": uint32 (H,W),
      "info": {"idToLabels": {id: {type: value}}}}. (annotators_default.py:1681-1759)
  - instance_segmentation (colorize=False) -> {"data": uint32 (H,W),
      "info": {"idToLabels": {id: prim_path}, "idToSemantics": {...}}}. (:1565-1646)
  - bounding_box_3d -> {"data": struct[semanticId,x/y/z_min,x/y/z_max,transform(4x4),
      occlusionRatio], "info": {"primPaths": [...], "bboxIds": [...]}}. (:1279-1291)
  - rep.functional.utils.get_world_transform(prim).GetMatrix() -> Gf.Matrix4d
      (row-vector local->world, stage units). (functional/utils.py:327-375)

Pose convention (documented, so downstream is unambiguous):
  All output 4x4 poses use the COLUMN-vector convention  p' = T @ p  (homogeneous), and
  translations are in METRES. Omniverse matrices are row-vector (p' = p @ M), so we
  transpose. camera_pose_world = camera-to-world; per-object pose_cam = object-to-camera.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np


class FrameCollector:
    def __init__(self, sensor, ann_cfg: Dict[str, Any]):
        self.sensor = sensor
        self.name = sensor.name
        self.render_product = sensor.render_product
        c = ann_cfg or {}
        self.want_rgb = bool(c.get("rgb", True))
        self.want_depth = bool(c.get("depth", False))
        self.want_semantic = bool(c.get("semantic_segmentation", False))
        self.want_bbox_2d = bool(c.get("bbox_2d", False))
        # amodal (full, unoccluded) per-object masks via isolated render passes (S4)
        self.want_amodal = bool(c.get("amodal", False))
        # per-object 2D bbox and amodal both need the instance segmentation annotator
        self.want_instance = (bool(c.get("instance_segmentation", False))
                              or self.want_bbox_2d or self.want_amodal)
        self.emit_instance_channel = bool(c.get("instance_segmentation", False))
        self.want_bbox_3d = bool(c.get("bbox_3d", False))
        self.want_normals = bool(c.get("normals", False))
        self.want_pose = bool(c.get("pose_6d", False))
        # keypoints: truthy (true or a non-empty list) enables projection of per-obj 3D
        # keypoints (loaded from assets/obj/<obj_id>/keypoints.json by the scene builder).
        self.want_keypoints = bool(c.get("keypoints"))
        self._annots: Dict[str, Any] = {}

    # --------------------------------------------------------------- lifecycle
    def setup(self) -> None:
        import omni.replicator.core as rep

        rp = self.render_product

        def attach(name, init_params=None):
            a = rep.annotators.get(name, init_params=init_params)
            a.attach(rp)
            self._annots[name] = a

        # camera_params is always needed (intrinsics + extrinsics + metersPerSceneUnit).
        attach("camera_params")
        if self.want_rgb:
            attach("rgb")
        if self.want_depth:
            attach("distance_to_image_plane")
        if self.want_semantic:
            attach("semantic_segmentation", {"colorize": False})
        if self.want_instance:
            attach("instance_segmentation", {"colorize": False})
        if self.want_bbox_3d:
            attach("bounding_box_3d")
        if self.want_normals:
            attach("normals")

    def teardown(self) -> None:
        for a in self._annots.values():
            try:
                a.detach()
            except Exception:
                pass
        self._annots.clear()

    def camera_ready(self) -> bool:
        """True once camera_params yields usable data: a non-singular view transform AND a
        non-zero aperture. The SyntheticData graph needs a step (or a few) before it returns
        valid camera data; until then cameraViewTransform is singular (all-zero) and
        cameraAperture is [0, 0], which would make _safe_inv fall back to pinv and _intrinsics
        divide by zero. run_sdg's warm-up loop polls this before capturing frame 0.
        """
        if "camera_params" not in self._annots:
            return True
        cam = self._annots["camera_params"].get_data()
        ap = np.asarray(cam.get("cameraAperture", [0.0, 0.0]), dtype=np.float64).reshape(-1)
        if ap.size < 2 or ap[0] == 0.0 or ap[1] == 0.0:
            return False
        view = np.asarray(cam.get("cameraViewTransform", []), dtype=np.float64).reshape(-1)
        if view.size < 16:
            return False
        m = view.reshape(4, 4)
        return bool(np.all(np.isfinite(m)) and abs(np.linalg.det(m)) > 1e-9)

    # ----------------------------------------------------------------- collect
    def collect(self, frame_id: int, scene_instances: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Read all attached annotators (call AFTER app.step()) into a Frame dict."""
        import omni.replicator.core as rep

        cam = self._annots["camera_params"].get_data()
        mpu = float(np.asarray(cam.get("metersPerSceneUnit", 1.0)).reshape(-1)[0])
        res = np.asarray(cam["renderProductResolution"]).reshape(-1)
        width, height = int(res[0]), int(res[1])

        # world->camera (row-vector) -> column-vector world->camera
        view_rv = np.asarray(cam["cameraViewTransform"], dtype=np.float64).reshape(4, 4)
        T_cam_world = view_rv.T
        camera_pose_world = _to_metres(_safe_inv(T_cam_world), mpu)
        # projection matrix (row-vector view->clip); used to project 3D points to pixels
        proj_rv = np.asarray(cam["cameraProjection"], dtype=np.float64).reshape(4, 4)
        cam_ctx = _CamCtx(view_rv, proj_rv, T_cam_world, mpu, width, height)

        frame: Dict[str, Any] = {
            "frame_id": frame_id,
            "sensor": self.name,
            "intrinsics": self._intrinsics(cam, width, height),
            "camera_pose_world": camera_pose_world.tolist(),
        }

        if self.want_rgb and "rgb" in self._annots:
            rgb = np.asarray(self._annots["rgb"].get_data())
            frame["rgb"] = rgb[..., :3]  # drop alpha (RGBA8 -> RGB8)

        if self.want_depth and "distance_to_image_plane" in self._annots:
            d = np.asarray(self._annots["distance_to_image_plane"].get_data(), dtype=np.float32)
            d = np.nan_to_num(d, nan=0.0, posinf=0.0, neginf=0.0) * mpu  # -> metres, bg=0
            # sensor hook: ideal returns GT depth unchanged; realsense_depth degrades it.
            frame["depth"] = np.asarray(self.sensor.postprocess_depth(d), dtype=np.float32)

        sem_labels = None
        if self.want_semantic and "semantic_segmentation" in self._annots:
            sem = self._annots["semantic_segmentation"].get_data()
            frame["semantic"] = np.asarray(sem["data"], dtype=np.uint32)
            sem_labels = sem.get("info", {}).get("idToLabels")
            frame["semantic_id_to_labels"] = _jsonable_labels(sem_labels)

        inst_data = inst_labels = None
        if self.want_instance and "instance_segmentation" in self._annots:
            inst = self._annots["instance_segmentation"].get_data()
            inst_data = np.asarray(inst["data"], dtype=np.uint32)
            inst_labels = inst.get("info", {}).get("idToLabels", {})
            if self.emit_instance_channel:
                frame["instance"] = inst_data
                frame["instance_id_to_labels"] = _jsonable_labels(inst_labels)

        if self.want_normals and "normals" in self._annots:
            frame["normals"] = np.asarray(self._annots["normals"].get_data())[..., :3]

        bbox3d = self._annots["bounding_box_3d"].get_data() if self.want_bbox_3d else None

        # Per-object GT, tied to each scene instance by prim path.
        objects: List[Dict[str, Any]] = []
        for inst in scene_instances:
            objects.append(self._object_gt(inst, cam_ctx, inst_data, inst_labels, bbox3d))
        frame["objects"] = objects
        return frame

    # ------------------------------------------------------------------- amodal
    def capture_amodal(self, app, scene, frame: Dict[str, Any]) -> None:
        """Add each object's amodal (full, unoccluded) mask to frame["objects"].

        Renders each object in isolation (hiding ground, distractors and the other objects),
        reads the instance mask, and attaches it as obj["_amodal_mask"] (bool HxW). Restores
        visibility afterwards. Costs one extra render step per object per frame.
        Call AFTER collect() (which has already read the main-frame annotators).
        """
        if not self.want_amodal or "instance_segmentation" not in self._annots:
            return
        import omni.replicator.core as rep
        import omni.usd

        stage = omni.usd.get_context().get_stage()
        inst_annot = self._annots["instance_segmentation"]
        obj_prims = [inst["prim"] for inst in scene.instances]
        bg_prims = [stage.GetPrimAtPath(f"{scene.world_path}/GroundPlane"),
                    stage.GetPrimAtPath(f"{scene.world_path}/Distractors"),
                    stage.GetPrimAtPath(f"{scene.world_path}/Occluders")]
        bg_prims = [p for p in bg_prims if p and p.IsValid()]
        by_id = {o["instance_id"]: o for o in frame.get("objects", [])}

        for p in bg_prims:
            rep.functional.modify.visibility(p, False)
        try:
            for tgt in scene.instances:
                for p in obj_prims:
                    rep.functional.modify.visibility(p, p is tgt["prim"])
                app.step()
                data = inst_annot.get_data()
                idmap = np.asarray(data["data"], dtype=np.uint32)
                ids = _matching_ids(data.get("info", {}).get("idToLabels", {}), tgt["prim_path"])
                mask = np.isin(idmap, ids) if ids else np.zeros(idmap.shape, dtype=bool)
                obj = by_id.get(tgt["instance_id"])
                if obj is not None:
                    obj["_amodal_mask"] = mask
        finally:
            # restore: objects visible; background container(s) visible (distractor children
            # keep their own per-frame visibility, re-set by the randomizer next frame)
            for p in obj_prims:
                rep.functional.modify.visibility(p, True)
            for p in bg_prims:
                rep.functional.modify.visibility(p, True)

    # ------------------------------------------------------------------ helpers
    def _intrinsics(self, cam: Dict[str, Any], width: int, height: int) -> Dict[str, float]:
        f = float(np.asarray(cam["cameraFocalLength"]).reshape(-1)[0])
        aperture = np.asarray(cam["cameraAperture"]).reshape(-1)
        offset = np.asarray(cam.get("cameraApertureOffset", [0.0, 0.0])).reshape(-1)
        ah, av = float(aperture[0]), float(aperture[1])
        # Guard against a not-ready camera_params (aperture [0,0]); the warm-up loop should
        # prevent this, but never divide by zero here (matches the cx/cy guards below).
        fx = f * width / ah if ah else 0.0
        fy = f * height / av if av else 0.0
        cx = width * (0.5 + (float(offset[0]) / ah if ah else 0.0))
        cy = height * (0.5 + (float(offset[1]) / av if av else 0.0))
        return {"fx": fx, "fy": fy, "cx": cx, "cy": cy, "width": float(width), "height": float(height)}

    def _object_gt(self, inst, cam_ctx, inst_data, inst_labels, bbox3d) -> Dict[str, Any]:
        import omni.replicator.core as rep

        out: Dict[str, Any] = {"obj_id": inst["obj_id"], "instance_id": inst["instance_id"]}
        prim_path = inst["prim_path"]

        # object world transform (row-vector local->world, stage units) — needed by pose
        # and keypoints; compute once if either is requested.
        obj_rv = None
        if self.want_pose or (self.want_keypoints and inst.get("keypoints_local")):
            obj_rv = np.asarray(rep.functional.utils.get_world_transform(inst["prim"]).GetMatrix())

        if self.want_pose and obj_rv is not None:
            pose_cam = _to_metres(cam_ctx.T_cam_world @ obj_rv.T, cam_ctx.mpu)  # object->camera, metres
            # obj_rv (local->world) often bakes a uniform modeling scale — e.g. a mm-authored
            # CAD mesh placed at scale 0.001 to be metre-sized — so the rotation block is
            # scale*R and det!=1. 6D-pose GT must be a rigid transform: strip the scale so
            # cam_R is a pure rotation (det=+1).
            pose_cam[:3, :3] = _orthonormalize_rotation(pose_cam[:3, :3])
            origin_local = inst.get("origin_local")
            if origin_local is not None:
                # Re-define the pose origin at a configured object-local point (same frame as
                # keypoints) so translation reports that point in camera-frame metres, keeping
                # the orientation. Consumer CAD must share this origin. (CONSUMER_6DPOSE.md §4-E)
                o_cam, _ = cam_ctx.project(np.asarray([origin_local], dtype=np.float64), obj_rv)
                pose_cam[:3, 3] = o_cam[0]
            out["pose_cam"] = pose_cam.tolist()

        if inst.get("parts"):
            # part masks live in the semantic/instance channels (each part sub-prim carries its
            # own class); expose the part list so downstream can find them by class/prim.
            out["parts"] = [{"name": p["name"], "class": p["class"], "prim_path": p["prim_path"]}
                            for p in inst["parts"]]

        # Render instance ids matched to this object's prim — lets mask-based writers
        # (e.g. BOP) slice per-object masks. Cheap; ignored by writers that don't use it.
        if inst_labels:
            ids = _matching_ids(inst_labels, prim_path)
            if ids:
                out["_instance_ids"] = ids

        if self.want_bbox_2d and inst_data is not None and inst_labels:
            bb = _bbox_from_instance_mask(inst_data, inst_labels, prim_path)
            if bb is not None:
                out["bbox_2d"] = bb

        if self.want_bbox_3d and bbox3d is not None:
            bb3 = _bbox_3d_for_prim(bbox3d, prim_path, cam_ctx)
            if bb3 is not None:
                out["bbox_3d"] = bb3

        if self.want_keypoints and obj_rv is not None and inst.get("keypoints_local"):
            pts_local = np.asarray(inst["keypoints_local"], dtype=np.float64).reshape(-1, 3)
            pts_cam, pts_2d = cam_ctx.project(pts_local, obj_rv)
            out["keypoints_3d"] = pts_cam.tolist()  # camera frame, metres
            out["keypoints_2d"] = pts_2d.tolist()   # [[u, v, visible], ...]
        return out


class _CamCtx:
    """Per-frame camera transforms + a projector for 3D->2D (keypoints, bbox_3d corners).

    All matrices are row-vector (Omniverse convention). `project` takes points in some
    local frame plus that frame's local->world (row-vector) matrix and returns
    (points_in_camera_metres, pixels[[u, v, visible], ...]).
    """

    def __init__(self, view_rv, proj_rv, T_cam_world, mpu, width, height):
        self.view_rv = view_rv
        self.proj_rv = proj_rv
        self.T_cam_world = T_cam_world
        self.mpu = mpu
        self.width = width
        self.height = height

    def project(self, points_local: np.ndarray, local_to_world_rv: np.ndarray):
        pts = np.asarray(points_local, dtype=np.float64).reshape(-1, 3)
        homo = np.hstack([pts, np.ones((len(pts), 1))])          # (N,4)
        view = homo @ local_to_world_rv @ self.view_rv           # camera/view frame (stage units)
        clip = view @ self.proj_rv                               # clip space
        w = clip[:, 3]
        safe_w = np.where(np.abs(w) < 1e-9, 1e-9, w)
        ndc = clip[:, :3] / safe_w[:, None]
        u = (ndc[:, 0] * 0.5 + 0.5) * self.width
        v = (1.0 - (ndc[:, 1] * 0.5 + 0.5)) * self.height        # image y is down
        in_front = w > 0
        visible = in_front & (u >= 0) & (u < self.width) & (v >= 0) & (v < self.height)
        pts_2d = np.column_stack([u, v, visible.astype(np.float64)])
        pts_cam_m = view[:, :3] * self.mpu                        # camera-frame metres
        return pts_cam_m, pts_2d


def _safe_inv(T: np.ndarray) -> np.ndarray:
    """Invert a 4x4, falling back to pseudo-inverse if the matrix is (near) singular.

    A singular cameraViewTransform means the SyntheticData graph had not produced valid
    camera data yet (see the warm-up step in run_sdg); warn rather than crash the run.
    """
    try:
        return np.linalg.inv(T)
    except np.linalg.LinAlgError:
        print("[sdg][collector] singular cameraViewTransform — using pinv "
              "(camera_params not ready?). Check warm-up / step count.")
        return np.linalg.pinv(T)


def _orthonormalize_rotation(R: np.ndarray) -> np.ndarray:
    """Nearest proper rotation (orthonormal, det=+1) to a 3x3 that may carry the object's
    modeling scale/shear, via polar decomposition (SVD). USD object transforms frequently
    bake a uniform scale (a mm-authored mesh placed at 0.001 to be metre-sized), which would
    otherwise leave 6D-pose cam_R with det!=1 and break any BOP/pose consumer.
    """
    U, _, Vt = np.linalg.svd(np.asarray(R, dtype=np.float64))
    Rn = U @ Vt
    if np.linalg.det(Rn) < 0.0:  # reflection -> flip the least-significant singular axis
        U[:, -1] *= -1.0
        Rn = U @ Vt
    return Rn


def _to_metres(T: np.ndarray, mpu: float) -> np.ndarray:
    """Scale the translation column of a column-vector 4x4 to metres."""
    T = np.array(T, dtype=np.float64, copy=True)
    T[:3, 3] *= mpu
    return T


def _matching_ids(id_to_labels: Dict[Any, Any], prim_path: str) -> List[int]:
    """Instance ids whose labelled prim path is (a descendant of) prim_path."""
    ids = []
    for k, v in id_to_labels.items():
        label = v if isinstance(v, str) else str(v)
        if label == prim_path or label.startswith(prim_path + "/"):
            try:
                ids.append(int(k))
            except (TypeError, ValueError):
                pass
    return ids


def _bbox_from_instance_mask(inst_data, id_to_labels, prim_path) -> Optional[List[int]]:
    ids = _matching_ids(id_to_labels, prim_path)
    if not ids:
        return None
    mask = np.isin(inst_data, ids)
    if not mask.any():
        return None
    ys, xs = np.where(mask)
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    return [x0, y0, x1 - x0 + 1, y1 - y0 + 1]  # [x, y, w, h]


def _bbox_3d_for_prim(bbox3d: Dict[str, Any], prim_path: str, cam_ctx: "_CamCtx") -> Optional[Dict[str, Any]]:
    """Build a self-describing 3D bounding box dict for one object instance.

    Returns (all lengths in metres):
      extents_min/extents_max   : axis-aligned box in the box's LOCAL frame
      transform_local_to_world  : 4x4 column-vector (p_world = T @ p_local_box)
      corners_cam               : 8 corners in the CAMERA frame
      corners_2d                : 8 corners projected to pixels [[u, v, visible], ...]
      occlusion                 : 0=visible .. 1=occluded (-1 if undefined)
    """
    data = bbox3d.get("data")
    prim_paths = list(bbox3d.get("info", {}).get("primPaths", []))
    if data is None or not len(data):
        return None
    idx = None
    for i, p in enumerate(prim_paths):
        p = p if isinstance(p, str) else str(p)
        if p == prim_path or p.startswith(prim_path + "/"):
            idx = i
            break
    if idx is None or idx >= len(data):
        return None
    row = data[idx]
    mpu = cam_ctx.mpu
    lo = (float(row["x_min"]), float(row["y_min"]), float(row["z_min"]))   # stage units, local
    hi = (float(row["x_max"]), float(row["y_max"]), float(row["z_max"]))
    tf_rv = np.asarray(row["transform"], dtype=np.float64).reshape(4, 4)   # row-vector local->world

    corners_local = np.array([[lo[0] if c & 1 else hi[0],
                               lo[1] if c & 2 else hi[1],
                               lo[2] if c & 4 else hi[2]] for c in range(8)])
    corners_cam, corners_2d = cam_ctx.project(corners_local, tf_rv)

    tf_col = tf_rv.T.copy()
    tf_col[:3, 3] *= mpu  # translation -> metres
    return {
        "extents_min": [lo[0] * mpu, lo[1] * mpu, lo[2] * mpu],
        "extents_max": [hi[0] * mpu, hi[1] * mpu, hi[2] * mpu],
        "transform_local_to_world": tf_col.tolist(),
        "corners_cam": corners_cam.tolist(),
        "corners_2d": corners_2d.tolist(),
        "occlusion": float(row["occlusionRatio"]) if "occlusionRatio" in row.dtype.names else None,
    }


def _jsonable_labels(labels) -> Optional[Dict[str, Any]]:
    if not labels:
        return None
    return {str(k): v for k, v in labels.items()}
