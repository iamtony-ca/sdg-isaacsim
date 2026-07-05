"""Import a CAD/mesh file into the SDG asset convention: assets/obj/<obj_id>/mesh.usd.

Converts STL/OBJ/FBX -> USD via omni.kit.asset_converter (verified 6.0.1 API:
standalone_examples/api/omni.kit.asset_converter/asset_usd_converter.py), then wraps it in
a metres-unit, origin-centred asset so the SDG scene builder can reference it directly.

Why the wrapper: raw CAD meshes carry no reliable unit (STL numbers are usually millimetres)
and are not centred on their origin. The SDG pipeline assumes metres (metersPerUnit=1) with
the object roughly centred (pose/camera look_at target it). So we bake a single transform op
= scale(input-units -> metres) and translate(-bbox_centre) over the converted geometry.

Object identity stays generic: the asset lives under an obj_id folder and is named only in
config — no object name is hardcoded (CLAUDE.md principle 2).

Usage (bundle python):
    /isaac-sim/python.sh tools/import_cad.py <input.stl> --obj-id obj_000 \
        [--input-units mm|cm|m] [--up-axis Z|Y] [--no-center] [--load-materials]
"""
import argparse
import asyncio
import os
import sys

from isaacsim import SimulationApp

_UNIT_FACTOR = {"mm": 0.001, "cm": 0.01, "m": 1.0}

WS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


async def _convert(in_file: str, out_file: str, load_materials: bool) -> bool:
    import omni.kit.asset_converter as ac

    ctx = ac.AssetConverterContext()
    ctx.ignore_materials = not load_materials
    task = ac.get_instance().create_converter_task(in_file, out_file, lambda p, n: None, ctx)
    ok = False
    while not ok:
        ok = await task.wait_until_finished()
        if not ok:
            await asyncio.sleep(0.1)
    return ok


def _world_bbox(stage, prim):
    from pxr import Usd, UsdGeom

    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(),
                             [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    rng = cache.ComputeWorldBound(prim).ComputeAlignedRange()
    lo, hi = rng.GetMin(), rng.GetMax()
    center = [(lo[i] + hi[i]) * 0.5 for i in range(3)]
    extents = [hi[i] - lo[i] for i in range(3)]
    return center, extents


def _build_asset(raw_usd: str, out_usd: str, unit_factor: float, up_axis: str, center: bool):
    from pxr import Gf, Usd, UsdGeom

    raw = Usd.Stage.Open(raw_usd)
    src_default = raw.GetDefaultPrim()
    c, ext = _world_bbox(raw, src_default)
    if not center:
        c = [0.0, 0.0, 0.0]

    # Build an in-memory wrapper, then FLATTEN so mesh.usd is self-contained (no _raw.usd
    # dependency, so no absolute/relative reference-path portability issues).
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z if up_axis.upper() == "Z" else UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)

    obj = UsdGeom.Xform.Define(stage, "/obj")
    stage.SetDefaultPrim(obj.GetPrim())
    # Single transform op (row-vector: P' = P * M): scale to metres about the bbox centre.
    uf = unit_factor
    M = Gf.Matrix4d(
        uf, 0, 0, 0,
        0, uf, 0, 0,
        0, 0, uf, 0,
        -uf * c[0], -uf * c[1], -uf * c[2], 1,
    )
    obj.AddTransformOp().Set(M)

    geo = stage.DefinePrim("/obj/geo")
    geo.GetReferences().AddReference(os.path.abspath(raw_usd))
    stage.Flatten().Export(out_usd)  # inline geometry -> self-contained mesh.usd

    verify = Usd.Stage.Open(out_usd)
    fc, fext = _world_bbox(verify, verify.GetDefaultPrim())
    return fext, [e * unit_factor for e in ext]


def main() -> None:
    ap = argparse.ArgumentParser(description="Import a CAD/mesh file into assets/obj/<obj_id>/mesh.usd")
    ap.add_argument("input", help="path to input mesh (stl/obj/fbx)")
    ap.add_argument("--obj-id", required=True, help="target obj_id (folder assets/obj/<obj_id>/)")
    ap.add_argument("--input-units", choices=list(_UNIT_FACTOR), default="mm")
    ap.add_argument("--up-axis", choices=["Z", "Y", "z", "y"], default="Z")
    ap.add_argument("--no-center", action="store_true", help="do not recentre on the bbox centre")
    ap.add_argument("--load-materials", action="store_true")
    args = ap.parse_args()

    in_path = os.path.abspath(args.input)
    if not os.path.isfile(in_path):
        raise SystemExit(f"input not found: {in_path}")

    out_dir = os.path.join(WS_ROOT, "assets", "obj", args.obj_id)
    os.makedirs(out_dir, exist_ok=True)
    raw_usd = os.path.join(out_dir, "_raw.usd")
    mesh_usd = os.path.join(out_dir, "mesh.usd")
    for p in (raw_usd, mesh_usd):
        if os.path.exists(p):
            os.remove(p)

    kit = SimulationApp({"headless": True})
    try:
        from isaacsim.core.utils.extensions import enable_extension
        enable_extension("omni.kit.asset_converter")

        print(f"[import_cad] converting {in_path} -> {raw_usd}")
        ok = asyncio.get_event_loop().run_until_complete(_convert(in_path, raw_usd, args.load_materials))
        if not ok or not os.path.isfile(raw_usd):
            raise SystemExit("[import_cad] asset conversion failed")

        final_ext, expected = _build_asset(
            raw_usd, mesh_usd, _UNIT_FACTOR[args.input_units], args.up_axis, not args.no_center
        )
        if os.path.exists(raw_usd):
            os.remove(raw_usd)  # mesh.usd is flattened/self-contained
        print(f"[import_cad] wrote {mesh_usd} (self-contained)")
        print(f"[import_cad] final size (m): {[round(x,4) for x in final_ext]}  "
              f"(expected ~{[round(x,4) for x in expected]})")
        print(f"[import_cad] obj_id '{args.obj_id}' ready — reference it from a config's objects[].obj_id")
    finally:
        kit.close()


if __name__ == "__main__":
    main()
