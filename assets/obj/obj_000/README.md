# obj_000 — example target object slot

Drop a target object's assets here (kept generic; obj_id = folder name):
  mesh.usd / mesh.obj      geometry (meter units, Z-up — align before use)
  material/                looks/PBR
  semantic.json            class/label metadata
  exemplar.png             (optional) reference crop for exemplar-based segmentation
  keypoints.json           (optional) 3D keypoints in the object's LOCAL frame (mesh units)
  parts.json               (optional) sub-prim semantic parts (e.g. a top flange)

keypoints.json format (any of):
  [[x,y,z], ...]
  {"keypoints": [[x,y,z], ...]}
  {"keypoints": [{"name": "tip", "point": [x,y,z]}, ...]}
When present and `annotators.keypoints: true`, each frame's meta records per-object
`keypoints_2d` ([[u,v,visible],...]) and `keypoints_3d` (camera frame, metres).

parts.json format:
  {"parts": [{"name": "top_flange", "prim": "<sub-prim path in the object USD>",
              "class": "<semantic class, defaults to name>"}]}
`prim` is relative to the spawned object prim (e.g. "flange" or "geo/flange"). Each part's
sub-prim gets its own semantic class, so it shows up as a distinct region in the semantic
(and instance) segmentation output — slice a part mask by its class via `semantic_id_to_labels`
(labels merge hierarchy, e.g. "obj_000,top_flange"). Per-frame meta lists them under each
object's `parts` (name/class/prim_path).

Pose origin (per-run, in config): `objects[].origin` re-defines where the object frame's
origin sits for 6D-pose GT — `[x,y,z]` in this LOCAL frame (mesh units), or `{keypoint: <i>}`
to reuse a keypoint. Default = the asset's own origin (import_cad centres the bbox). Use it
to place the origin on an observed surface (see CONSUMER_6DPOSE.md §4-E); the consumer's CAD
model must share the same origin.

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
