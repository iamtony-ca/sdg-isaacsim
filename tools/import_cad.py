"""Import a CAD/mesh file into the SDG asset convention: assets/obj/<obj_id>/mesh.usd.

Two converter back-ends, picked automatically from the file extension (verified against the
6.0.1 install — see CLAUDE.md principle 4):

  * tessellated meshes (STL/OBJ/FBX/glTF) -> `omni.kit.asset_converter`
    (standalone_examples/api/omni.kit.asset_converter/asset_usd_converter.py)
  * B-rep CAD (STEP/IGES/CATIA/SolidWorks/Inventor/Parasolid/JT/...) -> `omni.kit.converter.hoops_core`
    (HOOPS Exchange). `omni.kit.asset_converter` REJECTS these with UNSUPPORTED_IMPORT_FORMAT.
    The exact supported list is the extension's own filter table:
    omni/kit/converter/hoops_core/impl/filters.py — queried here via `is_format_supported()`.

Either way the converted geometry is wrapped into a metres-unit, origin-centred asset so the
SDG scene builder can reference it directly.

Why the wrapper: raw CAD carries a unit we must normalize (tessellated meshes usually carry
none — STL numbers are conventionally millimetres; B-rep files DO carry one, e.g. a STEP
authored in inches reports metersPerUnit=0.0254) and is not centred on its origin. The SDG
pipeline assumes metres (metersPerUnit=1) with the object roughly centred (pose/camera look_at
target it). So we bake a single transform op = scale(input-units -> metres) and
translate(-bbox_centre) over the converted geometry.

`--input-units auto` (default) reads metersPerUnit from the converted stage — correct for B-rep
CAD, which records its authoring unit. Tessellated formats carry no unit, so pass the unit
explicitly (`--input-units mm`) for those; auto warns when it has to fall back.

Object identity stays generic: the asset lives under an obj_id folder and is named only in
config — no object name is hardcoded (CLAUDE.md principle 2).

Usage (bundle python):
    /isaac-sim/python.sh tools/import_cad.py <input.stp|.stl|...> --obj-id obj_000 \
        [--input-units auto|mm|cm|m|in] [--up-axis Z|Y] [--tess-lod 0..5] \
        [--no-center] [--load-materials]
"""
import argparse
import asyncio
import os
import sys

from isaacsim import SimulationApp

_UNIT_FACTOR = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254}

# Extensions handled by omni.kit.asset_converter; everything else is routed to the HOOPS
# (B-rep CAD) back-end, which validates the extension itself via is_format_supported().
_MESH_EXT = {".stl", ".obj", ".fbx", ".gltf", ".glb", ".usd", ".usda", ".usdc", ".usdz"}

# metersPerUnit that Kit stamps when the source carried no unit information — treating it as
# an authoritative scale would silently shrink/blow up the asset, so `auto` refuses it.
_KIT_DEFAULT_MPU = 0.01

WS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _is_brep(in_file: str) -> bool:
    """True when the input needs the HOOPS (B-rep CAD) back-end rather than asset_converter."""
    return os.path.splitext(in_file)[1].lower() not in _MESH_EXT


async def _convert_mesh(in_file: str, out_file: str, load_materials: bool) -> bool:
    """Tessellated mesh (STL/OBJ/FBX/glTF) -> USD via omni.kit.asset_converter."""
    import omni.kit.asset_converter as ac

    ctx = ac.AssetConverterContext()
    ctx.ignore_materials = not load_materials
    task = ac.get_instance().create_converter_task(in_file, out_file, lambda p, n: None, ctx)
    ok = await task.wait_until_finished()
    # NB: wait_until_finished() returns True even for UNSUPPORTED_IMPORT_FORMAT (observed with a
    # .stp), so the real gate is get_status() + the output file existing. The NVIDIA sample's
    # `while not ok: sleep` retry loop is deliberately not copied — it never terminates on a
    # genuine failure.
    status = task.get_status()
    status_ok = getattr(ac, "OmniConverterStatus", None)
    bad = (not ok) or (status_ok is not None and status != status_ok.OK)
    if bad:
        print(f"[import_cad] asset_converter failed [{status}]: {task.get_error_message()}")
        return False
    return True


async def _convert_brep(in_file: str, out_file: str, tess_lod: int) -> bool:
    """B-rep CAD (STEP/IGES/CATIA/...) -> USD via omni.kit.converter.hoops_core (HOOPS Exchange).

    create_converter_task is a coroutine returning (output_url, ConverterStatus(code, msg)).
    """
    import omni.kit.converter.hoops_core as hoops

    if not hoops.is_format_supported(in_file):
        print(f"[import_cad] '{os.path.splitext(in_file)[1]}' is not a supported CAD format "
              f"(see omni/kit/converter/hoops_core/impl/filters.py for the full list)")
        return False

    converter = hoops.get_instance()
    if converter is None:
        print("[import_cad] hoops_core extension did not start")
        return False

    # tessLOD = tessellation level of detail for the B-rep -> triangle mesh step.
    url, status = await converter.create_converter_task(in_file, out_file, {"tessLOD": str(tess_lod)})
    if status.error_code != 0 or not url:
        print(f"[import_cad] HOOPS conversion failed [{status.error_code}]: {status.error_msg}")
        return False
    return True


def _resolve_unit_factor(raw_usd: str, requested: str) -> float:
    """Scale factor (input units -> metres). `auto` reads metersPerUnit off the converted stage."""
    from pxr import Usd, UsdGeom

    if requested != "auto":
        return _UNIT_FACTOR[requested]

    mpu = UsdGeom.GetStageMetersPerUnit(Usd.Stage.Open(raw_usd))
    if mpu and mpu > 0 and abs(mpu - _KIT_DEFAULT_MPU) > 1e-12:
        print(f"[import_cad] --input-units auto -> metersPerUnit={mpu} from the converted stage")
        return float(mpu)

    print(f"[import_cad] WARNING: converted stage reports metersPerUnit={mpu}, which carries no "
          f"usable unit (tessellated formats store none). Falling back to mm — pass "
          f"--input-units explicitly if the printed size below is wrong.")
    return _UNIT_FACTOR["mm"]


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
    ap.add_argument("input", help="path to input CAD/mesh (stp/step/igs/CATPart/sldprt/ipt/... or stl/obj/fbx)")
    ap.add_argument("--obj-id", required=True, help="target obj_id (folder assets/obj/<obj_id>/)")
    ap.add_argument("--input-units", choices=["auto"] + list(_UNIT_FACTOR), default="auto",
                    help="scale of the source. 'auto' reads metersPerUnit off the converted stage "
                         "(correct for B-rep CAD, which records its authoring unit); tessellated "
                         "meshes carry none, so pass mm/cm/m/in for those.")
    ap.add_argument("--up-axis", choices=["Z", "Y", "z", "y"], default="Z")
    ap.add_argument("--tess-lod", type=int, default=2,
                    help="B-rep tessellation level of detail (HOOPS tessLOD, default 2). Higher = "
                         "finer triangles = heavier asset. Ignored for tessellated inputs.")
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

    brep = _is_brep(in_path)
    kit = SimulationApp({"headless": True})
    failure = None
    try:
        from isaacsim.core.utils.extensions import enable_extension
        if brep:
            # hoops_core needs converter.common; both are shipped with 6.0.1 but are not on by default.
            for ext in ("omni.kit.converter.common", "omni.kit.converter.hoops_core"):
                if not enable_extension(ext):
                    raise SystemExit(f"[import_cad] could not enable {ext}")
        else:
            enable_extension("omni.kit.asset_converter")

        backend = "hoops (B-rep CAD)" if brep else "asset_converter (mesh)"
        print(f"[import_cad] converting {in_path} -> {raw_usd}  [{backend}]")
        loop = asyncio.get_event_loop()
        if brep:
            ok = loop.run_until_complete(_convert_brep(in_path, raw_usd, args.tess_lod))
        else:
            ok = loop.run_until_complete(_convert_mesh(in_path, raw_usd, args.load_materials))
        if not ok or not os.path.isfile(raw_usd):
            raise SystemExit("[import_cad] conversion failed — no USD produced")

        unit_factor = _resolve_unit_factor(raw_usd, args.input_units)
        final_ext, expected = _build_asset(
            raw_usd, mesh_usd, unit_factor, args.up_axis, not args.no_center
        )
        if os.path.exists(raw_usd):
            os.remove(raw_usd)  # mesh.usd is flattened/self-contained
        print(f"[import_cad] wrote {mesh_usd} (self-contained)")
        print(f"[import_cad] final size (m): {[round(x,4) for x in final_ext]}  "
              f"(expected ~{[round(x,4) for x in expected]})")
        print("[import_cad] ^ sanity-check this against the real part; if it is off by a unit "
              "factor, re-run with an explicit --input-units")
        print(f"[import_cad] obj_id '{args.obj_id}' ready — reference it from a config's objects[].obj_id")
    except SystemExit as e:
        failure = str(e)
    except Exception as e:  # noqa: BLE001 — report before the fast-shutdown swallows it
        failure = f"[import_cad] {type(e).__name__}: {e}"
    finally:
        if failure:
            # SimulationApp.close() ends the process with os._exit(0) (CLAUDE.md pitfall 1), which
            # would report success to callers (setup_assets.py runs this as a subprocess). Print
            # the reason and exit non-zero HERE, before close() can mask it.
            print(failure, file=sys.stderr)
            sys.stderr.flush()
            sys.stdout.flush()
            os._exit(2)
        kit.close()


if __name__ == "__main__":
    main()
