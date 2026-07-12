"""Localize Isaac Sim's background/DR asset pools into this project for OFFLINE generation.

WHY: the SDG randomizers swap the *ground material* (the surface the camera looks down at —
the visible "background") and the *dome HDRI* every frame. Isaac Sim does NOT ship those
libraries on disk; they live on the NVIDIA cloud assets server. To deploy/generate offline we
download a curated, realistic set ONCE into the repo, then configs reference the local dirs.

WHAT IT FETCHES (source of truth = sdg/assets.py):
  - realistic floor textures  {assets_root}/NVIDIA/Materials/{Base,vMaterials_2}/...  (enumerated)
        -> assets/textures/ground/
  - HDRI sky env maps         {assets_root}/NVIDIA/Assets/Skies/<Category>/*.hdr      (curated 15)
        -> assets/env/hdri/
  - env-USD backgrounds       {assets_root}/Isaac/Environments/<Name>/...  (--envs, opt-in)
        -> assets/env/usd/<name>/   (dependency-aware collect: stage + materials + textures)
`assets_root` is resolved at runtime by isaacsim.storage.native.get_assets_root_path() (the
S3/Nucleus root, e.g. ".../Assets/Isaac/5.0"). The exact cloud URLs fetched are recorded in
assets/ASSET_SOURCES.md so a from-scratch setup can be reproduced/audited later.

★ 6.0.1 API (verified against the install):
  - omni.client.list(url) -> (Result, [ListEntry(.relative_path, .flags)])   (nucleus.py:693)
  - omni.client.copy(src_url, dst_url, CopyBehavior.OVERWRITE) -> Result       (mobility_gen writer.py:60)
  - omni.client.make_file_url_if_possible(local_path) -> file:// url
  - SimulationApp is required: omni.client is a Kit extension (not importable standalone).

Usage (bundle python):
    /isaac-sim/python.sh tools/fetch_isaac_assets.py [--floors] [--skies] [--all]
        [--limit N] [--dry-run]
  default (no flag) = --all. --dry-run only enumerates + prints the URLs (no download).

Downloaded binaries are .gitignore'd (third-party); re-run this tool to regenerate them —
same reproducibility model as tools/import_cad.py (CAD -> mesh.usd).
"""
import argparse
import os
import sys

from isaacsim import SimulationApp

WS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WS_ROOT)

HDRI_DIR = os.path.join(WS_ROOT, "assets", "env", "hdri")
GROUND_DIR = os.path.join(WS_ROOT, "assets", "textures", "ground")
ENV_DIR = os.path.join(WS_ROOT, "assets", "env", "usd")
MANIFEST = os.path.join(WS_ROOT, "assets", "ASSET_SOURCES.md")

# Default env-USD presets to localize when `--envs` is given without an explicit list. Kept
# small (these stages + their deps are large); pass `--envs a,b,c` to pick others. Full preset
# list lives in sdg/assets.py::ISAAC_ENVIRONMENTS.
DEFAULT_ENVS = ["simple_room", "office"]


def _local_name(rel: str) -> str:
    """Unique, readable local filename from a cloud rel-path: parent + file, so textures from
    different material folders never collide (e.g. Oak/Oak_BaseColor.png -> Oak_BaseColor.png,
    kept distinct from a sibling by prefixing the parent dir when needed)."""
    parts = rel.strip("/").split("/")
    base = parts[-1]
    parent = parts[-2] if len(parts) >= 2 else ""
    # If the filename already starts with the parent (Oak/Oak_BaseColor.png), don't double it.
    if parent and not base.lower().startswith(parent.lower()):
        return f"{parent}_{base}"
    return base


_MANIFEST_PREAMBLE = (
    "# Asset sources (downloaded by tools/fetch_isaac_assets.py)\n"
    "One-time localization of Isaac Sim cloud assets for offline SDG. Each section is updated\n"
    "independently — running one category (e.g. `--envs`) does NOT wipe the others' records.\n"
    "Regenerate any section with `/isaac-sim/python.sh tools/fetch_isaac_assets.py ...`.\n"
)
_SECTION_ORDER = ["floors", "skies", "envs"]


def _write_manifest(root_url: str, new_sections: dict) -> None:
    """Merge this run's sections into ASSET_SOURCES.md, preserving sections from prior runs.
    Sections are delimited by `<!--sec:KEY-->` markers so re-running one category only replaces
    its own block. `new_sections` = {key: (header_str, [body_line, ...])}."""
    existing: dict = {}
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as f:
            content = f.read()
        for chunk in content.split("<!--sec:")[1:]:
            key, _, body = chunk.partition("-->")
            existing[key.strip()] = body
    for key, (header, body_lines) in new_sections.items():
        existing[key] = "\n## " + header + "\n" + "".join(body_lines)
    out = [_MANIFEST_PREAMBLE, f"\n**assets_root** = `{root_url}`\n"]
    ordered = [k for k in _SECTION_ORDER if k in existing] + \
              [k for k in existing if k not in _SECTION_ORDER]
    for key in ordered:
        out.append(f"<!--sec:{key}-->" + existing[key])
    with open(MANIFEST, "w") as f:
        f.write("".join(out))


def _collect_env(kit, src_url: str, dst_dir: str):
    """Dependency-aware localization of one env USD (stage + materials + textures + props) via
    omni.kit.usd.collect.Collector, remapping refs to the local copy. Returns (ok, local_root).
    Driven by pumping kit.update() until the async collect finishes (standalone pattern)."""
    import asyncio

    from isaacsim.core.utils.extensions import enable_extension
    enable_extension("omni.kit.usd.collect")
    from omni.kit.usd.collect import Collector

    os.makedirs(dst_dir, exist_ok=True)
    collector = Collector(src_url, dst_dir, skip_existing=True)
    task = asyncio.ensure_future(collector.collect())
    while not task.done():
        kit.update()
    ok, root = task.result()
    return ok, root


def main() -> None:
    ap = argparse.ArgumentParser(description="Localize Isaac Sim DR/background asset pools.")
    ap.add_argument("--floors", action="store_true", help="fetch realistic ground textures")
    ap.add_argument("--skies", action="store_true", help="fetch HDRI sky env maps")
    ap.add_argument("--envs", nargs="?", const="__default__", default=None,
                    help="localize env-USD backgrounds (dependency-aware collect). Bare flag "
                         "= default set; or --envs warehouse,office,simple_room")
    ap.add_argument("--all", action="store_true", help="fetch floors + skies (NOT envs; envs "
                    "are large -> opt in with --envs)")
    ap.add_argument("--limit", type=int, default=48, help="max floor textures to enumerate")
    ap.add_argument("--dry-run", action="store_true", help="list URLs only, no download")
    args = ap.parse_args()
    # If any specific flag is given, do only those; with no flag at all, default to floors+skies
    # (never envs implicitly — they are large and opt-in only).
    any_specific = args.floors or args.skies or (args.envs is not None)
    do_floors = args.floors or args.all or not any_specific
    do_skies = args.skies or args.all or not any_specific
    do_envs = args.envs is not None
    env_names = (DEFAULT_ENVS if args.envs == "__default__"
                 else [e.strip() for e in args.envs.split(",") if e.strip()]) if do_envs else []

    kit = SimulationApp({"headless": True})
    sections: dict = {}  # key -> (header, [body_lines]); merged into the manifest at the end
    try:
        from sdg import assets

        root = assets.isaac_assets_root()
        print(f"[fetch] assets root: {root}", flush=True)

        # ---- realistic floor textures (enumerated) --------------------------------------
        if do_floors:
            rels = assets.discover_ground_texture_rels(limit=args.limit)
            print(f"[fetch] discovered {len(rels)} floor texture(s)", flush=True)
            body = []
            for rel in rels:
                src = root + rel
                dst = os.path.join(GROUND_DIR, _local_name(rel))
                status = "DRY" if args.dry_run else assets.download(src, dst)
                print(f"  [{status}] {rel} -> {os.path.basename(dst)}", flush=True)
                body.append(f"- `{os.path.basename(dst)}` <- `{src}`\n")
            sections["floors"] = (f"Floor textures -> `assets/textures/ground/` ({len(rels)})", body)

        # ---- HDRI skies (curated 15) ----------------------------------------------------
        if do_skies:
            rels = assets.sky_rels()
            print(f"[fetch] {len(rels)} sky HDRI(s)", flush=True)
            body = []
            for rel in rels:
                src = root + rel
                dst = os.path.join(HDRI_DIR, os.path.basename(rel))
                status = "DRY" if args.dry_run else assets.download(src, dst)
                print(f"  [{status}] {rel} -> {os.path.basename(dst)}", flush=True)
                body.append(f"- `{os.path.basename(dst)}` <- `{src}`\n")
            sections["skies"] = (f"HDRI skies -> `assets/env/hdri/` ({len(rels)})", body)

        # ---- env-USD backgrounds (dependency-aware collect) -----------------------------
        if do_envs:
            print(f"[fetch] {len(env_names)} env(s): {env_names}", flush=True)
            body = []
            for name in env_names:
                rel = assets.ISAAC_ENVIRONMENTS.get(name.lower())
                if not rel:
                    print(f"  [skip] unknown env preset '{name}' (have {sorted(assets.ISAAC_ENVIRONMENTS)})",
                          flush=True)
                    continue
                src = root + rel
                dst = os.path.join(ENV_DIR, name.lower())
                if args.dry_run:
                    print(f"  [DRY] {name} <- {src}", flush=True)
                else:
                    ok, local_root = _collect_env(kit, src, dst)
                    print(f"  [{'ok' if ok else 'FAIL'}] {name} <- {src}\n           -> {local_root}",
                          flush=True)
                body.append(f"- `{name}/` <- `{src}` (collect: stage + dependencies)\n")
            sections["envs"] = ("Env-USD backgrounds -> `assets/env/usd/<name>/`", body)

        if not args.dry_run:
            _write_manifest(root, sections)
            print(f"[fetch] merged source manifest -> {MANIFEST}", flush=True)
        print("[fetch] done.", flush=True)
    finally:
        # SimulationApp.close() os._exit's — everything above must have finished already.
        kit.close()


if __name__ == "__main__":
    main()
