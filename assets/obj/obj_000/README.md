# obj_000 — example target object slot

Drop a target object's assets here (kept generic; obj_id = folder name):
  mesh.usd / mesh.obj      geometry (meter units, Z-up — align before use)
  material/                looks/PBR
  semantic.json            class/label metadata
  exemplar.png             (optional) reference crop for exemplar-based segmentation
  keypoints.json           (optional) 3D keypoints in the object's LOCAL frame (mesh units)

keypoints.json format (any of):
  [[x,y,z], ...]
  {"keypoints": [[x,y,z], ...]}
  {"keypoints": [{"name": "tip", "point": [x,y,z]}, ...]}
When present and `annotators.keypoints: true`, each frame's meta records per-object
`keypoints_2d` ([[u,v,visible],...]) and `keypoints_3d` (camera frame, metres).

Reference from a config with `objects: [{obj_id: obj_000, ...}]`. Never hardcode a
specific object name elsewhere — add new objects as new obj_id folders.

## Current asset (regenerate — mesh.usd is gitignored)

`mesh.usd` here is generated from the sample CAD in `assets/cad/6-inch-wafer-cassette/`
(a 6-inch wafer cassette, ~0.181 × 0.176 × 0.153 m). It is NOT committed (USD is
gitignored), so after a fresh clone regenerate it with the bundle python:

    /isaac-sim/python.sh tools/import_cad.py \
      "assets/cad/6-inch-wafer-cassette/Wafer Cassette_6 Inch - 25 Wafer Capacity.stl" \
      --obj-id obj_000 --input-units mm --up-axis Z

The tool converts STL/OBJ/FBX -> USD, scales input-units -> metres, and centres on the
bbox, producing a self-contained metres-unit `mesh.usd`. To use a different object, point
`import_cad.py` at another mesh and pick an `--obj-id` (the CAD sample is just one instance).
